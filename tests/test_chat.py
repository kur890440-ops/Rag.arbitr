import json
import sqlite3
import time
from types import SimpleNamespace
import pytest
from rag_arbiter.application.chat_models import ChatMessage, TaskState, TaskStatePatch, MemoryItem
from rag_arbiter.application.chat_memory import TaskMemoryUpdater, ContextualQueryBuilder, ChatContextBuilder
from rag_arbiter.application.chat import ChatSessionService
from rag_arbiter.storage import MetadataStore
from rag_arbiter.llm import LLMConfig, LLMResult
from test_rag import ragweb


@pytest.fixture(autouse=True)
def dialogue_orchestration_fixture(request):
    if 'ragweb' in request.fixturenames:
        from dialogue_fixtures import install
        client,_,llm,_=request.getfixturevalue('ragweb')
        install(client.app.state.chat,llm,client.app.state.runs.config.llm)


def user(text,sid='s',sequence=1):return ChatMessage(session_id=sid,role='USER',content=text,sequence=sequence)
def item(m,key,value=None):return MemoryItem(key=key,value=value or m.content,evidence=m.content,source_message_id=m.message_id)


def enable_rewrite(llm):
    original=llm.generate
    def generate(request):
        if request.question.startswith('Переформулируй исходный вопрос'):
            return LLMResult(model='test',status='SUCCESS',text='{"retrieval_query":"оплата"}')
        return original(request)
    llm.generate=generate


class MemoryProvider:
    """Scripted structured responses, not a claim of real model quality."""
    def __init__(self):self.calls=[]
    def generate(self,request):
        self.calls.append(request)
        if request.question.startswith('DAY25_VERIFY_MEMORY'):
            return LLMResult(model='test',status='SUCCESS',text='{"approved":true}',request_count=1)
        if request.question.startswith('DAY25_MEMORY'):
            d=json.loads('{'+request.question.split('\n{',1)[1])
            m=ChatMessage.model_validate(d['CURRENT_USER']);p=TaskStatePatch(expected_version=d['expected_version'])
            t=m.content.lower()
            if 'только судебные' in t:p.constraints_add=[item(m,'document_type')]
            elif 'жалобы' in t and ('исключи' in t or 'не учитывай' in t):p.constraints_add=[item(m,'exclude_complaints')]
            elif 'перенос заседания' in t:p.constraints_add=[item(m,'exclude_hearings')]
            elif t.startswith('под продлением'):p.terms_set=[item(m,'extension')]
            elif t.startswith('уточнение:'):p.clarifications_add=[item(m,'focus')]
            elif t.startswith('теперь сравни'):p.goal_update=item(m,'goal')
            elif t.startswith('удали ограничение'):p.constraints_remove=['exclude_complaints']
            date=TaskMemoryUpdater.date_item(TaskState(session_id=m.session_id,constraints_json=[MemoryItem(key=k,value=v,evidence=v,source_message_id='old') for k,v in d['state']['constraints_json'].items()]),m)
            if date:p.constraints_add.append(date)
            for added in p.constraints_add:
                if added.key in d['state']['constraints_json'] and added.value!=d['state']['constraints_json'][added.key]:p.constraints_remove.append(added.key)
            if (p.constraints_add or p.constraints_remove or p.terms_set or p.clarifications_add) and '?' not in t:p.intent='MEMORY_ONLY'
            return LLMResult(model='test',status='SUCCESS',text=p.model_dump_json(),request_count=1)
        if request.question.startswith('DAY25_RESOLVE'):
            d=json.loads('{'+request.question.split('\n{',1)[1]);goal=d['state']['goal']
            result='Какие обязанности конкурсного управляющего связаны с отчетностью?' if 'отчетностью' in d['current'] else d['current']+' Тема: '+goal
            return LLMResult(model='test',status='SUCCESS',text=json.dumps(dict(resolved_question=result,referents_resolved=[goal])))
        raise AssertionError('Unexpected external call')


