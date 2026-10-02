"""One explicit real reference-extraction call for a failed saved packet.

Stores a successful validated result in the existing compatible map cache.
Does not rerun other documents or final synthesis.
"""
import argparse
import json
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.exhaustive import (ExhaustiveNoRAG, ExhaustiveContextBatcher,
    referenced_retry, resolve_references, hash_value, MAP_VERSION, BASE_SYSTEM)
from rag_arbiter.llm import MiniMaxLLMProvider


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('comparison');parser.add_argument('batch')
    args=parser.parse_args()
    runs=RunService(Config.load())
    try:
        with runs.db() as db:
            record=db.get('rag_comparison_runs',args.comparison)
        cfg=Config(**record['config_snapshot']).llm
        cfg.api_key=runs.config.llm.api_key;cfg.temperature=cfg.exhaustive_map_temperature
        svc=ExhaustiveNoRAG(runs,None)
        batcher=ExhaustiveContextBatcher(cfg,record['question_text'])
        batch=next(b for b in batcher.build(svc.load(record['processing_run_id'],record['document_id'])) if b.batch_id==args.batch)
        request,quotes=referenced_retry(batcher,batch)
        assert request,'Reference request exceeds budget'
        response=MiniMaxLLMProvider(cfg).generate(request)
        diagnostic=dict(status=response.status,error=response.error,usage=response.usage,duration_ms=response.duration_ms,
            batch_id=batch.batch_id,files=batch.file_titles,evidence_mode='reference_v2',response=response.text)
        out=Path('data/day22/exhaustive/reference-repair.json')
        out.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
        if response.status!='SUCCESS':print(json.dumps({k:diagnostic[k] for k in ('status','error')}));return
        try:result=svc.parse(resolve_references(response.text,quotes),batch)
        except Exception:
            print('INVALID_REFERENCE_EXTRACTION; diagnostic saved; cache unchanged');return
        cachekey=hash_value([hash_value(record['question_text']),batch.content_hash,cfg.model_dump(exclude={'exhaustive_synthesis_budget'}),MAP_VERSION,batch.normalization_version,BASE_SYSTEM])
        with runs.db() as db:db.put('exhaustive_map_cache',cachekey,result.model_dump())
        diagnostic.update(validated=True,findings=len(result.findings),cache_key=cachekey)
        out.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status=result.status,findings=len(result.findings),cached=True,usage=response.usage,duration_ms=response.duration_ms)))
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
