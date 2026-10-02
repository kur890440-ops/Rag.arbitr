import json
from urllib.error import HTTPError
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from conftest import FakeEmbedding, make_scanned_pdf
from test_recognition import FakeRuntime
from test_web import factory, upload_pdf, wait_run, HEADERS
from rag_arbiter.web.app import create_app
from rag_arbiter.llm import LLMConfig, LLMRequest, LLMResult, MiniMaxLLMProvider
from rag_arbiter.application.rag import RAGContextBuilder, source_metrics


def test_provider_fair_stateless_and_secret(monkeypatch, caplog):
    monkeypatch.setenv('MINIMAX_API_KEY','synthetic-secret')
    requests=[]
    def send(url,headers,payload,timeout):
        requests.append(payload)
        assert url=='https://api.minimax.io/v1/chat/completions'
        return {'choices':[{'message':{'content':'<think>private</think>answer synthetic-secret'},'finish_reason':'stop'}], 'usage':{'total_tokens':15}}
    p=MiniMaxLLMProvider(LLMConfig(),send)
    a=p.generate(LLMRequest(question='Question'))
    b=p.generate(LLMRequest(question='Question',context='[S1] chunk'))
    assert a.status==b.status=='SUCCESS' and a.usage['total_tokens']==15
    assert requests[0]['messages'][1]['content']=='Question'
    assert '[S1] chunk' in requests[1]['messages'][1]['content']
    assert requests[0]['messages'][0]==requests[1]['messages'][0]
    assert len(requests[0]['messages'])==len(requests[1]['messages'])==2
    assert {k:v for k,v in requests[0].items() if k!='messages'}=={k:v for k,v in requests[1].items() if k!='messages'}
    assert 'synthetic-secret' not in a.model_dump_json()+caplog.text and 'private' not in a.text


