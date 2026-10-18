import json
import pytest
from rag_arbiter.application.exhaustive import (ExhaustiveNoRAG, ExhaustiveContextBatcher,
    compare_conditions, referenced_retry, resolve_references)
from rag_arbiter.application.grounding import numeric_support
from rag_arbiter.config import LLMConfig


def batch():
    doc=dict(document_id='random-id',document_version_id='version-a',file_name='neutral.docx',
             locator_kind='structure',pages=[],page_count=0,
             blocks=[dict(block_id='block-a',reading_order=0,page_number=0,text='Original wording. Report is due in 7 days.')])
    return ExhaustiveContextBatcher(LLMConfig(full_document_context_budget=24000),'question').build([doc])[0]


def test_twenty_three_valid_items_survive_one_invalid():
    b=batch()
    items=[dict(unit_id='U1',statement=f'Fact {i}',evidence_text='Original wording.') for i in range(23)]
    items.insert(11,dict(unit_id='U1',statement='bad',evidence_text='Changed wording.'))
    result=ExhaustiveNoRAG.parse(json.dumps(dict(relevant=True,findings=items)),b)
    assert len(result.findings)==23
    assert result.status=='PARTIAL'
    assert result.rejected_items==[dict(index=11,status='UNSUPPORTED',reason='INVALID_EVIDENCE')]
    assert all(f.document_version_id=='version-a' for f in result.findings)


def test_immutable_reference_resolves_original_text_and_recovers_siblings():
    b=batch();batcher=ExhaustiveContextBatcher(LLMConfig(full_document_context_budget=24000),'q')
    _,quotes=referenced_retry(batcher,b)
    raw=json.dumps(dict(relevant=True,findings=[dict(evidence_ref='Q1',statement='valid'),dict(evidence_ref='Q999',statement='bad')]))
    result=ExhaustiveNoRAG.parse(resolve_references(raw,quotes),b)
    assert len(result.findings)==1 and len(result.rejected_items)==1
    assert result.findings[0].evidence_text==b.units[0].text


@pytest.mark.parametrize('claim,quote,expected',[
    ('Дата 01.02.2014','Дата «01» февраля 2014 г.',True),
    ('Дата 02.02.2014','Дата «01» февраля 2014 г.',False),
    ('Цена 02 рубля','Цена 2 рубля',False),
    ('Дата 31.02.2014','Дата «31» февраля 2014 г.',False),
    ('Дата 01.02.2014 и сумма 99','Дата «01» февраля 2014 г. и сумма 98',False),
])
def test_numeric_normalization_is_date_scoped(claim,quote,expected):
    assert numeric_support(claim,quote)==expected


def condition(doc,value,role='subagent',period='after month',kind='report_deadline'):
    return dict(reference='E'+doc,evidence=[dict(document_id=doc,document_version_id='v'+doc,block_id='b'+doc,
         condition=dict(condition_type=kind,value=value,unit='days',period=period,applicable_role=role))])


def test_comparison_separates_roles_triggers_and_missing_documents():
    same=compare_conditions([condition('1','7'),condition('2','7')],['1','2'])
    assert same[0]['comparison_status']=='SAME'
    different=compare_conditions([condition('1','7'),condition('2','5')],['1','2'])
    assert different[0]['comparison_status']=='DIFFERENT'
    mixed=compare_conditions([condition('1','7'),condition('2','5',role='agent')],['1','2'])
    assert len(mixed)==2 and all(c['comparison_status']=='INSUFFICIENT_DATA' for c in mixed)
    triggers=compare_conditions([condition('1','7'),condition('2','7',period='after receipt')],['1','2'])
    assert triggers[0]['comparison_status']=='INSUFFICIENT_DATA'
    missing=compare_conditions([condition('1','7')],['1','2'])
    assert missing[0]['comparison_status']=='INSUFFICIENT_DATA'


