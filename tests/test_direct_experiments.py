import copy
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from test_rag import ragweb
from rag_arbiter.local_llm import LocalLLMProvider
from rag_arbiter.application.direct_experiments import comparison, DirectExperimentService

@pytest.fixture
def direct_lab(ragweb, monkeypatch):
    client, rid, cloud, _ = ragweb
    calls=[]
    def forbidden(*args, **kwargs):
        raise AssertionError('Direct experiment must not access the RAG pipeline')
    monkeypatch.setattr(client.app.state.rag, 'prepare', forbidden)
    monkeypatch.setattr(client.app.state.rag, 'execute', forbidden)
    monkeypatch.setattr(client.app.state.rag, 'reranker_factory', forbidden)
    monkeypatch.setattr('rag_arbiter.application.context_selection.ContextSelector.select', forbidden)
    monkeypatch.setattr('rag_arbiter.vectorstore.LocalVectorStore.__init__', forbidden)
    def transport(path, body=None, timeout=None):
        calls.append((path,copy.deepcopy(body)))
        if path=='/api/tags':return {'models':[{'name':'test-local:Q5','digest':'fixed'}]},''
        if path=='/api/show':return {'capabilities':['completion'],'model_info':{'test.context_length':32768},'details':{'quantization_level':'Q5_K_M'}},''
        if path=='/api/ps':return {'models':[]},''
        if path=='/api/chat':return {'done':True,'done_reason':'stop','message':{'content':'Plain answer <script>unsafe</script>'},'prompt_eval_count':40,'eval_count':20,'eval_duration':2_000_000_000,'load_duration':100_000_000,'prompt_eval_duration':200_000_000},''
        return {},''
    def provider(cfg, **kwargs):return LocalLLMProvider(cfg,transport=transport,**kwargs)
    monkeypatch.setattr('rag_arbiter.application.direct_experiments.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.web.optimization.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.application.direct_experiments.Resources',lambda:nullcontext(SimpleNamespace(summary=lambda:{})))
    body={'question':'Explain recursion','options':{'model':'test-local:Q5','temperature':.13,'context_window':4096,'max_output_tokens':950,'seed':73,'prompt_version':'day29-baseline'}}
    return client,rid,body,calls,cloud

def launch(client,body):
    response=client.post('/api/local-llm-experiments',json=body)
    assert response.status_code==200,response.text
    client.app.state.runs.executor.submit(lambda:None).result(timeout=30)
    return client.get('/api/local-llm-experiments/'+response.json()['id']).json()

def test_direct_options_storage_no_rag_and_metrics(direct_lab):
    c,rid,body,calls,cloud=direct_lab
    before=c.app.state.runs.config.model_dump(mode='json')
    row=launch(c,body)
    assert row['status']=='SUCCESS' and row['finished']
    assert row['profile']['context_window']==4096 # no RAG repair reserve
    assert row['quantization']=='Q5_K_M'
    assert row['prompt_tokens']==40 and row['output_tokens']==20 and row['tokens_per_second']==10
    assert row['generation_duration']>=0 and row['total_duration']>=row['generation_duration']
    assert row['ram_peak'] is None and row['vram_peak'] is None
    chats=[p for path,p in calls if path=='/api/chat']
    assert len(chats)==1 and 'format' not in chats[0]
    assert chats[0]['messages']==[{'role':'system','content':row['system_prompt']},{'role':'user','content':body['question']}]
    assert chats[0]==row['exact_input'] and row['transport_attempted']
    assert chats[0]['options']=={'temperature':.13,'num_ctx':4096,'num_predict':950,'seed':73}
    body['options'].update(prompt_version='day29-optimized',temperature=0,max_output_tokens=800)
    second=launch(c,body)
    assert row['id']!=second['id'] and row['system_prompt']!=second['system_prompt']
    assert c.app.state.runs.config.model_dump(mode='json')==before and not cloud.calls
    assert len(DirectExperimentService(c.app.state.runs).history())==2
    assert row['id'] not in c.get(f'/ui/runs/{rid}/rag/history').text
    assert c.get('/chat').status_code==200

def test_columns_baseline_question_and_no_rag_fields(direct_lab):
    c,_,body,_,_=direct_lab
    a=launch(c,body);b=launch(c,body)
    html=c.get('/ui/local-llm-experiments',params={'ids':a['id']+','+b['id'],'baseline':a['id']}).text
    assert html.count('data-direct-run=')==2 and 'FAIR COMPARISON' in html
    assert 'direct-baseline' in html and 'data-direct-delta="generation_duration"' in html
    assert 'unsafe</script>' not in html
    for term in ('Grounding','Citations','Claims','Repairs','data-experiment-context','VRAM peak'):
        assert term not in html
    body['question']='Different question';d=launch(c,body)
    html=c.get('/ui/local-llm-experiments',params={'ids':d['id'],'baseline':a['id']}).text
    assert 'DIFFERENT QUESTION' in html and 'FAIR COMPARISON' not in html and 'data-direct-delta=' not in html
    form=c.get('/ui/llm-optimization').text
    for term in ('processing_run_id','reuse_run_id','day29-legal','grounding','name="top_p"'):
        assert term not in form
    assert 'day29-baseline' in form and 'day29-judicial-v1' in form
    assert 'test-local:Q5' in form and 'direct-columns' in form

