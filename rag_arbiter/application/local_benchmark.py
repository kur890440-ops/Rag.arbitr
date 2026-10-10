"""Replay frozen retrieval evidence through the existing local grounding pipeline."""
import copy
from contextlib import nullcontext
import hashlib
import json
import time
from ..llm import LLMRequest, LocalLLMConfig
from ..local_llm import LocalLLMProvider
from .grounding import CitationBuilder, grounded_generation


def snapshot_digest(snapshot):
    payload = {k: v for k, v in snapshot.items() if k != 'integrity_sha256'}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verified_snapshot(snapshot):
    snapshot = copy.deepcopy(snapshot)
    if snapshot_digest(snapshot) != snapshot.get('integrity_sha256'):
        raise ValueError('Frozen context integrity mismatch')
    if len(snapshot['text'].encode('utf-8')) != snapshot['context_size']:
        raise ValueError('Frozen context size mismatch')
    if not snapshot['sources']:
        raise ValueError('Frozen context has no sources')
    return snapshot


def replay_snapshot(runs, snapshot, profile, reranker, provider_factory=LocalLLMProvider, capture_diagnostics=False):
    """No retriever, embedding call, source selection or prompt/context truncation here."""
    snapshot = verified_snapshot(snapshot)
    profile = LocalLLMConfig.model_validate(profile.model_dump())
    started = time.perf_counter()
    provider = provider_factory(profile, operation_lock=runs.operation_lock, ocr_model=runs.config.recognition.model)
    branch = {'sources': copy.deepcopy(snapshot['sources'])}
    attempts = []
    def generate(context, context_type):
        request = LLMRequest(capture_diagnostics=capture_diagnostics,diagnostic_rag_context=snapshot['text'] if capture_diagnostics else None,question=snapshot['question'], context=context, context_type=context_type,
                             prompt_version=profile.prompt_version)
        result = provider.generate(request).model_dump()
        attempts.append(dict(status=result['status'], error=result['error'], duration_ms=result['duration_ms'],
            usage=copy.deepcopy(result['usage']), diagnostics=copy.deepcopy(result['diagnostics']),
            prompt_bytes=sum(len(m['content'].encode('utf-8')) for m in request.messages()),
            # Initial and repair raw contract retained locally for reproducible quality review.
            raw_text=result['text']))
        return result
    session = provider.generation_session() if hasattr(provider, 'generation_session') else nullcontext()
    with session, runs.db() as store:
        result = grounded_generation(branch, snapshot['text'], generate, CitationBuilder(store), reranker,
            snapshot['claim_support_threshold'],
            repair_context_limit=len(snapshot['text'].encode('utf-8')) + profile.repair_reserve)
    cleanup_error = getattr(provider, 'cleanup_error', None)
    branch_ms = (time.perf_counter() - started) * 1000
    branch.pop('sources')
    return dict(**branch, cleanup_error=cleanup_error, question_id=snapshot['question_id'], profile=profile.model_dump(),
        retrieval_run_id=snapshot['retrieval_run_id'], context_snapshot_id=snapshot['context_snapshot_id'],
        integrity_sha256=snapshot['integrity_sha256'], context_ids=snapshot['context_ids'],
        source_ids=[s['reference'] for s in snapshot['sources']], context_size=snapshot['context_size'],
        provider='local', model=profile.model, prompt_version=profile.prompt_version,
        result=result, answer=result['text'], generation_ms=result['duration_ms'],
        shared_pipeline_ms=snapshot['shared_pipeline_ms'], branch_ms=branch_ms,
        total_ms=snapshot['shared_pipeline_ms']+branch_ms,
        total_ms_definition='frozen retrieval duration + measured generation/grounding branch',
        claims_count=len(branch.get('claims_json', [])), citations_count=len(branch.get('citations_json', [])),
        repair_count=int(branch.get('repair_used', False)), error=result['error'], attempts=attempts)


def same_evidence(item, snapshot):
    """Reject mismatched recorded evidence; legacy runs may omit newer metadata."""
    if item['context_snapshot_id'] != snapshot['context_snapshot_id'] or item['context_ids'] != snapshot['context_ids']:
        raise ValueError('Comparison snapshot mismatch')
    for key in ('question_id', 'retrieval_run_id', 'integrity_sha256', 'context_size', 'retrieval_policy_json', 'reranker_settings'):
        if key in item and item[key] != snapshot.get(key):
            raise ValueError('Comparison evidence mismatch: ' + key)
    for key in ('question', 'question_text'):
        if key in item and item[key] != snapshot['question']:
            raise ValueError('Comparison question mismatch')
    if 'source_ids' in item and item['source_ids'] != [s['reference'] for s in snapshot['sources']]:
        raise ValueError('Comparison source order mismatch')


