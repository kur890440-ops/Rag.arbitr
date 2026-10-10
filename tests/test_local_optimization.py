from contextlib import contextmanager
from types import SimpleNamespace
import copy
import pytest
from pydantic import ValidationError
from rag_arbiter.llm import LLMRequest, LocalLLMConfig, LLMConfig, MiniMaxLLMProvider, LLMResult, BASE_SYSTEM
from rag_arbiter.local_llm import LocalLLMProvider
from rag_arbiter.application.local_benchmark import replay_snapshot, snapshot_digest, verified_snapshot
from test_local_generation import transport_fixture
from test_grounding import Store, Reranker, contract


def test_local_options_and_ollama_metrics():
    calls=[]; transport=transport_fixture(calls)
    def send(*a, **kw):
        data, raw=transport(*a, **kw)
        if a[0]=='/api/chat':data.update(prompt_eval_duration=123,eval_duration=456)
        return data, raw
    cfg=LocalLLMConfig(enabled=True,temperature=.2,seed=123,context_window=12288,max_output_tokens=1024,prompt_version='day29-legal-v1')
    request=LLMRequest(question='Who?',context='source',context_type='grounded_rag')
    result=LocalLLMProvider(cfg,send).generate(request)
    body=calls[-1][1]
    assert body['options']==dict(num_ctx=12288,num_predict=1024,temperature=.2,seed=123)
    assert body['messages']==request.messages('day29-legal-v1')
    assert body['format']==request.output_schema()
    assert result.diagnostics['ollama']['eval_duration']==456
    assert result.diagnostics['ollama']['prompt_eval_count']==99


