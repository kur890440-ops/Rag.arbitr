"""Day29: one retrieval per question; subsequent variants replay frozen evidence locally."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.local_llm import LocalLLMProvider

OUT = Path('data/day29')
RUN = 'd6575efd47b349f1a8a733bb2bc50d2c'

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)

from rag_arbiter.application.benchmark_resources import Resources

def freeze(cfg):
    from rag_arbiter.llm import LocalLLMConfig
    cfg.llm.local = LocalLLMConfig.model_validate(json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))['config'])
    runs = RunService(cfg)
    service = RAGComparisonService(runs)
    # Reuse Day28 questions, reordered: fact, cross-document analysis, duties/reporting.
    old = [json.loads(Path(f'data/day28/q{i}-r1.json').read_text(encoding='utf-8')) for i in (1, 3, 2)]
    with runs.db() as store:
        old.append(store.get('rag_comparison_runs', '59a874a5e45f46cebbba29a2bc987b7a'))
    old.append(dict(question_text='Кто указан конкурсным управляющим ООО «РУСМЕТ» и кто — ОО «Авиакомпания «ВИМ-АВИА»? Укажи источники.'))
    try:
        for number, prior in enumerate(old, 1):
            path = OUT / f'q{number}-snapshot.json'
            if path.exists(): continue
            attempts = []
            class Measured(LocalLLMProvider):
                def generate(self, request):
                    original = self.transport
                    def transport(endpoint, *args, **kwargs):
                        data, raw = original(endpoint, *args, **kwargs)
                        if endpoint == '/api/chat':
                            attempts.append({k: data[k] for k in ('prompt_eval_count','eval_count','prompt_eval_duration','eval_duration','load_duration','total_duration') if k in data})
                        return data, raw
                    self.transport = transport
                    try: return super().generate(request)
                    finally: self.transport = original
            with patch('rag_arbiter.local_llm.LocalLLMProvider', Measured), Resources() as resources:
                record = service.prepare(RUN, question=prior['question_text'], generation_mode='local', rag_pipeline_mode='BASELINE' if number >= 4 else 'RERANK', max_context_sources=5)
                service.put(record)
                result = service.execute(record['comparison_run_id'])
            if not result.get('context_text') or not result.get('generation_runs'):
                save(OUT / f'q{number}-freeze-error.json', result)
                raise RuntimeError('Retrieval/baseline failed; inspect local artifact')
            g = result['generation_runs'][0]
            snapshot = dict(question_id=f'Q{number}', question=result['question_text'], retrieval_run_id=result['comparison_run_id'],
                processing_run_id=RUN, context_snapshot_id=result['context_snapshot_id'], context_ids=result['used_chunk_ids_json'],
                context_size=result['context_tokens'], text=result['context_text'], sources=result['sources'],
                shared_pipeline_ms=g['shared_pipeline_ms'], claim_support_threshold=result['claim_support_threshold'],
                reranker_settings=result['reranker_settings'], context_budget=result['context_token_budget'])
            snapshot['integrity_sha256'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            save(path, snapshot)
            save(OUT / f'q{number}-baseline-record.json', result)
            save(OUT / f'q{number}-baseline.json', dict(question_id=f'Q{number}', variant='baseline', profile=cfg.llm.local.model_dump(),
                **g, attempts=attempts, resources=resources.summary()))
            print(f'Q{number} baseline: {g["grounding_status"]}, {g["generation_ms"]:.0f} ms, repairs={g["repair_count"]}', flush=True)
    finally: runs.close()

def main():
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['freeze', 'variant', 'paired']); parser.add_argument('--name'); parser.add_argument('--overrides', default='{}')
    parser.add_argument('--keep-alive', choices=['0','60s'])
    parser.add_argument('--temperature', type=float)
    parser.add_argument('--context-window', type=int)
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--prompt-version', choices=['day28-baseline','day29-legal-v1','day29-legal-v2','day29-legal-v3'])
    args = parser.parse_args(); cfg = Config.load()
    if args.action == 'variant':
        import re
        if not args.name or not re.fullmatch(r'[a-z0-9-]+', args.name):
            parser.error('--name must use lowercase letters, digits or hyphens')
    if args.action == 'freeze': freeze(cfg)
    else:
        from rag_arbiter.application.local_benchmark import replay_snapshot
        from rag_arbiter.llm import LocalLLMConfig
        from rag_arbiter.reranking import LocalReranker
        baseline = json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
        overrides = json.loads(args.overrides)
        overrides.update({key:getattr(args,key) for key in ('temperature','context_window','max_output_tokens','prompt_version') if getattr(args,key) is not None})
        if args.keep_alive is not None: overrides['keep_alive'] = 0 if args.keep_alive == '0' else args.keep_alive
        profile = LocalLLMConfig.model_validate(baseline['config'] | overrides)
        baseline_profile = LocalLLMConfig.model_validate(baseline['config'])
        info = LocalLLMProvider(profile).inspect()
        if info['digest'] != baseline['model']['digest']:
            raise RuntimeError('Installed model changed since frozen baseline')
        runs = RunService(cfg); reranker = LocalReranker(cfg.reranker)
        try:
            reranker.load()
            variants = [('baseline-confirm', baseline_profile), ('optimized', profile)] if args.action == 'paired' else [(args.name, profile)]
            for number in (1,2,3,4,5):
                if not (OUT/f'q{number}-snapshot.json').exists(): continue
                for name, selected in variants:
                    target=OUT/f'q{number}-{name}.json'
                    if target.exists(): continue
                    snapshot=json.loads((OUT/f'q{number}-snapshot.json').read_text(encoding='utf-8'))
                    with Resources() as resources:
                        result=replay_snapshot(runs,snapshot,selected,reranker)
                    result.update(variant=name,resources=resources.summary())
                    save(target,result)
                    print(f'Q{number} {name}: {result["grounding_status"]}, {result["generation_ms"]:.0f} ms, repairs={result["repair_count"]}',flush=True)
        finally: runs.close()

if __name__ == '__main__': main()
