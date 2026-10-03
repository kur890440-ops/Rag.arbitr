import copy
import json
import random
from unittest.mock import patch
import pytest
from rag_arbiter.application.exhaustive import (AnalysisBatch, BatchExtractionResult, ExhaustiveContextBatcher,
    ExhaustiveEvidenceMerger, ExhaustiveNoRAG, Finding, estimate)
from rag_arbiter.llm import LLMConfig, LLMResult
from test_rag import ragweb, compare


def docs(count=3,size=5000):
    return [dict(document_id=f'd{i}',file_name=f'{i}.pdf',page_count=2,parser_version='1',cache_identity=f'v{i}',
        pages=[{'page_number':p} for p in (1,2)],blocks=[dict(block_id=f'b{i}:{p}',page_number=p,reading_order=0,section='section',text=('evidence. '*size)+f'end-{i}-{p}') for p in (1,2)]) for i in range(count)]


class MapLLM:
    def __init__(self):self.calls=[];self.fail_unit=None;self.malformed=False;self.hook=None;self.temps=[]
    def factory(self,cfg):self.temps.append(cfg.temperature);return self
    def generate(self,request):
        self.calls.append(request)
        if self.hook:self.hook(request)
        if '\nUNITS:\n' in request.question:
            units=json.loads(request.question.split('\nUNITS:\n')[1]);u=units[0]
            if self.fail_unit==u['unit_id']:return LLMResult(model='fake',error={'code':'TIMEOUT'},request_count=1)
            if self.malformed:
                self.malformed=False
                return LLMResult(model='fake',text='malformed',status='SUCCESS',request_count=1)
            source=u.get('text') or u['excerpts'][0]['text']
            irrelevant=source.startswith('irrelevant')
            quote=u['excerpts'][0]['ref'] if 'excerpts' in u else source[-15:]
            findings=[] if irrelevant else [dict(unit_id=u['unit_id'],normalized_key='category',statement='fact',category='type',**({'evidence_ref':quote} if 'excerpts' in u else {'evidence_text':quote}))]
            text=json.dumps(dict(relevant=not irrelevant,findings=findings,limitations=[]))
        elif request.context_type=='grounded_rag':
            fact='Оплата через десять дней.'
            text=json.dumps(dict(answer=fact,claims=[dict(claim_id='C1',text=fact,supporting_source_ids=['S1'])]))
        else:text='Краткий ответ.'
        return LLMResult(model='fake',status='SUCCESS',text=text,request_count=1,usage={'prompt_tokens':20,'completion_tokens':10})


def cfg():return LLMConfig(full_document_context_budget=6000,max_output_tokens=256)


def test_batch_coverage_order_boundaries_budget():
    source=docs(size=400)
    batcher=ExhaustiveContextBatcher(cfg(),'question');batches=batcher.build(source)
    assert len(batches)>3
    assert batches==batcher.build(list(reversed(source)))
    for doc in source:
        for block in doc['blocks']:
            us=[u for b in batches for u in b.units if u.block_id==block['block_id']]
            assert ''.join(u.text for u in us)==block['text']
    assert all(estimate(batcher.request(b.units))+256<=6000 for b in batches)
    assert all(len(b.document_ids)==1 for b in batches)


def test_order_abc_independent_maps_and_counts():
    batcher=ExhaustiveContextBatcher(cfg(),'q');batches=batcher.build(docs(size=60))
    allmerged=[]
    for order in (batches,list(reversed(batches)),random.Random(42).sample(batches,len(batches))):
        llm=MapLLM();results=[]
        for b in order:results.append(ExhaustiveNoRAG.parse(llm.generate(batcher.request(b.units)).text,b))
        allmerged.append(ExhaustiveEvidenceMerger().merge(results,3))
        assert all('MERGED DATA' not in r.question and 'Краткий ответ' not in r.question for r in llm.calls)
    assert allmerged[0]==allmerged[1]==allmerged[2]
    assert allmerged[0][0]['document_count']==3
    # Every occurrence of the extracted quote is counted, not as a new document.
    b=batches[0];u=b.units[0]
    payload=json.dumps(dict(relevant=True,findings=[dict(unit_id=u.unit_id,statement='fact',normalized_key='X',evidence_text='evidence.')]))
    result=ExhaustiveNoRAG.parse(payload,b)
    merged=ExhaustiveEvidenceMerger().merge([result,result],3)
    assert merged[0]['document_count']==1 and merged[0]['occurrence_count']==60
    assert merged[0]['evidence'][0]['block_id']==u.block_id