def test_memory_explicit_patch_lifecycle_and_conflict():
    provider=MemoryProvider();up=TaskMemoryUpdater(lambda cfg:provider,LLMConfig());state=TaskState(session_id='s')
    for text in ['Цель: изучить обязанности управляющего','Жалобы исключи.','Уточнение: только сроки.','Под продлением имею в виду конкурсное производство.']:
        m=user(text);p,_=up.propose(state,m);state=up.apply(state,p,m)
    assert state.goal.value=='Цель: изучить обязанности управляющего'
    assert state.constraints_json and state.clarifications_json and state.terms_json
    m=user('Удали ограничение на жалобы');p,_=up.propose(state,m);state=up.apply(state,p,m)
    assert not state.constraints_json
    for text in ['Используй только 2025 год.','Включи также 2024 год.']:
        m=user(text);p,_=up.propose(state,m);state=up.apply(state,p,m)
    assert [i.value for i in state.constraints_json]==['Годы: 2024, 2025']


@pytest.mark.parametrize('body',[
    '```json\n{"expected_version":1}\n```',
    '{"expected_version":1,"constraints_add":null,"terms_remove":null}',
])
def test_memory_noop_formats_preserve_existing_state(body):
    m=user('Какие из этих обязанностей повторяются чаще?')
    old=user('Жалобы исключи.')
    state=TaskState(session_id='s',version=1,goal=item(old,'goal'),constraints_json=[item(old,'complaints')])
    patch=TaskMemoryUpdater.parse_patch(body)
    assert TaskMemoryUpdater.apply(state,patch,m).model_dump()==state.model_dump()


@pytest.mark.parametrize('second',['valid','invalid','stale'])
def test_memory_format_repair_is_bounded_and_validated(second):
    m=user('Какие из этих обязанностей повторяются чаще?')
    state=TaskState(session_id='s',version=1,goal=item(user('Обязанности управляющего'),'goal'))
    calls=[]
    class Provider:
        def generate(self,request):
            calls.append(request)
            if request.question.startswith('DAY25_VERIFY_MEMORY'):
                return LLMResult(model='test',status='SUCCESS',text='{"approved":true}',request_count=1,usage={'total_tokens':10})
            body='not JSON private text'
            if len(calls)==2:
                assert 'expected_version' in request.question
                body={'valid':'{"expected_version":1}','invalid':'{}','stale':'{"expected_version":0}'}[second]
            return LLMResult(model='test',status='SUCCESS',text=body,request_count=1,usage={'total_tokens':10})
    updater=TaskMemoryUpdater(lambda cfg:Provider(),LLMConfig())
    if second=='valid':
        patch,diag=updater.propose(state,m)
        assert updater.apply(state,patch,m).model_dump()==state.model_dump()
        assert diag['format_repair_used'] and diag['requests']==3 and diag['usage']['total_tokens']==30
    else:
        with pytest.raises(ValueError) as caught:updater.propose(state,m)
        assert caught.value.diagnostics['requests']==2
        assert 'private text' not in json.dumps(caught.value.diagnostics)
    assert len(calls)==(3 if second=='valid' else 2) and state.version==1


@pytest.mark.parametrize('fault',['assistant','foreign_session','wrong_version','invented','foreign_message','unknown_remove','duplicate','conflict'])
def test_patch_validation_rejects_invalid_memory(fault):
    m=user('Жалобы исключи.');state=TaskState(session_id='s');p=TaskStatePatch(expected_version=0,constraints_add=[item(m,'complaints')])
    if fault=='assistant':m.role='ASSISTANT'
    if fault=='foreign_session':m.session_id='other'
    if fault=='wrong_version':p.expected_version=99
    if fault=='invented':p.constraints_add[0].value='Суд взыскал миллион рублей'
    if fault=='foreign_message':p.constraints_add[0].source_message_id='other'
    if fault=='unknown_remove':p.constraints_remove=['missing']
    if fault=='duplicate':p.constraints_add*=2
    if fault=='conflict':state.constraints_json=[item(user('Учитывай жалобы'),'complaints')]
    with pytest.raises(ValueError):TaskMemoryUpdater.apply(state,p,m)
    assert state.version==0


SCENARIO_A=[
 'Какие обязанности чаще всего возлагаются на конкурсного управляющего?',
 'Учитывай только судебные определения.','Жалобы исключи.',
 'Какие из них связаны с отчетностью?','Смотри только документы после 2024 года.',
 'Какие сроки обычно устанавливают?','Какие документы должен представить управляющий?',
 'Кому направляются отчеты?','Уточнение: интересуют сроки представления отчетов.',
 'Какие из них установлены судом?','Есть ли требования к содержанию отчета?',
 'Какие из них связаны с отчетностью?']