def test_multispan_bundle_keeps_offsets_and_rejects_changed_version():
    from rag_arbiter.application.grounding import CitationBuilder,GroundedClaim
    class Store:
        def __init__(self):
            self.rows={'chunks':{'c':dict(document_id='d',chunk_id='c',source_block_ids=['a','b'])},
                'document_blocks':{key:dict(document_id='d',page_number=0,recognition_id='v1',text=text)
                    for key,text in [('a','Person Alpha is the agent.'),('b','Contract dated 01.02.2014.')]}}
        def get(self,table,key):return self.rows[table].get(key)
    store=Store();builder=CitationBuilder(store)
    source=dict(reference='S1',document_id='d',chunk_id='c',source_block_ids=['a','b'],text='Person Alpha is the agent.\nContract dated 01.02.2014.')
    candidates=builder.build(GroundedClaim(claim_id='C1',text=source['text']),source)
    bundle=next(c for c in candidates if c.locator.get('kind')=='evidence_bundle')
    assert len(bundle.parts)==2 and builder.exact(bundle)
    assert bundle.document_version_id=='v1'
    store.rows['document_blocks']['b']['recognition_id']='v2'
    assert not builder.exact(bundle)
    assert not any(c.locator.get('kind')=='evidence_bundle' for c in builder.build(GroundedClaim(claim_id='C1',text=source['text']),source))


def test_routing_projection_keeps_contract_anchors_without_repeating_long_facts():
    from rag_arbiter.application.dialogue import AnswerModeRouter,DialogueWorkingContext,Referent
    from rag_arbiter.llm import LLMResult
    ctx=DialogueWorkingContext(session_id='s',entities_json=[dict(kind='CONTRACT',document_id='d',document_version_id='v',party='A')],referents_json=[Referent(referent_id='r',label='Verified set',canonical_text='Verified fact. '*2000,claim_ids=['c'],source_turn_id='t',source_message_id='m')])
    original=ctx.model_dump_json()
    class Provider:
        def generate(self,request):
            assert len(request.question.encode())<24000
            payload=json.loads(request.question[request.question.index('{"question":'):])
            assert payload['context']['entities_json'][0]['document_version_id']=='v'
            assert payload['context']['referents_json'][0]['id']=='r'
            return LLMResult(model='test',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question='compare',topic='contracts',reference_kind='ANAPHORA',full_coverage=True)))
    intent=AnswerModeRouter(lambda cfg:Provider(),LLMConfig(full_document_context_budget=28096)).consult('compare',[],ctx,[])
    assert intent.reference_kind=='ANAPHORA' and ctx.model_dump_json()==original


def test_pdf_bundle_pins_document_version_not_individual_page_recognition():
    from rag_arbiter.application.grounding import CitationBuilder,GroundedClaim
    class Store:
        def __init__(self):
            blocks={key:dict(block_id=key,document_id='d',page_number=i,recognition_id='page'+key,text=text)
                    for i,(key,text) in enumerate([('a','Party Alpha is agent.'),('b','Date 01.02.2014.')],1)}
            self.rows={'chunks':{'c':dict(document_id='d',chunk_id='c',source_block_ids=['a','b'])},
                       'document_blocks':blocks,'normalized_documents':{'version':dict(document_id='d',blocks=list(blocks.values()))}}
        def get(self,table,key):return self.rows[table].get(key)
    store=Store();builder=CitationBuilder(store)
    source=dict(reference='S1',document_id='d',document_version_id='version',chunk_id='c',source_block_ids=['a','b'],text='Party Alpha is agent.\nDate 01.02.2014.')
    claim=GroundedClaim(claim_id='C1',text=source['text'])
    bundle=next(c for c in builder.build(claim,source) if c.locator.get('kind')=='evidence_bundle')
    assert bundle.document_version_id=='version' and builder.exact(bundle)
    store.rows['normalized_documents']['version']['blocks']=store.rows['normalized_documents']['version']['blocks'][:1]
    assert not builder.exact(bundle)
    assert not any(c.locator.get('kind')=='evidence_bundle' for c in builder.build(claim,source))


@pytest.mark.parametrize('proposed,source,expected',[
    ('ООО «Север»»','ООО «Север»','ООО «Север»'),
    ('ООО "Север"','ООО «Север»','ООО «Север»'),
    ('ООО «Север»','ООО «Юг»','ООО «Север»'),
    ('Иванов','Ивановский','Иванов'),
    ('Фонд 101','Фонд 100','Фонд 101'),
])
def test_field_typography_returns_only_source_text_without_fuzzy_names(proposed,source,expected):
    from rag_arbiter.application.contract_extraction import canonical_field_value
    assert canonical_field_value(proposed,source)==expected


def test_semantic_review_batching_accounts_for_response_capacity():
    from test_evidence_review import reviewer
    review=reviewer([])
    batches=review.batches('FACTS',[dict(id=str(i),texts=['short source']) for i in range(23)])
    assert len(batches)>=3 and sum(map(len,batches))==23 and max(map(len,batches))<=8


