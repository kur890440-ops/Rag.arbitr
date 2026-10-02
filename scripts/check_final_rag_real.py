"""Opt-in external test of all 16 normalized documents. Requires corpus approval.

Run with --approved-corpus only after explicit consent for both corpus tests.
The 5-candidate variation reuses the 20-candidate comparison's exhaustive result.
"""
import argparse
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.llm import LLMRequest, MiniMaxLLMProvider
from scripts.check_candidate_pipeline import RUN, QUESTIONS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-corpus',action='store_true')
    args=parser.parse_args()
    if not args.approved_corpus:parser.error('Explicit approval for all 16 documents is required')
    out=Path('data/day22/candidates');out.mkdir(parents=True,exist_ok=True)
    local=json.loads((out/'local-retrieval.json').read_text(encoding='utf-8'))
    cfg=Config.load();runs=RunService(cfg);service=RAGComparisonService(runs)
    try:
        for label,question in QUESTIONS:
            key=service.start(RUN,question=question,candidate_top_n=20,max_context_sources=5,background=False)
            result=service.get(key)
            (out/f'real-{label}-20.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(label=label,comparison_id=key,status=result['status'])),flush=True)
            if result['no_rag_result'].get('error',{}).get('code') in ('AUTH_ERROR','PAYMENT_REQUIRED','NOT_CONFIGURED'):break
            if label=='factual':
                variant=next(r for r in local if r['label']==label and r['strategy']=='structure' and r['candidate_top_n']==5)
                # Ensure the baseline local artifact matches this live retrieval,
                # rejecting a stale corpus/index/config diagnostic.
                reference=next(r for r in local if r['label']==label and r['strategy']=='structure' and r['candidate_top_n']==20)
                if reference['context']!=result['context_text']:
                    raise RuntimeError('Local diagnostic is stale; rerun local retrieval first')
                answer=MiniMaxLLMProvider(cfg.llm).generate(LLMRequest(question=question,context=variant['context']))
                variant={**variant,'answer':answer.model_dump(),'exhaustive_reused_from':key}
                (out/'real-factual-5.json').write_text(json.dumps(variant,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