SCENARIO_B=[
 'Какие причины продления встречаются в документах?',
 'Под продлением дальше имею в виду только продление конкурсного производства.',
 'Перенос заседания не учитывай.','Какие из этих причин повторяются?',
 'Теперь сравни причины продления конкурсного производства между документами.',
 'Есть ли связь с реализацией имущества?','Какие документы подтверждают причины?',
 'Какие сроки запрашивают?','Уточнение: отличай просьбу от решения суда.',
 'Есть ли незавершенные расчеты с кредиторами?','Какие из этих причин связаны с расчетами?',
 'Какие из этих причин повторяются?']


@pytest.mark.parametrize('scenario',[SCENARIO_A,SCENARIO_B],ids=['A_12_turns','B_12_turns'])
def test_long_scenarios_memory_resolution_and_history_budget(scenario,tmp_path):
    provider=MemoryProvider();cfg=LLMConfig();up=TaskMemoryUpdater(lambda cfg:provider,cfg)
    resolver=ContextualQueryBuilder(lambda cfg:provider,cfg);state=TaskState(session_id='s');history=[];trace=[]
    for n,text in enumerate(scenario,1):
        m=user(text,sequence=n*2-1);history.append(m)
        patch,_=up.propose(state,m);state=up.apply(state,patch,m)
        resolved=resolver.build(m,state,history[-6:]);ctx=ChatContextBuilder.prepare(state,m,history,cfg)
        # Assistant/source facts are deliberately not input to the memory updater.
        history.append(ChatMessage(session_id='s',role='ASSISTANT',sequence=n*2,content='CORPUS_FACT_123 [S1]'))
        assert 'CORPUS_FACT_123' not in state.model_dump_json()
        assert ctx['history_messages_used']<=6 and ctx['context_token_budget']>0
        trace.append(dict(turn=n,original=text,resolved=resolved.resolved_question,state=state.model_dump()))
    assert len(trace)==12 and len(history)==24
    if scenario==SCENARIO_A:
        assert state.goal.value==scenario[0]
        assert {i.key for i in state.constraints_json}=={'document_type','exclude_complaints','years'}
        assert 'После: 2024' in resolved.resolved_question and 'Жалобы исключи.' in resolved.resolved_question
        assert resolved.resolved_question.startswith('Какие обязанности конкурсного управляющего связаны с отчетностью?')
    else:
        assert state.goal.value==scenario[4]
        assert state.terms_json[0].value==scenario[1]
        assert scenario[2] in resolved.resolved_question
    (tmp_path/'scenario.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')


def test_context_budget_preserves_evidence_over_history():
    cfg=LLMConfig();m=user('Вопрос');state=TaskState(session_id='s')
    history=[ChatMessage(session_id='s',role='ASSISTANT',content='x'*19000,sequence=i+1) for i in range(20)]
    result=ChatContextBuilder.prepare(state,m,history,cfg)
    assert result['context_token_budget']==cfg.context_budget and result['history_messages_used']==0
    assert 'Вопрос' in result['generation_question']
    with pytest.raises(ValueError):ChatContextBuilder.prepare(state,m,history,LLMConfig(full_document_context_budget=1024))


def test_migration_is_additive(tmp_path):
    path=tmp_path/'db.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
        db.execute('INSERT INTO documents VALUES (?,?)',('d','{"untouched":true}'))
        db.execute('PRAGMA user_version=8')
    store=MetadataStore(path)
    assert store.get('documents','d')=={'untouched':True}
    assert store.db.execute('PRAGMA user_version').fetchone()[0]==10
    assert store.all('chat_sessions')==[];store.close()


