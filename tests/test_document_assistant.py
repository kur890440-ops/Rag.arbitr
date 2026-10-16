"""Command tests: real app/registries; semantic classification explicitly scripted."""
import json
import pytest
from rag_arbiter.application.archive_status import ArchiveStatusProvider
from rag_arbiter.application.dialogue import AssistantIntent,AnswerModeRouter
from rag_arbiter.application.chat_models import ChatMessage
from rag_arbiter.llm import LLMResult
from test_web import web


def ask(client,sid,text):
    chat=client.app.state.chat
    before=chat.load(sid)
    response=client.post(f'/api/chat/sessions/{sid}/messages',json=dict(content=text,expected_version=before['task_state']['version'],expected_scope_version=before['session']['scope_version']))
    assert response.status_code==202,response.text
    chat.futures[response.json()['turn_id']].result(timeout=30)
    result=chat.load(sid)['turns'][-1]
    assert result['status']=='COMPLETED',result
    return result


def test_help_registry_scope_pending_errors_no_llm_no_scan(web,tmp_path):
    client,_,_=web;svc=client.app.state.network;chat=client.app.state.chat
    sources=[]
    for n in range(2):
        root=tmp_path/str(n);root.mkdir();svc.config.network_sources.allowed_roots.append(str(root))
        sources.append(svc.add(dict(name=f'Source {n}',root_path=str(root))))
    with svc.runs.db() as store:
        for n,source in enumerate(sources):
            store.put('network_scans',f'scan{n}',dict(scan_id=f'scan{n}',source_id=source['id'],started_at='2026-10-15T01:00:00+00:00',status='ERROR' if n==0 else 'COMPLETED',new_count=1,modified_count=0,unchanged_count=0,error_count=1 if n==0 else 0,errors=['READ_ERROR'] if n==0 else []))
            store.put('network_files',f'file{n}',dict(id=f'file{n}',source_id=source['id'],filename=f'secret{n}.docx',relative_path=f'folder{n}/secret{n}.docx',status='NEW',ingestion_status='PENDING',indexing_status='SUCCESS',document_id=f'fake{n}',seen_scan=f'scan{n}'))
    sid=chat.create(None).session_id;chat.select_source(sid,'NETWORK',sources[0]['id'],0)
    def forbidden(*a,**kw):raise AssertionError('Read-only help must not run inference or scan')
    chat.rag.llm_factory=forbidden;chat.memory.propose=forbidden;svc.start=forbidden
    before=chat.load(sid)
    result=ask(client,sid,'/help')
    assert result['response']['status']=='ARCHIVE_STATUS'
    info=result['trace']['archive_status']
    assert info['counts']['discovered']==1 and info['counts']['indexed']==0 and info['counts']['pending']==1
    assert 'READ_ERROR' in result['response']['answer_text']
    assert 'secret0.docx' in result['response']['answer_text'] and 'secret1' not in json.dumps(info)
    assert str(tmp_path) not in json.dumps(info)
    after=chat.load(sid)
    assert after['task_state']==before['task_state'] and after['dialogue_context']==before['dialogue_context']
    assert result['comparison_run_id'] is None
    chat.router.assistant_intent=lambda _:AssistantIntent(target='ARCHIVE_STATUS',status_view='LAST_SCAN',confidence=1)
    status=ask(client,sid,'/help Какие документы обнаружены при последнем scan?')
    assert 'Файлов по запросу: 1' in status['response']['answer_text']
    assert 'secret0.docx' in client.get(f'/ui/chat/{sid}').text
    chat.select_source(sid,'MANUAL',None,1)
    manual=ask(client,sid,'/help')
    assert manual['trace']['archive_status']['counts']['discovered']==0
    assert 'secret0' not in manual['response']['answer_text']
    chat.select_source(sid,'NETWORK',None,2)
    all_network=ask(client,sid,'/help')
    assert all_network['trace']['archive_status']['counts']['discovered']==2
    assert 'secret1.docx' in client.get(f'/ui/chat/{sid}/discovery').text


def test_assistant_typed_route_only_sends_question(config):
    calls=[]
    class Provider:
        def generate(self,request):
            calls.append(request)
            return LLMResult(model='MOCK',status='SUCCESS',text=json.dumps(dict(target='ARCHIVE_STATUS',status_view='PENDING',confidence=1)))
    router=AnswerModeRouter(lambda _:Provider(),config.llm)
    intent=router.assistant_intent('Какие файлы ещё не обработаны?')
    assert intent.status_view=='PENDING'
    payload=json.loads('{'+calls[0].question.split('\n{',1)[1])
    assert set(payload)=={'question','schema'}
    assert not calls[0].context


def test_status_views_are_registry_filters():
    info=dict(scope='ALL_DOCUMENTS',source_id=None,sources=[],folders=[],counts=dict(discovered=3,indexed=1,pending=1,queued=1,processing=0,failed=1,missing=0),files=[
        dict(filename='a.docx',format='DOCX',availability='INDEXED',status='UNCHANGED',last_scan=True),
        dict(filename='b.docx',format='DOCX',availability='QUEUED',status='NEW',last_scan=True),
        dict(filename='c.docx',format='DOCX',availability='FAILED',status='ERROR',last_scan=False)])
    text,_=ArchiveStatusProvider.answer(info,'NEW');assert 'b.docx' in text and 'a.docx' not in text
    text,_=ArchiveStatusProvider.answer(info,'PENDING');assert 'b.docx' in text and 'c.docx' not in text
    text,_=ArchiveStatusProvider.answer(info,'ERRORS');assert 'c.docx' in text and 'a.docx' not in text

