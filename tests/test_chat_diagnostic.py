import json
from pathlib import Path
import pytest
from rag_arbiter.application.chat_diagnostic import ChatDiagnosticService, redact
from rag_arbiter.application.diagnostic_trace import events
from rag_arbiter.application.chat_memory import TaskMemoryUpdater
from rag_arbiter.llm import LLMResult
from test_rag import ragweb
from test_chat import MemoryProvider, enable_rewrite
from dialogue_fixtures import install


def test_redaction_nested_free_text_and_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MINIMAX_API_KEY', 'env-secret-value')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'router-secret-value')
    Path('.env').write_text('DB_PASSWORD=dotenv-secret-value\n', encoding='utf-8')
    data={'Authorization':'Bearer test-token', 'nested':[
        'env-secret-value router-secret-value dotenv-secret-value',
        'Authorization: Bearer hidden\nCookie: session=cookie-value',
        'postgres://admin:dbpassword@host/db', 'password=another-secret',
        '{"Authorization": "Basic old-auth"}', '{"password":"old-password"}',
        'MINIMAX_API_KEY OPENROUTER_API_KEY'], 'token_count':123}
    text=json.dumps(redact(data))
    for value in ['Authorization','Bearer','cookie-value','test-token','dbpassword','another-secret',
                  'old-auth','old-password','env-secret-value','router-secret-value','dotenv-secret-value','MINIMAX_API_KEY','OPENROUTER_API_KEY']:
        assert value not in text
    assert '123' in text and '[REDACTED]' in text


def test_problem_scenario_export_readonly_focus_and_trace(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=client.app.state.runs.config.llm
    install(chat,llm,cfg);enable_rewrite(llm)
    chat.memory=TaskMemoryUpdater(lambda _:MemoryProvider(),cfg)
    with chat.runs.db() as db:
        for doc in db.all('documents'):
            doc['retrieval_metadata']=dict(document_id=doc['document_id'],document_type='COURT_RULING',classification_status='CONFIRMED',classification_source='TRUSTED_MANUAL')
            db.put('documents',doc['document_id'],doc)
    sid=chat.create(rid).session_id
    questions=['Какие обязанности конкурсного управляющего встречаются в документах?',
               'Учитывай только судебные определения.','Жалобы исключи.',
               'Какие из этих обязанностей связаны с отчетностью?']
    original=llm.generate
    def generate(request):
        if request.question.startswith('DAY25_PREDICATE_EVALUATION'):
            d=json.loads('{'+request.question.split('\n{',1)[1])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'results':[
                dict(claim_id=c['claim_id'],status='UNKNOWN',supporting_source_ids=[],reason='No relation in sources',confidence=1) for c in d['claims']]}))
        return original(request)
    llm.generate=generate
    for question in questions:
        t=chat.start(sid,question,chat.load(sid)['task_state']['version'])
        chat.futures[t.turn_id].result(timeout=30)
    before=chat.load(sid);q1,q2,q3,q4=before['turns']
    assert q4['grounding_status']=='INSUFFICIENT_CONTEXT'
    response=client.get(f'/api/chat/sessions/{sid}/diagnostic')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    report=response.text
    for text in [questions[0],questions[3],'Оплата через десять дней.',q4['turn_id'],
                 'REFERENCE RESOLUTION','TASK STATE','RETRIEVAL POLICY','RAW CANDIDATES','RERANKING',
                 'FINAL CONTEXTS SENT TO LLM','ALL_UNKNOWN_OR_NOT_RECONFIRMED',
                 'DAY25_REFERENCES','rewrite_guard','DAY25_PREDICATE_EVALUATION']:
        assert text in report
    assert chat.load(sid)==before
    assert events.get() is None
    assert client.get(f'/api/chat/sessions/{sid}/diagnostic?turn_id={q1["turn_id"]}').status_code==200
    other=chat.create(rid).session_id
    assert client.get(f'/api/chat/sessions/{other}/diagnostic?turn_id={q4["turn_id"]}').status_code==404
    assert client.get('/api/chat/sessions/missing/diagnostic').status_code==404
    assert client.get(f'/api/chat/sessions/{sid}/diagnostic?download=true').headers['content-disposition'].startswith('attachment;')
    page=client.get('/chat?session_id='+sid).text
    assert 'diagnostic-copy' in page and 'Скопировать диагностику' in page


def test_empty_session_not_recorded_and_local_key_redacted(ragweb):
    client,rid,_,_=ragweb;chat=client.app.state.chat;sid=chat.create(rid).session_id
    report=ChatDiagnosticService(chat.runs).build(sid)
    assert 'NOT RECORDED' in report
    from pydantic import SecretStr
    chat.runs.config.llm.api_key=SecretStr('local-diagnostic-secret')
    with chat.runs.db() as db:
        s=db.get('chat_sessions',sid);s['title']='local-diagnostic-secret';db.put('chat_sessions',sid,s)
    assert 'local-diagnostic-secret' not in ChatDiagnosticService(chat.runs).build(sid)
