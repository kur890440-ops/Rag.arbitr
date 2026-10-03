"""Use the ONE unused repair of the multi-claim probe; no other generations."""
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.grounding import CitationBuilder,grounded_generation
from rag_arbiter.reranking import LocalReranker
from rag_arbiter.llm import MiniMaxLLMProvider,LLMRequest


def main():
    cfg=Config.load();runs=RunService(cfg);path=Path('data/day24/11.json')
    try:
        with runs.db() as store:
            public=json.loads(path.read_text(encoding='utf-8'))
            r=store.get('rag_comparison_runs',public['comparison_run_id'])
            assert not r.get('repair_used') and r['grounding_status']=='INSUFFICIENT_CONTEXT'
            previous=r['grounding_diagnostics'];prior=r['rag_result'];provider=MiniMaxLLMProvider(cfg.llm)
            context=r['context_text']+'\nREPAIR: используй только подтвержденные факты; не повторяй дату вопроса в утверждении об имени.\n'+json.dumps(dict(claims=r['claims_json'],validation=r['grounding_result']),ensure_ascii=False)
            def generate(context,kind):
                r['generation_call_count']+=1
                result=provider.generate(LLMRequest(question=r['question_text'],context=context,context_type=kind)).model_dump()
                r['minimax_requests_count']+=result['request_count']
                return result
            result=grounded_generation(r,context,generate,CitationBuilder(store),LocalReranker(cfg.reranker),r['claim_support_threshold'],repair_only=True)
            result['request_count']+=prior['request_count'];result['duration_ms']+=prior['duration_ms']
            for k,v in prior['usage'].items():result['usage'][k]=result['usage'].get(k,0)+v
            r.update(rag_result=result,rag_answer=result['text'],rag_duration_ms=result['duration_ms'])
            r['grounding_diagnostics']=previous+r['grounding_diagnostics']
            store.put('rag_comparison_runs',r['comparison_run_id'],r)
            safe={k:v for k,v in r.items() if k not in ('config_snapshot','index_snapshot','owner_pid','owner_started')}
            path.write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(r['grounding_result']),flush=True)
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
