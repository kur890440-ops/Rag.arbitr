import json
from unittest.mock import patch
import pytest
from rag_arbiter.application.dialogue import (DialogueWorkingContext,WorkingContextService,
    ReferenceResolutionResult,ReferenceResolver,QuestionIntent,AnswerModeRouter)
from rag_arbiter.application.chat_models import ChatTurn,TaskState
from rag_arbiter.application.chat_memory import TaskMemoryUpdater
from rag_arbiter.application.chat_memory import ContextualQueryBuilder,ChatContextBuilder
from rag_arbiter.application.chat import ChatSessionService
from rag_arbiter.application.exhaustive import ExhaustiveNoRAG
from rag_arbiter.llm import LLMConfig,LLMResult
from rag_arbiter.storage import MetadataStore
from test_chat import user,MemoryProvider,enable_rewrite
from test_retrieval_policy import policy,constraint
from test_rag import ragweb
from dialogue_fixtures import install


def turn():
    return ChatTurn(session_id='s',user_message_id='u',assistant_message_id='a',task_state_version=0,
        original_question='Обязанности',owner_pid=1,owner_started=1,grounding_status='GROUNDED',
        comparison_run_id='r',citations_json=[dict(claim_id='C1',exact_match=True)])


def claims():
    return [dict(claim_id='C1',text='Подготовить отчет.',support_status='SUPPORTED'),
        dict(claim_id='C2',text='Выдумка.',support_status='UNSUPPORTED'),
        dict(claim_id='C3',text='Удалено.',support_status='SUPPORTED',repair_status='REMOVED')]


def context():
    t=turn();return WorkingContextService.update(DialogueWorkingContext(session_id='s'),t,claims())


def provider(body):
    class P:
        def generate(self,request):return LLMResult(model='test',status='SUCCESS',text=json.dumps(body))
    return lambda _:P()


def test_grounded_only_context_and_non_grounded_preserves_anchor():
    c=context();assert len(c.referents_json[0].claim_ids)==1
    assert c.referents_json[0].canonical_text=='Подготовить отчет.'
    t=turn();t.grounding_status='INSUFFICIENT_CONTEXT'
    assert WorkingContextService.update(c,t,claims())==c
    with pytest.raises(ValueError):WorkingContextService.update(DialogueWorkingContext(session_id='other'),t,claims())


@pytest.mark.parametrize('fault',['none','session','message','claim','text','status'])
def test_canonical_provenance_validation(tmp_path,fault):
    t=turn();c=WorkingContextService.update(DialogueWorkingContext(session_id='s'),t,claims())
    db=MetadataStore(tmp_path/'db')
    db.put('chat_turns',t.turn_id,t.model_dump())
    db.put('chat_messages','a',dict(session_id='s',role='ASSISTANT'))
    db.put('rag_comparison_runs','r',dict(claims_json=claims()))
    if fault=='session':c.session_id='other'
    if fault=='message':c.referents_json[0].source_message_id='nonexistent'
    if fault=='claim':c.referents_json[0].claim_ids=['fake:C1']
    if fault=='text':c.referents_json[0].canonical_text='Подмена'
    if fault=='status':
        t.grounding_status='ERROR';db.put('chat_turns',t.turn_id,t.model_dump())
    try:
        if fault=='none':assert WorkingContextService.validate(db,c)==c
        else:
            with pytest.raises(ValueError):WorkingContextService.validate(db,c)
    finally:db.close()


@pytest.mark.parametrize('word',['эти обязанности','из них','ранее найденные','перечисленные'])
@pytest.mark.parametrize('invalid',[False,True])
def test_structured_refs_accept_existing_reject_invented(word,invalid):
    c=context();r=c.referents_json[0];m=user(word)
    body=dict(original_message=word,resolved_message='Какие обязанности связаны с отчетностью?',
        resolved_referents=[r.referent_id],candidate_prior_claim_ids=['fake:C1'] if invalid else r.claim_ids,
        source_turn_ids=[r.source_turn_id],unresolved_referents=[],status='RESOLVED',confidence=.98)
    result=ReferenceResolver(provider(body),LLMConfig()).resolve(m,TaskState(session_id='s'),c,[m])
    assert result.status==('UNRESOLVED' if invalid else 'RESOLVED')
    if invalid:assert not result.candidate_prior_claim_ids


def test_foreign_history_rejected():
    with pytest.raises(ValueError):ReferenceResolver(provider({}),LLMConfig()).resolve(
        user('их'),TaskState(session_id='s'),context(),[user('secret',sid='foreign')])