def test_chat_end_to_end_existing_rag_reload_sources_and_isolation(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat
    enable_rewrite(llm)
    provider=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    chat.resolver=ContextualQueryBuilder(lambda cfg:provider,client.app.state.runs.config.llm)
    created=client.post('/api/chat/sessions',json={'processing_run_id':rid});assert created.status_code==201
    sid=created.json()['session_id'];assert client.get('/api/chat/sessions/'+sid).json()['task_state']['version']==0
    response=client.post(f'/api/chat/sessions/{sid}/messages',json={'content':'Когда оплата?','expected_version':0})
    assert response.status_code==202,response.text
    tid=response.json()['turn_id'];chat.futures[tid].result(timeout=30)
    data=client.get('/api/chat/sessions/'+sid).json();turn=data['turns'][0]
    assert [m['role'] for m in data['messages']]==['USER','ASSISTANT']
    assert turn['status']=='COMPLETED',turn['trace']
    assert turn['grounding_status']=='GROUNDED' and turn['sources_json'] and turn['citations_json']
    r=client.app.state.rag.get(turn['comparison_run_id'])
    assert r['point_only'] and r['rag_scope']=='ALL_DOCUMENTS' and r['rag_pipeline_mode']=='REWRITE_RERANK'
    assert r['question_text']==turn['resolved_question']
    assert r['rewrite_result']['original_question']==r['chat_search_question']=='Когда оплата?'
    assert 'Условия задачи' not in r['chat_search_question']
    final=next(q for q in llm.calls if q.context_type=='grounded_rag')
    assert 'Текущий вопрос пользователя: Когда оплата?' in final.question
    assert 'retrieving' in [s['stage'] for s in turn['trace']['stages']]
    assert {'reranking','generating','grounding'}<=set(s['stage'] for s in turn['trace']['stages'])
    html=client.get('/chat?session_id='+sid).text
    assert 'Источники ·' in html and 'Показать страницу' in html and 'Когда оплата?' in html
    c=turn['citations_json'][0]
    assert client.get(f"/ui/runs/{rid}/pages/{c['locator']['recognition_id']}").status_code==200
    restored=ChatSessionService(client.app.state.runs,client.app.state.rag).load(sid)
    assert restored['messages']==data['messages'] and restored['task_state']==data['task_state']
    fresh=chat.create(rid);assert chat.load(fresh.session_id)['messages']==[]
    assert chat.load(fresh.session_id)['task_state']['goal'] is None
    assert client.get('/').status_code==200


def test_chat_refusal_and_memory_edit(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat
    enable_rewrite(llm)
    provider=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id
    # Real Day24 path: provider explicitly refuses, no fabricated answer/citation.
    original=llm.generate
    def generate(req):
        if req.context_type=='grounded_rag':return LLMResult(model='test',status='SUCCESS',text='{"answer":"","claims":[],"insufficient_context":true}')
        return original(req)
    llm.generate=generate
    t=chat.start(sid,'Жалобы исключи.',0);chat.futures[t.turn_id].result(timeout=30)
    data=chat.load(sid)
    assert data['turns'][0]['response']['status']=='MEMORY_UPDATED'
    t=chat.start(sid,'Есть ли ответ на неизвестный вопрос?',data['task_state']['version'])
    chat.futures[t.turn_id].result(timeout=30)
    data=chat.load(sid);t=data['turns'][-1]
    assert t['grounding_status']=='INSUFFICIENT_CONTEXT' and not t['citations_json']
    assert 'Не знаю по имеющимся документам' in data['messages'][-1]['content']
    version=data['task_state']['version'];chat.edit(sid,version,'constraints_json','exclude_complaints')
    assert chat.load(sid)['task_state']['constraints_json']==[]
    with pytest.raises(ValueError):chat.edit(sid,version,'constraints_json','exclude_complaints')


@pytest.mark.parametrize('scenario',[SCENARIO_A,SCENARIO_B],ids=['A_routed_12_turns','B_routed_12_turns'])
def test_scenarios_route_factual_and_full_coverage_answers(ragweb,scenario,tmp_path):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;provider=MemoryProvider()
    # Synthetic fixture previously lacked metadata. Make its intended scope explicit.
    with client.app.state.runs.db() as store:
        for doc in store.all('documents'):
            doc['retrieval_metadata']=dict(document_id=doc['document_id'],document_type='COURT_RULING',
                classification_status='CONFIRMED',classification_source='TRUSTED_MANUAL',document_year=2025)
            store.put('documents',doc['document_id'],doc)
    chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    chat.resolver=ContextualQueryBuilder(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id
    original=llm.generate
    def generate(request):
        if request.question.startswith('Переформулируй исходный вопрос'):
            return LLMResult(model='test',status='SUCCESS',text='{"retrieval_query":"обязанности конкурсного управляющего"}')
        return original(request)
    llm.generate=generate
    for content in scenario:
        before=chat.load(sid);t=chat.start(sid,content,before['task_state']['version'])
        chat.futures[t.turn_id].result(timeout=30)
        latest=chat.load(sid)['turns'][-1]
        assert latest['status']=='COMPLETED',latest['trace']
        if latest['response']['status']=='MEMORY_UPDATED':
            assert latest['grounding_status']=='NOT_APPLICABLE' and latest['comparison_run_id'] is None
            assert not latest['citations_json'] and latest['trace']['memory_only']
            continue
        if latest['trace']['answer_mode']=='EXHAUSTIVE_NO_RAG':
            assert latest['grounding_status']=='FULL_COVERAGE',latest['trace']
            assert latest['trace']['coverage']['coverage_percent']==100
            assert not latest['trace'].get('retrieved_candidates')
            continue
        assert latest['grounding_status']=='GROUNDED'
        assert latest['citations_json'] and all(c['exact_match'] for c in latest['citations_json'])
        assert latest['trace']['rewrite']['status'] in ('SUCCESS','GUARDED_FALLBACK')
    final=chat.load(sid)
    assert len(final['messages'])==24 and len(final['turns'])==12
    if scenario==SCENARIO_A:
        assert final['task_state']['goal']['value']==scenario[0]
        assert {x['key'] for x in final['task_state']['constraints_json']}=={'document_type','exclude_complaints','years'}
    else:assert final['task_state']['terms_json'][0]['value']==scenario[1]
    (tmp_path/'scenario_integration.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')


def test_user_saved_before_failed_extraction_and_concurrent_send_blocked(ragweb):
    import threading
    client,rid,_,_=ragweb;chat=client.app.state.chat;sid=chat.create(rid).session_id
    entered=threading.Event();release=threading.Event()
    def blocked(state,message):
        entered.set();release.wait(10);raise RuntimeError('private provider details must not leak')
    chat.memory.propose=blocked
    t=chat.start(sid,'Первый вопрос',0);assert entered.wait(5)
    try:
        assert chat.load(sid)['messages'][0]['content']=='Первый вопрос'
        with pytest.raises(ValueError):chat.start(sid,'Повтор',0)
    finally:release.set()
    chat.futures[t.turn_id].result(timeout=10)
    data=chat.load(sid)
    assert data['task_state']['version']==0 and data['turns'][0]['status']=='FAILED'
    assert 'private provider' not in json.dumps(data)


def test_chat_never_silently_downgrades_when_rewrite_unavailable(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat
    provider=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id;t=chat.start(sid,'Когда оплата?',0)
    chat.futures[t.turn_id].result(timeout=30)
    turn=chat.load(sid)['turns'][0]
    assert turn['status']=='FAILED' and 'Query Rewrite' in turn['trace']['warning']
    assert turn['trace']['rag_error']['code']=='QUERY_REWRITE_UNAVAILABLE'
    assert not turn['sources_json'] and not any(r.context_type=='grounded_rag' for r in llm.calls)


def test_constraint_message_acknowledges_without_document_search(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;provider=MemoryProvider()
    chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id
    t=chat.start(sid,'Учитывай только судебные определения.',0)
    chat.futures[t.turn_id].result(timeout=20)
    data=chat.load(sid);turn=data['turns'][-1]
    assert turn['response']['status']=='MEMORY_UPDATED' and turn['comparison_run_id'] is None
    assert not llm.calls and data['task_state']['constraints_json']
    assert 'Учту это условие' in data['messages'][-1]['content']
    html=client.get('/chat?session_id='+sid).text
    assert 'Условие сохранено' in html and 'Источники · 0' not in html


@pytest.mark.parametrize('command',['Жалобы исключи.','Исключи жалобы.','Жалобы не учитывай.'])
def test_complaint_exclusion_preserves_court_only_condition(command):
    old=user('Учитывай только судебные определения.')
    state=TaskState(session_id='s',version=2,constraints_json=[item(old,'document_type')])
    provider=MemoryProvider()
    updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig());m=user(command)
    patch,diag=updater.propose(state,m);result=updater.apply(state,patch,m)
    assert not patch.constraints_remove and diag['requests']==2 and diag['semantic_verified']
    assert {i.key for i in result.constraints_json}=={'document_type','exclude_complaints'}
    assert result.constraints_json[0].model_dump()==state.constraints_json[0].model_dump()
    assert result.constraints_json[1].source_message_id==m.message_id


def test_court_only_command_uses_llm_and_preserves_independent_constraints():
    provider=MemoryProvider()
    updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig());state=TaskState(session_id='s')
    for text in ['Жалобы исключи.','Учитывай только судебные определения.','Используй только судебные определения.']:
        m=user(text);patch,diag=updater.propose(state,m);state=updater.apply(state,patch,m)
        assert diag['requests']==2 and diag['semantic_verified']
        assert next(i for i in state.constraints_json if i.source_message_id==m.message_id).value==text
    assert {i.key for i in state.constraints_json}=={'document_type','exclude_complaints'}


class SemanticProvider:
    def __init__(self,proposal,approved=True):
        self.proposal,self.approved,self.calls=proposal,approved,[]

    def generate(self,request):
        self.calls.append(request)
        data=json.JSONDecoder().raw_decode('{'+request.question.split('\n{',1)[1])[0]
        if request.question.startswith('DAY25_VERIFY_MEMORY'):
            body={'approved':self.approved}
        else:
            body=self.proposal(data)
        return LLMResult(model='test',status='SUCCESS',text=json.dumps(body,ensure_ascii=False),request_count=1)


@pytest.mark.parametrize('text',[
    'Давай ограничимся определениями суда, остальное сейчас не нужно.',
    'Можно оставить в рассмотрении лишь судебные определения?',
    'Мне нужны исключительно определения, вынесенные судом.',
])
def test_free_form_memory_accepts_verified_paraphrase(text):
    state=TaskState(session_id='s');m=user(text)
    provider=SemanticProvider(lambda d:TaskStatePatch(expected_version=0,intent='MEMORY_ONLY',
        constraints_add=[item(m,'document_type','Только судебные определения')]).model_dump())
    updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig())
    patch,diag=updater.propose(state,m);result=updater.apply(state,patch,m)
    assert result.constraints_json[0].value=='Только судебные определения'
    assert result.constraints_json[0].evidence==text
    assert result.goal is None and diag['requests']==2
    # Serialization or later modification cannot reuse semantic authorization.
    with pytest.raises(ValueError):updater.apply(state,TaskStatePatch.model_validate(patch.model_dump()),m)
    patch.constraints_add[0].value='Выдуманное дополнительное условие'
    with pytest.raises(ValueError):updater.apply(state,patch,m)


def test_free_form_cancellation_preserves_unrelated_constraint():
    old=user('Только определения суда, без жалоб')
    state=TaskState(session_id='s',version=3,constraints_json=[item(old,'document_type','Только определения суда'),item(old,'exclude_complaints','без жалоб')])
    m=user('Насчет жалоб я передумал, пусть тоже будут.')
    provider=SemanticProvider(lambda d:dict(expected_version=3,intent='MEMORY_ONLY',constraints_remove=['exclude_complaints']))
    updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig())
    patch,_=updater.propose(state,m);result=updater.apply(state,patch,m)
    assert [i.key for i in result.constraints_json]==['document_type'] and result.version==4


@pytest.mark.parametrize('fault',['unrelated_removal','invented_value','missed_condition'])
def test_semantic_rejection_is_bounded_and_atomic(fault):
    old=user('Только определения суда');m=user('Жалобы давай оставим за рамками.')
    state=TaskState(session_id='s',version=1,goal=item(old,'goal'),constraints_json=[item(old,'document_type')])
    before=state.model_dump()
    def proposal(d):
        p=TaskStatePatch(expected_version=1,intent='MEMORY_ONLY',constraints_add=[item(m,'exclude_complaints','Без жалоб')])
        if fault=='unrelated_removal':p.constraints_remove=['document_type']
        if fault=='invented_value':p.constraints_add[0].value='Только документы за 2025 год'
        if fault=='missed_condition':p=TaskStatePatch(expected_version=1)
        return p.model_dump()
    provider=SemanticProvider(proposal,approved=False);updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig())
    with pytest.raises(ValueError) as caught:updater.propose(state,m)
    assert state.model_dump()==before and len(provider.calls)==4
    assert caught.value.diagnostics['requests']==4


