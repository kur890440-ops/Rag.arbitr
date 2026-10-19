import pytest
from rag_arbiter.application.workspace_validation import draft_batches,check_coverage,DraftClaims
from rag_arbiter.application.provider_failures import category
from rag_arbiter.llm import ProviderNotCalled

def test_all_draft_blocks_including_late_table_and_oversized():
    blocks=[dict(block_id=str(i),text='text '+str(i)) for i in range(75)]
    blocks.append(dict(block_id='table',text='x'*30000))
    batches=list(draft_batches(blocks))
    assert [b for batch in batches for b in batch]==blocks
    assert all(len(b)<=6 for b in batches)
    assert batches[-1][0]['text']=='x'*30000

@pytest.mark.parametrize('items', [[],[dict(block_id='missing',kind='EDITORIAL',reason='heading',complete=True)], [dict(block_id='b',kind='FACTUAL',reason='fact',complete=True)]])
def test_fail_closed_coverage(items):
    with pytest.raises(ValueError):check_coverage(DraftClaims(blocks=items),[dict(block_id='b',text='claim')])

def test_no_invented_assertion():
    result=DraftClaims(blocks=[dict(block_id='b',kind='FACTUAL',reason='fact',complete=True,claims=[dict(claim_id='C1',text='invented')])])
    with pytest.raises(ValueError,match='INVENTED_DRAFT_CLAIM'):check_coverage(result,[dict(block_id='b',text='original')])

def test_failure_categories_preserve_no_call_and_unknown():
    assert category(ProviderNotCalled())=='ADMISSION_REJECTED'
    assert category(TimeoutError())=='TIMEOUT'
    assert category(ValueError('some provider failure'))=='PROVIDER_ERROR'
    assert category(ValueError('INVALID_EVIDENCE'))=='INVALID_EVIDENCE'
from test_document_workspace import ws
from rag_arbiter.application.document_workspace import ToolInput

def test_backend_reads_only_previously_discovered_pinned_versions(ws):
    service,admin,_,_=ws
    service.knowledge.grants=lambda:{'d':dict(recognition_version='v',title='fixture')}
    with service.runs.db() as db:db.put('normalized_documents','v',dict(document_id='d',content_hash='h',blocks=[]))
    task=service.task(admin,'analyze')
    service.call(admin,task['id'],ToolInput(operation='search_documents'))
    def stop():raise RuntimeError('READS_COMPLETE')
    service.knowledge.snapshot=stop
    with pytest.raises(RuntimeError,match='READS_COMPLETE'):
        service.call(admin,task['id'],ToolInput(operation='analyze_documents',document_ids=['d']))
    task=service.get('workspace_tasks',task['id'],admin)
    assert task['sources']==[dict(document_id='d',version_id='v',content_hash='h')]
    assert task['tools'][-1]['initiator']=='BACKEND_REQUIRED_READ'
    with pytest.raises(ValueError,match='DOCUMENT_NOT_READ'):
        service.call(admin,task['id'],ToolInput(operation='analyze_documents',document_ids=['foreign']))

def test_backend_required_read_rejects_replaced_version(ws):
    service,admin,_,_=ws
    service.knowledge.grants=lambda:{'d':dict(recognition_version='old',title='fixture')}
    task=service.task(admin,'analyze');service.call(admin,task['id'],ToolInput(operation='search_documents'))
    service.knowledge.grants=lambda:{'d':dict(recognition_version='new',title='fixture')}
    from fastapi import HTTPException
    with pytest.raises(HTTPException):service.call(admin,task['id'],ToolInput(operation='analyze_documents',document_ids=['d']))
    assert not service.get('workspace_tasks',task['id'],admin)['sources']

def test_truncated_review_splits_only_failed_packet(monkeypatch):
    from rag_arbiter.application.evidence_review import EvidenceReview,Review
    from rag_arbiter.application.dialogue import StructuredProviderError
    from rag_arbiter.llm import LLMConfig
    seen=[]
    def generate(factory,config,prompt,payload,model):
        seen.append([x['claim'] for x in payload['items']])
        if len(seen)==1:raise StructuredProviderError('OUTPUT_TRUNCATED')
        return Review(decisions=[dict(id=x['id'],status='SUPPORTED',evidence=x['evidence_ids'],reason='test') for x in payload['items']])
    monkeypatch.setattr('rag_arbiter.application.evidence_review.structured',generate)
    review=EvidenceReview(None,LLMConfig(),'question')
    items=[dict(id=str(i),claim=str(i),texts=['evidence']) for i in range(4)]
    assert review.check('FACTS',items)=={'0','1','2','3'}
    assert seen==[['0','1','2','3'],['0','1'],['2','3']]
    assert review.trace[0]['retry']=='SPLIT_ONCE'

