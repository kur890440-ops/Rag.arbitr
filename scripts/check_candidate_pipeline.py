"""Local real-corpus retrieval diagnostic; never calls an external LLM."""
import json
import sys
import time
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService, RAGContextBuilder
from rag_arbiter.application.context_selection import CandidateProcessor, ContextSelector
from rag_arbiter.retrieval import SemanticRetriever

RUN='d6575efd47b349f1a8a733bb2bc50d2c'
EXPECTED='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'
QUESTIONS=[('factual','Кто указан заявителем в ходатайстве ООО «РУСМЕТ» от 24 февраля 2025 года?'),
           ('corpus','Что обычно пишут в резолютивной части в этих документах?')]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    runs=RunService(Config.load());service=RAGComparisonService(runs)
    out=Path('data/day22/candidates');out.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        record=service.prepare(RUN,question=QUESTIONS[0][1])
        cfg=Config(**record['config_snapshot'])
        with runs.operation_lock():
            pipeline=runs.pipeline_factory(cfg)
            try:
                pipeline.resources()
                for label,question in QUESTIONS:
                    started=time.perf_counter();vector=pipeline.provider.encode([question])[0]
                    embedding_ms=(time.perf_counter()-started)*1000
                    for strategy in ('structure','fixed'):
                        record=service.prepare(RUN,question=question,strategy=strategy)
                        index=record['index_snapshot']
                        for n in (5,20):
                            started=time.perf_counter()
                            hits=SemanticRetriever(pipeline.provider,pipeline.vectors,pipeline.store).retrieve_vector(vector,index,n)
                            candidates,stats=CandidateProcessor(pipeline.store,index,strategy,cfg.context_expansion_budget).build(hits)
                            selected=ContextSelector().select(candidates,5,cfg.llm.context_budget,RAGContextBuilder(),cfg.context_diversity_penalty)
                            context=RAGContextBuilder().build(selected,cfg.llm.context_budget)
                            result=dict(question=question,label=label,strategy=strategy,candidate_top_n=n,max_context_sources=5,
                                expected_source_rank=next((h['rank'] for h in hits if h['document_id']==EXPECTED),None),
                                candidates_retrieved=len(hits),**stats,contexts_used=len(context['sources']),context_tokens=context['token_count'],
                                candidate_documents=len({h['document_id'] for h in hits}),context_documents=len({h['document_id'] for h in context['sources']}),
                                embedding_ms=embedding_ms,retrieval_processing_ms=(time.perf_counter()-started)*1000,
                                sources=context['sources'],context=context['text'])
                            results.append(result)
                            print(json.dumps({k:v for k,v in result.items() if k not in ('sources','context','dedup_diagnostics','question')},ensure_ascii=False),flush=True)
            finally:pipeline.close()
        (out/'local-retrieval.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
