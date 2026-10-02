import json
import sqlite3
from unittest.mock import patch
from test_rag import ragweb, compare
from rag_arbiter.application.full_document import FullDocumentContextBuilder
from rag_arbiter.llm import LLMConfig, LLMRequest, LLMResult, MiniMaxLLMProvider
from rag_arbiter.storage import MetadataStore


def chosen(client,rid):
    return max(client.app.state.views.files(rid),key=lambda d:d['pages'])['document_id']


def test_canonical_order_without_search_or_recognition(ragweb):
    c,rid,llm,runtime=ragweb;doc=chosen(c,rid);runs=c.app.state.runs
    entry=next(d for d in c.app.state.views.snapshot(rid)['corpus']['documents'] if d['document_id']==doc)
    with runs.db() as db:
        saved=db.get('normalized_documents',entry['recognition_version'])
        saved['pages'].reverse();saved['blocks'].reverse()
        for p in saved['pages']:p['ocr_text']='RAW_ARTIFACT_MUST_NOT_BE_USED'
        db.put('normalized_documents',entry['recognition_version'],saved)
    with patch.object(runs,'pipeline_factory',side_effect=AssertionError('Pipeline forbidden')):
        result=FullDocumentContextBuilder(runs).build(rid,doc,100000,'question',8192)
    assert result['status']=='READY' and result['included_pages']==[1,2]
    assert result['text'].index('[PAGE 1]')<result['text'].index('[PAGE 2]')
    ordered=sorted(saved['blocks'],key=lambda b:(b['page_number'],b['reading_order'],b['block_id']))
    expected=saved['file_name']+'\n'+'\n\n'.join(f'[PAGE {n}]\n'+'\n'.join(b['text'] for b in ordered if b['page_number']==n) for n in (1,2))
    assert result['text']==expected and 'RAW_ARTIFACT' not in result['text']
    assert result['token_count']==len(expected.encode('utf-8'))
    assert result['total_reserved_tokens']==result['input_tokens']+8192
    edge=FullDocumentContextBuilder(runs).build(rid,doc,result['total_reserved_tokens']-1,'question',8192)
    assert edge['status']=='CONTEXT_TOO_LARGE' and edge['text']=='' and edge['included_pages']==[]
    assert edge['token_count']==result['token_count']