@pytest.mark.parametrize('mixed',[False,True])
def test_free_form_intent_routes_acknowledgment_or_rag(ragweb,mixed):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;enable_rewrite(llm)
    text='Можно ограничиться определениями суда?'
    if mixed:text='Давай ограничимся определениями суда и расскажи об оплате.'
    def proposal(d):
        m=ChatMessage.model_validate(d['CURRENT_USER'])
        return TaskStatePatch(expected_version=d['expected_version'],intent='MIXED' if mixed else 'MEMORY_ONLY',
            constraints_add=[item(m,'document_type','Только определения суда')]).model_dump()
    provider=SemanticProvider(proposal)
    chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id;t=chat.start(sid,text,0)
    chat.futures[t.turn_id].result(timeout=20)
    data=chat.load(sid);turn=data['turns'][-1]
    assert data['task_state']['constraints_json'][0]['value']=='Только определения суда'
    if mixed:
        assert turn['comparison_run_id'] and turn['response']['status']!='MEMORY_UPDATED'
        assert any(r.context_type=='grounded_rag' for r in llm.calls)
    else:
        assert turn['response']['status']=='MEMORY_UPDATED' and not llm.calls


def test_truncated_semantic_check_never_saves_unverified_memory():
    state=TaskState(session_id='s');m=user('Давай оставим лишь определения суда.')
    class TruncatedProvider(SemanticProvider):
        def generate(self,request):
            result=super().generate(request)
            if request.question.startswith('DAY25_VERIFY_MEMORY'):
                result.status='TRUNCATED'
                # Even an apparently complete JSON must not bypass finish_reason.
                result.text='{"approved":true}'
            return result
    provider=TruncatedProvider(lambda d:TaskStatePatch(expected_version=0,intent='MEMORY_ONLY',
        constraints_add=[item(m,'document_type','Только определения суда')]).model_dump())
    updater=TaskMemoryUpdater(lambda cfg:provider,LLMConfig())
    with pytest.raises(ValueError) as caught:updater.propose(state,m)
    assert state.version==0 and not state.constraints_json and len(provider.calls)==4
    assert any(a.get('verification_status')=='TRUNCATED' for a in caught.value.diagnostics['attempts'])


