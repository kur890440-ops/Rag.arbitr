import asyncio
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from rag_arbiter.application.runs import RunService
from rag_arbiter.network.sync import SyncService
from rag_arbiter.network.connector import Connector, SourceError
from rag_arbiter.network.client import connect, call
from rag_arbiter.web.app import create_app
from test_web import factory
from test_recognition import FakeRuntime


def setup(config, tmp_path):
    root = tmp_path / 'share'
    root.mkdir()
    config.network_sources.allowed_roots = [str(root)]
    config.network_sources.stability_seconds = 0
    runtime = FakeRuntime()
    runs = RunService(config, factory(runtime))
    sync = SyncService(runs)
    source = sync.add(dict(name='Test source', root_path=str(root)))
    return root, runs, sync, source, runtime


def scan(sync, source):
    sid = sync.start(source['id'])
    deadline = time.monotonic()+40
    while time.monotonic() < deadline:
        with sync.runs.db() as store:
            row = store.get('network_scans', sid)
        if row['status'] not in {'QUEUED','RUNNING'}:
            return row
        time.sleep(.05)
    raise AssertionError('scan timeout')


def png(path, color='white'):
    Image.new('RGB', (50,50), color).save(path)


def test_incremental_real_mcp_existing_ingestion(config, tmp_path):
    root,runs,sync,source,runtime = setup(config,tmp_path)
    try:
        png(root/'a.png')
        first = scan(sync, source)
        assert first['status']=='COMPLETED', first
        assert first['new_count']==first['ingestion_completed']==1
        files = sync.page('network_files',source['id'])
        old = files[0]
        assert old['ingestion_status']=='SUCCESS'
        with runs.db() as store:
            assert store.get('documents',old['document_id'])
            assert store.all('network_provenance')[0]['source_id']==source['id']
        calls = runtime.calls
        repeat = scan(sync,source)
        assert repeat['unchanged_count']==1 and repeat['ingestion_queued']==0
        assert runtime.calls==calls
        png(root/'a.png','black')
        modified = scan(sync,source)
        assert modified['modified_count']==modified['ingestion_completed']==1
        current = sync.page('network_files',source['id'])[0]
        assert current['document_id']!=old['document_id']
        from rag_arbiter.application.views import ResultsService
        snapshot = ResultsService(runs).snapshot(current['run_id'])
        assert all(old['document_id'] not in i['document_ids'] for i in snapshot['indexes'].values())
        png(root/'b.png')
        added = scan(sync,source)
        assert added['new_count']==1 and added['unchanged_count']==1
        (root/'a.png').unlink()
        missing = scan(sync,source)
        assert missing['missing_count']==1
        with runs.db() as store:
            assert store.get('documents',current['document_id'])
        root.rename(tmp_path/'offline')
        offline = scan(sync,source)
        assert offline['status']=='ERROR' and offline['missing_count']==0
        assert next(f for f in sync.page('network_files',source['id']) if f['filename']=='b.png')['status']=='UNCHANGED'
        print('DAY31_FIXTURE', {k:first[k] for k in ('files_discovered','new_count','ingestion_completed')}, {k:repeat[k] for k in ('unchanged_count','ingestion_queued')})
    finally:
        sync.close(); runs.close()