@pytest.mark.parametrize('version',['day29-legal-v1','day29-legal-v2','day29-legal-v3'])
def test_prompt_version_and_cloud_unchanged(version):
    request=LLMRequest(question='Q',context='SOURCE',context_type='grounded_rag')
    a,b=request.messages(),request.messages(version)
    assert a[0]['content']==BASE_SYSTEM and a[0]!=b[0] and a[1]==b[1]
    calls=[]
    MiniMaxLLMProvider(LLMConfig(api_key='synthetic',local=LocalLLMConfig(prompt_version=version)),
        lambda *args:(calls.append(args[2]) or {'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]})).generate(request)
    assert calls[0]['messages']==a
    with pytest.raises(ValidationError):LocalLLMConfig(prompt_version='unknown')


@pytest.mark.parametrize('version',['day28-baseline','day29-legal-v1','day29-legal-v2','day29-legal-v3'])
def test_overflow_does_not_trim_or_send(version):
    calls=[];cfg=LocalLLMConfig(enabled=True,prompt_version=version,context_window=8192,max_output_tokens=1024)
    request=LLMRequest(question='Q',context='X'*9000,context_type='grounded_rag')
    result=LocalLLMProvider(cfg,transport_fixture(calls)).generate(request)
    assert result.error['code']=='LOCAL_CONTEXT_LIMIT' and result.request_count==0
    assert request.context=='X'*9000 and not any(x[0]=='/api/chat' for x in calls)


def fixture_snapshot():
    store=Store()
    source=dict(reference='S1',document_id='d',chunk_id='c',anchor_chunk_ids=['c'],source_block_ids=['b'],
        file_name='file.pdf',text=store.rows['document_blocks']['b']['text'],score=.4)
    snapshot=dict(question_id='Q1',question='Payment?',retrieval_run_id='once',processing_run_id='p',
        context_snapshot_id='original',context_ids=['c'],text=source['text'],sources=[source],
        context_size=len(source['text'].encode()),shared_pipeline_ms=17,claim_support_threshold=.5)
    snapshot['integrity_sha256']=snapshot_digest(snapshot)
    return snapshot,store


def test_replay_same_snapshot_existing_grounding_and_local_repair():
    snapshot,store=fixture_snapshot();before=copy.deepcopy(snapshot);calls=[]
    @contextmanager
    def db():yield store
    runs=SimpleNamespace(db=db,operation_lock=lambda:None,config=SimpleNamespace(recognition=SimpleNamespace(model='ocr')))
    class Local:
        def __init__(self,profile,**kwargs):self.count=0
        def generate(self,request):
            calls.append(request);self.count+=1
            return LLMResult(provider='local',model='qwen',status='SUCCESS',request_count=1,
                text='invalid json' if self.count==1 else contract().model_dump_json())
    outputs=[replay_snapshot(runs,snapshot,LocalLLMConfig(temperature=t),Reranker(),Local) for t in (0,.1)]
    assert snapshot==before
    assert calls[0].context==calls[2].context==snapshot['text']
    assert all(x.context.startswith(snapshot['text']) for x in calls)
    assert all(o['context_ids']==['c'] and o['context_snapshot_id']=='original' and o['retrieval_run_id']=='once' for o in outputs)
    assert all(o['grounding_status']=='GROUNDED' and o['repair_count']==1 and o['structured_output_valid'] for o in outputs)
    assert all(o['citations_count']==1 and len(o['attempts'])==2 for o in outputs)
    snapshot['sources'][0]['text']='tampered'
    with pytest.raises(ValueError,match='integrity'):verified_snapshot(snapshot)


def test_demo_rejects_different_snapshot_and_renders_shared_sources(ragweb, tmp_path):
    import json
    from rag_arbiter.application.local_benchmark import load_demo
    c,rid,_,_=ragweb
    snapshot,_=fixture_snapshot();snapshot['processing_run_id']=rid
    snapshot['sources'][0].update(page_start=1,page_end=1,source_pages=[])
    snapshot['integrity_sha256']=snapshot_digest(snapshot)
    root=c.app.state.runs.config.web_data_path.parent/'day29';root.mkdir(parents=True,exist_ok=True)
    (root/'q1-snapshot.json').write_text(json.dumps(snapshot),encoding='utf-8')
    item=dict(provider='local',profile=LocalLLMConfig().model_dump(),context_snapshot_id=snapshot['context_snapshot_id'],
        context_ids=snapshot['context_ids'],grounding_status='GROUNDED',citations_json=[],repair_used=False,
        result=LLMResult(provider='local',model='qwen',text='Synthetic answer',status='SUCCESS',request_count=1).model_dump(),
        generation_ms=123,total_ms=456)
    for v in ('baseline','optimized'):(root/f'q1-{v}.json').write_text(json.dumps(item),encoding='utf-8')
    page=c.get(f'/ui/runs/{rid}/rag/local-optimization')
    assert page.status_code==200 and page.text.count('class="generation-card"')==2
    assert 'LOCAL BASELINE' in page.text and 'LOCAL OPTIMIZED' in page.text
    assert page.text.count('data-shared-sources')==1 and 'data-parameter="temperature">0' in page.text
    assert not load_demo(root,'another-corpus')
    item['context_snapshot_id']='different'
    (root/'q1-optimized.json').write_text(json.dumps(item),encoding='utf-8')
    assert not load_demo(root,rid)


from test_rag import ragweb


def test_local_lease_holds_existing_lock_through_repair_and_unloads():
    state={'held':False,'entries':0};calls=[]
    @contextmanager
    def lock():
        assert not state['held'];state.update(held=True,entries=state['entries']+1)
        try:yield
        finally:state['held']=False
    base=transport_fixture(calls)
    def transport(path,body=None,timeout=None):
        assert state['held']
        if path=='/api/ps':return {'models':[{'name':'qwen3:4b-q4_K_M'}]},''
        return base(path,body,timeout)
    p=LocalLLMProvider(LocalLLMConfig(enabled=True,keep_alive='60s'),transport,operation_lock=lock)
    with pytest.raises(RuntimeError,match='validation failure'):
        with p.generation_session():
            for _ in range(2):assert p.generate(LLMRequest(question='Q')).status=='SUCCESS'
            assert state['held']
            raise RuntimeError('validation failure')
    assert state['entries']==1 and not state['held']
    assert [c[1]['keep_alive'] for c in calls if c[0]=='/api/chat']==['60s','60s']
    assert calls[-1][0]=='/api/generate' and calls[-1][1]['keep_alive']==0


def test_local_lease_busy_is_typed_failure_without_network():
    @contextmanager
    def busy():
        raise ValueError('resource busy')
        yield
    calls=[];p=LocalLLMProvider(LocalLLMConfig(enabled=True,keep_alive='60s'),transport_fixture(calls),operation_lock=busy)
    with p.generation_session():r=p.generate(LLMRequest(question='Q'))
    assert r.status=='LOCAL_GENERATION_UNAVAILABLE' and r.request_count==0 and not calls


def test_local_lease_cleanup_failure_preserves_answer():
    calls=[];base=transport_fixture(calls)
    def send(path,*args,**kwargs):
        if path=='/api/ps':raise TimeoutError()
        return base(path,*args,**kwargs)
    p=LocalLLMProvider(LocalLLMConfig(enabled=True,keep_alive='60s'),send)
    with p.generation_session():r=p.generate(LLMRequest(question='Q'))
    assert r.status=='SUCCESS' and p.cleanup_error=='LOCAL_RELEASE_FAILED_TTL_BOUNDED'


def test_busy_local_lease_keeps_cloud_compare(ragweb,monkeypatch):
    from test_rag import compare
    c,rid,cloud,_=ragweb
    @contextmanager
    def busy():
        raise ValueError('busy')
        yield
    local=LocalLLMProvider(LocalLLMConfig(enabled=True,keep_alive='60s'),transport_fixture([]),operation_lock=busy)
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',lambda *a,**kw:local)
    r=compare(c,rid,generation_mode='compare')
    assert r['status']=='PARTIAL' and len(cloud.calls)==1
    assert r['generation_runs'][0]['error']['code']=='LOCAL_GENERATION_UNAVAILABLE'
    assert r['generation_runs'][1]['grounding_status']=='GROUNDED'


def test_day29_delta_missing_zero_and_nonfinite():
    from rag_arbiter.application.local_benchmark import metric_delta
    assert metric_delta(6800,4100)['percent']==pytest.approx(-39.70588235)
    assert metric_delta(0,10)==dict(before=0,after=10,percent=None)
    assert metric_delta(10,0)['percent']==-100
    for missing in (None,float('nan'),float('inf'),-1,True):
        assert metric_delta(missing,10)==dict(before=None,after=10,percent=None)
        assert metric_delta(10,missing)==dict(before=10,after=None,percent=None)


@pytest.mark.parametrize('field,value',[
    ('context_snapshot_id','other'),('context_ids',['other']),('question_id','Q2'),
    ('question','other'),('retrieval_run_id','other'),('integrity_sha256','other'),
    ('context_size',1),('source_ids',['S2']),('retrieval_policy_json',{'different':True}),
    ('reranker_settings',{'different':True})])
def test_day29_rejects_inconsistent_evidence(field,value):
    from rag_arbiter.application.local_benchmark import same_evidence
    snapshot,_=fixture_snapshot()
    item=dict(context_snapshot_id=snapshot['context_snapshot_id'],context_ids=snapshot['context_ids'])
    same_evidence(item,snapshot)
    item[field]=value
    with pytest.raises(ValueError):same_evidence(item,snapshot)


def test_day29_actual_parameters_metrics_experiments_and_missing_vram(ragweb):
    import json
    from rag_arbiter.application.local_benchmark import load_demo,demo_variant
    c,rid,_,_=ragweb
    snapshot,_=fixture_snapshot();snapshot['processing_run_id']=rid
    snapshot['sources'][0].update(page_start=1,page_end=1,source_pages=[])
    snapshot['integrity_sha256']=snapshot_digest(snapshot)
    root=c.app.state.runs.config.web_data_path.parent/'day29';root.mkdir(parents=True,exist_ok=True)
    def save(name,item):(root/f'{name}.json').write_text(json.dumps(item),encoding='utf-8')
    save('q1-snapshot',snapshot)
    before=dict(provider='local',profile=dict(model='custom:Q5',temperature=.23,context_window=7000,
        max_output_tokens=1300,prompt_version='recorded-v7'),context_snapshot_id=snapshot['context_snapshot_id'],
        context_ids=snapshot['context_ids'],grounding_status='GROUNDED',citations_json=[],claims_json=[],repair_used=True,
        result=LLMResult(provider='local',model='custom:Q5',text='Recorded answer',status='SUCCESS',
            usage={'completion_tokens':1200},diagnostics={'details':{'quantization_level':'Q5_K_M'}}).model_dump(),
        generation_ms=6800,total_ms=9100)
    after=copy.deepcopy(before)
    after.update(generation_ms=4100,total_ms=6400,repair_used=False)
    after['profile'].update(temperature=.03,context_window=6000,max_output_tokens=950,prompt_version='recorded-v8')
    after['result']['usage']['completion_tokens']=780
    save('q1-baseline',before);save('q1-optimized',after)
    save('q1-temp03',after)
    invalid=copy.deepcopy(after);invalid['context_ids']=['wrong'];save('q1-invalid',invalid)
    (root/'q1-malformed.json').write_text('{',encoding='utf-8')
    questions=load_demo(root,rid)
    assert len(questions)==1 and [v['label'] for v in questions[0]['experiments']]==['temp03']
    assert questions[0]['deltas']['generation_ms']['percent']==pytest.approx(-39.70588235)
    html=c.get(f'/ui/runs/{rid}/rag/local-optimization').text
    for text in ('custom:Q5','Q5_K_M','0.23','0.03','7000','6000','1300','950','recorded-v7','recorded-v8',
                 '6.800 s','4.100 s','9.100 s','6.400 s','-39.7%','1200','780','temp03'):
        assert text in html
    assert 'data-metric="vram_mib"' not in html and 'data-delta="vram_mib"' not in html
    assert 'data-metric="ram_mib"' not in html and html.count('data-shared-sources')==1
    assert snapshot['integrity_sha256'] in html and 'data-context-ids>c' in html
    after['resources']=dict(rss_peak_bytes=2097152,device_vram_peak_mib=5.5)
    after['result']['usage']['completion_tokens']=0
    variant=demo_variant(after,'LOCAL OPTIMIZED','',{})
    assert variant['metrics']['ram_mib']==2 and variant['metrics']['vram_mib']==5.5
    assert variant['metrics']['output_tokens']==0 and variant['metrics']['claims_count']==0
    save('q1-optimized',after)
    html=c.get(f'/ui/runs/{rid}/rag/local-optimization').text
    assert 'data-metric="vram_mib">5.5 MiB' in html
    assert 'data-delta="vram_mib"' not in html  # Only one side measured.
    after['profile']={}
    variant=demo_variant(after,'LOCAL OPTIMIZED','',{'explicit_options':{'temperature':.9},'prompt_version':'old'})
    assert variant['profile'].get('temperature') is None and variant['profile'].get('prompt_version') is None
