import copy
import json
import pytest
from test_rag import ragweb
from test_manual_optimization import manual_lab,launch
from test_context_selection import chunk,processor,hits
from rag_arbiter.application.context_selection import ContextSelector
from rag_arbiter.application.rag import RAGContextBuilder
from rag_arbiter.local_diagnostics import input_budget
from rag_arbiter.llm import LLMRequest,LocalLLMConfig
from rag_arbiter.local_llm import LocalLLMProvider
from test_local_generation import transport_fixture


def test_budget_components_and_actual_transport_payload():
    calls=[];send=transport_fixture(calls)
    def transport(path,*args,**kw):
        data,raw=send(path,*args,**kw)
        if path=='/api/chat':data.update(prompt_eval_count=71,eval_count=13,prompt_eval_duration=100,eval_duration=200,total_duration=500,load_duration=50)
        return data,raw
    cfg=LocalLLMConfig(enabled=True,temperature=.17,max_output_tokens=900)
    req=LLMRequest(question='Question?',context='[S1] evidence',context_type='grounded_rag',capture_diagnostics=True)
    result=LocalLLMProvider(cfg,transport).generate(req)
    payload=next(body for path,body,_ in calls if path=='/api/chat')
    assert result.diagnostics['exact_input']==payload
    assert result.diagnostics['transport_attempted'] is True
    b=result.diagnostics['input_budget']
    assert b['total_estimated_input']==sum(len(m['content'].encode()) for m in payload['messages'])
    assert b['rag_estimated_tokens']==len(req.context.encode())
    assert sum(b[k] for k in ('rag_estimated_tokens','system_estimated_tokens','question_estimated_tokens','formatting_estimated_tokens','other_estimated_tokens'))==b['total_estimated_input']
    assert b['estimated_required']==b['total_estimated_input']+512+900
    assert b['estimated_remaining']==cfg.context_window-b['estimated_required']
    assert b['prompt_truncated'] is False and b['exact_pre_inference'] is False
    assert result.diagnostics['ollama']['prompt_eval_count']==71 and result.diagnostics['ollama']['eval_count']==13
    assert 'base_url' not in json.dumps(result.diagnostics['exact_input'])
    repair=req.model_copy(update={'context':req.context+' feedback','diagnostic_rag_context':req.context})
    assert input_budget(repair,cfg)['other_estimated_tokens']==len(' feedback'.encode())


def test_budget_exclusion_is_observed_without_changing_context():
    raw=hits([chunk('a','First fact'),chunk('b','Second fact',doc='b')])
    builder=RAGContextBuilder()
    one=builder.build(raw[:1],10000)
    result=builder.build(raw,one['token_count'])
    assert result['text']==one['text'] and result['used_chunk_ids']==['a']
    assert builder.diagnostics['budget_excluded'][0]['context_ids']==['b']
    assert builder.diagnostics['budget_excluded'][0]['text_bytes']==len('Second fact'.encode())
    candidates,_=processor([chunk('a','First fact')],'structure').build(raw[:1])
    selector=ContextSelector()
    expanded=candidates[0].model_copy(update={'context_text':'x'*10000,'expansion_type':'parent_section'})
    chosen=selector.select([expanded],5,500,builder)
    assert chosen[0].expansion_type=='anchor_budget_fallback'
    assert selector.diagnostics[0]['reason']=='BUDGET_FALLBACK'
    assert not selector.select(candidates,5,1,builder)
    assert selector.diagnostics[0]['reason']=='BUDGET'
    selector.select(candidates,0,500,builder)
    assert selector.diagnostics[0]['reason']=='MAX_CONTEXTS'


