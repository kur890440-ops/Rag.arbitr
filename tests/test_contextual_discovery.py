"""Real registry, MCP and indexing; inference fixtures are explicitly synthetic."""
import threading,time,copy
from test_rag import ragweb
from test_chat_sources import document, FACTS
from test_network_sources import scan
from test_chat import MemoryProvider, enable_rewrite
from test_document_assistant import ask
from dialogue_fixtures import install
from rag_arbiter.application.chat_memory import TaskMemoryUpdater, ContextualQueryBuilder
from rag_arbiter.application.contextual_discovery import ContextualDiscovery
from rag_arbiter.application.chat_models import MemoryItem
from rag_arbiter.application.retrieval_policy import ConstraintPredicate
from rag_arbiter.network.notifications import cursor


def test_contextual_discovery_lifecycle_and_policy(ragweb,tmp_path,monkeypatch):
    client,rid,llm,_=ragweb;net=client.app.state.network;chat=client.app.state.chat;runs=chat.runs
    root=tmp_path/'share';root.mkdir();net.config.network_sources.allowed_roots.append(str(root));net.config.network_sources.stability_seconds=0
    source=net.add(dict(name='<Source>',root_path=str(root)))
    document(root/'base.docx',FACTS[0]);assert scan(net,source)['ingestion_completed']==1
    base=net.page('network_files',source['id'])[0]['document_id']
    sid=chat.create(rid).session_id;other=chat.create(rid).session_id
    selected=chat.select_source(sid,'NETWORK',source['id'],0,[base]);other_before=chat.load(other)
    with runs.db() as store:
        memory=store.get('task_states',sid)
        memory['constraints_json']=[MemoryItem(key='exclude-complaint',value='Исключить жалобы',evidence='Исключить жалобы',source_message_id='fixture',predicates=[ConstraintPredicate(type='DOCUMENT_TYPE_EXCLUDE',values=['COMPLAINT'])]).model_dump()]
        store.put('task_states',sid,memory)
    svc=ContextualDiscovery(chat);assert not svc.snapshot(sid)['files']
    after=cursor(runs)
    def poll():
        r=client.get('/api/chat/knowledge-updates',params={'after':after,'session_id':sid});assert r.status_code==200,r.text
        return r.json()
    def decide(action,notice):
        return client.post(f'/api/chat/sessions/{sid}/discovery-decision',json=dict(action=action,event_ids=[f['event_id'] for f in notice['files']],document_ids=[f['document_id'] for f in notice['files'] if f['status']=='INDEXED'],expected_version=notice['scope_version']))
    before=threading.Event();release_before=threading.Event();processing=threading.Event();release_processing=threading.Event()
    original_ingest=net.ingest;original_execute=runs.execute
    def held_ingest(*args):
        before.set();assert release_before.wait(20);return original_ingest(*args)
    def held_execute(*args):
        processing.set();assert release_processing.wait(20);return original_execute(*args)
    monkeypatch.setattr(net,'ingest',held_ingest);monkeypatch.setattr(runs,'execute',held_execute)
    document(root/'new.docx',FACTS[1]);scan_id=net.start(source['id'])
    try:
        assert before.wait(20)
        notice=poll()['notice'];assert [f['status'] for f in notice['files']]==['NEW']
        assert notice['ready_count']==0 and decide('add',notice).status_code==400
        release_before.set();assert processing.wait(20)
        notice=poll()['notice'];assert [f['status'] for f in notice['files']]==['PROCESSING']
        assert decide('add',notice).status_code==400
    finally:release_before.set();release_processing.set()
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        notice=poll()['notice']
        if notice['ready_count']==1:break
        time.sleep(.1)
    assert notice['ready_count']==1
    assert chat.load(sid)['session']['selected_document_ids']==[base]
    assert not poll()['events']  # No toast for the source whose proposal is inline.
    assert ContextualDiscovery(chat).snapshot(sid)['files']==notice['files']  # Reopen/service recreation.
    added=notice['files'][0]['document_id']
    bad=client.post(f'/api/chat/sessions/{sid}/discovery-decision',json=dict(action='add',event_ids=[f['event_id'] for f in notice['files']],document_ids=[base],expected_version=notice['scope_version']))
    assert bad.status_code==400
    result=decide('add',notice);assert result.status_code==200,result.text
    assert result.json()['session']['selected_document_ids']==[base,added]
    assert not result.json()['notice']['files']
    assert decide('add',notice).status_code==400  # Stale replay cannot duplicate selection.
    assert chat.load(sid)['task_state']==memory
    assert chat.load(other)['session']==other_before['session']
    monkeypatch.setattr(net,'ingest',original_ingest);monkeypatch.setattr(runs,'execute',original_execute)
    # Both analysis paths consume the same expanded hard policy, preserving Task Memory.
    enable_rewrite(llm);provider=MemoryProvider()
    chat.memory=TaskMemoryUpdater(lambda _:provider,runs.config.llm);chat.resolver=ContextualQueryBuilder(lambda _:provider,runs.config.llm)
    install(chat,llm,runs.config.llm)
    point=ask(client,sid,'/help Какие условия?')
    assert set(point['trace']['eligible_document_ids'])=={base,added}
    full=ask(client,sid,'/help Сравни все условия.')
    assert set(full['trace']['coverage']['eligible_document_ids'])=={base,added}
    assert chat.load(sid)['task_state']['constraints_json']==memory['constraints_json']
    # Another source is a general toast only.
    root2=tmp_path/'other';root2.mkdir();net.config.network_sources.allowed_roots.append(str(root2))
    source2=net.add(dict(name='Other',root_path=str(root2)));document(root2/'other.docx',FACTS[2])
    assert scan(net,source2)['ingestion_completed']==1
    assert not poll()['notice']['files'] and [e['count'] for e in poll()['events']]==[1]
    # Dismiss survives a fresh service instance and unchanged scan. Changed version is distinct.
    document(root/'later.docx',FACTS[2]);assert scan(net,source)['ingestion_completed']==1
    notice=poll()['notice'];version=chat.load(sid)['session']['scope_version']
    assert decide('dismiss',notice).status_code==200
    assert chat.load(sid)['session']['scope_version']==version
    assert not ContextualDiscovery(chat).snapshot(sid)['files']
    scan(net,source);assert not poll()['notice']['files']
    document(root/'later.docx',FACTS[0]+' Новый текст.');scan(net,source)
    replacement=poll()['notice'];assert replacement['files'][0]['event_id']!=notice['files'][0]['event_id']
    # Canonical readiness, not a SUCCESS label alone.
    with runs.db() as store:
        row=next(r for r in store.all('network_files') if r['filename']=='later.docx')
        row['indexed_discovery_event_id']=None;store.put('network_files',row['id'],row)
    assert svc.snapshot(sid)['ready_count']==0  # Old successful index is not this discovery version.
    with runs.db() as store:
        row['indexed_discovery_event_id']=row['discovery_event_id'];row['active_run_id']=None;store.put('network_files',row['id'],row)
    assert svc.snapshot(sid)['ready_count']==0
    assert decide('add',svc.snapshot(sid)).status_code==400
    with runs.db() as store:
        row['ingestion_status']='FAILED';row['indexing_status']='FAILED';store.put('network_files',row['id'],row)
    assert svc.snapshot(sid)['files'][0]['status']=='FAILED'
    # Permissions and dynamic scopes never offer hidden/automatically included files.
    net.config.network_sources.allowed_roots.remove(str(root));assert not svc.snapshot(sid)['files']
    net.config.network_sources.allowed_roots.append(str(root))
    chat.select_source(sid,'NETWORK',source['id'],version)
    assert not svc.snapshot(sid)['files']
    assert [e['count'] for e in poll()['events']]==[1]  # Previously inline source events stay suppressed after scope change.
    chat.select_source(sid,'ALL_DOCUMENTS',None,version+1)
    assert not svc.snapshot(sid)['files']