@pytest.mark.parametrize('response,expected',[(None,'INVALID_RESPONSE'),({'choices':[]},'INVALID_RESPONSE'),({'base_resp':{'status_code':1004}},'AUTH_ERROR'),({'base_resp':{'status_code':1002}},'RATE_LIMIT'),({'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]},'OUTPUT_LIMIT')])
def test_provider_bad_responses(monkeypatch,response,expected):
    monkeypatch.setenv('MINIMAX_API_KEY','synthetic-secret')
    result=MiniMaxLLMProvider(LLMConfig(),lambda *a:response).generate(LLMRequest(question='q'))
    assert result.error['code']==expected


@pytest.mark.parametrize('exc,expected',[(TimeoutError(),'TIMEOUT'),(HTTPError('url',401,'secret',{},None),'AUTH_ERROR'),(HTTPError('url',402,'secret',{},None),'PAYMENT_REQUIRED'),(HTTPError('url',429,'secret',{},None),'RATE_LIMIT'),(HTTPError('url',503,'secret',{},None),'SERVER_ERROR')])
def test_provider_transport_errors(monkeypatch,exc,expected):
    monkeypatch.setenv('MINIMAX_API_KEY','synthetic-secret')
    def fail(*args):raise exc
    assert MiniMaxLLMProvider(LLMConfig(),fail).generate(LLMRequest(question='q')).error['code']==expected


def test_no_key_does_not_call_network(monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY',raising=False)
    def forbidden(*args):raise AssertionError('Network used')
    assert MiniMaxLLMProvider(LLMConfig(),forbidden).generate(LLMRequest(question='q')).status=='NOT_CONFIGURED'


def test_local_config_key_is_used_but_never_serialized(monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY',raising=False)
    cfg=LLMConfig(api_key='synthetic-local-secret')
    assert cfg.resolved_key()=='synthetic-local-secret'
    assert 'synthetic-local-secret' not in cfg.model_dump_json()+repr(cfg)
    legacy=LLMConfig(api_key_env='synthetic-local-secret')
    assert legacy.api_key_env=='MINIMAX_API_KEY'
    assert 'synthetic-local-secret' not in legacy.model_dump_json()
    def send(url,headers,payload,timeout):
        assert headers['Authorization']=='Bearer synthetic-local-secret'
        return {'choices':[{'message':{'content':'answer'},'finish_reason':'stop'}]}
    assert MiniMaxLLMProvider(cfg,send).generate(LLMRequest(question='q')).status=='SUCCESS'
    monkeypatch.setenv('MINIMAX_API_KEY','environment-secret')
    assert cfg.resolved_key()=='environment-secret'


def test_empty_content_at_output_limit(monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY','synthetic-secret')
    p=MiniMaxLLMProvider(LLMConfig(),lambda *a:{'choices':[{'message':{'content':None},'finish_reason':'length'}]})
    assert p.generate(LLMRequest(question='q')).status=='TRUNCATED'


def test_context_budget_duplicates_provenance():
    h=dict(chunk_id='a',text='content',file_name='file.pdf',document_id='doc',page_start=2,page_end=3,section='Section',score=.8)
    hits=[h,h,{**h,'chunk_id':'b'},{**h,'chunk_id':'c','text':'different'}]
    context=RAGContextBuilder().build(hits,10000)
    assert context['used_chunk_ids']==['a','c'] and context['sources'][0]['page_start']==2
    assert '[S1]' in context['text'] and '[S2]' in context['text']
    assert context['token_count']==len(context['text'].encode())
    assert not RAGContextBuilder().build(hits,1)['sources']
    assert source_metrics(hits,[{'file_name':'file.pdf','page_start':3}],5)['source_hit@1']==1
    assert source_metrics(hits,[],5)=={}
    assert source_metrics(hits,[{'file_name':'missing'}],1)['source_hit@3'] is None


class FakeLLM:
    def __init__(self,cfg):self.cfg=cfg;self.calls=[];self.fail=None
    def generate(self,request):
        self.calls.append(request)
        if self.fail==('rag' if request.context else 'no_rag'):
            return LLMResult(model=self.cfg.model,error={'code':'TIMEOUT'})
        return LLMResult(model=self.cfg.model,text='Answer [S1]' if request.context else 'No context',status='SUCCESS',duration_ms=2)


@pytest.fixture
def ragweb(config):
    runtime=FakeRuntime();llm=FakeLLM(config.llm)
    with TestClient(create_app(config,factory(runtime),lambda cfg:llm),base_url='http://127.0.0.1',headers=HEADERS) as client:
        uid=upload_pdf(client,config)
        other=config.corpus_path/'other.pdf';make_scanned_pdf(other,2)
        uid2=client.post('/api/uploads',files={'files':('other.pdf',other.read_bytes(),'application/pdf')}).json()['uploads'][0]['upload_id']
        rid=client.post('/api/runs',json={'upload_ids':[uid,uid2]}).json()['run_id']
        assert wait_run(client,rid)['status']=='COMPLETED'
        yield client,rid,llm,runtime


def compare(client,rid,**kwargs):
    response=client.post(f'/api/runs/{rid}/rag/compare',json={'question':'Question',**kwargs})
    assert response.status_code==202,response.text
    key=response.json()['comparison_run_id'];client.app.state.rag.futures[key].result(timeout=20)
    return client.get('/api/rag/comparisons/'+key).json()


def test_real_qdrant_both_strategies_scope_and_no_reindex(ragweb):
    c,rid,llm,runtime=ragweb
    docs=c.app.state.views.files(rid);calls=runtime.calls
    original=FakeEmbedding.encode;queries=[]
    def encode(self,texts):queries.extend(texts);return original(self,texts)
    with patch.object(FakeEmbedding,'encode',encode):
        for strategy in ('fixed','structure'):
            for doc in (None,docs[0]['document_id']):
                r=compare(c,rid,strategy=strategy,document_id=doc,rag_scope="SELECTED_DOCUMENT" if doc else "ALL_DOCUMENTS",top_k=2)
                assert r['status']=='COMPLETED',r['error_json']
                assert 0<r['used_count']<=2 and r['retrieved_count']<=20
                assert r['max_context_sources']==2 and r['candidate_top_n']==20
                assert all(h['document_id']==doc for h in r['sources']) if doc else True
                assert set(r['used_chunk_ids_json'])<=set(r['retrieved_chunk_ids_json'])
                assert 'config_snapshot' not in r
                assert c.get('/ui/rag/comparisons/'+r['comparison_run_id']).status_code==200
                page=r['sources'][0]['source_pages'][0]['recognition_id']
                assert c.get(f'/ui/runs/{rid}/pages/{page}').status_code==200
    assert queries==['Question']*4 and runtime.calls==calls
    assert sum(call.context is None for call in llm.calls)==4
    assert sum(call.context_type=='full_document' for call in llm.calls)==4
    assert sum(call.context is not None and call.context_type=='rag' for call in llm.calls)==4
    assert c.post(f'/api/runs/{rid}/rag/compare',json={'question':'q','top_k':11}).status_code==422
    assert c.post(f'/api/runs/{rid}/rag/compare',json={'question':'q','chunking_run_id':'wrong'}).status_code==400


@pytest.mark.parametrize('branch',['rag','no_rag'])
def test_partial_preserves_other_branch(ragweb,branch):
    c,rid,llm,_=ragweb;llm.fail=branch
    r=compare(c,rid)
    assert r['status']=='PARTIAL' and r['no_rag_answer' if branch=='rag' else 'rag_answer']


def test_questions_limits_edit_delete_bulk_and_persistence(ragweb):
    c,rid,llm,_=ragweb
    for n in range(10):
        response=c.post('/api/rag/questions',json={'question':f'Q{n}','expected_answer':'expected','expected_sources':[{'file_name':'sample.pdf'}]})
        assert response.status_code==201
    assert c.post('/api/rag/questions',json={'question':'overflow','expected_answer':''}).status_code==400
    rows=c.get('/api/rag/questions').json();key=rows[0]['question_id']
    assert c.put('/api/rag/questions/'+key,json={'question':'edited','expected_answer':'saved','expected_sources':[]}).status_code==200
    assert compare(c,rid,question_id=key)['expected_answer']=='saved'
    assert c.delete('/api/rag/questions/'+key).status_code==200
    assert len(c.get('/api/rag/questions').json())==9
    assert '<details' not in c.get('/ui/rag/questions').text
    b=c.post(f'/api/runs/{rid}/rag/batch',json={}).json()['batch_id'];c.app.state.rag.futures[b].result(timeout=40)
    batch=c.get('/api/rag/batches/'+b).json()
    assert batch['completed']==9 and batch['status']=='COMPLETED'
    assert c.get('/ui/rag/batches/'+b).status_code==200
    with c.app.state.runs.db() as db:
        assert len(db.all('rag_comparison_runs'))==10
        assert db.get('rag_evaluation_questions',key)['active'] is False
    from rag_arbiter.application.rag import RAGComparisonService
    reopened=RAGComparisonService(c.app.state.runs)
    assert len(reopened.questions.list())==9
    assert reopened.get(b,'rag_batches')['completed']==9


def test_key_not_in_http_responses_or_persisted_results(ragweb,monkeypatch):
    c,rid,llm,_=ragweb
    monkeypatch.setenv('MINIMAX_API_KEY','synthetic-secret')
    result=compare(c,rid)
    for path in ['/', '/api/system/status','/ui/system?details=true',f'/ui/runs/{rid}/workspace','/api/rag/comparisons/'+result['comparison_run_id']]:
        response=c.get(path)
        assert response.status_code==200
        assert 'synthetic-secret' not in response.text
    with c.app.state.runs.db() as db:
        assert 'synthetic-secret' not in json.dumps(db.all('rag_comparison_runs'))


def test_comparison_restores_local_key_only_in_memory(ragweb,monkeypatch):
    from pydantic import SecretStr
    monkeypatch.delenv('MINIMAX_API_KEY',raising=False)
    c,rid,llm,_=ragweb
    c.app.state.runs.config.llm.api_key=SecretStr('local-service-secret')
    def provider(cfg):
        assert cfg.resolved_key()=='local-service-secret'
        return llm
    c.app.state.rag.llm_factory=provider
    result=compare(c,rid)
    assert result['status']=='COMPLETED'
    with c.app.state.runs.db() as db:
        assert 'local-service-secret' not in json.dumps(db.all('rag_comparison_runs'))


def test_missing_index_empty_retrieval_and_search_failure(ragweb):
    c,rid,llm,_=ragweb;svc=c.app.state.rag
    record=svc.prepare(rid,question='q');record['index_snapshot']=None;svc.put(record)
    r=svc.execute(record['comparison_run_id']);assert r['rag_result']['status']=='NO_ACTIVE_INDEX' and r['status']=='PARTIAL'
    with patch('rag_arbiter.application.rag.SemanticRetriever.retrieve_vector',return_value=[]):
        assert compare(c,rid)['rag_result']['status']=='EMPTY_RETRIEVAL'
    with patch('rag_arbiter.application.rag.SemanticRetriever.retrieve_vector',side_effect=RuntimeError('private')):
        r=compare(c,rid);assert r['status']=='PARTIAL' and r['rag_result']['status']=='RETRIEVAL_ERROR'
        assert 'private' not in json.dumps(r)