def test_recorded_diagnostics_exact_sources_and_reuse(manual_lab):
    c,_,body,calls,builds,_=manual_lab
    key,r=launch(c,body)
    d=c.app.state.experiments.view(r)['diagnostics']
    assert d['retrieval']['candidates_retrieved']==r['candidates_retrieved']
    assert d['retrieval']['rerank_output_count']==len([x for x in r['candidate_trace'] if x.get('rerank_rank') is not None])
    assert d['retrieval']['candidates_after_threshold']==r['candidates_after_rerank']
    assert d['rag_estimated_tokens']==len(r['context_text'].encode())
    actual=next(b for path,b in calls if path=='/api/chat')
    assert d['attempts'][0]['payload']==actual
    for row in d['rows']:
        assert row['source']['text'] in actual['messages'][1]['content']
        assert '['+row['source']['reference']+']' in actual['messages'][1]['content']
    html=c.get('/ui/llm-experiments/'+key).text
    assert 'data-retrieval-diagnostics' in html and 'data-final-contexts' in html
    assert 'data-exact-input' in html and 'data-ollama="eval_count">17' in html
    assert 'data-ollama="prompt_eval_count">37' in html
    assert d['attempts'][0]['actual']['total_duration']==400
    assert 'utf8_bytes_upper_estimate' not in actual['messages'][1]['content']
    body['reuse_run_id']=key
    other,replayed=launch(c,body)
    rd=c.app.state.experiments.view(replayed)['diagnostics']
    assert len(builds)==1 and rd['retrieval_run_id']==key
    assert rd['retrieval']==d['retrieval'] and rd['rag_estimated_tokens']==d['rag_estimated_tokens']
    assert replayed['experiment_snapshot']==r['experiment_snapshot']


def test_small_reuse_budget_is_rejected_without_provider(manual_lab):
    c,_,body,calls,builds,_=manual_lab
    key,r=launch(c,body)
    from rag_arbiter.application.local_benchmark import snapshot_digest
    s=r['experiment_snapshot'];s['text']='large '*2000;s['context_size']=len(s['text'].encode())
    s['integrity_sha256']=snapshot_digest(s);c.app.state.rag.put(r)
    body.update(reuse_run_id=key)
    body['options'].update(context_window=8192,max_output_tokens=900)
    before=len(calls)
    response=c.post('/api/llm-experiments',json=body)
    assert response.status_code==400 and 'num_ctx' in response.json()['detail']
    assert len(calls)==before and len(builds)==1
    assert c.app.state.experiments.get(key)['experiment_snapshot']==s


def test_oversize_provider_never_silently_truncates():
    calls=[]
    req=LLMRequest(question='Q',context='abc'*10000,context_type='grounded_rag',capture_diagnostics=True)
    result=LocalLLMProvider(LocalLLMConfig(enabled=True),transport_fixture(calls)).generate(req)
    assert result.error['code']=='LOCAL_CONTEXT_LIMIT'
    assert not any(path=='/api/chat' for path,_,_ in calls)
    assert req.context in result.diagnostics['exact_input']['messages'][1]['content']
    assert result.diagnostics['transport_attempted'] is False


def test_actual_fit_is_distinct_from_estimate_and_unknown_history():
    from rag_arbiter.application.experiment_diagnostics import diagnostics_view
    req=LLMRequest(question='Q',context='Source',context_type='grounded_rag')
    cfg=LocalLLMConfig(enabled=True,max_output_tokens=900)
    budget=input_budget(req,cfg)
    raw=dict(diagnostics={'input_budget':budget,'ollama':{'prompt_eval_count':71,'eval_count':13}})
    record=dict(comparison_run_id='r',retrieval_started=True,context_selection_diagnostics={'selection':[],'final_builder':{}},sources=[],context_text='')
    d=diagnostics_view(record,{'result':raw},None)
    assert d['attempts'][0]['required']==971
    assert d['attempts'][0]['remaining']==cfg.context_window-971 and d['attempts'][0]['fit']=='OK'
    raw['diagnostics']['ollama']['prompt_eval_count']=16000
    assert diagnostics_view(record,{'result':raw},None)['attempts'][0]['fit']=='OVER BUDGET'
    old=diagnostics_view({'comparison_run_id':'old'},{},None)
    assert old['context_reduced'] is None and old['dropped_count'] is None
    assert old['retrieval']['candidates_retrieved'] is None and not old['attempts']


def test_empty_final_context_retains_insufficient_diagnostics(manual_lab):
    c,_,body,_,_,_=manual_lab
    key,r=launch(c,body)
    r.pop('generation_runs',None)
    r.update(grounding_status='INSUFFICIENT_CONTEXT',sources=[],context_text='',used_count=0)
    r.pop('experiment_snapshot',None)
    c.app.state.rag.put(r)
    html=c.get('/ui/llm-experiments/'+key).text
    assert 'data-insufficient-diagnostics' in html
    assert c.app.state.experiments.view(r)['diagnostics']['final_count']==0
