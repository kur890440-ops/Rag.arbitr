import json
from pathlib import Path
import pytest
from rag_arbiter.recognition import Qwen3VLRecognitionProvider, ClassicOCRRecognitionProvider
from rag_arbiter.recognition.models import PageOutput, RecognitionResult, RecognizedBlock
from rag_arbiter.recognition.providers import normalize_page
from rag_arbiter.recognition.recovery import merge_fragments
from rag_arbiter.recognition.cache import RecognitionCache
from rag_arbiter.storage import MetadataStore
from rag_arbiter.pipeline import Pipeline
from test_recognition import FakeRuntime, page_output, page_request
from test_core import FakeOCR
from conftest import make_scanned_pdf, FakeEmbedding


def configured(config):
    config.recognition.recovery_retries=1
    config.recognition.split_enabled=True
    config.recognition.fallback_enabled=True
    return config


class SequenceRuntime(FakeRuntime):
    def __init__(self, sequence):
        super().__init__()
        self.sequence=iter(sequence)
    def generate(self, request, *args):
        item=next(self.sequence)
        self.done_reason='length' if item=='truncated' else 'stop'
        self.content='{bad' if item in ('truncated','bad') else json.dumps({'blocks':[{'type':'paragraph','text':item}]})
        return super().generate(request,*args)


@pytest.mark.parametrize('sequence,fragmented,fallback,calls', [
    (['truncated','recovered'],False,False,2),
    (['truncated','truncated','top','bottom'],True,False,4),
    (['bad','bad'],False,True,2),
    (['truncated','truncated','bad','bottom'],False,True,4)])
def test_bounded_recovery(config,page_request,sequence,fragmented,fallback,calls):
    runtime=SequenceRuntime(sequence)
    p=Qwen3VLRecognitionProvider(configured(config),runtime,ClassicOCRRecognitionProvider(config,FakeOCR()))
    result=p.recognize_page(page_request)
    assert result.status!='FAILED' and runtime.calls==calls
    assert result.diagnostics['fragmented']==fragmented
    assert result.diagnostics['fallback_used']==fallback
    assert bool(result.diagnostics['fallback_reason'])==fallback
    assert result.provider==('classic_ocr' if fallback else 'qwen3_vl')
    assert len(result.diagnostics['attempts'])==calls
    assert all(Path(a['raw_output_path']).exists() for a in result.diagnostics['attempts'])
    if fragmented:
        assert result.normalized_text=='top\n\nbottom'
        assert all(a['coordinates'] for a in result.diagnostics['attempts'] if a['kind']=='fragment')


