import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from rag_arbiter.application.policy_predicate import (PriorClaimPolicyRevalidator, ClaimPolicyValidationResult,
    PredicateEvaluator, PredicateEvaluationResult, ReferenceAnswerBuilder)
from rag_arbiter.application.grounding import GroundingValidator
from rag_arbiter.application.retrieval_policy import RetrievalPolicy
from rag_arbiter.application.query_rewrite import RewriteResult, guard_rewrite, protected_qualifiers
from rag_arbiter.llm import LLMConfig, LLMResult
from test_grounding import setup, contract, Reranker
from test_rag import ragweb
from test_chat import MemoryProvider,enable_rewrite
from dialogue_fixtures import install
from rag_arbiter.application.chat_memory import TaskMemoryUpdater


def validation(id='t:C1',status='REVALIDATED_EXISTING_SOURCE'):
    return ClaimPolicyValidationResult(claim_id=id,claim_text='Payment is 100 rubles.',status=status,
        supporting_source_ids=['S1'],policy_version=2,reason='fixture')


@pytest.mark.parametrize('fault',['none','excluded','changed_canonical','inactive_chunk','unsupported'])
def test_original_evidence_policy_and_canonical_check(setup,fault):
    store,builder,s=setup;a=contract();g,cs=GroundingValidator(builder,Reranker(),.5).validate(a,[s])
    store.rows['chat_turns']={'t':dict(grounding_status='GROUNDED',comparison_run_id='r')}
    store.rows['rag_comparison_runs']={'r':dict(claims_json=[c.model_dump() for c in a.claims],sources=[s],citations_json=[c.model_dump() for c in cs])}
    policy=RetrievalPolicy(scope='ALL_DOCUMENTS',corpus_id='corpus',task_state_version=2,
        corpus_document_ids=['d'],eligible_document_ids=[] if fault=='excluded' else ['d'])
    if fault=='changed_canonical':store.rows['document_blocks']['b']['text']='Changed'
    if fault=='unsupported':store.rows['rag_comparison_runs']['r']['claims_json'][0]['support_status']='UNSUPPORTED'
    with patch('rag_arbiter.vectorstore.LocalVectorStore.search',side_effect=AssertionError('No retrieval')):
        result,evidence=PriorClaimPolicyRevalidator.existing(store,dict(claim_id='t:C1',claim_text=a.claims[0].text),
            policy,dict(chunk_ids=[] if fault=='inactive_chunk' else ['c']),.5)
    assert (result.status=='REVALIDATED_EXISTING_SOURCE')==(fault=='none')
    assert bool(evidence)==(fault=='none')
    if fault=='excluded':assert result.old_source_checks[0]['policy_allowed'] is False


def test_fresh_search_claim_only_reuses_existing_engine():
    captured={}
    def prepare(run_id,**kwargs):captured.update(kwargs);return dict(comparison_run_id='child')
    def execute(id,progress):return captured
    rag=SimpleNamespace(prepare=prepare,put=lambda r:captured.update(r),execute=execute)
    policy=RetrievalPolicy(scope='ALL_DOCUMENTS',corpus_id='corpus',task_state_version=3)
    parent=dict(processing_run_id='run',comparison_run_id='parent',retrieval_strategy='structure',candidate_top_n=20,
        max_context_sources=5,context_token_budget=12000,rerank_threshold=.1,reference_predicate='связаны с отчетностью')
    prior=dict(claim_id='t:C1',claim_text='Поиск и возврат имущества должника.')
    PriorClaimPolicyRevalidator.fresh(rag,parent,prior,policy,lambda x:None)
    assert captured['question']==captured['revalidation_claim']==prior['claim_text']
    assert 'отчетност' not in captured['question']
    assert captured['rag_pipeline_mode']=='RERANK' and captured['retrieval_policy_json']==policy.model_dump()


@pytest.mark.parametrize('status',['MATCH','NO_MATCH','UNKNOWN'])
def test_predicate_status_is_not_policy_status(status):
    v=validation();payloads=[]
    class Provider:
        def generate(self,r):
            payloads.append(r.question)
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'results':[
                dict(claim_id=v.claim_id,status=status,supporting_source_ids=['S1'],reason='Semantic verdict',confidence=.95)]}))
    result=PredicateEvaluator.evaluate('Payment?', [v], [dict(reference='S1',text='Payment is 100 rubles.')],lambda _:Provider(),LLMConfig())
    assert result[0].status==status and v.status=='REVALIDATED_EXISTING_SOURCE'
    assert result[0].predicate=='Payment?'
    assert PredicateEvaluator.evaluate('Payment?',[],[],lambda _:pytest.fail('No unvalidated input'),LLMConfig())==[]


@pytest.mark.parametrize('statuses,expected',[
    (['NO_MATCH']*3,'VALID_EMPTY_RESULT'), (['UNKNOWN']*3,'INSUFFICIENT_CONTEXT'),
    (['MATCH','NO_MATCH','UNKNOWN'],'PARTIAL_REFERENCE_RESULT'), (['MATCH','NO_MATCH','NO_MATCH'],'SUCCESS')])
