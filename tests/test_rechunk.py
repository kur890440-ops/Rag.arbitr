import json
from unittest.mock import patch
import pytest
from test_web import web, completed, upload_pdf, wait_run
from rag_arbiter.application.rechunk import ChunkSettings


def settings(client, rid, doc=None, **updates):
    params = client.get(f'/api/runs/{rid}/chunk-settings').json()['global_settings']
    params.update(updates)
    r = client.post(f'/api/runs/{rid}/chunk-settings', json={'document_id':doc,'settings':params})
    assert r.status_code==200, r.text
    return params


def rechunk(client, rid, doc=None, strategies=None):
    response = client.post(f'/api/runs/{rid}/rechunk', json={'document_id':doc,'strategies':strategies or ['fixed','structure'],'confirmed':True})
    assert response.status_code==202, response.text
    result = wait_run(client, response.json()['run_id'])
    assert result['status']=='COMPLETED', result
    return result['run_id']


def test_no_recognition_render_and_fixed_only(completed):
    c,runtime,cfg,run=completed
    rid=run['run_id'];doc=c.get(f'/api/runs/{rid}/files').json()[0]['document_id']
    before=c.app.state.views.snapshot(rid)
    settings(c,rid,doc,fixed_tokens=5,fixed_overlap=1)
    calls=runtime.calls
    with patch('rag_arbiter.ingestion.Ingestor.ingest',side_effect=AssertionError('OCR forbidden')), \
         patch('rag_arbiter.ingestion.iter_render_pages',side_effect=AssertionError('render forbidden')), \
         patch('rag_arbiter.recognition.providers.Qwen3VLRecognitionProvider.recognize_page',side_effect=AssertionError('Qwen forbidden')), \
         patch('rag_arbiter.recognition.providers.ClassicOCRRecognitionProvider.recognize_page',side_effect=AssertionError('Classic forbidden')):
        new=rechunk(c,rid,doc,['fixed'])
    after=c.app.state.views.snapshot(new)
    assert runtime.calls==calls
    assert before['indexes']['structure']==after['indexes']['structure']
    assert before['indexes']['fixed']['chunk_ids']!=after['indexes']['fixed']['chunk_ids']
    assert c.get(f'/api/runs/{new}/pages').json()==c.get(f'/api/runs/{rid}/pages').json()
    chunks=c.get(f'/api/runs/{new}/chunks?document_id={doc}&limit=1').json()
    assert len(chunks['fixed']['chunks'])==1
    chunk=chunks['fixed']['chunks'][0]
    assert all(key in chunk for key in ('chunk_id','source','title','file_name','section','token_count','page_start','page_end'))
    search=c.post(f'/api/runs/{new}/search',json={'question':'test'})
    assert search.status_code==200,search.text
    assert {h['chunk_id'] for h in search.json()['fixed']['hits']} <= set(after['indexes']['fixed']['chunk_ids'])
    assert after['evaluation']['status']=='STALE'


def test_file_isolation_global_overrides_and_strategy_changes(web):
    c,runtime,cfg=web
    ids=[upload_pdf(c,cfg) for _ in range(2)]
    rid=c.post('/api/runs',json={'upload_ids':ids}).json()['run_id'];wait_run(c,rid)
    a,b=[f['document_id'] for f in c.get(f'/api/runs/{rid}/files').json()]
    before=c.app.state.views.snapshot(rid)
    settings(c,rid,a,fixed_tokens=5,fixed_overlap=1)
    new=rechunk(c,rid,a)
    after=c.app.state.views.snapshot(new)
    assert before['indexes']['structure']==after['indexes']['structure']
    with c.app.state.runs.db() as store:
        old_b={k for k in before['indexes']['fixed']['chunk_ids'] if store.get('chunks',k)['document_id']==b}
        new_b={k for k in after['indexes']['fixed']['chunk_ids'] if store.get('chunks',k)['document_id']==b}
    assert old_b==new_b
    settings(c,new,fixed_tokens=6,fixed_overlap=1)
    saved=c.get(f'/api/runs/{new}/chunk-settings?document_id={a}').json()
    assert saved['effective']['fixed_tokens']==5
    assert saved['global_settings']['fixed_tokens']==6
    assert c.post(f'/api/runs/{new}/rechunk',json={}).status_code==400
    preview=c.post(f'/api/runs/{new}/rechunk-preview',json={}).json()
    assert preview['documents']==2 and preview['recognition'] is False and preview['keep_overrides']
    assert preview['affected_documents']==1
    mass=rechunk(c,new)
    active=c.app.state.views.snapshot(mass)
    assert active['indexes']['structure']==after['indexes']['structure']
    with c.app.state.runs.db() as store:
        active_a={k for k in active['indexes']['fixed']['chunk_ids'] if store.get('chunks',k)['document_id']==a}
        prior_a={k for k in after['indexes']['fixed']['chunk_ids'] if store.get('chunks',k)['document_id']==a}
        assert active_a==prior_a
        assert ChunkSettings(cfg,store).read(a)['override']['fixed_tokens']==5
    settings(c,mass,structure_max_tokens=5)
    final=rechunk(c,mass,None,['structure'])
    assert c.app.state.views.snapshot(final)['indexes']['fixed']==active['indexes']['fixed']
    assert c.get(f'/api/runs/{final}/chunk-settings?document_id={a}').json()['effective']['structure_max_tokens']==8