def test_schema_irrelevant_and_invalid_evidence():
    b=ExhaustiveContextBatcher(cfg(),'q').build(docs(size=1))[0]
    result=ExhaustiveNoRAG.parse('{"relevant":false,"findings":[]}',b)
    assert result.status=='SUCCESS' and not result.relevant
    with pytest.raises(Exception):ExhaustiveNoRAG.parse('{"relevant":"false","findings":[]}',b)
    with pytest.raises(ValueError):ExhaustiveNoRAG.parse(json.dumps(dict(relevant=True,findings=[dict(unit_id=b.units[0].unit_id,statement='bad',evidence_text='invented')])),b)


def test_batched_persistence_cache_resume_partial_and_invalidation(ragweb,monkeypatch):
    c,rid,_,runtime=ragweb;runs=c.app.state.runs
    source=docs(size=400);llm=MapLLM();service=ExhaustiveNoRAG(runs,llm.factory)
    monkeypatch.setattr(service,'load',lambda *a:source)
    batches=ExhaustiveContextBatcher(cfg(),'question').build(source)
    def record(key='test',question='question'):
        return dict(comparison_run_id=key,processing_run_id=rid,document_id=None,question_text=question,full_document_context={},generation_call_count=0,minimax_requests_count=0)
    saved=[]
    def save(r):saved.append(copy.deepcopy(r['exhaustive']))
    llm.fail_unit='U1';llm.malformed=True
    original_generate=llm.generate
    failed_request=ExhaustiveContextBatcher(cfg(),'question').request(batches[1].units).question
    def conditional_failure(request):
        llm.fail_unit='U1' if request.question.split('\nQUESTION:\n')[-1]==failed_request.split('\nQUESTION:\n')[-1] else None
        return original_generate(request)
    llm.generate=conditional_failure
    def immediate(request):
        if '\nUNITS:\n' in request.question and len(llm.calls)>=4:
            with runs.db() as db:assert db.all('exhaustive_batch_results')
    llm.hook=immediate
    with patch('rag_arbiter.embeddings.BgeM3EmbeddingProvider.encode',side_effect=AssertionError('BGE forbidden')),patch('rag_arbiter.vectorstore.LocalVectorStore.__init__',side_effect=AssertionError('Qdrant forbidden')),patch.object(runs,'pipeline_factory',side_effect=AssertionError('Pipeline forbidden')):
        r=record();service.run(r,cfg(),save)
    ex=r['exhaustive'];assert ex['status']=='PARTIAL' and ex['pages_covered']<ex['pages_total']
    assert ex['batches_completed']==len(batches)-1 and ex['input_tokens_processed']>0
    assert len(saved)>len(batches) and ex['merged_findings']
    assert 'MERGED DATA' in llm.calls[-1].question and source[0]['blocks'][0]['text'] not in llm.calls[-1].question
    assert 0 in llm.temps
    with runs.db() as db:
        rows=db.all('exhaustive_batch_results')
        assert any(a['status']=='INVALID_EXTRACTION' and a['raw_safe']=='malformed' for row in rows for a in row['stats']['attempts'])
    llm.generate=original_generate;llm.fail_unit=None;llm.hook=None;r2=record('resume');service.run(r2,cfg(),save)
    assert r2['exhaustive']['status']=='SUCCESS' and r2['exhaustive']['coverage_percent']==100
    assert r2['exhaustive']['cache_hits']==len(batches)-1
    r3=record('cached');service.run(r3,cfg(),save)
    assert r3['exhaustive']['cache_hits']==len(batches) and r3['exhaustive']['minimax_requests']==1
    synthesis_cfg=cfg();synthesis_cfg.exhaustive_synthesis_budget=24000
    resized=record('synthesis-budget-only');service.run(resized,synthesis_cfg,save)
    assert resized['exhaustive']['cache_hits']==len(batches)
    assert resized['exhaustive']['map_requests']==0 and resized['exhaustive']['minimax_requests']==1
    assert resized['exhaustive']['synthesis_budget']==24000
    source[0]['blocks'][0]['text']+='changed'
    r4=record('changed');service.run(r4,cfg(),save)
    assert 0<r4['exhaustive']['cache_hits']<len(batches)
    r5=record('question','another question');service.run(r5,cfg(),save)
    assert r5['exhaustive']['cache_hits']==0

    changed_cfg=cfg();changed_cfg.model='another-model'
    r6=record('model');service.run(r6,changed_cfg,save)
    assert r6['exhaustive']['cache_hits']==0
    monkeypatch.setattr('rag_arbiter.application.exhaustive.MAP_VERSION','new-test-version')
    r7=record('prompt');service.run(r7,cfg(),save)
    assert r7['exhaustive']['cache_hits']==0