def test_prior_claim_text_is_generation_context_not_retrieval_query():
    c=context();r=c.referents_json[0];m=user('Какие из них связаны с отчетностью?');state=TaskState(session_id='s')
    reference=ReferenceResolutionResult(original_message=m.content,resolved_message='Какие обязанности управляющего связаны с отчетностью?',
        resolved_referents=[r.referent_id],candidate_prior_claim_ids=r.claim_ids,source_turn_ids=[r.source_turn_id],status='RESOLVED',confidence=1)
    resolved=ContextualQueryBuilder(None,LLMConfig()).build(m,state,[m],reference,c)
    assert r.canonical_text not in resolved.resolved_question
    assert resolved.prior_concepts==[r.canonical_text]
    generation=ChatContextBuilder.prepare(state,m,[m],LLMConfig(),resolved)
    assert r.canonical_text in generation['generation_question']
    assert 'НЕ доказательства' in generation['generation_question']


def test_old_chat_backfill_is_session_scoped(tmp_path):
    db=MetadataStore(tmp_path/'db');t=turn()
    db.put('chat_turns',t.turn_id,t.model_dump());db.put('rag_comparison_runs','r',dict(claims_json=claims()))
    try:
        restored=WorkingContextService.load(db,'s')
        assert restored.referents_json[0].claim_ids==[t.turn_id+':C1']
        assert not WorkingContextService.load(db,'other').referents_json
    finally:db.close()


@pytest.mark.parametrize('full',[False,True])
def test_structured_intent_and_inconsistent_route(full):
    body=dict(question_type='FREQUENCY' if full else 'FACTUAL',answer_mode='EXHAUSTIVE_NO_RAG' if full else 'POINT_RAG',
        requires_full_coverage=full,requires_counting=False,requires_frequency_analysis=full,
        requires_cross_document_comparison=False,requires_prior_referents=False,reason='Semantic classification',confidence=.9)
    ref=ReferenceResolutionResult(original_message='q',resolved_message='q',status='NOT_NEEDED',confidence=1)
    result=AnswerModeRouter(provider(body),LLMConfig()).route('arbitrary wording',TaskState(session_id='s'),ref,policy())
    assert result.answer_mode==body['answer_mode']
    if full:
        body['answer_mode']='POINT_RAG'
        with pytest.raises(ValueError):QuestionIntent(**body)
        result=AnswerModeRouter(provider(body),LLMConfig()).route('q',TaskState(session_id='s'),ref,policy())
        assert result.question_type=='UNCERTAIN' and result.confidence==0


def test_exhaustive_filters_before_document_loading(ragweb):
    client,rid,_,_=ragweb;runs=client.app.state.runs
    p=policy(constraint('only','DOCUMENT_TYPE_INCLUDE',['COURT_RULING']),constraint('exclude','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT']))
    entries=[dict(document_id=k) for k in p.corpus_document_ids]
    with runs.db() as db:db.put('documents','court',dict(document_id='court'))
    with patch('rag_arbiter.application.exhaustive.ResultsService.snapshot',return_value={'corpus':{'documents':entries}}),patch(
        'rag_arbiter.application.exhaustive.FullDocumentContextBuilder.build',return_value={'status':'READY'}) as load:
        assert ExhaustiveNoRAG(runs,None).load(rid,None,p)==[{'document_id':'court'}]
        assert load.call_count==1 and load.call_args.args[1]=='court'
    with patch('rag_arbiter.application.exhaustive.ResultsService.snapshot',return_value={'corpus':{'documents':[]}}):
        with pytest.raises(ValueError,match='POLICY_SCOPE_INCOMPLETE'):ExhaustiveNoRAG(runs,None).load(rid,None,p)


