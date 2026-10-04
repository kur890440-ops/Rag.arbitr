"""Local replay of reported Q4 retrieval. No LLM/network, no re-embedding documents."""
from rag_arbiter.config import Config
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.documents import save_json
from rag_arbiter.retrieval import SemanticRetriever
from rag_arbiter.reranking import LocalReranker,RerankCandidate
from rag_arbiter.application.chat_models import TaskState
from rag_arbiter.application.retrieval_policy import RetrievalPolicyBuilder
from rag_arbiter.application.document_metadata import enrich_documents,sync_payload
from rag_arbiter.application.context_selection import CandidateProcessor,ContextSelector
from rag_arbiter.application.rag import RAGContextBuilder


FOCUSED_QUESTION = 'Какие обязанности конкурсного управляющего связаны с отчетностью?'


def main():
    cfg=Config.load();pipeline=Pipeline(cfg)
    try:
        old=pipeline.store.get('rag_comparison_runs','d963a1a27e8d4fd681958e136aac5e39')
        turn=pipeline.store.get('chat_turns','5af6c6cb3ced4cab9e9ea0826e7c9a51')
        index=old['index_snapshot']
        old['question_text']=FOCUSED_QUESTION
        metadata=enrich_documents(pipeline.store,index['document_ids'],index['corpus_id'])
        policy=RetrievalPolicyBuilder().build(TaskState.model_validate(turn['trace']['task_state_after']),'ALL_DOCUMENTS',index['corpus_id'],metadata)
        pipeline.resources();sync_payload(pipeline.vectors,index,metadata)
        vector=pipeline.provider.encode([old['retrieval_query']])[0]
        hits=SemanticRetriever(pipeline.provider,pipeline.vectors,pipeline.store).retrieve_vector(vector,index,20,policy=policy)
        print('Candidates',len(hits),'eligible documents',len(policy.eligible_document_ids),flush=True)
        processor=CandidateProcessor(pipeline.store,index,'structure',min(cfg.context_expansion_budget,12000))
        cleaned,_=processor.dedup(hits,None)
        inputs=[RerankCandidate(chunk_id=h['chunk_id'],document_id=h['document_id'],text=h['text'],
            file_title=h.get('file_name',''),section=h.get('section',''),page_start=h['page_start'],page_end=h['page_end'],
            retrieval_rank=h['rank'],retrieval_score=h['score']) for h in cleaned]
        ranked=LocalReranker(cfg.reranker).rerank(old['question_text'],inputs)
        by_id={h['chunk_id']:h for h in cleaned}
        accepted=[{**by_id[c.chunk_id],'rerank_score':c.rerank_score,'rerank_rank':c.rerank_rank} for c in ranked if c.rerank_score>=old['rerank_threshold']]
        candidates,_=processor.build(accepted,precleaned=True)
        candidates=policy.guard(candidates,'final_context')
        selected=ContextSelector().select(candidates,5,12000,RAGContextBuilder(),cfg.context_diversity_penalty)
        context=RAGContextBuilder().build(selected,12000)
        assert hits and all(metadata[h['document_id']]['document_type']=='COURT_RULING' for h in hits+context['sources'])
        report=dict(mode='LOCAL_RETRIEVAL_ONLY_NO_LLM',policy=policy.model_dump(),
            eligible=[dict(document_id=id,file_name=pipeline.store.get('documents',id)['file_name']) for id in policy.eligible_document_ids],
            candidates=[{k:h.get(k) for k in ('document_id','chunk_id','rank','score')} for h in hits],
            sources=[{k:h.get(k) for k in ('document_id','file_name','page_start','page_end','rerank_score')} for h in context['sources']],
            document_embeddings_recomputed=0)
        save_json('data/day25/focused_query_local_regression.json',report)
        print('PASS: complaints absent; final context sources:',len(context['sources']),flush=True)
        for source in report['sources']:print(source,flush=True)
    finally:pipeline.close()


if __name__=='__main__':main()