def test_mcp_protocol_and_chunk_limit(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    (root/'large.pdf').write_bytes(b'%PDF-'+b'x'*600000)
    for i in range(21):
        (root/f'{i}.pdf').write_bytes(b'%PDF-test')
    async def exercise():
        async with connect(config) as session:
            assert {t.name for t in (await session.list_tools()).tools}=={'list_sources','list_directory','scan_source','get_file_metadata','read_file_chunk'}
            cursor, names = '', []
            while True:
                batch = await call(session, 'scan_source', source_id=source['id'], cursor=cursor)
                assert len(batch['files']) <= 10
                names.extend(row['relative_path'] for row in batch['files'])
                cursor = batch['cursor']
                if not cursor:
                    break
            assert len(names) == len(set(names)) == 22
            result=await call(session,'read_file_chunk',source_id=source['id'],relative_path='large.pdf')
            import base64
            assert len(base64.b64decode(result['data']))==262144 and not result['eof']
            response=await session.call_tool('read_file_chunk',{'source_id':source['id'],'relative_path':'large.pdf','length':10000000})
            assert response.isError
    try:
        asyncio.run(exercise())
    finally:
        sync.close(); runs.close()


def test_root_traversal_and_junction(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    connector=Connector(config.network_sources,sync.source)
    try:
        for relative in ['../secret','..\\secret','C:\\Windows\\win.ini','/etc/passwd','a.png:secret']:
            with pytest.raises((ValueError,OSError)):
                connector.path(source['id'],relative)
        with pytest.raises(ValueError):
            sync.add(dict(name='Forbidden',root_path=str(tmp_path)))
        outside=tmp_path/'outside'; outside.mkdir(); png(outside/'escape.png')
        link=root/'link'
        if os.name=='nt':
            import subprocess
            result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
            assert result.returncode==0, result.stderr
        else:
            link.symlink_to(outside,target_is_directory=True)
        with pytest.raises(SourceError):
            connector.read(source['id'],'link/escape.png')
    finally:
        sync.close(); runs.close()


def test_bounded_scan_and_partial_does_not_mark_missing(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    try:
        png(root/'a.png')
        assert scan(sync,source)['ingestion_completed']==1
        config.network_sources.max_files_per_scan=2
        for i in range(5):
            (root/f'{i}.txt').write_text('x')
        result=scan(sync,source)
        assert result['status']=='ERROR' and result['missing_count']==0
        connector=Connector(config.network_sources,sync.source)
        assert len(connector.directory(source['id'],limit=2)['entries'])==2
    finally:
        sync.close(); runs.close()


def test_unstable_deferred_and_duplicate_scan(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    png(root/'a.png')
    entered,release=threading.Event(),threading.Event()
    async def unstable(*args):
        entered.set()
        await asyncio.to_thread(release.wait,5)
        raise ValueError('FILE_UNSTABLE')
    try:
        with patch.object(sync,'download',unstable):
            sid=sync.start(source['id'])
            assert entered.wait(10)
            assert sync.start(source['id'])==sid
            release.set()
            deadline=time.monotonic()+10
            while sid in sync.signals and time.monotonic()<deadline:
                time.sleep(.05)
        rows=sync.page('network_files',source['id'])
        assert rows[0]['status']=='DEFERRED'
        assert scan(sync,source)['ingestion_completed']==1
    finally:
        release.set(); sync.close(); runs.close()


def test_source_ui_and_csrf(config,tmp_path):
    root=tmp_path/'share'; root.mkdir()
    config.network_sources.allowed_roots=[str(root)]
    with TestClient(create_app(config,factory(FakeRuntime())),base_url='http://127.0.0.1') as client:
        assert client.get('/sources').status_code==200
        assert client.post('/api/sources',json={'name':'x','root_path':str(root)}).status_code==403
        response=client.post('/api/sources',json={'name':'x','root_path':str(root)},headers={'X-RAG-Request':'1'})
        assert response.status_code==200, response.text
        sid=response.json()['id']
        assert client.get(f'/sources/{sid}').status_code==200
        assert client.get(f'/sources/{sid}/tree').status_code==200
        assert client.post(f'/api/sources/{sid}/check',headers={'X-RAG-Request':'1'}).json()['status']=='ACCESS_OK'
        assert client.get('/').status_code==200
        assert client.get('/chat').status_code==200

def test_filters_limits_and_error_isolation(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    try:
        (root/'nested').mkdir()
        png(root/'nested'/'ok.png')
        png(root/'~$temp.png')
        png(root/'ignore.png')
        (root/'file.docx').write_bytes(b'PK')
        with runs.db() as store:
            source['exclude_patterns']=['ignore*']
            store.put('network_sources',source['id'],source)
        result=scan(sync,source)
        rows=sync.page('network_files',source['id'])
        assert result['ingestion_completed']==1 and result['ingestion_failed']==1
        assert {r['relative_path'] for r in rows}=={'nested/ok.png','file.docx'}
        assert next(r for r in rows if r['filename']=='file.docx')['ingestion_status']=='FAILED'
        config.network_sources.max_file_size=2
        png(root/'large.png')
        result=scan(sync,source)
        assert any(r['error']=='FILE_TOO_LARGE' for r in sync.page('network_files',source['id']))
        assert result['missing_count']==0
    finally:
        sync.close(); runs.close()


def test_stability_retry_checks_metadata_and_cleans_temp(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    config.network_sources.stability_retries=1
    calls=[]
    async def changing(session,name,**args):
        calls.append(name)
        return {'relative_path':'a.png','size':len(calls),'mtime_ns':len(calls)}
    try:
        with patch('rag_arbiter.network.sync.call',changing):
            with pytest.raises(ValueError,match='FILE_UNSTABLE'):
                asyncio.run(sync.download(None,source['id'],'a.png',threading.Event()))
        assert len(calls)==4
        assert not list((config.web_data_path/'network'/'staging').iterdir())
    finally:
        sync.close(); runs.close()


def test_cancel_queued_and_retry(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    png(root/'a.png')
    gate=threading.Event()
    blocker=runs.executor.submit(gate.wait,10)
    try:
        sid=sync.start(source['id'])
        sync.cancel(sid)
        gate.set(); blocker.result()
        deadline=time.monotonic()+10
        while sid in sync.signals and time.monotonic()<deadline:
            time.sleep(.05)
        with runs.db() as store:
            assert store.get('network_scans',sid)['status']=='CANCELLED'
        assert not sync.page('network_files',source['id'])
        assert scan(sync,source)['ingestion_completed']==1
    finally:
        gate.set(); sync.close(); runs.close()


def test_identical_content_retains_source_provenance(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    other=tmp_path/'second'; other.mkdir()
    config.network_sources.allowed_roots.append(str(other))
    second=sync.add(dict(name='Second',root_path=str(other)))
    png(root/'same.png'); png(other/'same.png')
    try:
        assert scan(sync,source)['ingestion_completed']==1
        assert scan(sync,second)['ingestion_completed']==1
        a=sync.page('network_files',source['id'])[0]
        b=sync.page('network_files',second['id'])[0]
        assert a['content_hash']==b['content_hash'] and a['document_id']!=b['document_id']
        with runs.db() as store:
            assert {p['source_id'] for p in store.all('network_provenance')}=={source['id'],second['id']}
    finally:
        sync.close(); runs.close()

def test_active_cancellation_survives_mcp_taskgroup(config,tmp_path):
    root,runs,sync,source,_ = setup(config,tmp_path)
    png(root/'a.png')
    entered=threading.Event()
    async def pending(session,sid,relative,signal):
        entered.set()
        while not signal.is_set():
            await asyncio.sleep(.01)
        sync.check(signal)
    try:
        with patch.object(sync,'download',pending):
            sid=sync.start(source['id'])
            assert entered.wait(15)
            sync.cancel(sid)
            deadline=time.monotonic()+10
            while sid in sync.signals and time.monotonic()<deadline:
                time.sleep(.05)
        with runs.db() as store:
            assert store.get('network_scans',sid)['status']=='CANCELLED'
    finally:
        sync.close(); runs.close()


def test_timeout_diagnostic():
    assert SyncService.code(ExceptionGroup('transport', [ValueError('Timed out while waiting for response to ClientRequest')])) == 'MCP_TIMEOUT'
    assert SyncService.code(TimeoutError()) == 'MCP_TIMEOUT'