def test_table_review_has_each_row_with_its_own_citations():
    from rag_arbiter.application.workspace_validation import review_blocks
    from rag_arbiter.application.workspace_formats import create
    raw=create('docx','Review','| Contract | Deadline |\n| --- | --- |\n| A | 5 days [S1] |\n| B | 7 days [S2] |')
    blocks=review_blocks(raw,'docx');rows=[b for b in blocks if b['block_type']=='table']
    assert len(rows)==3
    assert '[S1]' in rows[1]['text'] and '[S2]' not in rows[1]['text']
    assert '[S2]' in rows[2]['text'] and '[S1]' not in rows[2]['text']
    assert len({r['block_id'] for r in rows})==3

def test_workspace_output_bounded_batches_preserve_every_block():
    from rag_arbiter.application.exhaustive import ExhaustiveContextBatcher
    from rag_arbiter.llm import LLMConfig
    blocks=[dict(block_id=str(i),page_number=0,reading_order=i,text='whole canonical block '+str(i)) for i in range(90)]
    doc=dict(document_id='d',file_name='fixture.docx',document_version_id='v',pages=[],blocks=blocks,locator_kind='structure')
    cfg=LLMConfig(full_document_context_budget=67000,exhaustive_max_units_per_batch=32)
    batches=ExhaustiveContextBatcher(cfg,'question',immutable=True).build([doc])
    assert [len(b.units) for b in batches]==[32,32,26]
    assert [u.text for b in batches for u in b.units]==[b['text'] for b in blocks]

def test_unknown_condition_trigger_keeps_fact_without_false_comparison():
    from rag_arbiter.application.exhaustive import ReferencedFinding,compare_conditions
    fact=ReferencedFinding.model_validate(dict(evidence_ref='Q1',statement='Report is required.',condition=dict(condition_type='report_deadline',value='5',unit='days',period=None,applicable_role='subagent')))
    assert fact.condition.period is None
    merged=[dict(reference='E1',evidence=[dict(document_id='d',condition=fact.condition.model_dump())])]
    assert compare_conditions(merged,['d','e'])==[]

def test_appendix_quotes_need_exact_pinned_provenance():
    from rag_arbiter.application.workspace_validation import canonical_appendix_checks
    citation=dict(source_id='S1',file_title='contract',document_id='d',document_version_id='v',quote='Exact source.',locator=dict(block_id='source',char_start=0,char_end=13))
    label='[S1] contract / версия v / '+str(citation['locator'])
    blocks=[dict(block_id='main',text='Exact source.'),dict(block_id='heading',text='Подтверждающие фрагменты:'),dict(block_id='label',text=label),dict(block_id='quote',text='Exact source.'),dict(block_id='invented',text='Not in source.')]
    checks=canonical_appendix_checks(blocks,[citation],{('d','v','source'):'Exact source.'})
    assert set(checks)=={'label','quote'}
    assert checks['quote']['document_version_id']=='v'
    assert canonical_appendix_checks(blocks,[citation],{('d','other','source'):'Exact source.'})=={}

def test_comparison_semantics_cannot_downgrade_to_point_lookup():
    from rag_arbiter.application.dialogue import QuestionIntent
    intent=QuestionIntent(question_type='COMPARISON',answer_mode='POINT_RAG',requires_full_coverage=False,requires_counting=False,requires_frequency_analysis=False,requires_cross_document_comparison=True,requires_prior_referents=True,reason='compare selected documents',confidence=.9)
    assert intent.answer_mode=='EXHAUSTIVE_NO_RAG' and intent.requires_full_coverage

def test_draft_review_uses_literal_text_and_ignores_model_rewriting(monkeypatch):
    from rag_arbiter.application.workspace_validation import extract_batches,DraftDecisions
    from rag_arbiter.llm import LLMConfig
    def classify(*args):
        return DraftDecisions(blocks=[dict(block_id='B1',kind='FACTUAL',reason='test',subject='Party',predicate='reports',object='5 days')])
    monkeypatch.setattr('rag_arbiter.application.workspace_validation.structured',classify)
    block=dict(block_id='canonical-draft-span',text='Report: 5 days. [S1]')
    batch,result=next(extract_batches(None,LLMConfig(),[block]))
    assert result.blocks[0].claims[0].text=='Report: 5 days.'
    assert result.blocks[0].block_id=='canonical-draft-span'