def test_q1_q5_fresh_grounding_routing_reload_isolation(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=client.app.state.runs.config.llm
    install(chat,llm,cfg);enable_rewrite(llm);mem=MemoryProvider()
    chat.memory=TaskMemoryUpdater(lambda _:mem,cfg)
    with client.app.state.runs.db() as db:
        for d in db.all('documents'):
            d['retrieval_metadata']=dict(document_id=d['document_id'],document_type='COURT_RULING',classification_status='CONFIRMED',classification_source='TRUSTED_MANUAL')
            db.put('documents',d['document_id'],d)
    sid=chat.create(rid).session_id
    questions=['Какие обязанности конкурсного управляющего встречаются в документах?',
        'Учитывай только судебные определения.','Жалобы исключи.',
        'Какие из этих обязанностей связаны с отчетностью?','Какие из них встречаются чаще всего?']
    turns=[]
    for n,q in enumerate(questions):
        state=chat.load(sid)['task_state']
        with patch.object(chat.rag,'execute',side_effect=chat.rag.execute) as point:
            if n==4:
                with patch('rag_arbiter.vectorstore.LocalVectorStore.__init__',side_effect=AssertionError('No Qdrant')):
                    t=chat.start(sid,q,state['version']);chat.futures[t.turn_id].result(timeout=30)
                assert point.call_count==0
            else:
                t=chat.start(sid,q,state['version']);chat.futures[t.turn_id].result(timeout=30)
                assert point.call_count==(1 if n in (0,3) else 0)
        turns.append(chat.load(sid)['turns'][-1])
    q1,q2,q3,q4,q5=turns
    assert q1['grounding_status']==q4['grounding_status']=='GROUNDED'
    assert q4['comparison_run_id']!=q1['comparison_run_id']
    assert q4['trace']['candidate_prior_claim_ids']==[q1['turn_id']+':C1']
    assert q4['trace']['answer_mode']=='POINT_RAG' and q4['trace']['reused_claims']==1
    assert q4['trace']['fresh_retrieval_calls']==0
    assert q5['trace']['candidate_prior_claim_ids']==[q4['turn_id']+':C1']
    assert q5['trace']['answer_mode']=='EXHAUSTIVE_NO_RAG' and q5['grounding_status']=='FULL_COVERAGE'
    assert set(q5['trace']['coverage']['eligible_document_ids'])==set(q5['trace']['eligible_document_ids'])
    data=chat.load(sid);restored=ChatSessionService(client.app.state.runs,chat.rag).load(sid)
    assert data['dialogue_context']==restored['dialogue_context']
    assert data['dialogue_context']['version']==2
    assert len(data['task_state']['constraints_json'])==2
    fresh=chat.create(rid);assert not chat.load(fresh.session_id)['dialogue_context']['referents_json']
    assert 'Покрытие: 100' in client.get('/chat?session_id='+sid).text


def test_old_claims_do_not_answer_when_new_sources_refuse(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=client.app.state.runs.config.llm
    install(chat,llm,cfg);enable_rewrite(llm);mem=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda _:mem,cfg)
    sid=chat.create(rid).session_id
    t=chat.start(sid,'Когда оплата?',0);chat.futures[t.turn_id].result(timeout=30)
    before=chat.load(sid);assert before['dialogue_context']['version']==1
    original=llm.generate
    def refuse(r):
        if r.question.startswith('DAY25_PREDICATE_EVALUATION'):
            d=json.loads('{'+r.question.split('\n{',1)[1])
            return LLMResult(model='test',status='SUCCESS',text=json.dumps({'results':[
                dict(claim_id=c['claim_id'],status='UNKNOWN',supporting_source_ids=[],reason='No current evidence',confidence=1) for c in d['claims']]}))
        if r.context_type=='grounded_rag':return LLMResult(model='test',status='SUCCESS',text='{"answer":"","claims":[],"insufficient_context":true}')
        return original(r)
    llm.generate=refuse
    t=chat.start(sid,'Какие из них связаны с отчетностью?',before['task_state']['version']);chat.futures[t.turn_id].result(timeout=30)
    after=chat.load(sid);assert after['turns'][-1]['grounding_status']=='INSUFFICIENT_CONTEXT'
    assert after['dialogue_context']==before['dialogue_context'] and not after['turns'][-1]['citations_json']


def test_resolution_failure_is_clarification_not_weak_context(ragweb):
    client,rid,_,_=ragweb;chat=client.app.state.chat;cfg=client.app.state.runs.config.llm
    mem=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda _:mem,cfg)
    chat.references=ReferenceResolver(provider({'fabricated':'schema'}),cfg)
    sid=chat.create(rid).session_id
    with patch.object(chat.rag,'execute',side_effect=AssertionError('No retrieval on unresolved references')):
        t=chat.start(sid,'Какие из них?',0);chat.futures[t.turn_id].result(timeout=30)
    result=chat.load(sid)['turns'][-1]
    assert result['response']['status']=='NEEDS_CLARIFICATION'
    assert result['grounding_status']=='NOT_APPLICABLE' and result['trace']['dialogue_failure']=='REFERENCE_UNRESOLVED'
    assert result['comparison_run_id'] is None