def measured_number(value):
    import math
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def metric_delta(before, after):
    before, after = measured_number(before), measured_number(after)
    return dict(before=before, after=after,
                percent=(after-before)/before*100 if before and after is not None else None)


def demo_variant(item, label, note, baseline):
    from .generation_result import GenerationResult
    view = GenerationResult.from_record(dict(status='COMPLETED', generation_runs=[item]), 'local').model_dump()
    profile = dict(item.get('profile') or {})
    diagnostics = item['result'].get('diagnostics') or {}
    # Only recorded settings, including the frozen pre-change manifest for legacy runs.
    if label in ('LOCAL BASELINE', 'baseline') and (not profile.get('prompt_version') or 'temperature' not in profile):
        original = baseline.get('config', {}) | baseline.get('explicit_options', {})
        if 'temperature' not in profile: profile['temperature'] = original.get('temperature')
        if not profile.get('prompt_version'): profile['prompt_version'] = baseline.get('prompt_version')
    quantization = (diagnostics.get('details') or {}).get('quantization_level')
    if not quantization and profile.get('model') == baseline.get('model', {}).get('model'):
        quantization = baseline['model'].get('details', {}).get('quantization_level')
    resources = item.get('resources') or {}
    rss = measured_number(resources.get('rss_peak_bytes'))
    metrics = dict(generation_ms=measured_number(item.get('generation_ms')), total_ms=measured_number(item.get('total_ms')),
        claims_count=measured_number(item.get('claims_count', len(item['claims_json']) if 'claims_json' in item else None)),
        citations_count=view['citations_count'], repair_count=view['repair_count'],
        output_tokens=measured_number((item['result'].get('usage') or {}).get('completion_tokens')),
        ram_mib=rss/1024**2 if rss is not None else None,
        vram_mib=measured_number(resources.get('device_vram_peak_mib')))
    return dict(label=label, profile=profile, run=view, quantization=quantization,
                metrics=metrics, quality_note=note)


def load_demo(directory, processing_run_id):
    """Read-only view of saved measurements; never retrieves, reranks or generates."""
    from pathlib import Path
    directory = Path(directory)
    def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
    def optional(name):
        try:
            value = read(directory / name)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError): return {}
    notes, baseline = optional('quality.json'), optional('baseline.json')
    questions = []
    for path in sorted(directory.glob('q*-snapshot.json')):
        try:
            snapshot = verified_snapshot(read(path))
            if snapshot['processing_run_id'] != processing_run_id: continue
            prefix = path.name.removesuffix('-snapshot.json')
            variants = []
            artifacts = []
            # A new UI pair becomes primary only after both branches were saved.
            complete = [p for p in directory.glob(f'{prefix}-ui-*-optimized.json')
                        if p.with_name(p.name.replace('-optimized.json', '-baseline.json')).exists()]
            latest = max(complete, key=lambda p: p.stat().st_mtime_ns) if complete else None
            for name, label in [('baseline', 'LOCAL BASELINE'), ('optimized', 'LOCAL OPTIMIZED')]:
                artifact = 'baseline-confirm' if name == 'baseline' and (directory / f'{prefix}-baseline-confirm.json').exists() else name
                if latest:
                    artifact = latest.stem[len(prefix)+1:].removesuffix('-optimized') + '-' + name
                item = read(directory / f'{prefix}-{artifact}.json')
                same_evidence(item, snapshot)
                artifacts.append(artifact)
                variants.append(demo_variant(item, label, notes.get(f'{prefix}-{artifact}', ''), baseline))
            a, b = variants
            deltas = {key: metric_delta(a['metrics'][key], b['metrics'][key]) for key in a['metrics']}
            experiments = []
            for experiment in sorted(directory.glob(f'{prefix}-*.json')):
                name = experiment.stem[len(prefix)+1:]
                if name in ('snapshot', 'baseline-record', *artifacts): continue
                try:
                    item = read(experiment)
                    if 'profile' not in item: continue
                    same_evidence(item, snapshot)
                    experiments.append(demo_variant(item, name, '', baseline))
                except (OSError, ValueError, KeyError, TypeError): continue
            questions.append(dict(snapshot=snapshot, variants=variants, deltas=deltas, experiments=experiments))
        except (OSError, ValueError, KeyError, TypeError):
            # An invalid pair is never presented as a shared-context comparison.
            continue
    return questions
