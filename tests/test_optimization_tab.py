import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from test_rag import ragweb
from test_local_optimization import fixture_snapshot
from rag_arbiter.application.local_benchmark import snapshot_digest, load_demo
from rag_arbiter.application.optimization import LocalBenchmarkService, laboratory
from rag_arbiter.llm import LocalLLMConfig,LLMResult


@pytest.fixture
def laboratory_data(ragweb,monkeypatch):
    c,rid,_,_=ragweb
    root=c.app.state.local_benchmark.directory;root.mkdir(parents=True,exist_ok=True)
    profile=LocalLLMConfig(enabled=True).model_dump()
    manifest=dict(config=profile,model=dict(model=profile['model'],digest='fixed',details={'quantization_level':'Q4_K_M'}))
    (root/'baseline.json').write_text(json.dumps(manifest),encoding='utf-8')
    for number in (1,2):
        snapshot,_=fixture_snapshot()
        snapshot.update(question_id=f'Q{number}',question=f'Question {number}',processing_run_id=rid)
        snapshot['integrity_sha256']=snapshot_digest(snapshot)
        (root/f'q{number}-snapshot.json').write_text(json.dumps(snapshot),encoding='utf-8')
        item=dict(provider='local',question_id=snapshot['question_id'],profile=profile,
            context_snapshot_id=snapshot['context_snapshot_id'],context_ids=snapshot['context_ids'],
            integrity_sha256=snapshot['integrity_sha256'],grounding_status='GROUNDED',citations_json=[],claims_json=[],repair_used=False,
            result=LLMResult(provider='local',model=profile['model'],text=f'Answer {number}',status='SUCCESS',usage={'completion_tokens':100}).model_dump(),
            generation_ms=6800,total_ms=8000)
        for variant in ('baseline','optimized','trial'):
            result=copy.deepcopy(item)
            if variant=='optimized':result['generation_ms']=4100
            (root/f'q{number}-{variant}.json').write_text(json.dumps(result),encoding='utf-8')
    monkeypatch.setattr('rag_arbiter.web.optimization.LocalLLMProvider',lambda cfg:SimpleNamespace(inspect=lambda:dict(details={'quantization_level':'Q4_K_M'})))
    monkeypatch.setattr('rag_arbiter.web.optimization.gpu_diagnostics',lambda:{'nvidia_detected':False})
    return c,rid,root


def test_tab_selection_all_experiments_and_isolation(laboratory_data):
    c,rid,root=laboratory_data
    page=c.get('/').text
    assert 'data-tab="llm-optimization"' in page and 'id="optimization-content"' in page
    response=c.get(f'/ui/runs/{rid}/workspace')
    assert response.status_code==200
    workspace=response.text
    assert 'local-optimization' not in workspace
    page=c.get('/ui/llm-optimization/archive').text
    assert page.count('class="generation-card"')==2 and page.count('data-shared-sources')==1
    assert 'Answer 1' in page and 'Answer 2' not in page and '-39.7%' in page
    assert 'data-all-experiments' in page and len(laboratory(root)['experiments'])==6
    assert 'data-metric="vram_mib"' not in page
    assert 'Q4_K_M' in page and 'data-recommended="temperature"' in page
    assert 'SUCCESS' in page and 'trial' in page
    page=c.get('/ui/llm-optimization/archive?question_id=Q2').text
    assert 'Answer 2' in page and 'Answer 1' not in page
    assert c.get('/chat').status_code==200
    assert c.get(f'/ui/runs/{rid}/rag/local-optimization').status_code==200


def test_tab_empty_and_offline(ragweb,monkeypatch):
    c,_,_,_=ragweb
    monkeypatch.setattr('rag_arbiter.web.optimization.gpu_diagnostics',lambda:{})
    def offline(*args):raise RuntimeError('offline')
    monkeypatch.setattr('rag_arbiter.web.optimization.LocalLLMProvider',offline)
    page=c.get('/ui/llm-optimization/archive')
    assert page.status_code==200 and 'class="empty"' in page.text
    assert 'data-local-variant' not in page.text 
    assert 'data-all-experiments' not in page.text
    assert c.post('/ui/llm-optimization/benchmark',headers={'X-RAG-Request':'1'}).status_code==400
    assert c.post('/ui/llm-optimization/benchmark',headers={'X-RAG-Request':'0'}).status_code==403


def test_benchmark_reuses_replay_and_keeps_originals(laboratory_data,monkeypatch):
    c,rid,root=laboratory_data
    runs=c.app.state.runs
    runs.config.llm.local=LocalLLMConfig(enabled=True,temperature=.2,keep_alive='60s')
    queued=[]
    monkeypatch.setattr(runs.executor,'submit',lambda *args:queued.append(args))
    calls=[]
    def replay(runs,snapshot,profile,reranker):
        calls.append((copy.deepcopy(snapshot),profile.model_dump()))
        item=json.loads((root/(snapshot['question_id'].lower()+'-baseline.json')).read_text(encoding='utf-8'))
        item.update(profile=profile.model_dump(),generation_ms=3000,error={})
        return item
    monkeypatch.setattr('rag_arbiter.application.optimization.replay_snapshot',replay)
    monkeypatch.setattr('rag_arbiter.application.optimization.LocalLLMProvider',lambda cfg:SimpleNamespace(inspect=lambda:{'digest':'fixed'}))
    monkeypatch.setattr('rag_arbiter.application.optimization.LocalReranker',lambda cfg:SimpleNamespace(load=lambda:None))
    monkeypatch.setattr('rag_arbiter.application.optimization.Resources',lambda:nullcontext(SimpleNamespace(summary=lambda:{})))
    originals={p.name:p.read_bytes() for p in root.glob('*.json')}
    for _ in range(2):
        r=c.post('/ui/llm-optimization/benchmark',headers={'X-RAG-Request':'1'})
        assert r.status_code==200 and 'QUEUED' in r.text
    assert len(queued)==1
    fn,*args=queued[0];fn(*args)
    assert len(calls)==4 and calls[0][0]==calls[1][0] and calls[2][0]==calls[3][0]
    assert calls[0][1]['temperature']==0 and calls[1][1]['temperature']==.2
    assert all((root/name).read_bytes()==value for name,value in originals.items())
    assert len(laboratory(root)['experiments'])==10
    assert load_demo(root,rid)[0]['variants'][1]['metrics']['generation_ms']==3000
    status=c.get('/ui/llm-optimization/benchmark')
    assert 'COMPLETED' in status.text and status.headers['HX-Trigger']=='optimizationFinished'


def test_benchmark_model_mismatch_preserves_results(laboratory_data,monkeypatch):
    c,rid,root=laboratory_data
    runs=c.app.state.runs;runs.config.llm.local.enabled=True
    queued=[]
    monkeypatch.setattr(runs.executor,'submit',lambda *args:queued.append(args))
    monkeypatch.setattr('rag_arbiter.application.optimization.LocalLLMProvider',lambda cfg:SimpleNamespace(inspect=lambda:{'digest':'changed'}))
    c.app.state.local_benchmark.start()
    fn,*args=queued[0];fn(*args)
    assert c.app.state.local_benchmark.status()['status']=='FAILED'
    assert not list(root.glob('*ui-*.json')) and len(load_demo(root,rid))==2