def test_grouped_evidence_references_are_complete_and_bounded():
    from rag_arbiter.application.contract_extraction import normalize_evidence_markers
    allowed={'E1','E2','E3'}
    assert normalize_evidence_markers('Fact [E1, E3].',allowed)=='Fact [E1] [E3].'
    assert normalize_evidence_markers('Fact [E1–E3].',allowed)=='Fact [E1] [E2] [E3].'
    with pytest.raises(ValueError):normalize_evidence_markers('Fact [E1, E99].',allowed)
    with pytest.raises(ValueError):normalize_evidence_markers('Fact [E1–E999999].',allowed)


def test_public_map_uses_single_id_space_and_accounts_for_its_actual_prompt():
    b=batch();cfg=LLMConfig(full_document_context_budget=24000)
    batcher=ExhaustiveContextBatcher(cfg,'compare',immutable=True)
    request,refs=referenced_retry(batcher,b)
    units=json.loads(request.question.split('\nUNITS:\n')[1])
    assert all('unit_id' not in u for u in units)
    assert len(refs)==len(b.units)
    assert batcher.request(b.units)==request


def test_multispan_map_preserves_role_and_name_from_same_version():
    from rag_arbiter.application.exhaustive import SourceUnit,ExhaustiveEvidenceMerger
    b=batch()
    b.units.append(SourceUnit(unit_id='role',document_id='random-id',document_version_id='version-a',file_title='neutral.docx',page=0,block_id='role-block',text='The duties are those of the subagent.'))
    quotes={('U1','Q1'):b.units[0].text,('U2','Q2'):b.units[1].text}
    raw=json.dumps(dict(relevant=True,findings=[dict(evidence_refs=['Q1','Q2'],statement='The subagent reports within 7 days.')]))
    result=ExhaustiveNoRAG.parse(resolve_references(raw,quotes),b)
    assert len(result.findings)==1
    assert {e['block_id'] for e in result.findings[0].evidence_bundle}=={'block-a','role-block'}
    merged=ExhaustiveEvidenceMerger().merge([result],1)
    assert len(merged[0]['evidence'])==2 and merged[0]['occurrence_count']==1
    b.units[1].document_version_id='other-version'
    with pytest.raises(ValueError,match='EVIDENCE_VERSION_MISMATCH'):ExhaustiveNoRAG.parse(resolve_references(raw,quotes),b)


def test_comparison_normalizes_only_controlled_period_aliases():
    rows=compare_conditions([condition('1','7',period='reporting_month_end'),condition('2','7',period='end of each calendar month from the start of the Contract')],['1','2'])
    assert len(rows)==1 and rows[0]['comparison_status']=='SAME'
    rows=compare_conditions([condition('1','7',period='reporting_month_end'),condition('2','7',period='reporting_month_end if premium is paid')],['1','2'])
    assert all(r['comparison_status']=='INSUFFICIENT_DATA' for r in rows)


def test_grouped_markers_render_as_public_sources_not_internal_e_ids():
    from rag_arbiter.application.contract_extraction import render_exhaustive_answer
    from rag_arbiter.llm import LLMResult
    findings=[dict(reference='E'+str(i),statements=['Common duty.'],evidence=[dict(document_id='d'+str(i),document_version_id='v',file_title='doc'+str(i),block_id='b'+str(i),page=0,evidence_text='Common duty.',statement='Common duty.',validation_status='CONFIRMED')]) for i in (1,2)]
    record=dict(question_text='duties',full_document_status='SUCCESS',full_document_answer='Common duty [E1, E2].',exhaustive=dict(documents_total=2,documents_covered=2,merged_findings=findings))
    class Provider:
        def generate(self,request):
            data=json.loads(request.question[request.question.index('{"question":'):])
            assert len(data['items'][0]['texts'])==2
            return LLMResult(model='test',status='SUCCESS',text=json.dumps(dict(decisions=[dict(id=i['id'],status='SUPPORTED',reason='supported',evidence=i['evidence_ids']) for i in data['items']])))
    text,citations=render_exhaustive_answer(record,lambda cfg:Provider(),LLMConfig())
    assert '[E' not in text and '[S1]' in text and '[S2]' in text
    assert len(citations)==2 and len(record['exhaustive']['verified_claims'])==1