def test_order_conflict_preserves_text(config,page_request):
    output=page_output();output['tables'][0]['order']=0
    result=Qwen3VLRecognitionProvider(config,FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.status=='UNCERTAIN'
    assert result.diagnostics['reading_order_status']=='UNCERTAIN'
    assert all(b['text'] in result.normalized_text for b in output['blocks'])
    assert result.reading_order==list(range(len(result.blocks)))


def test_duplicate_table_repaired(config,page_request):
    output=page_output();output['blocks'].append(dict(type='table',text='Товар Цена',order=2))
    result=Qwen3VLRecognitionProvider(config,FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.diagnostics['reading_order_status']=='REPAIRED'
    assert result.tables


def test_overlap_merge_only_boundary():
    def result(texts):
        return RecognitionResult(provider='qwen3_vl',model='test',model_version='1',runtime='test',device='cpu',quantization='test',page_number=1,raw_output='',blocks=[RecognizedBlock(type='paragraph',text=t,order=i) for i,t in enumerate(texts)])
    merged=merge_fragments([result(['same','top','boundary']),result(['boundary','bottom','same'])])
    assert [b.text for b in merged.blocks]==['same','top','boundary','bottom','same']


def test_malformed_complete_json_repair(config,page_request):
    raw='prefix {"blocks":[{"type":"paragraph","text":"content"},]} suffix'
    r=Qwen3VLRecognitionProvider(config,FakeRuntime(raw)).recognize_page(page_request)
    assert r.status!='FAILED' and r.diagnostics['json_repaired'] and r.normalized_text=='content'
    assert raw in json.loads(r.raw_output)['message']['content']


def test_partial_pages_persist_resume_and_share_chunkers(config):
    make_scanned_pdf(config.corpus_path/'partial.pdf',pages=3)
    class PageFailure(FakeRuntime):
        def generate(self, request, *args):
            if request.page_number==2:
                with MetadataStore(config.sqlite_path).db as db:
                    assert db.execute('SELECT count(*) FROM document_blocks').fetchone()[0]>0
                self.content='{bad'
            else:self.content=None
            return super().generate(request,*args)
    runtime=PageFailure()
    pipeline=Pipeline(config,FakeEmbedding(),Qwen3VLRecognitionProvider(config,runtime))
    try:
        first=pipeline.day21(do_evaluate=False)
        doc=pipeline.store.all('documents')[0]
        assert doc['status']=='PARTIAL' and doc['failed_pages']==[2]
        assert first['status']=='PARTIAL' and runtime.calls==3
        for index in first['indexes'].values():
            assert index['chunks']>0
            for cid in index['chunk_ids']:
                trace=pipeline.store.trace_chunk(cid)
                assert all(s['page']['page_number']!=2 for s in trace['sources'])
        second=pipeline.day21(do_evaluate=False)
        assert second['recognition']['cache_hits']==2 and runtime.calls==4
    finally:pipeline.close()


def test_fallback_cache_retry_with_qwen_and_force(config,page_request):
    config=configured(config)
    runtime=SequenceRuntime(['bad','bad','new Qwen','forced'])
    provider=Qwen3VLRecognitionProvider(config,runtime,ClassicOCRRecognitionProvider(config,FakeOCR()))
    store=MetadataStore(config.sqlite_path)
    cache=RecognitionCache(config,store,provider);identity=provider.identity()
    try:
        first=cache.recognize(page_request,identity)
        assert first[1].provider=='classic_ocr'
        assert cache.recognize(page_request,identity)[3] and runtime.calls==2
        config.retry_mode='fallback'
        qwen=cache.recognize(page_request,identity)
        assert qwen[1].provider=='qwen3_vl' and runtime.calls==3
        config.retry_mode='force';config.retry_document_id=page_request.document_id;config.retry_page_number=1
        forced=cache.recognize(page_request,identity)
        assert forced[0]!=qwen[0] and runtime.calls==4
        assert Path(first[2]['normalized_output_path']).exists()
    finally:store.close()


def test_mixed_provider_document(config):
    configured(config)
    make_scanned_pdf(config.corpus_path/'mixed.pdf',pages=2)
    runtime=SequenceRuntime(['first Qwen','bad','bad'])
    provider=Qwen3VLRecognitionProvider(config,runtime,ClassicOCRRecognitionProvider(config,FakeOCR()))
    pipeline=Pipeline(config,FakeEmbedding(),provider)
    try:
        snapshot=pipeline.day21(do_evaluate=False)
        doc=pipeline.store.all('documents')[0]
        assert doc['status']=='SUCCESS' and doc['recognition_provider']=='mixed'
        assert [p['recognition_provider'] for p in doc['pages']]==['qwen3_vl','classic_ocr']
        assert snapshot['recognition']['reliability']['classic_fallback']==1
        assert runtime.calls==3
    finally:pipeline.close()


def test_runtime_unavailable_falls_back(config,page_request):
    from rag_arbiter.recognition.runtime import RuntimeUnavailable
    class Unavailable(FakeRuntime):
        def preflight(self):raise RuntimeUnavailable('Unavailable test runtime')
        def generate(self,*args):
            self.calls+=1
            raise RuntimeUnavailable('Unavailable test runtime')
    runtime=Unavailable()
    provider=Qwen3VLRecognitionProvider(configured(config),runtime,ClassicOCRRecognitionProvider(config,FakeOCR()))
    result=provider.recognize_page(page_request)
    assert result.provider=='classic_ocr' and result.status!='FAILED'
    assert 'Unavailable' in result.diagnostics['fallback_reason'] and runtime.calls==2


def test_json_repair_does_not_change_text():
    from rag_arbiter.recognition.providers import repair_json
    assert repair_json('{"text":"literal ,] and ,}",}')['text']=='literal ,] and ,}'


def test_force_failure_preserves_previous_page(config,page_request):
    runtime=SequenceRuntime(['valid','bad'])
    provider=Qwen3VLRecognitionProvider(config,runtime)
    store=MetadataStore(config.sqlite_path)
    cache=RecognitionCache(config,store,provider)
    try:
        first=cache.recognize(page_request,provider.identity())
        config.retry_mode='force'
        second=cache.recognize(page_request,provider.identity())
        assert second[0]==first[0] and second[1].normalized_text=='valid'
        assert len(store.all('recognition_metadata'))==2
    finally:store.close()


def test_recovery_budgets_and_oom_mitigation(config,page_request):
    from rag_arbiter.recognition.runtime import RuntimeUnavailable
    class OOMOnce(FakeRuntime):
        def __init__(self):
            super().__init__();self.settings=config.recognition;self.sizes=[]
        def generate(self,req,*args):
            self.sizes.append(self.settings.max_image_resolution)
            if len(self.sizes)==1:raise RuntimeUnavailable('CUDA out of memory')
            return super().generate(req,*args)
    runtime=OOMOnce()
    result=Qwen3VLRecognitionProvider(configured(config),runtime).recognize_page(page_request)
    assert result.status=='SUCCESS' and runtime.sizes==[1280,896]
    assert runtime.settings is config.recognition
    runtime=SequenceRuntime(['truncated','ok']);runtime.settings=config.recognition
    result=Qwen3VLRecognitionProvider(config,runtime).recognize_page(page_request)
    assert result.diagnostics['attempts'][1]['configured_max_output']==12288
    assert result.diagnostics['attempts'][1]['configured_context']==16384


def test_interruption_preserves_pages_and_resume(config):
    from rag_arbiter.progress import RunCancelled
    make_scanned_pdf(config.corpus_path/'resume.pdf',pages=3)
    class Interrupt(FakeRuntime):
        def generate(self,req,*args):
            if req.page_number==2:raise RunCancelled()
            return super().generate(req,*args)
    first=Pipeline(config,FakeEmbedding(),Qwen3VLRecognitionProvider(config,Interrupt()))
    try:
        with pytest.raises(RunCancelled):first.ingest()
        assert first.store.all('document_blocks')
        assert first.store.all('document_pages')[0]['status']=='SUCCESS'
    finally:first.close()
    runtime=FakeRuntime()
    resumed=Pipeline(config,FakeEmbedding(),Qwen3VLRecognitionProvider(config,runtime))
    try:
        docs,snapshot=resumed.ingest()
        assert docs[0].status=='SUCCESS' and snapshot['recognition']['cache_hits']==1
        assert runtime.calls==2
    finally:resumed.close()


def test_selected_retry_does_not_infer_missing_other_page(config,page_request):
    config.retry_mode='force';config.retry_document_id='different-document'
    runtime=FakeRuntime();provider=Qwen3VLRecognitionProvider(config,runtime)
    store=MetadataStore(config.sqlite_path)
    try:
        result=RecognitionCache(config,store,provider).recognize(page_request,provider.identity())
        assert result[1].status=='FAILED' and result[1].diagnostics['skipped']
        assert runtime.calls==0
    finally:store.close()


def test_both_runtimes_unavailable_is_global_failure(config,page_request):
    from rag_arbiter.recognition.runtime import RuntimeUnavailable
    class Broken(FakeRuntime):
        def generate(self,*args):
            self.calls+=1
            raise RuntimeUnavailable('Qwen offline')
    class BrokenOCR(FakeOCR):
        def convert_page(self,*args):raise RuntimeError('OCR offline')
    runtime=Broken()
    provider=Qwen3VLRecognitionProvider(configured(config),runtime,ClassicOCRRecognitionProvider(config,BrokenOCR()))
    with pytest.raises(RuntimeUnavailable,match='Qwen runtime and Classic fallback unavailable'):
        provider.recognize_page(page_request)
    assert runtime.calls==2


def test_initial_large_budget_splits_without_full_page_retry(config,page_request):
    configured(config)
    config.recognition.max_generation_tokens=12288
    config.recognition.split_after_first_truncation=True
    runtime=SequenceRuntime(['truncated','top','bottom']);runtime.settings=config.recognition
    result=Qwen3VLRecognitionProvider(config,runtime).recognize_page(page_request)
    assert result.status=='SUCCESS' and result.diagnostics['fragmented']
    attempts=result.diagnostics['attempts']
    assert [a['kind'] for a in attempts]==['initial','fragment','fragment']
    assert [a['configured_max_output'] for a in attempts]==[12288,8192,8192]
    assert runtime.calls==3


def test_large_budget_still_retries_malformed_output(config,page_request):
    configured(config);config.recognition.max_generation_tokens=12288
    config.recognition.split_after_first_truncation=True
    runtime=SequenceRuntime(['bad','repaired'])
    result=Qwen3VLRecognitionProvider(config,runtime).recognize_page(page_request)
    assert result.status=='SUCCESS' and result.diagnostics['retried'] and runtime.calls==2


def test_budget_change_reuses_complete_pages_only(config,page_request):
    runtime=SequenceRuntime(['valid'])
    provider=Qwen3VLRecognitionProvider(config,runtime)
    store=MetadataStore(config.sqlite_path)
    try:
        cache=RecognitionCache(config,store,provider)
        old=cache.recognize(page_request,provider.identity())
        config.recognition.max_generation_tokens=12288
        config.recognition.split_after_first_truncation=True
        new=cache.recognize(page_request,provider.identity())
        assert new[3] and new[0]==old[0] and runtime.calls==1
        assert new[2]['settings']['settings']['max_generation_tokens']==8192
    finally:store.close()
