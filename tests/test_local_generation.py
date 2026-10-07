"""Day28 transport, shared retrieval/context and provider-isolated repair acceptance."""
import json
from unittest.mock import patch
import pytest
from rag_arbiter.llm import LLMRequest, LLMResult, LocalLLMConfig, MiniMaxLLMProvider, LLMConfig
from rag_arbiter.local_llm import LocalLLMProvider
from rag_arbiter.application.rag import RAGContextBuilder
from rag_arbiter.application.context_selection import ContextSelector
from conftest import FakeEmbedding
from test_rag import ragweb, compare, FakeLLM


def transport_fixture(calls, fail=None):
    def send(path, body=None, timeout=None):
        calls.append((path, body, timeout))
        if path == '/api/tags':
            return {'models': [{'name': 'qwen3:4b-q4_K_M', 'digest': 'pinned'}]}, ''
        if path == '/api/show':
            return {'capabilities': ['completion'], 'model_info': {'qwen3.context_length': 40960}}, ''
        if path == '/api/ps':
            return {'models': [{'name': 'ocr'}]}, ''
        if path == '/api/generate':
            return {}, ''
        if fail:
            raise fail
        return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"answer":""}'},
                'prompt_eval_count': 99, 'eval_count': 5}, ''
    return send


def test_local_request_and_shared_prompt(monkeypatch):
    calls=[]; cloud=[]
    request=LLMRequest(question='Q', context='[S1] source', context_type='grounded_rag')
    cfg=LocalLLMConfig(enabled=True)
    p=LocalLLMProvider(cfg, transport_fixture(calls), ocr_model='ocr')
    result=p.generate(request)
    assert result.status=='SUCCESS' and result.provider=='local'
    path, body, timeout=calls[-1]
    assert path=='/api/chat' and timeout==cfg.timeout
    assert body['model']==cfg.model and body['stream'] is False and body['think'] is False
    assert body['keep_alive']==0 and body['options']['num_ctx']==cfg.context_window
    assert body['format']==request.output_schema()
    assert calls[-2][1]['model']=='ocr' and calls[-2][1]['keep_alive']==0
    def send(*args):
        cloud.append(args[2]);return {'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
    MiniMaxLLMProvider(LLMConfig(api_key='synthetic'),send).generate(request)
    assert cloud[0]['messages']==body['messages']


@pytest.mark.parametrize('fault',[TimeoutError(), OSError('offline'), ValueError('bad JSON')])
def test_local_error_never_falls_back(fault):
    calls=[]
    with patch.object(MiniMaxLLMProvider,'generate',side_effect=AssertionError('fallback')):
        r=LocalLLMProvider(LocalLLMConfig(enabled=True),transport_fixture(calls,fault)).generate(LLMRequest(question='Q'))
    assert r.status=='LOCAL_GENERATION_UNAVAILABLE' and r.request_count==1


@pytest.mark.parametrize('kind',['disabled','missing','vision','cloud','window'])
def test_local_preflight_rejects_without_generation(kind):
    calls=[]
    base=transport_fixture(calls)
    def send(path,body=None,timeout=None):
        data,raw=base(path,body,timeout)
        if kind=='missing' and path=='/api/tags':data['models']=[]
        if path=='/api/show':
            if kind=='vision':data['capabilities'].append('vision')
            if kind=='cloud':data['remote_host']='remote'
            if kind=='window':data['model_info']['qwen3.context_length']=4096
        return data,raw
    r=LocalLLMProvider(LocalLLMConfig(enabled=kind!='disabled'),send).generate(LLMRequest(question='Q'))
    assert r.error['code']=='LOCAL_GENERATION_UNAVAILABLE'
    assert not any(c[0]=='/api/chat' for c in calls)


def test_local_full_prompt_budget():
    calls=[]
    r=LocalLLMProvider(LocalLLMConfig(enabled=True),transport_fixture(calls)).generate(LLMRequest(question='Q'*20000))
    assert r.error['code']=='LOCAL_CONTEXT_LIMIT' and r.request_count==0


@pytest.mark.parametrize('mode',['local','compare'])
def test_shared_snapshot_grounding_and_local_repair(ragweb,monkeypatch,mode):
    c,rid,cloud,_=ragweb
    class Local(FakeLLM):
        def __init__(self,cfg,**kwargs):super().__init__(cfg)
        def generate(self,request):
            if not self.calls:
                self.calls.append(request)
                return LLMResult(provider='local',model='text-qwen',status='SUCCESS',text='bad json',request_count=1)
            out=super().generate(request)
            return out.model_copy(update={'provider':'local','model':'text-qwen','request_count':1})
    local=Local(c.app.state.runs.config.llm.local)
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',lambda *a,**kw:local)
    queries=[];original=FakeEmbedding.encode;selections=[]
    select=ContextSelector.select
    def encode(self,texts):queries.extend(texts);return original(self,texts)
    def selection(self,*args,**kw):selections.append(1);return select(self,*args,**kw)
    with patch.object(FakeEmbedding,'encode',encode),patch.object(ContextSelector,'select',selection):
        r=compare(c,rid,generation_mode=mode)
    assert r['status']=='COMPLETED',r['error_json']
    assert queries==['Question'] and len(selections)==1
    assert len(local.calls)==2 and 'REPAIR:' in local.calls[1].context
    assert len(cloud.calls)==(1 if mode=='compare' else 0)
    assert r['generation_runs'][0]['repair_count']==1
    assert r['generation_runs'][0]['grounding_status']=='GROUNDED'
    assert r['generation_runs'][0]['citations_json']
    assert r['comparison_context_reduced']
    if mode=='compare':
        a,b=r['generation_runs']
        assert a['context_snapshot_id']==b['context_snapshot_id']==r['context_snapshot_id']
        assert a['context_ids']==b['context_ids'] and a['source_ids']==b['source_ids']
        assert local.calls[0].messages()==cloud.calls[0].messages()
        assert a['context_budget']==b['context_budget'] and a['prompt_version']==b['prompt_version']
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id'])
    assert html.status_code==200 and 'Provider:' in html.text and 'text-qwen' in html.text
    assert ('CLOUD' in html.text)==(mode=='compare')


def test_compare_local_failure_preserves_cloud(ragweb,monkeypatch):
    c,rid,cloud,_=ragweb
    class Broken:
        def generate(self,request):return LLMResult(provider='local',model='missing',status='LOCAL_GENERATION_UNAVAILABLE',error={'code':'LOCAL_GENERATION_UNAVAILABLE'})
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',lambda *a,**kw:Broken())
    r=compare(c,rid,generation_mode='compare')
    assert r['status']=='PARTIAL' and len(cloud.calls)==1
    assert r['generation_runs'][0]['error']['code']=='LOCAL_GENERATION_UNAVAILABLE'
    assert r['generation_runs'][1]['grounding_status']=='GROUNDED'
    assert r['generation_runs'][0]['claims_count']==0

def test_repair_feedback_fits_shared_local_reserve():
    from test_grounding import Store, Reranker
    from rag_arbiter.application.grounding import grounded_generation, CitationBuilder
    calls=[]
    # A malformed response exercises the same bounded repair path for either adapter.
    def generate(context,kind):
        calls.append(context)
        return LLMResult(model='test',status='SUCCESS',text='invalid JSON',request_count=1).model_dump()
    record={'sources':[]}
    grounded_generation(record,'SOURCE',generate,CitationBuilder(Store()),Reranker(),.5,
                        repair_context_limit=2054)
    assert len(calls)==2 and len(calls[1].encode('utf-8'))<=2054
    assert record['repair_used'] and record['structured_output_valid'] is False

@pytest.mark.parametrize('limit',[2054,4102])
def test_valid_contract_repair_feedback_is_bounded(limit):
    from test_grounding import Store, Reranker, contract
    from rag_arbiter.application.grounding import grounded_generation, CitationBuilder
    store=Store()
    source=dict(reference='S1',document_id='d',chunk_id='c',anchor_chunk_ids=['c'],source_block_ids=['b'],
                file_name='file.pdf',text=store.rows['document_blocks']['b']['text'],score=.4)
    calls=[]
    def generate(context,kind):
        calls.append(context)
        answer=contract('Payment is 999 rubles. '+('Unconfirmed words. '*40)) if len(calls)==1 else contract()
        return LLMResult(model='test',status='SUCCESS',text=answer.model_dump_json(),request_count=1).model_dump()
    record={'sources':[source]}
    result=grounded_generation(record,'SOURCE',generate,CitationBuilder(store),Reranker(),.5,repair_context_limit=limit)
    assert len(calls)==2 and len(calls[1].encode('utf-8'))<=limit
    assert result['status']=='SUCCESS' and record['grounding_status']=='GROUNDED'
