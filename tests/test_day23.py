import json
from unittest.mock import patch
import pytest
from conftest import FakeEmbedding
from test_rag import ragweb,compare
from rag_arbiter.reranking import RerankedCandidate,RerankerUnavailable
from rag_arbiter.application.query_rewrite import QueryRewriteService
from rag_arbiter.llm import LLMResult


class FakeReranker:
    def __init__(self):self.calls=[];self.fail=False
    def rerank(self,question,candidates):
        self.calls.append((question,candidates))
        if self.fail:raise RerankerUnavailable('test')
        return [RerankedCandidate(**c.model_dump(),rerank_rank=i+1,rerank_score=.9-i*.1) for i,c in enumerate(reversed(candidates))]


def test_rerank_original_scores_order_threshold_trace_and_expansion(ragweb):
    c,rid,llm,_=ragweb;fake=FakeReranker();c.app.state.rag.reranker_factory=lambda cfg:fake
    r=compare(c,rid,rag_pipeline_mode='RERANK',candidate_top_n=8,max_context_sources=2,rerank_threshold=.65)
    assert r['status']=='COMPLETED',r['error_json']
    assert fake.calls[0][0]=='Question' and len(fake.calls[0][1])<=8
    accepted=[t for t in r['candidate_trace'] if t.get('rerank_rank') and t['accepted']]
    assert len(accepted)==3 and accepted[0]['retrieval_rank']>accepted[-1]['retrieval_rank']
    raw={h['chunk_id']:h for h in r['retrieved_sources']}
    assert all(t['retrieval_score']==raw[t['chunk_id']]['score'] for t in accepted)
    assert all(t['filter_reason']=='BELOW_RERANK_THRESHOLD' for t in r['candidate_trace'] if t.get('rerank_rank') and not t['accepted'])
    assert 0<r['contexts_used']<=2
    assert all(s['rerank_score']>=.65 for s in r['sources'])
    assert llm.calls[-1].question=='Question' and llm.calls[-1].context==r['context_text']
    assert 'rerank_score' not in llm.calls[-1].context
    html=c.get('/ui/rag/comparisons/'+r['comparison_run_id']).text
    assert 'До / после reranking' in html
    # Shared service keeps the same provider across queries.
    r2=compare(c,rid,rag_pipeline_mode='RERANK',rerank_threshold=1)
    assert r2['rag_result']['status']=='NO_RELEVANT_CONTEXT' and not r2['sources']
    assert len(c.app.state.rag.rerankers)==1
    fake.fail=True
    r3=compare(c,rid,rag_pipeline_mode='RERANK')
    assert r3['rag_result']['status']=='RERANKER_UNAVAILABLE' and not r3['rag_answer']


def test_rewrite_only_retrieval_original_reranker_final_and_cache(ragweb):
    c,rid,llm,_=ragweb;fake=FakeReranker();service=c.app.state.rag;service.reranker_factory=lambda cfg:fake
    original_generate=llm.generate
    def generate(request):
        if 'retrieval_query' in request.question:
            llm.calls.append(request)
            return LLMResult(model='fake',status='SUCCESS',text='{"retrieval_query":"Search rewritten"}',request_count=1)
        return original_generate(request)
    llm.generate=generate
    queries=[];original_encode=FakeEmbedding.encode
    def encode(self,texts):queries.extend(texts);return original_encode(self,texts)
    with patch.object(FakeEmbedding,'encode',encode):
        r=compare(c,rid,rag_pipeline_mode='REWRITE_RERANK')
    assert r['rewrite_used'] and r['retrieval_query']=='Search rewritten'
    assert queries==['Search rewritten'] and fake.calls[0][0]=='Question'
    assert llm.calls[-1].question=='Question'
    assert llm.calls[0].question=='Question' and llm.calls[0].context is None
    assert llm.calls[1].question=='Question' and llm.calls[1].context_type=='full_document'
    n=len(llm.calls);again=compare(c,rid,rag_pipeline_mode='REWRITE_RERANK')
    assert again['rewrite_result']['cache_hit'] and len(llm.calls)==n+3
    # No rewrite in baseline and no reranker in either no-RAG branch.
    fake.calls.clear();base=compare(c,rid,rag_pipeline_mode='BASELINE')
    assert not fake.calls and base['rewrite_status']=='DISABLED'


def test_rewrite_failure_and_cache_invalidation(ragweb):
    c,rid,llm,_=ragweb;service=c.app.state.rag;fake=FakeReranker();service.reranker_factory=lambda cfg:fake
    r=compare(c,rid,rag_pipeline_mode='REWRITE_RERANK')
    assert r['rewrite_fallback'] and r['retrieval_query']==r['original_question']=='Question'
    assert r['rag_result']['status']=='SUCCESS' and fake.calls[0][0]=='Question'
    rewrite=QueryRewriteService(service.runs,service.runs.config.llm,lambda cfg:llm)
    llm.generate=lambda request:LLMResult(model='fake',status='SUCCESS',text='{"retrieval_query":"ok"}')
    assert not rewrite.rewrite('unique').cache_hit
    assert rewrite.rewrite('unique').cache_hit
    rewrite.config=rewrite.config.model_copy(update={'model':'different'})
    assert not rewrite.rewrite('unique').cache_hit


def test_reranker_explicit_missing_model_and_cuda_no_silent_fallback(tmp_path):
    from rag_arbiter.reranking import LocalReranker,RerankerConfig
    provider=LocalReranker(RerankerConfig(local_path=str(tmp_path/'missing')))
    with pytest.raises(RerankerUnavailable):provider.load()
    assert provider.status()['status']=='ERROR' and provider.model is None
    gpu=LocalReranker(RerankerConfig(device='cuda',local_path=str(tmp_path/'missing')))
    with patch('torch.cuda.is_available',return_value=False),pytest.raises(RerankerUnavailable):gpu.load()
    assert gpu.device=='cuda' and gpu.model is None


def test_mode_evaluation_skips_no_rag_and_persists(ragweb):
    c,rid,llm,_=ragweb;service=c.app.state.rag;fake=FakeReranker();service.reranker_factory=lambda cfg:fake
    c.post('/api/rag/questions',json={'question':'Question','expected_answer':'','expected_sources':[]})
    with patch('rag_arbiter.application.rag.FullDocumentContextBuilder.build',side_effect=AssertionError('No exhaustive allowed')):
        response=c.post(f'/api/runs/{rid}/rag/evaluate',json={'candidate_top_n':5})
        assert response.status_code==202
        key=response.json()['batch_id'];service.futures[key].result(timeout=30)
    batch=service.get(key,'rag_batches');records=[service.get(k) for k in batch['comparison_ids']]
    assert {r['rag_pipeline_mode'] for r in records}=={'BASELINE','RERANK','REWRITE_RERANK'}
    assert all(r['point_only'] and r['full_document_status']=='NOT_RUN' and r['no_rag_result'] is None for r in records)
    assert c.get(f'/ui/runs/{rid}/rag/evaluation').status_code==200
    assert 'Source rank:' in c.get(f'/ui/runs/{rid}/rag/evaluation').text
