from unittest.mock import patch
from test_rag import ragweb,compare
from test_rechunk import settings,rechunk
from rag_arbiter.vectorstore import LocalVectorStore
from rag_arbiter.application.rag import source_metrics


def test_default_all_document_selection_independent_and_filters(ragweb):
    c,rid,llm,_=ragweb;docs=c.app.state.views.files(rid);doc=docs[0]['document_id']
    original=LocalVectorStore.search;calls=[]
    def search(self,name,vector,k,document_ids=None,chunk_ids=None):
        calls.append((name,document_ids,chunk_ids))
        return original(self,name,vector,k,document_ids,chunk_ids)
    with patch.object(LocalVectorStore,'search',search):
        for strategy in ('fixed','structure'):
            r=compare(c,rid,selected_document_id=doc,strategy=strategy,top_k=10)
            assert r['rag_scope']=='ALL_DOCUMENTS' and r['selected_document_id']==doc
            assert r['scope_type']=='CORPUS' and r['full_document_id']==doc
            assert len(r['documents_represented'])==2
            assert r['corpus_chunks_eligible']>=r['retrieved_count']
            assert r['index_versions_used'] and r['corpus_id']
            assert all(h['corpus_id']==r['corpus_id'] and h['chunking_run_id'] for h in r['retrieved_sources'])
    assert all(ids is None and chunks for _,ids,chunks in calls)
    calls.clear()
    with patch.object(LocalVectorStore,'search',search):
        r=compare(c,rid,selected_document_id=doc,rag_scope='SELECTED_DOCUMENT',top_k=10)
    assert all(ids==[doc] for _,ids,_ in calls)
    assert r['documents_represented']==[doc]


def test_missing_selection_and_raw_search_share_scope(ragweb):
    c,rid,llm,_=ragweb
    with patch.object(c.app.state.runs,'pipeline_factory',side_effect=AssertionError('No retrieval allowed')):
        r=compare(c,rid,rag_scope='SELECTED_DOCUMENT')
    assert r['rag_result']['status']=='DOCUMENT_REQUIRED' and r['status']=='PARTIAL'
    doc=c.app.state.views.files(rid)[0]['document_id']
    c.app.state.runs.config.top_k=10
    for scope in ('ALL_DOCUMENTS','SELECTED_DOCUMENT'):
        result=c.post(f'/api/runs/{rid}/search',json={'question':'Question','selected_document_id':doc,'rag_scope':scope})
        assert result.status_code==200,result.text
        if scope=='SELECTED_DOCUMENT':assert all(h['document_id']==doc for g in result.json().values() for h in g['hits'])
    assert c.post(f'/api/runs/{rid}/search',json={'question':'q','rag_scope':'SELECTED_DOCUMENT'}).status_code==400


def test_history_excluded_and_per_file_overrides(ragweb):
    c,rid,llm,_=ragweb;docs=c.app.state.views.files(rid);a,b=[d['document_id'] for d in docs]
    original=c.app.state.views.snapshot(rid)['indexes']['fixed']
    settings(c,rid,a,fixed_tokens=5,fixed_overlap=1,fixed_max_tokens=5)
    newer=rechunk(c,rid,a,['fixed'])
    settings(c,newer,b,fixed_tokens=6,fixed_overlap=1,fixed_max_tokens=6)
    newest=rechunk(c,newer,b,['fixed'])
    active=c.app.state.views.snapshot(newest)['indexes']['fixed']
    inactive=set(original['chunk_ids'])-set(active['chunk_ids'])
    assert inactive and len(active['partitions'])==2
    # Query each document explicitly and the whole active corpus, with old vectors still on disk.
    for scope,doc in [('ALL_DOCUMENTS',a),('SELECTED_DOCUMENT',a),('SELECTED_DOCUMENT',b)]:
        r=compare(c,newest,strategy='fixed',top_k=10,rag_scope=scope,selected_document_id=doc)
        ids=set(r['retrieved_chunk_ids_json'])
        assert ids and not ids.intersection(inactive) and ids<=set(active['chunk_ids'])
        assert {h['chunking_run_id'] for h in r['retrieved_sources']}<={p['run_id'] for p in active['partitions']}
        if scope=='ALL_DOCUMENTS':assert set(r['documents_represented'])=={a,b}