def test_panel_metadata_filter_and_evaluation(completed):
    c,_,cfg,run=completed;rid=run['run_id']
    doc=c.get(f'/api/runs/{rid}/files').json()[0]['document_id']
    panel=c.get(f'/ui/runs/{rid}/files').text
    assert 'data-group="processed"' in panel and 'data-group="unprocessed"' in panel
    assert panel.index('data-group="processed"')<panel.index('data-group="unprocessed"')
    assert 'data-select-document=""' in panel and '1с,Q1,F' in panel
    assert '/O0' not in panel and ',!0' not in panel
    ui=c.get(f'/ui/runs/{rid}/workspace?document_id={doc}')
    assert ui.status_code==200 and 'target_tokens' in ui.text
    assert 'id="document-select"' not in ui.text
    chunks=c.get(f'/ui/runs/{rid}/chunks?document_id={doc}').text
    for field in ['chunk_id','source','title/file','section','FIXED SIZE','STRUCTURE AWARE']:assert field in chunks
    assert c.get(f'/api/runs/{rid}/chunks?page=999').json()['fixed']['total']==0
    assert c.get(f'/api/runs/{rid}/chunks?limit=101').status_code==422
    settings(c,rid,doc,fixed_tokens=5,fixed_overlap=1)
    new=rechunk(c,rid,doc,['fixed']);snap=c.app.state.views.snapshot(new)
    cfg.queries_path.write_text(json.dumps({'corpus_hash':snap['corpus']['corpus_hash'],'queries':[{'query_id':'q','question':'test','expected_document':doc}]}))
    eid=c.post(f'/api/runs/{new}/evaluate').json()['run_id'];wait_run(c,eid)
    result=c.app.state.views.snapshot(eid)['evaluation']
    assert result['status']=='SUCCESS'
    assert result['index_runs']=={s:i['run_id'] for s,i in snap['indexes'].items()}
    report=c.get(f'/api/runs/{eid}/report').text
    assert 'parameters_json' in report and 'chunk_id' in report


def test_failed_experiment_keeps_active_versions(completed):
    c,_,_,run=completed;rid=run['run_id']
    original=c.app.state.views.snapshot(rid)
    doc=c.get(f'/api/runs/{rid}/files').json()[0]['document_id']
    settings(c,rid,doc,fixed_tokens=4,fixed_overlap=1)
    with patch('rag_arbiter.vectorstore.LocalVectorStore.upsert',side_effect=RuntimeError('test failure')):
        response=c.post(f'/api/runs/{rid}/rechunk',json={'document_id':doc,'strategies':['fixed']})
        failed=wait_run(c,response.json()['run_id'])
    assert failed['status']=='FAILED'
    assert c.app.state.views.snapshot(rid)==original
    assert c.app.state.views.snapshot(failed['run_id'])==original


def test_settings_validation_and_persistence(completed):
    from rag_arbiter.storage import MetadataStore
    c,_,cfg,run=completed;rid=run['run_id']
    doc=c.get(f'/api/runs/{rid}/files').json()[0]['document_id']
    saved=settings(c,rid,doc,fixed_tokens=700,fixed_overlap=80,fixed_max_tokens=1000)
    store=MetadataStore(cfg.sqlite_path)
    try:assert ChunkSettings(cfg,store).read(doc)['effective']==saved
    finally:store.close()
    assert c.post(f'/api/runs/{rid}/chunk-settings',json={'settings':{'fixed_tokens':-1}}).status_code==400
    assert c.post(f'/api/runs/{rid}/chunk-settings',json={'document_id':'outside','settings':saved}).status_code==400
    assert c.post(f'/api/runs/{rid}/chunk-settings',json={'document_id':doc,'use_global':True}).status_code==200
    assert c.get(f'/api/runs/{rid}/chunk-settings?document_id={doc}').json()['override'] is None


def test_embedding_view_reads_exact_saved_vector_without_model(completed):
    import numpy as np
    from rag_arbiter.documents import digest
    c,runtime,cfg,run=completed;rid=run['run_id']
    snapshot=c.app.state.views.snapshot(rid)
    index=snapshot['indexes']['fixed'];chunk_id=index['chunk_ids'][0]
    calls=runtime.calls
    with patch('rag_arbiter.embeddings.BgeM3EmbeddingProvider.__init__',side_effect=AssertionError('No model load')):
        result=c.get(f'/api/runs/{rid}/chunks/{chunk_id}/embedding')
    assert result.status_code==200
    data=result.json()
    with c.app.state.runs.db() as store:
        chunk=store.get('chunks',chunk_id)
    key=digest([chunk['content_hash'],index['embedding']])
    expected=np.load(cfg.cache_path/'embeddings'/f'{key}.npy',allow_pickle=False)
    assert data['vector']==expected.tolist() and data['dimension']==len(expected)
    assert runtime.calls==calls
    assert c.get(f'/api/runs/{rid}/chunks/unknown/embedding').status_code==404
    html=c.get(f'/ui/runs/{rid}/chunks').text
    assert 'class="embedding-details"' in html and 'data-embedding-url=' in html
    assert json.dumps(data['vector']) not in html
