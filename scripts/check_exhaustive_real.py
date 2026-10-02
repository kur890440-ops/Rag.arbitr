"""Requested Day22 diagnostic: order test on 3 documents, one corpus comparison."""
import json
import random
import sys
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.application.exhaustive import ExhaustiveNoRAG, ExhaustiveContextBatcher, ExhaustiveEvidenceMerger, hash_value
from rag_arbiter.llm import MiniMaxLLMProvider

sys.stdout.reconfigure(encoding='utf-8')
OUT=Path('data/day22/exhaustive');OUT.mkdir(parents=True,exist_ok=True)
SOURCE='d6575efd47b349f1a8a733bb2bc50d2c'
QUESTION='Что обычно пишут в резолютивной части в этих документах?'
SMALL={'f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29',
       '68ed8b9dc98f48bc28931da88254459430ca3d9c591ad562ae464ded84ee2daa',
       '5e8ab681f205aae711f608f50ae1056e63b39bd3f286f9e346045fe5cc3ee283'}

def main():
    cfg=Config.load();runs=RunService(cfg)
    try:
        service=ExhaustiveNoRAG(runs,MiniMaxLLMProvider)
        mapcfg=cfg.llm.model_copy(deep=True);mapcfg.temperature=mapcfg.exhaustive_map_temperature
        small=[d for d in service.load(SOURCE,None) if d['document_id'] in SMALL]
        assert len(small)==3
        batcher=ExhaustiveContextBatcher(mapcfg,QUESTION);batches=batcher.build(small)
        provider=MiniMaxLLMProvider(mapcfg);order_results=[]
        for label,order in [('A',batches),('B',list(reversed(batches))),('C',random.Random(42).sample(batches,len(batches)))]:
            results=[]
            for batch in order:
                result=None
                for attempt in range(mapcfg.exhaustive_map_retries+1):
                    response=provider.generate(batcher.request(batch.units))
                    (OUT/f'{label}-{batch.batch_id[:12]}-{attempt}.json').write_text(json.dumps(response.model_dump(),ensure_ascii=False,indent=2),encoding='utf-8')
                    if response.status!='SUCCESS':break
                    try:result=service.parse(response.text,batch);break
                    except Exception:
                        (OUT/f'{label}-{batch.batch_id[:12]}-units.json').write_text(batch.model_dump_json(indent=2),encoding='utf-8')
                if response.status!='SUCCESS':
                    evidence=dict(status='BLOCKED',order=label,error=response.error,model=response.model)
                    (OUT/'order.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps(evidence),flush=True)
                    # No bulk retries for a provider-wide billing/auth failure.
                    if response.error.get('code') in ('PAYMENT_REQUIRED','AUTH_ERROR','NOT_CONFIGURED'):return
                    raise RuntimeError('Order diagnostic provider failure')
                if result is None:
                    evidence=dict(status='BLOCKED',order=label,error={'code':'INVALID_EXTRACTION'},model=response.model)
                    (OUT/'order.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8');print(json.dumps(evidence),flush=True);return
                results.append(result)
                print(json.dumps({'order':label,'batch':batch.batch_id[:8],'findings':len(result.findings)}),flush=True)
            merged=ExhaustiveEvidenceMerger().merge(results,len(small))
            order_results.append(dict(order=label,merged=merged,coverage=dict(documents=3,pages=sum(d['page_count'] for d in small)),digest=hash_value(merged)))
            (OUT/f'order-{label}.json').write_text(json.dumps(order_results[-1],ensure_ascii=False,indent=2),encoding='utf-8')
        invariance=dict(status='PASS' if len({r['digest'] for r in order_results})==1 else 'MODEL_VARIATION',digests=[r['digest'] for r in order_results])
        (OUT/'order.json').write_text(json.dumps(invariance,indent=2),encoding='utf-8');print(json.dumps(invariance),flush=True)
        if '--order-only' in sys.argv:return
        comparison=RAGComparisonService(runs)
        key=comparison.start(SOURCE,question=QUESTION,background=True)
        (OUT/'comparison-id.txt').write_text(key)
        print(json.dumps({'comparison_id':key}),flush=True)
        comparison.futures[key].result()
        result=comparison.get(key)
        (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':result['status'],'exhaustive':{k:v for k,v in result.get('exhaustive',{}).items() if k not in ('merged_findings','batch_diagnostics')},'full_answer':result['full_document_answer'],'rag_answer':result['rag_answer'],'no_context_answer':result['no_rag_answer']},ensure_ascii=False),flush=True)
    finally:runs.close()

if __name__=='__main__':main()