def test_document_metrics_identity_and_precise_sources():
    hits=[dict(document_id='A',file_name='renamed.pdf',page_start=3,page_end=4,section='Exact section',chunk_id='one')]
    expected=[dict(document_id='A',file_name='old.pdf',page_start=9)]
    m=source_metrics(hits,expected,5)
    assert m['document_hit@1']==1 and m['source_hit@1']==0
    assert source_metrics(hits,[dict(document_id='A',page_start=4)],5)['source_hit@1']==1
    assert source_metrics(hits,[dict(document_id='A',section='Exact')],5)['source_hit@1']==0
    assert source_metrics(hits,[dict(document_id='B',file_name='renamed.pdf')],5)['document_hit@1']==0


def test_control_and_batch_default_all_with_selected_file(ragweb):
    c,rid,llm,_=ragweb;doc=c.app.state.views.files(rid)[0]['document_id']
    q=c.post('/api/rag/questions',json={'question':'Q','expected_answer':''}).json()
    r=compare(c,rid,question_id=q['question_id'],selected_document_id=doc)
    assert r['rag_scope']=='ALL_DOCUMENTS' and r['full_document_id']==doc
    key=c.post(f'/api/runs/{rid}/rag/batch',json={'selected_document_id':doc}).json()['batch_id']
    c.app.state.rag.futures[key].result(timeout=30)
    result=c.app.state.rag.get(c.app.state.rag.get(key,'rag_batches')['comparison_ids'][0])
    assert result['rag_scope']=='ALL_DOCUMENTS' and result['full_document_status']=='SUCCESS'


def test_foreign_chunks_in_collection_excluded_before_top_k(ragweb):
    from uuid import uuid4
    from qdrant_client import models
    from conftest import FakeEmbedding
    c,rid,_,_=ragweb
    index=c.app.state.views.snapshot(rid)['indexes']['structure']
    vector=FakeEmbedding().encode(['Question'])[0]
    vectors=LocalVectorStore(c.app.state.runs.config.qdrant_path)
    try:
        vectors.client.upsert(index['collection'],points=[models.PointStruct(id=str(uuid4()),vector=vector.tolist(),payload={
            'chunk_id':'foreign-not-active','document_id':'foreign-corpus-doc','strategy':'structure'})])
    finally:vectors.close()
    r=compare(c,rid,candidate_top_n=1,max_context_sources=1)
    assert r['status']=='COMPLETED' and r['retrieved_count']==1
    assert r['retrieved_chunk_ids_json'][0] in index['chunk_ids']


def test_candidate_parameters_persist_and_legacy_history(ragweb):
    c,rid,llm,_=ragweb
    r=compare(c,rid,candidate_top_n=20,max_context_sources=1,context_token_budget=3000)
    assert r['status']=='COMPLETED'
    assert r['candidates_retrieved']>r['contexts_used']==1
    assert r['context_tokens']<=3000 and r['context_candidate_metadata_json']
    assert r['final_source_ids']==[s['source_id'] for s in r['sources']]
    assert all(set(s['anchor_chunk_ids'])<=set(r['retrieved_chunk_ids_json']) for s in r['sources'])
    assert len(llm.calls)==3 and llm.calls[0].context is None
    assert llm.calls[-1].context==r['context_text']
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    assert 'Contexts built:' in html and 'anchor_chunk_ids' in html
    assert c.post(f'/api/runs/{rid}/rag/compare',json={'question':'q','candidate_top_n':0}).status_code==422
    assert c.post(f'/api/runs/{rid}/rag/compare',json={'question':'q','max_context_sources':21}).status_code==422
    assert c.post(f'/api/runs/{rid}/rag/compare',json={'question':'q','top_k':2,'max_context_sources':3}).status_code==400
    service=c.app.state.rag
    old=service.get(r['comparison_run_id'],public=False)
    for key in ('candidate_top_n','max_context_sources','candidate_policy_version','context_document_ids'):
        old.pop(key)
    old['top_k']=3;service.put(old)
    legacy=service.get(old['comparison_run_id'])
    assert legacy['candidate_top_n']==legacy['max_context_sources']==3
    assert legacy['candidate_policy_version']=='legacy-raw-top-k'
