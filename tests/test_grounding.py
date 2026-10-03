import json
import pytest
from rag_arbiter.application.grounding import (AnswerContract, CitationBuilder, GroundingValidator,
    GroundedClaim, grounded_generation, evaluate_grounding)
from rag_arbiter.llm import LLMResult
from rag_arbiter.reranking import RerankedCandidate


class Store:
    def __init__(self):
        self.rows = {'chunks': {'c': dict(chunk_id='c', document_id='d', source_block_ids=['b'])},
            'document_blocks': {'b': dict(document_id='d', page_number=8, section='Payment',
                recognition_id='page8', text='Payment is 100 rubles.\nThe deadline is 10 days.')}}
    def get(self, table, key): return self.rows[table].get(key)


class Reranker:
    def __init__(self, score=.95): self.score=score; self.calls=[]
    def rerank(self, question, candidates):
        self.calls.append((question,candidates))
        return [RerankedCandidate(**c.model_dump(),rerank_rank=i+1,rerank_score=self.score) for i,c in enumerate(candidates)]


@pytest.fixture
def setup():
    store=Store();builder=CitationBuilder(store)
    source=dict(reference='S1',document_id='d',chunk_id='c',anchor_chunk_ids=['c'],source_block_ids=['b'],
        file_name='file.pdf',text=store.rows['document_blocks']['b']['text'],score=.4,rerank_score=.8)
    return store,builder,source


def contract(text='Payment is 100 rubles.', refs=None):
    return AnswerContract(answer=text,claims=[GroundedClaim(claim_id='C1',text=text,supporting_source_ids=refs or ['S1'])])


def test_sources_citations_exact_provenance_and_separate_scores(setup):
    store,builder,source=setup;a=contract();g,quotes=GroundingValidator(builder,Reranker(),.5).validate(a,[source])
    assert g.grounded and g.sources_count==g.citations_count==g.claims_supported==1
    c=quotes[0]
    assert c.source_id=='S1' and c.page_start==c.page_end==8 and c.section=='Payment'
    assert c.chunk_id=='c' and c.anchor_chunk_ids==['c'] and c.locator['recognition_id']=='page8'
    assert c.exact_match and c.quote in store.rows['document_blocks']['b']['text']
    assert source['score']==.4 and source['rerank_score']==.8 and c.support_score==.95
    assert a.claims[0].support_scores['S1']['score']==.95
    c.quote='Paraphrased payment';assert not builder.exact(c)


@pytest.mark.parametrize('fault',['unknown_ref','inline_ref','number','negation','weak','no_sources','hidden_fact','duplicate_claim','foreign_doc','changed_canonical','no_blocks'])
def test_unsupported_and_coverage(setup,fault):
    store,builder,s=setup;a=contract();sources=[s];score=.95
    if fault=='unknown_ref':a.claims[0].supporting_source_ids=['S99']
    if fault=='number':a=contract('Payment is 999 rubles.')
    if fault=='inline_ref':a=contract('Payment is 100 rubles [S999].')
    if fault=='negation':a=contract('Payment is not 100 rubles.')
    if fault=='weak':score=.1
    if fault=='no_sources':sources=[]
    if fault=='hidden_fact':a.answer+=' It was paid yesterday.'
    if fault=='duplicate_claim':a.claims.append(a.claims[0])
    if fault=='foreign_doc':store.rows['document_blocks']['b']['document_id']='other'
    if fault=='changed_canonical':store.rows['document_blocks']['b']['text']='Changed text'
    if fault=='no_blocks':s['source_block_ids']=[]
    g,c=GroundingValidator(builder,Reranker(score),.5).validate(a,sources)
    assert not g.grounded
    if fault=='unknown_ref':assert g.invalid_source_refs==['S99'] and not c


def test_multiple_sources_each_checked_and_one_support_suffices(setup):
    _,builder,s=setup;a=contract(refs=['S1','S2']);provider=Reranker()
    g,c=GroundingValidator(builder,provider,.5).validate(a,[s,{**s,'reference':'S2'}])
    assert g.grounded and g.sources_count==2 and len(provider.calls)==2
    assert set(a.claims[0].support_scores)=={'S1','S2'}