from test_rag import ragweb
from test_chat import MemoryProvider,enable_rewrite
from dialogue_fixtures import install
from rag_arbiter.application.chat_memory import TaskMemoryUpdater,ContextualQueryBuilder


def test_help_pdf_comparison_explicit_names_and_followup(ragweb):
    client,rid,llm,_=ragweb;chat=client.app.state.chat;cfg=client.app.state.runs.config
    enable_rewrite(llm);memory=MemoryProvider()
    chat.memory=TaskMemoryUpdater(lambda _:memory,cfg.llm);chat.resolver=ContextualQueryBuilder(lambda _:memory,cfg.llm)
    install(chat,llm,cfg.llm)
    sid=chat.create(rid).session_id
    chat.router.assistant_intent=lambda _:AssistantIntent(target='DOCUMENT_CONTENT',document_names=['sample.pdf'],confidence=1)
    one=ask(client,sid,'/help Когда оплата в sample.pdf?')
    assert one['grounding_status']=='GROUNDED'
    assert len(one['trace']['eligible_document_ids'])==1
    assert all(c['page_start']>=1 for c in one['citations_json'])
    assert '/help' not in one['trace']['command_question']
    prior=chat.load(sid)['dialogue_context']
    help_turn=ask(client,sid,'/help')
    assert help_turn['trace']['archive_status']['counts']['indexed']==2
    assert chat.load(sid)['dialogue_context']==prior
    follow=ask(client,sid,'А когда оплата?')
    assert follow['grounding_status']=='GROUNDED'
    chat.router.assistant_intent=lambda _:AssistantIntent(target='DOCUMENT_CONTENT',document_names=['sample.pdf','other.pdf'],confidence=1)
    comparison=ask(client,sid,'/help Сравни sample.pdf и other.pdf.')
    assert comparison['trace']['answer_mode']=='EXHAUSTIVE_NO_RAG'
    assert len(comparison['trace']['coverage']['eligible_document_ids'])==2
    chat.router.assistant_intent=lambda _:AssistantIntent(target='DOCUMENT_CONTENT',document_names=['missing.pdf'],confidence=1)
    missing=ask(client,sid,'/help Сравни missing.pdf.')
    assert missing['response']['status']=='NEEDS_CLARIFICATION' and not missing['sources_json']


def test_manual_partial_registry_includes_failed_upload(ragweb):
    client,rid,_,_=ragweb;runs=client.app.state.runs
    with runs.db() as store:
        run=store.get('processing_runs',rid);run['status']='PARTIAL';run['upload_ids'].append('failed-upload')
        store.put('processing_runs',rid,run)
        store.put('uploads','failed-upload',dict(filename='failed.docx'))
    session=client.app.state.chat.create(rid).model_dump();session['source_scope']='MANUAL'
    info=ArchiveStatusProvider(runs).snapshot(session)
    assert info['counts']['discovered']==3 and info['counts']['indexed']==2 and info['counts']['failed']==1


def test_connector_rejects_service_files(config,tmp_path):
    from rag_arbiter.network.connector import Connector,SourceError
    root=tmp_path/'private';root.mkdir();(root/'secrets').mkdir()
    (root/'ok.docx').write_bytes(b'fixture');(root/'secrets'/'private.docx').write_bytes(b'not read')
    (root/'credentials.docx').write_bytes(b'not read');(root/'.env').write_bytes(b'not read')
    config.network_sources.allowed_roots=[str(root)]
    source=dict(root_path=str(root),include_patterns=['*'],exclude_patterns=[],recursive=True)
    connector=Connector(config.network_sources,lambda _:source)
    for name in ('.env','secrets/private.docx','credentials.docx'):
        with pytest.raises(SourceError,match='ACCESS_DENIED'):connector.read('s',name)
    assert [r['relative_path'] for r in connector.scan('s')]==['ok.docx']


def test_discovery_search_before_pagination_and_scope(web,tmp_path):
    client,_,_=web;svc=client.app.state.network;chat=client.app.state.chat
    root=tmp_path/'listing';root.mkdir();svc.config.network_sources.allowed_roots.append(str(root))
    source=svc.add(dict(name='Listing',root_path=str(root)))
    with svc.runs.db() as store:
        for i in range(105):
            store.put('network_files',str(i),dict(id=str(i),source_id=source['id'],filename=f'Документ {i:03}.docx',status='NEW'))
    sid=chat.create(None).session_id
    chat.select_source(sid,'NETWORK',source['id'],0)
    url=f'/ui/chat/{sid}/discovery'
    page=client.get(url).text
    assert 'Страница 1 из 11' in page and 'Документ 009.docx' in page and 'Документ 010.docx' not in page
    last=client.get(url,params={'offset':100}).text
    assert 'Страница 11 из 11' in last and 'Документ 104.docx' in last
    found=client.get(url,params={'q':'ДОКУМЕНТ 104','offset':100}).text
    assert 'Показаны 1–1 из 1' in found and 'Документ 104.docx' in found and 'Документ 103.docx' not in found
    assert 'Документы не найдены' in client.get(url,params={'q':'absent'}).text
    assert 'Страница 1 из 5' in client.get(url,params={'page_size':25}).text
    assert client.get(url,params={'offset':-1}).status_code==422
    assert client.get(url,params={'page_size':999}).status_code==400
    chat.select_source(sid,'MANUAL',None,1)
    assert 'Документ 104' not in client.get(url,params={'q':'104'}).text
