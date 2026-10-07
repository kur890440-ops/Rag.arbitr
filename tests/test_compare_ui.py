"""Compare UI/API acceptance on synthetic sources and injected providers."""
from unittest.mock import patch
import pytest
from test_rag import ragweb, compare, FakeLLM
from rag_arbiter.llm import LLMResult
from rag_arbiter.application.generation_result import CompareResult, GenerationResult


def local_factory(cfg, **kwargs):
    class Local(FakeLLM):
        def generate(self, request):
            return super().generate(request).model_copy(update={'provider':'local'})
    return Local(cfg)


@pytest.mark.parametrize('mode',['minimax','local','compare'])
def test_provider_ui_shapes_and_shared_api(ragweb,monkeypatch,mode):
    c,rid,cloud,_=ragweb
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',local_factory)
    r=compare(c,rid,generation_mode=mode)
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    if mode=='compare':
        view=CompareResult.model_validate(r['compare_result'])
        assert view.shared.retrieval_run_id==r['comparison_run_id']
        assert view.shared.context_ids==r['used_chunk_ids_json']
        assert view.shared.sources==r['sources'] and view.shared.context_size==r['context_tokens']
        assert view.shared.context_snapshot_id==r['context_snapshot_id']
        assert view.local.grounding_label==view.cloud.grounding_label=='PASS'
        assert html.count('class="generation-card"')==2
        assert html.count('data-shared-sources')==1
        assert html.count('data-shared-source=')==len(r['sources'])
        assert 'data-generation-citation' in html and 'class="source-page secondary"' in html
    else:
        assert 'compare_result' not in r
        view=GenerationResult.model_validate(r['generation_result'])
        assert view.provider==mode and view.grounding_label=='PASS'
        assert 'generation-card' not in html and 'class="comparison rag-answers"' in html
        assert 'Provider:' in html and 'Generation:' in html and 'Repair:' in html
        assert ('NO CONTEXT' in html)==(mode=='minimax')


@pytest.mark.parametrize('failed',['local','minimax'])
def test_partial_failure_keeps_other_card(ragweb,monkeypatch,failed):
    c,rid,cloud,_=ragweb
    def factory(cfg,**kwargs):
        if failed=='local':
            class Broken:
                def generate(self,request):return LLMResult(provider='local',model=cfg.model,status='LOCAL_GENERATION_UNAVAILABLE',error={'code':'LOCAL_GENERATION_UNAVAILABLE'})
            return Broken()
        return local_factory(cfg)
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',factory)
    if failed=='minimax':cloud.fail='rag'
    r=compare(c,rid,generation_mode='compare')
    assert r['status']==r['compare_result']['status']=='PARTIAL'
    broken=r['compare_result']['local' if failed=='local' else 'cloud']
    good=r['compare_result']['cloud' if failed=='local' else 'local']
    assert broken['error_message'] and broken['grounding_label']=='FAIL'
    assert good['answer'] and good['grounding_label']=='PASS'
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    assert html.count('class="generation-card"')==2 and good['answer'].split(' [S')[0] in html
    assert 'PARTIAL' in html and 'role="alert"' in html


def test_progress_persisted_for_polling(ragweb,monkeypatch):
    c,rid,_,_=ragweb;service=c.app.state.rag
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',local_factory)
    record=service.prepare(rid,question='Question',generation_mode='compare')
    service.put(record);seen=[]
    def progress(stage):
        r=service.get(record['comparison_run_id'])
        seen.append((stage,r['compare_result']['active_provider']))
        assert r['compare_result']['stage']==stage
        html=c.get('/ui/rag/comparisons/'+record['comparison_run_id']).text
        assert html.count('class="generation-card"')==2 and 'role="status"' in html
        if stage=='generating' and r['compare_result']['active_provider']=='minimax':
            assert r['compare_result']['local']['answer']
            assert r['compare_result']['cloud']['status']=='PENDING'
    result=service.execute(record['comparison_run_id'],progress=progress)
    assert result['status']=='COMPLETED'
    assert ('retrieving','minimax') in seen
    for provider in ('local','minimax'):
        assert ('generating',provider) in seen and ('grounding',provider) in seen


def test_queued_and_empty_retrieval_have_two_cards(ragweb,monkeypatch):
    c,rid,_,_=ragweb;service=c.app.state.rag
    r=service.prepare(rid,question='Question',generation_mode='compare')
    service.put(r)
    queued=service.get(r['comparison_run_id'])['compare_result']
    assert queued['local']['status']==queued['cloud']['status']=='PENDING'
    assert queued['local']['generation_ms'] is None
    r['index_snapshot']=None;service.put(r)
    result=service.execute(r['comparison_run_id'])
    assert result['status']=='FAILED'
    for key in ('local','cloud'):
        assert result['compare_result'][key]['error']['code']=='NO_ACTIVE_INDEX'
        assert result['compare_result'][key]['generation_ms'] is None
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    assert html.count('class="generation-card"')==2 and html.count('data-shared-sources')==1

@pytest.mark.parametrize('mode',['compare','minimax'])
def test_compare_cloud_budget_is_scoped_and_persisted(ragweb,monkeypatch,mode):
    c,rid,_,_=ragweb
    service=c.app.state.rag
    cfg=service.runs.config.llm
    cfg.compare_max_output_tokens=16384
    cfg.compare_timeout=240
    configs=[]
    def factory(config):
        configs.append(config)
        return FakeLLM(config)
    service.llm_factory=factory
    monkeypatch.setattr('rag_arbiter.local_llm.LocalLLMProvider',local_factory)
    r=compare(c,rid,generation_mode=mode)
    expected=16384 if mode=='compare' else cfg.max_output_tokens
    assert configs[0].max_output_tokens==expected
    assert configs[0].timeout==(240 if mode=='compare' else cfg.timeout)
    assert cfg.max_output_tokens==8192 and cfg.timeout==120
    if mode=='compare':
        assert r['effective_cloud_generation_settings']==dict(max_output_tokens=16384,timeout=240)
        assert len(r['generation_runs'])==2
    else:assert 'effective_cloud_generation_settings' not in r