def test_delta_exact_zero_missing_and_mismatch():
    a={'id':'a','question':'Q','finished':True,'generation_duration':42.3,'tokens_per_second':0,'output_tokens':1635}
    b={'id':'b','question':'Q','finished':True,'generation_duration':31.4,'tokens_per_second':17.8,'output_tokens':1040}
    delta=comparison(b,a)['deltas']
    assert delta['generation_duration']['percent']==pytest.approx(-25.7683215)
    assert delta['tokens_per_second']['percent'] is None and 'ram_peak' not in delta
    assert comparison(b|{'question':'Other'},a)=={'fair':False,'deltas':{}}

@pytest.mark.parametrize('field,value',[('temperature',3),('context_window',4096.5),('seed',1.1),('prompt_version','day28-baseline'),('top_p',.5)])
def test_invalid_direct_options(direct_lab,field,value):
    c,_,body,calls,_=direct_lab;body['options'][field]=value
    assert c.post('/api/local-llm-experiments',json=body).status_code==422
    assert not calls

def test_legacy_preserved_separate_and_recovery(direct_lab):
    c,_,body,_,_=direct_lab
    service=c.app.state.direct_experiments
    with c.app.state.runs.db() as store:
        store.put('rag_comparison_runs','sentinel',{'manual_experiment':True,'comparison_run_id':'sentinel','created_at':'2020'})
    row=launch(c,body)
    with c.app.state.runs.db() as store:
        assert store.get('rag_comparison_runs','sentinel')['manual_experiment']
        row.update(owner_pid=99999999,finished=False,status='RUNNING')
        store.put('local_llm_experiment_runs',row['id'],row)
    recovered=DirectExperimentService(c.app.state.runs).get(row['id'])
    assert recovered['status']=='INTERRUPTED' and recovered['finished']
    assert len(service.history())==1

@pytest.mark.parametrize('case',['missing','zero','truncated','network'])
def test_direct_partial_metrics_and_failures(direct_lab,monkeypatch,case):
    c,_,body,_,_=direct_lab
    from rag_arbiter.application import direct_experiments as module
    original=module.LocalLLMProvider
    def provider(cfg,**kwargs):
        adapter=original(cfg,**kwargs);transport=adapter.transport
        def response(path,payload=None,timeout=None):
            if path!='/api/chat':return transport(path,payload,timeout)
            if case=='network':raise OSError('private server body')
            data,_=transport(path,payload,timeout)
            if case=='missing':
                for k in ('eval_count','prompt_eval_count','eval_duration','load_duration','prompt_eval_duration'):data.pop(k,None)
            if case=='zero':data['eval_duration']=0
            if case=='truncated':data['done_reason']='length'
            return data,''
        adapter.transport=response
        return adapter
    monkeypatch.setattr(module,'LocalLLMProvider',provider)
    row=launch(c,body)
    assert row['finished'] and row['transport_attempted']
    if case in ('missing','zero','network'):assert row['tokens_per_second'] is None
    if case=='truncated':assert row['status']=='TRUNCATED' and row['answer'] and row['error']['code']=='OUTPUT_LIMIT'
    if case=='network':assert row['status']=='LOCAL_GENERATION_UNAVAILABLE' and 'private' not in str(row)
    assert c.get('/ui/local-llm-experiments',params={'ids':row['id']}).status_code==200

def test_direct_lab_without_any_corpus(config,monkeypatch):
    from fastapi.testclient import TestClient
    from rag_arbiter.web.app import create_app
    def forbidden(*args,**kwargs):raise AssertionError('No document pipeline allowed')
    monkeypatch.setattr('rag_arbiter.vectorstore.LocalVectorStore.__init__',forbidden)
    def transport(path,body=None,timeout=None):
        if path=='/api/tags':return {'models':[{'name':'installed'}]},''
        if path=='/api/show':return {'capabilities':['completion'],'model_info':{'test.context_length':8192}},''
        if path=='/api/chat':return {'done':True,'done_reason':'stop','message':{'content':'Answer'}},''
        return {},''
    def provider(cfg,**kwargs):return LocalLLMProvider(cfg,transport=transport,**kwargs)
    monkeypatch.setattr('rag_arbiter.application.direct_experiments.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.web.optimization.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.application.direct_experiments.Resources',lambda:nullcontext(SimpleNamespace(summary=lambda:{})))
    with TestClient(create_app(config,pipeline_factory=forbidden),base_url='http://127.0.0.1',headers={'X-RAG-Request':'1'}) as c:
        assert c.app.state.runs.list()==[]
        page=c.get('/ui/llm-optimization').text
        assert 'type="submit" disabled' in page and 'processing_run_id' not in page
        row=launch(c,{'question':'Question','options':{'model':'installed','temperature':0,'context_window':8192,'max_output_tokens':800,'seed':42,'prompt_version':'day29-baseline'}})
        assert row['status']=='SUCCESS' and row['answer']=='Answer'
        assert row['quantization'] is None and row['tokens_per_second'] is None