def test_policy_trace_and_semantics_reach_rewrite_and_reranker(ragweb):
    from rag_arbiter.application.retrieval_policy import ConstraintPredicate
    client,rid,llm,_=ragweb;chat=client.app.state.chat;enable_rewrite(llm)
    rewrite_requests=[];generate=llm.generate
    def record_request(request):
        if request.question.startswith('Переформулируй'):rewrite_requests.append(request.question)
        return generate(request)
    llm.generate=record_request
    provider=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    sid=chat.create(rid).session_id
    condition=item(user('Только обязанности по отчетности',sid),'reporting')
    condition.predicates=[ConstraintPredicate(type='SEMANTIC_CONSTRAINT',values=[condition.value])]
    with client.app.state.runs.db() as store:
        state=TaskState.model_validate(store.get('task_states',sid));state.constraints_json=[condition]
        store.put('task_states',sid,state.model_dump())
    factory=client.app.state.rag.reranker_factory;queries=[]
    def recording(cfg):
        reranker=factory(cfg);original=reranker.rerank
        def rerank(question,candidates):queries.append(question);return original(question,candidates)
        reranker.rerank=rerank;return reranker
    client.app.state.rag.reranker_factory=recording
    t=chat.start(sid,'Перечисли обязанности',0);chat.futures[t.turn_id].result(timeout=30)
    turn=chat.load(sid)['turns'][-1]
    assert turn['status']=='COMPLETED'
    assert turn['trace']['semantic_constraints_json']==[condition.value]
    assert any(condition.value in q for q in queries)
    assert any(condition.value in question for question in rewrite_requests)
    assert turn['trace']['eligible_document_ids'] and turn['trace']['retrieved_candidates']
    assert turn['trace']['retrieval_counts']['corpus_documents']==2
    assert not chat.load(chat.create(rid).session_id)['task_state']['constraints_json']


def test_forbidden_expanded_context_never_reaches_llm(ragweb,monkeypatch):
    from rag_arbiter.application.context_selection import CandidateProcessor
    client,rid,llm,_=ragweb;chat=client.app.state.chat;enable_rewrite(llm)
    provider=MemoryProvider();chat.memory=TaskMemoryUpdater(lambda cfg:provider,client.app.state.runs.config.llm)
    original=CandidateProcessor.build
    def broken(self,*args,**kwargs):
        candidates,trace=original(self,*args,**kwargs)
        return [c.model_copy(update={'document_id':'foreign'}) for c in candidates],trace
    monkeypatch.setattr(CandidateProcessor,'build',broken)
    sid=chat.create(rid).session_id;t=chat.start(sid,'Перечисли обязанности',0)
    chat.futures[t.turn_id].result(timeout=30)
    turn=chat.load(sid)['turns'][-1]
    assert not turn['sources_json']
    assert not any(r.context_type=='grounded_rag' for r in llm.calls)