def test_three_requests_and_prompt_fairness(monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY',raising=False)
    captured=[]
    def send(url,headers,payload,timeout):
        captured.append(payload)
        return {'choices':[{'message':{'content':'Answer'},'finish_reason':'stop'}]}
    p=MiniMaxLLMProvider(LLMConfig(api_key='synthetic-secret'),send)
    for context,kind in [(None,'rag'),('[PAGE 1] whole normalized document','full_document'),('[S1] selected chunk','rag')]:
        assert p.generate(LLMRequest(question='same question',context=context,context_type=kind)).request_count==1
    assert captured[0]['messages'][1]['content']=='same question'
    assert 'DOCUMENT:' in captured[1]['messages'][1]['content'] and '[PAGE 1]' in captured[1]['messages'][1]['content']
    assert 'whole normalized document' not in captured[2]['messages'][1]['content']
    assert all(len(p['messages'])==2 and p['messages'][0]==captured[0]['messages'][0] for p in captured)
    assert all({k:v for k,v in p.items() if k!='messages'}=={k:v for k,v in captured[0].items() if k!='messages'} for p in captured)


def test_three_way_persistence_and_full_panel(ragweb):
    c,rid,llm,runtime=ragweb;doc=chosen(c,rid);before=runtime.calls
    r=compare(c,rid,document_id=doc,top_k=1)
    assert r['status']=='COMPLETED' and r['full_document_status']=='SUCCESS'
    assert r['full_document_pages']==2 and r['full_document_answer']
    assert r['generation_call_count']==3 and runtime.calls==before
    assert len(llm.calls)==3 and llm.calls[0].context is None
    assert llm.calls[1].context_type=='full_document' and '[PAGE 2]' in llm.calls[1].context
    assert llm.calls[2].context==r['context_text'] and llm.calls[2].context_type=='rag'
    assert r['used_count']==1 and r['max_context_sources']==1
    assert r['candidate_top_n']==20 and r['retrieved_count']>1
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    for label in ('БЕЗ КОНТЕКСТА','БЕЗ RAG · ПОЛНЫЙ ПРОСМОТР','С RAG',r['full_document_context']['file_name']):assert label in html
    assert 'Показать документ' in html
    saved=c.app.state.rag.get(r['comparison_run_id'])
    assert saved['full_document_context']==r['full_document_context']


def test_corpus_full_and_oversize_continues(ragweb):
    c,rid,llm,_=ragweb
    queued=c.app.state.rag.prepare(rid,question='q')
    assert queued['full_document_status']=='NOT_RUN'
    r=compare(c,rid)
    assert r['status']=='COMPLETED' and r['generation_call_count']==3
    assert r['full_document_status']=='SUCCESS'
    assert r['full_document_context']['documents_included']==2
    assert '[DOCUMENT D2]' in llm.calls[1].context
    c.app.state.runs.config.llm.full_document_context_budget=1024
    r=compare(c,rid,document_id=chosen(c,rid))
    assert r['full_document_status']=='CONTEXT_TOO_LARGE' and r['status']=='PARTIAL'
    assert r['generation_call_count']==2 and r['no_rag_answer'] and r['rag_answer']
    assert r['full_document_context']['text']==''
    assert 'CONTEXT_TOO_LARGE' in c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text


def test_full_api_and_builder_errors_are_independent(ragweb):
    c,rid,llm,_=ragweb
    original=llm.generate
    def fail(request):
        if request.context_type=='full_document':return LLMResult(model=llm.cfg.model,error={'code':'TIMEOUT'})
        return original(request)
    llm.generate=fail
    r=compare(c,rid,document_id=chosen(c,rid))
    assert r['full_document_status']=='API_ERROR' and r['status']=='PARTIAL'
    assert r['no_rag_result']['status']==r['rag_result']['status']=='SUCCESS'
    with patch.object(FullDocumentContextBuilder,'build',side_effect=ValueError('private')):
        r=compare(c,rid,document_id=chosen(c,rid))
        assert r['full_document_status']=='BUILD_ERROR' and r['no_rag_answer'] and r['rag_answer']


def test_batch_document_three_branches(ragweb):
    c,rid,llm,_=ragweb
    for n in range(2):c.post('/api/rag/questions',json={'question':f'Question {n}','expected_answer':'expectation'})
    b=c.post(f'/api/runs/{rid}/rag/batch',json={'document_id':chosen(c,rid)}).json()['batch_id']
    c.app.state.rag.futures[b].result(timeout=30)
    batch=c.app.state.rag.get(b,'rag_batches')
    assert batch['completed']==2 and len(llm.calls)==6
    for key in batch['comparison_ids']:assert c.app.state.rag.get(key)['full_document_status']=='SUCCESS'


def test_migration_preserves_old_answers(tmp_path):
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=6')
        db.execute('CREATE TABLE rag_comparison_runs(id TEXT PRIMARY KEY,data TEXT)')
        db.execute('INSERT INTO rag_comparison_runs VALUES (?,?)',('old',json.dumps({'no_rag_answer':'old answer','status':'COMPLETED'})))
    db=MetadataStore(path)
    try:
        r=db.get('rag_comparison_runs','old')
        assert r['no_rag_answer']=='old answer' and r['status']=='COMPLETED'
        assert r['full_document_status']=='NOT_RUN'
        assert db.db.execute('PRAGMA user_version').fetchone()[0]==8
    finally:db.close()


def test_full_corpus_exact_coverage_budget_no_retrieval(ragweb):
    c,rid,llm,runtime=ragweb;runs=c.app.state.runs
    builder=FullDocumentContextBuilder(runs)
    with patch.object(runs,'pipeline_factory',side_effect=AssertionError('No pipeline')):
        full=builder.build(rid,None,100000,'q',8192)
        assert full['status']=='READY'
        assert full['documents_included']==2 and full['pages_included']==3
        assert [d['document_id'] for d in full['documents']]==sorted(d['document_id'] for d in full['documents'])
        for d in full['documents']:
            single=builder.build(rid,d['document_id'],100000,'q',8192)
            assert '[DOCUMENT '+d['reference']+']\n'+single['text'] in full['text']
        assert full==builder.build(rid,None,100000,'q',8192)
        edge=builder.build(rid,None,full['total_reserved_tokens']-1,'q',8192)
        assert edge['status']=='CONTEXT_TOO_LARGE' and not edge['text']
        assert edge['documents_included']==edge['pages_included']==0
    runs.config.llm.full_document_context_budget=1024
    r=compare(c,rid)
    assert r['full_document_status']=='CONTEXT_TOO_LARGE' and r['status']=='PARTIAL'
    assert len(llm.calls)==2 and all(call.context_type!='full_document' for call in llm.calls)


def test_full_corpus_missing_document_not_silently_skipped(ragweb):
    c,rid,llm,_=ragweb;runs=c.app.state.runs
    entry=c.app.state.views.snapshot(rid)['corpus']['documents'][0]
    with runs.db() as db:
        doc=db.get('normalized_documents',entry['recognition_version'])
        doc['status']='PARTIAL';db.put('normalized_documents',entry['recognition_version'],doc)
    r=compare(c,rid)
    assert r['full_document_status']=='NORMALIZED_DOCUMENT_INCOMPLETE'
    assert not r['full_document_context']['text'] and r['generation_call_count']==2
    assert c.post(f'/api/runs/{rid}/analysis',json={'question':'q'}).status_code==404
    assert c.get('/static/corpus_analysis.js').status_code==404


def test_run_all_whole_corpus_three_requests_each(ragweb):
    c,rid,llm,_=ragweb
    for n in range(2):
        assert c.post('/api/rag/questions',json={'question':f'Question {n}','expected_answer':''}).status_code==201
    b=c.post(f'/api/runs/{rid}/rag/batch',json={}).json()['batch_id']
    c.app.state.rag.futures[b].result(timeout=30)
    assert len(llm.calls)==6
    for call in llm.calls[1::3]:assert call.context_type=='full_document' and '[DOCUMENT D2]' in call.context
