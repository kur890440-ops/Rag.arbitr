"""Complete the explicitly approved Day23 generation tests from saved final contexts.

Reuses local retrieval/reranking artifacts; never invokes exhaustive processing.
"""
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.application.views import ResultsService
from rag_arbiter.documents import now,digest
from rag_arbiter.llm import LLMRequest,MiniMaxLLMProvider


def main():
    out=Path('data/day23');results=json.loads((out/'comparison.json').read_text(encoding='utf-8'))
    runs=RunService(Config.load());service=RAGComparisonService(runs)
    try:
        for public in results:
            record=service.get(public['comparison_run_id'],public=False)
            if record['rag_result']['status']!='NOT_RUN':continue
            snapshot=ResultsService(runs).snapshot(record['processing_run_id'])
            assert snapshot['indexes'][record['retrieval_strategy']]['run_id']==record['chunking_run_id'],'Index changed'
            assert record['point_only'] and record['context_text'] and record['no_rag_result'] is None
            cfg=Config(**record['config_snapshot']).llm;cfg.api_key=runs.config.llm.api_key
            response=MiniMaxLLMProvider(cfg).generate(LLMRequest(question=record['original_question'],context=record['context_text']))
            record.update(rag_result=response.model_dump(),rag_answer=response.text,rag_duration_ms=response.duration_ms,
                finished_at=now(),status='COMPLETED' if response.status=='SUCCESS' else 'FAILED',error_json=[response.error] if response.error else [])
            record['minimax_requests_count']+=response.request_count
            service.put(record)
            public.update(service.get(record['comparison_run_id']))
            (out/'comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(id=record['comparison_run_id'],mode=record['rag_pipeline_mode'],threshold=record['rerank_threshold'],
                status=response.status,duration_ms=response.duration_ms,usage=response.usage)),flush=True)
            if response.error.get('code') in ('AUTH_ERROR','PAYMENT_REQUIRED'):break
        selected=[r for r in results if r['rerank_threshold']==.1]
        if len(selected)==6 and all(r['status']=='COMPLETED' for r in selected):
            ids=[r['comparison_run_id'] for r in selected]
            service.put(dict(batch_id='day23-'+digest(ids)[:24],processing_run_id=selected[0]['processing_run_id'],
                day23_evaluation=True,status='COMPLETED',comparison_ids=ids,total=6,current=6,completed=6,
                failed=0,partial=0,created_at=now(),finished_at=now()),'rag_batches')
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
