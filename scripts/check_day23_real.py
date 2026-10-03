"""Point-only Day23 experiments. Default: local retrieval/rerank, question-only rewrite.

--answers transmits final selected contexts and requires corpus approval.
No full-document or exhaustive calls in either mode.
"""
import argparse
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.llm import MiniMaxLLMProvider,LLMResult

RUN='d6575efd47b349f1a8a733bb2bc50d2c'
FACT='Кто указан заявителем в ходатайстве ООО «РУСМЕТ» от 24 февраля 2025 года?'
HARD='Кто там от РУСМЕТа подал ходатайство 24 февраля 2025-го, как её зовут?'
DOC='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--answers',action='store_true')
    args=parser.parse_args();cfg=Config.load();runs=RunService(cfg)
    class ControlledProvider:
        def __init__(self,config):self.provider=MiniMaxLLMProvider(config);self.model=config.model
        def generate(self,request):
            if request.context is not None and not args.answers:
                return LLMResult(model=self.model,status='NOT_RUN',error={'code':'AWAITING_CORPUS_APPROVAL'})
            return self.provider.generate(request)
    service=RAGComparisonService(runs,ControlledProvider)
    out=Path('data/day23');out.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        for label,question in [('factual',FACT),('hard',HARD)]:
            for mode in ('BASELINE','RERANK','REWRITE_RERANK'):
                thresholds=(.01,.1,.5) if label=='factual' and mode=='RERANK' else (.1,)
                for threshold in thresholds:
                    record=service.prepare(RUN,question=question,rag_pipeline_mode=mode,rerank_threshold=threshold,point_only=True)
                    record['expected_sources']=[dict(document_id=DOC,page_start=1)]
                    service.put(record);service.execute(record['comparison_run_id'])
                    r=service.get(record['comparison_run_id']);results.append(r)
                    (out/f'{label}-{mode}-{threshold}.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
                    summary=dict(label=label,mode=mode,threshold=threshold,id=r['comparison_run_id'],rag_status=r['rag_result']['status'],
                        rewrite=r['rewrite_status'],rank=r['source_metrics'].get('expected_source_rank'),retrieved=r['candidates_retrieved'],
                        cleaned=r['candidates_after_cleanup'],accepted=r['candidates_after_rerank'],contexts=r['contexts_used'],tokens=r['context_tokens'],
                        retrieval_ms=r['vector_retrieval_duration_ms'],rerank_ms=r['rerank_duration_ms'],rewrite_ms=r['rewrite_duration_ms'],generation_ms=r['rag_duration_ms'])
                    print(json.dumps(summary),flush=True)
        (out/'comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