def test_integrated_three_panels_fast_and_batched(ragweb):
    c,rid,old,runtime=ragweb;before=runtime.calls
    fast=compare(c,rid)
    assert fast['exhaustive']['path']=='FAST' and fast['generation_call_count']==3
    runs=c.app.state.runs;runs.config.llm.full_document_context_budget=6000;runs.config.llm.max_output_tokens=256
    llm=MapLLM();c.app.state.rag.llm_factory=llm.factory
    # Force the scope over budget without changing the retrieval inputs/index.
    entries=c.app.state.views.snapshot(rid)['corpus']['documents']
    with runs.db() as db:
        for entry in entries:
            doc=db.get('normalized_documents',entry['recognition_version'])
            doc['blocks'][0]['text']+=' source tail '*600
            db.put('normalized_documents',entry['recognition_version'],doc)
    r=compare(c,rid)
    assert r['full_document_status']=='SUCCESS' and r['exhaustive']['path']=='BATCHED',r['full_document_error_json']
    assert r['exhaustive']['documents_covered']==2 and r['exhaustive']['pages_covered']==3
    assert llm.calls[0].context is None and llm.calls[0].question=='Question'
    assert llm.calls[-1].context_type=='grounded_rag' and llm.calls[-1].context==r['context_text']
    assert runtime.calls==before
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    assert 'ПОЛНЫЙ ПРОСМОТР' in html and 'Покрытие: 100' in html and 'query-mode' not in html
    assert html.count('full-document-panel')==1


def test_compact_synthesis_preserves_all_groups_without_repeated_provenance():
    from rag_arbiter.application.exhaustive import synthesis_request,estimate
    merged=[dict(reference=f'E{i}',normalized_key=f'fact{i}',statements=[f'fact number {i}'],categories=[],
        document_count=1,occurrence_count=2,documents_total=16,countable=True,
        evidence=[dict(document_id='opaque'*20,file_title='file.pdf',page=1,section='long section '*200,
            block_id='block'*20,char_start=0,evidence_text='exact quote '*20)]) for i in range(44)]
    request=synthesis_request('question',merged,{'documents_total':16},[],80)
    data=json.loads(request.question.split('MERGED DATA:\n')[1])
    assert len(data['findings'])==44 and len(data['documents'])==1
    assert all(a['statements']==b['statements'] and a['occurrence_count']==2 for a,b in zip(data['findings'],merged))
    assert all(a['representative_evidence'][0]['quote_truncated'] for a in data['findings'])
    assert 'opaque' not in request.question and 'long section' not in request.question
    assert estimate(request)+8192<65536
    packed=synthesis_request('question',merged,{'documents_total':16},[],64,packed=True)
    data=json.loads(packed.question.split('MERGED DATA:\n')[1])
    restored=[dict(zip(data['columns'],row)) for row in data['findings']]
    assert len(restored)==len(merged)
    for actual,original in zip(restored,merged):
        for key in ('reference','statements','categories','document_count','occurrence_count','countable'):
            assert actual[key]==original[key]
    assert estimate(packed)<estimate(request)


def test_evidence_whitespace_tolerance_preserves_source_offsets():
    source=docs(size=1)
    source[0]['blocks'][0]['text']='prefix Exact\n  quotation. suffix'
    batch=ExhaustiveContextBatcher(cfg(),'question').build(source)[0]
    payload=dict(relevant=True,findings=[dict(unit_id='U1',statement='fact',evidence_text='Exact quotation.')],limitations=[])
    result=ExhaustiveNoRAG.parse(json.dumps(payload),batch)
    assert result.findings[0].evidence_text=='Exact\n  quotation.'
    assert result.findings[0].char_start==7
    payload['findings'][0]['evidence_text']='Exact changed quotation.'
    with pytest.raises(ValueError,match='INVALID_EVIDENCE'):ExhaustiveNoRAG.parse(json.dumps(payload),batch)


def test_reference_retry_exact_source_coverage_and_wrong_unit_rejected():
    from rag_arbiter.application.exhaustive import referenced_retry,resolve_references
    batcher=ExhaustiveContextBatcher(cfg(),'question')
    batch=batcher.build(docs(count=1,size=70))[0]
    request,quotes=referenced_retry(batcher,batch)
    assert request and estimate(request)+256<=6000
    units=json.loads(request.question.split('\nUNITS:\n')[1])
    for original,unit in zip(batch.units,units):
        assert ''.join(s['text'] for s in unit['excerpts'])==original.text
    text=MapLLM().generate(request).text
    result=ExhaustiveNoRAG.parse(resolve_references(text,quotes),batch)
    assert result.findings[0].evidence_text==batch.units[0].text[:500]
    without_unit=json.loads(text)
    without_unit['findings'][0].pop('unit_id')
    assert resolve_references(json.dumps(without_unit),quotes)==resolve_references(text,quotes)
    payload=json.loads(text);payload['findings'][0]['unit_id']='U999'
    with pytest.raises(ValueError):resolve_references(json.dumps(payload),quotes)
    payload['findings'][0]['unit_id']='U1';payload['findings'][0]['evidence_ref']='invented quote'
    with pytest.raises(ValueError):resolve_references(json.dumps(payload),quotes)