def test_answer_semantics(statuses,expected):
    validations=[validation('t:C'+str(n+1)) for n in range(3)]
    evaluations=[PredicateEvaluationResult(claim_id=v.claim_id,claim_text=v.claim_text,predicate='reporting',
        status=status,reason='fixture',confidence=1,supporting_source_ids=['S1']) for v,status in zip(validations,statuses)]
    status,text=ReferenceAnswerBuilder.render(validations,evaluations,['Positive [S1]'] if 'MATCH' in statuses else [])
    assert status==expected
    if status=='VALID_EMPTY_RESULT':assert 'ни одно' in text and 'недостаточно' not in text
    if status=='PARTIAL_REFERENCE_RESULT':assert 'Positive' in text and 'Не соответствуют' in text and 'Недостаточно' in text


@pytest.mark.parametrize('original,proposed',[
    ('Какие обязанности связаны с отчетностью?','обязанности, связанные с отчетностью'),
    ('конкурсный управляющий','конкурсного управляющего'),
    ('обязанность','обязанности'),('продлить срок','продление сроки'),
    ('Что обязан сделать управляющий?','задачи управляющего и его функции')])
def test_safe_morphology_and_paraphrase(original,proposed):
    r=RewriteResult(original_question=original,retrieval_query=proposed,provider='test',model='test',status='SUCCESS')
    assert guard_rewrite(r,[original]).status=='SUCCESS'


@pytest.mark.parametrize('qualifier,kind',[
    ('по статье 129','LEGAL_ARTICLE'),('24.02.2025','DATE'),('А60-3101/2024','CASE_NUMBER'),
    ('100 рублей','MONEY'),('Иванов Иван Иванович','PERSON_NAME'),('ООО «Ромашка»','ORGANIZATION_NAME'),
    ('судебное определение','DOCUMENT_TYPE'),('a'*32,'DOCUMENT_ID'),('ИНН 1234567890','EXPLICIT_IDENTIFIER')])
def test_new_factual_qualifier_rejected_allowed_preserved(qualifier,kind):
    q='обязанности, связанные с отчетностью'
    r=RewriteResult(original_question=q,retrieval_query=q+' '+qualifier,provider='test',model='test',status='SUCCESS')
    assert kind in {k for k,v in protected_qualifiers(qualifier)}
    rejected=guard_rewrite(r,[q]);assert rejected.status=='GUARDED_FALLBACK' and rejected.retrieval_query==q
    assert guard_rewrite(r,[q,qualifier]).status=='SUCCESS'


def test_all_no_match_integration_reuses_evidence_without_qdrant(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=chat.runs.config.llm
    install(chat,llm,cfg);enable_rewrite(llm);chat.memory=TaskMemoryUpdater(lambda _:MemoryProvider(),cfg)
    sid=chat.create(rid).session_id
    t=chat.start(sid,'Когда оплата?',0);chat.futures[t.turn_id].result(timeout=30)
    original=llm.generate
    def generate(r):
        if r.question.startswith('DAY25_PREDICATE_EVALUATION'):
            d=json.loads('{'+r.question.split('\n{',1)[1])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'results':[
                dict(claim_id=c['claim_id'],status='NO_MATCH',supporting_source_ids=c['supporting_source_ids'],reason='Different topic',confidence=.95) for c in d['claims']]}))
        return original(r)
    llm.generate=generate
    with patch('rag_arbiter.vectorstore.LocalVectorStore.search',side_effect=AssertionError('No Qdrant on reuse')):
        t=chat.start(sid,'Какие из них связаны с отчетностью?',chat.load(sid)['task_state']['version'])
        chat.futures[t.turn_id].result(timeout=30)
    t=chat.load(sid)['turns'][-1]
    assert t['grounding_status']=='VALID_EMPTY_RESULT' and t['status']=='COMPLETED'
    assert t['trace']['fresh_retrieval_calls']==0 and t['trace']['reused_claims']==1
    assert not t['citations_json']
    assert t['trace']['policy_validation_results'][0]['status']=='REVALIDATED_EXISTING_SOURCE'
    assert t['trace']['predicate_evaluation_results'][0]['status']=='NO_MATCH'


def test_old_complaint_retriggers_claim_only_search_under_current_policy(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=chat.runs.config.llm
    install(chat,llm,cfg);enable_rewrite(llm);chat.memory=TaskMemoryUpdater(lambda _:MemoryProvider(),cfg)
    sid=chat.create(rid).session_id
    t=chat.start(sid,'Когда оплата?',0);chat.futures[t.turn_id].result(timeout=30)
    first=chat.load(sid)['turns'][0]
    denied={c['document_id'] for c in first['citations_json']}
    with chat.runs.db() as db:
        for doc in db.all('documents'):
            doc['retrieval_metadata']=dict(document_id=doc['document_id'],document_type='COMPLAINT' if doc['document_id'] in denied else 'COURT_RULING',
                classification_status='CONFIRMED',classification_source='TRUSTED_MANUAL')
            db.put('documents',doc['document_id'],doc)
    for q in ['Жалобы исключи.','Какие из них связаны с отчетностью?']:
        t=chat.start(sid,q,chat.load(sid)['task_state']['version']);chat.futures[t.turn_id].result(timeout=30)
    turn=chat.load(sid)['turns'][-1]
    assert turn['status']=='COMPLETED'
    v=turn['trace']['policy_validation_results'][0]
    assert not v['source_reused'] and v['fresh_retrieval']
    assert all(not c['policy_allowed'] for c in v['old_source_checks'])
    assert v['fresh_query']=='Оплата через десять дней.' and 'отчетност' not in v['fresh_query']
    assert all(not (set(s['document_ids']) & denied) for s in turn['trace']['reference_searches'])
    if v['status'] in ('NOT_RECONFIRMED','UNKNOWN'):assert not turn['trace']['predicate_evaluation_results']