@pytest.mark.parametrize('repair',['good','bad','new_source','hidden_fact','malformed','refuse'])
def test_one_repair_revalidated_no_unsupported_display(setup,repair):
    _,builder,s=setup;r={'sources':[s]};calls=[]
    def generate(context,kind):
        calls.append((context,kind));a=contract('Payment is 999 rubles.')
        if len(calls)==2:
            if repair=='good':a=contract()
            if repair=='new_source':a=contract(refs=['S9'])
            if repair=='hidden_fact':a=contract();a.answer+=' Invented fact.'
            if repair=='refuse':a=AnswerContract(answer='',insufficient_context=True)
        return LLMResult(model='test',status='SUCCESS',request_count=1,text='not json' if repair=='malformed' else a.model_dump_json()).model_dump()
    result=grounded_generation(r,'SOURCE',generate,builder,Reranker(),.5)
    assert len(calls)==2 and result['request_count']==2
    assert 'S1' in calls[1][0] or repair=='malformed'
    assert r['repair_used']
    if repair=='good':assert r['grounding_status']=='GROUNDED' and r['citations_json']
    else:
        assert result['status']=='INSUFFICIENT_CONTEXT' and not r['citations_json']
        assert '999' not in result['text'] and 'S9' not in result['text']


def test_model_refusal_no_repair(setup):
    _,builder,s=setup;r={'sources':[s]};calls=[]
    def generate(*args):
        calls.append(args)
        return LLMResult(model='test',status='SUCCESS',text=AnswerContract(answer='',insufficient_context=True).model_dump_json()).model_dump()
    assert grounded_generation(r,'s',generate,builder,Reranker(),.5)['status']=='INSUFFICIENT_CONTEXT'
    assert len(calls)==1 and not r['repair_used']


def test_repair_explains_missing_attribution_and_preserves_supported_amount(setup):
    store,builder,s=setup
    quote='Взыскать в конкурсную массу ОАО АК «Вим-Авиа» сумму убытков в размере 4 104 326 405,76 рублей.'
    store.rows['document_blocks']['b']['text']=s['text']=quote
    claim='Банк Российский Кредит просит взыскать в конкурсную массу ОАО АК «Вим-Авиа» сумму убытков в размере 4 104 326 405,76 рублей.'
    record={'sources':[s]};calls=[]
    def generate(context,kind):
        calls.append(context)
        if len(calls)==1:
            a=contract(claim)
        else:
            assert 'missing_terms' in context and 'российский' in context
            assert quote in context and 'LOW_FACT_COVERAGE' in context
            a=contract('Размер убытков — 4 104 326 405,76 рублей.')
        return LLMResult(model='test',status='SUCCESS',request_count=1,text=a.model_dump_json()).model_dump()
    result=grounded_generation(record,'SOURCE',generate,builder,Reranker(),.5)
    assert len(calls)==2 and result['status']=='SUCCESS'
    assert record['grounding_status']=='GROUNDED' and record['repair_used']
    assert record['citations_json'][0]['quote']==quote
    assert 'Российский' not in result['text']


def test_diagnostic_reason_matches_highest_scoring_quote(setup):
    store,builder,s=setup
    store.rows['document_blocks']['b']['text']=s['text']='Payment is 100 rubles.\nOther paragraph.'
    a=contract('Bank requests payment of 100 rubles.')
    g,_=GroundingValidator(builder,Reranker(),.5).validate(a,[s])
    diagnostic=a.claims[0].support_scores['S1']
    assert not g.grounded
    assert diagnostic['reason']=='LOW_FACT_COVERAGE'
    assert 'bank' in diagnostic['quote_checks'][0]['missing_terms']
    assert any(q['reason']=='UNSUPPORTED_NUMBERS' for q in diagnostic['quote_checks'])


def test_high_relevance_of_date_cannot_support_applicant_name(setup):
    store,builder,s=setup
    store.rows['document_blocks']['b']['text']='24 февраля 2025 г.'
    s['text']='24 февраля 2025 г.'
    a=contract('В ходатайстве от 24 февраля 2025 года заявителем указана Погосян Лилия Гамлетовна.')
    g,c=GroundingValidator(builder,Reranker(.999),.5).validate(a,[s])
    assert not g.grounded and not c


def test_evaluation_correct_refusal_and_false_confident():
    a=dict(grounding_status='INSUFFICIENT_CONTEXT',expected_unanswerable=True)
    b=dict(grounding_status='GROUNDED',expected_unanswerable=True)
    e=evaluate_grounding([a,b]);assert e['correct_refusals']==e['false_confident_answers']==1
    assert e['answers_with_sources']==e['answers_with_citations']==0


def test_gate_no_factual_generation(ragweb):
    from test_rag import compare
    c,rid,llm,_=ragweb
    r=compare(c,rid,minimum_candidate_score=1)
    assert r['rag_result']['status']=='INSUFFICIENT_CONTEXT'
    assert not [x for x in llm.calls if x.context_type=='grounded_rag']


from test_rag import ragweb
