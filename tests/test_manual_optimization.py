import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from test_rag import ragweb
from rag_arbiter.local_llm import LocalLLMProvider
from rag_arbiter.application.context_selection import ContextSelector


@pytest.fixture
def manual_lab(ragweb,monkeypatch):
    c,rid,cloud,_=ragweb;calls=[];builds=[]
    original=ContextSelector.select
    def build(self,*a,**kw):builds.append(1);return original(self,*a,**kw)
    monkeypatch.setattr(ContextSelector,'select',build)
    def transport(path,body=None,timeout=None):
        calls.append((path,copy.deepcopy(body)))
        if path=='/api/tags':return {'models':[{'name':'test-local:Q5','digest':'fixed'}]},''
        if path=='/api/show':return {'capabilities':['completion'],'model_info':{'context_length.context_length':32768},'details':{'quantization_level':'Q5_K_M'}},''
        if path=='/api/ps':return {'models':[]},''
        if path=='/api/chat':return {'done':True,'done_reason':'stop','message':{'content':json.dumps({'answer':'','claims':[],'insufficient_context':True})},'eval_count':17,'prompt_eval_count':37,'prompt_eval_duration':100,'eval_duration':200,'total_duration':400,'load_duration':50},''
        return {},''
    def provider(cfg,**kwargs):return LocalLLMProvider(cfg,transport=transport,**kwargs)
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.application.manual_optimization.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.web.optimization.LocalLLMProvider',provider)
    monkeypatch.setattr('rag_arbiter.application.manual_optimization.Resources',lambda:nullcontext(SimpleNamespace(summary=lambda:{})))
    body=dict(processing_run_id=rid,question='Question',options=dict(model='test-local:Q5',temperature=.13,
        context_window=16384,max_output_tokens=950,prompt_version='day29-legal-v2',seed=73))
    return c,rid,body,calls,builds,cloud


def launch(c,body):
    response=c.post('/api/llm-experiments',json=body)
    assert response.status_code==200,response.text
    c.app.state.runs.executor.submit(lambda:None).result(timeout=30)
    key=response.json()['id']
    return key,c.app.state.experiments.get(key)


def test_manual_exact_options_isolation_reuse_and_fair(manual_lab,monkeypatch):
    c,rid,body,calls,builds,cloud=manual_lab
    before=c.app.state.runs.config.model_dump(mode='json')
    a,record=launch(c,body)
    assert record['status']=='COMPLETED',record.get('error_json')
    assert record['experiment_finished'] and record.get('experiment_snapshot')
    assert len(builds)==1
    chat=[b for path,b in calls if path=='/api/chat']
    assert chat and all(b['options']==dict(temperature=.13,num_ctx=16384,num_predict=950,seed=73) for b in chat)
    assert all(b['model']=='test-local:Q5' for b in chat)
    assert record['generation_runs'][0]['result']['diagnostics']['prompt_version']=='day29-legal-v2'
    old_snapshot=copy.deepcopy(record['experiment_snapshot'])
    def no_retrieval(*a,**kw):raise AssertionError('Replay must not prepare retrieval')
    monkeypatch.setattr(c.app.state.rag,'prepare',no_retrieval)
    body['reuse_run_id']=a;body['options'].update(temperature=0,max_output_tokens=800,prompt_version='day28-baseline')
    b,replayed=launch(c,body)
    assert replayed['status']=='COMPLETED',replayed.get('error_json')
    assert len(builds)==1 and replayed['experiment_snapshot']==old_snapshot
    assert record['generation_runs'][0]['context_ids']==replayed['generation_runs'][0]['context_ids']
    assert [data for path,data in calls if path=='/api/chat'][-1]['options']['temperature']==0
    assert c.app.state.runs.config.model_dump(mode='json')==before and not cloud.calls
    html=c.get('/ui/llm-experiments/compare',params={'run_a':a,'run_b':b}).text
    assert 'FAIR' in html and html.count('data-experiment-context')==1
    assert 'data-parameter="temperature">0.13' in html and 'data-parameter="temperature">0.0' in html
    assert 'data-metric="vram_mib"' not in html
    history=c.get('/ui/llm-experiments/history').text
    assert 'test-local:Q5' in history and 'day29-legal-v2' in history and '950' in history and '800' in history
    # Integrity-valid but different evidence must never receive FAIR.
    from rag_arbiter.application.local_benchmark import snapshot_digest
    replayed['experiment_snapshot']['context_ids']=['other']
    replayed['experiment_snapshot']['integrity_sha256']=snapshot_digest(replayed['experiment_snapshot'])
    c.app.state.rag.put(replayed)
    assert 'FAIR' not in c.get('/ui/llm-experiments/compare',params={'run_a':a,'run_b':b}).text
    assert c.get('/chat').status_code==200
    history=c.get(f'/ui/runs/{rid}/rag/history').text
    assert a not in history and b not in history


@pytest.mark.parametrize('key,value',[('temperature',3),('context_window',-1),('context_window',8192.5),
    ('max_output_tokens',99999),('prompt_version','invented'),('seed',1.1),('model',''),('top_p',.5)])
def test_invalid_options_never_reach_provider(manual_lab,key,value):
    c,_,body,calls,_,_=manual_lab
    body['options'][key]=value
    assert c.post('/api/llm-experiments',json=body).status_code==422
    assert not calls and not c.app.state.experiments.history()


def test_invalid_combined_budget_no_provider(manual_lab):
    c,_,body,calls,_,_=manual_lab
    body['options'].update(context_window=4096,max_output_tokens=2048)
    assert c.post('/api/llm-experiments',json=body).status_code==400
    assert not calls


def test_manual_form_actual_config_models_and_prompt_list(manual_lab):
    c,_,_,_,_,_=manual_lab
    c.app.state.runs.config.llm.local.temperature=.27
    html=c.get('/ui/llm-optimization').text
    assert 'id="manual-experiment-form"' in html and 'test-local:Q5' in html
    assert 'value="0.27"' in html and 'name="seed"' in html
    assert 'day29-judicial-v1' in html and 'name="top_p"' not in html
    assert 'hx-post="/ui/llm-optimization/benchmark"' not in html


def test_reuse_changed_question_rejected(manual_lab):
    c,_,body,_,_,_=manual_lab
    key,_=launch(c,body)
    body.update(reuse_run_id=key,question='Different question')
    assert c.post('/api/llm-experiments',json=body).status_code==400
