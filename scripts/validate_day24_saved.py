"""Local-only revalidation of saved Day24 responses; never calls an LLM API."""
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.grounding import AnswerContract,CitationBuilder,GroundingValidator,REFUSAL,evaluate_grounding
from rag_arbiter.reranking import LocalReranker


def main():
    cfg=Config.load();runs=RunService(cfg);provider=LocalReranker(cfg.reranker);out=Path('data/day24');records=[]
    try:
        with runs.db() as store:
            for path in sorted(out.glob('[0-9][0-9].json')):
                r=json.loads(path.read_text(encoding='utf-8'))
                claims=r.get('claims_json') or next((d['claims'] for d in reversed(r.get('grounding_diagnostics',[])) if d.get('claims')),[])
                if claims:
                    was_grounded=r.get('grounding_status')=='GROUNDED'
                    prior=r['grounding_result']
                    answer=AnswerContract(answer=' '.join(c['text'] for c in claims),claims=claims)
                    g,c=GroundingValidator(CitationBuilder(store),provider,r['claim_support_threshold']).validate(answer,r['sources'])
                    g.repair_used=r.get('repair_used',False)
                    r.setdefault('grounding_diagnostics',[]).append(dict(local_revalidation=True,result=g.model_dump()))
                    r['claims_json']=[c.model_dump() for c in answer.claims]
                    if not was_grounded:
                        g.grounded=False
                        g.coverage_valid=prior.get('coverage_valid',False)
                        g.refusal_reason=prior.get('refusal_reason')
                    elif not g.grounded:
                        g.refusal_reason='LOCAL_REVALIDATION_FAILED_NO_ADDITIONAL_REPAIR'
                        r.update(grounding_status='INSUFFICIENT_CONTEXT',rag_answer=REFUSAL,citations_json=[])
                        r['rag_result'].update(text=REFUSAL,status='INSUFFICIENT_CONTEXT',error={})
                    else:r['citations_json']=[x.model_dump() for x in c]
                    r['grounding_result']=g.model_dump()
                if r.get('grounding_status')=='INSUFFICIENT_CONTEXT':
                    r['grounding_result'].update(sources_count=0,citations_count=0)
                g=r.get('grounding_result',{})
                for k in ('claims_total','claims_supported','claims_unsupported','refusal_reason','repair_used'):
                    if k in g:r[k]=g[k]
                original=store.get('rag_comparison_runs',r['comparison_run_id'])
                store.put('rag_comparison_runs',r['comparison_run_id'],{**original,**r})
                path.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');records.append(r)
                print(json.dumps(dict(number=path.stem,status=r['grounding_status'],grounding=g)),flush=True)
            metrics=dict(controls=evaluate_grounding(records[:10]),probes=evaluate_grounding(records[10:]),all=evaluate_grounding(records))
            (out/'evaluation.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
            for batch in store.all('rag_batches'):
                if batch.get('day24_evaluation'):
                    batch['grounding_metrics']=metrics;store.put('rag_batches',batch['batch_id'],batch)
            print(json.dumps(metrics),flush=True)
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
