"""Bounded, sequential page recovery; every attempt is persisted before the next call."""
import json
import time
from pathlib import Path
from uuid import uuid4
from PIL import Image
from ..documents import save_json, file_hash


def merge_fragments(results, overlap=True):
    merged = results[0].model_copy(deep=True)
    merged.blocks = []
    merged.tables = []
    for result in results:
        blocks = [b.model_copy(deep=True) for b in result.blocks]
        # Remove only exact suffix/prefix matches at the adjacent region boundary.
        # A repeated word elsewhere on a page is not an overlap duplicate.
        if overlap:
            for n in range(min(len(merged.blocks), len(blocks)), 0, -1):
                if [b.text for b in merged.blocks[-n:]] == [b.text for b in blocks[:n]]:
                    blocks = blocks[n:]
                    break
        for block in blocks:
            old = block.order
            block.order = len(merged.blocks)
            merged.blocks.append(block)
            for table in result.tables:
                if table.order == old:
                    merged.tables.append(table.model_copy(update={'order': block.order}))
    merged.normalized_text = '\n\n'.join(b.text for b in merged.blocks)
    merged.reading_order = [b.order for b in merged.blocks]
    merged.status = 'UNCERTAIN' if any(r.status == 'UNCERTAIN' for r in results) else 'SUCCESS'
    merged.docling_document = None  # Fragment-local geometry is never presented as page geometry.
    merged.diagnostics['reading_order_status'] = 'UNCERTAIN' if any(r.diagnostics.get('reading_order_status') == 'UNCERTAIN' for r in results) else 'REPAIRED'
    return merged


def recover_page(provider, request):
    started = time.perf_counter()
    cfg = provider.config.recognition
    root = (request.attempt_dir or provider.config.cache_path / 'recognition-attempts' / request.document_id / str(request.page_number)) / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    attempts = []
    original = getattr(provider.runtime, 'settings', None)

    def attempt(req, kind, settings=None):
        if settings is not None and original is not None:
            provider.runtime.settings = settings
        result = provider.recognize_once(req)
        number = len(attempts) + 1
        raw = root / f'{number:02}-{kind}.raw'
        raw.write_text(result.raw_output, encoding='utf-8')
        meta = dict(attempt=number, kind=kind, page_number=req.page_number,
                    fragment_id=req.fragment_id, coordinates=req.region, provider=result.provider,
                    status=result.status, duration_ms=result.duration_ms, raw_output_path=str(raw.resolve()),
                    configured_max_output=(settings or cfg).max_generation_tokens,
                    configured_context=(settings or cfg).context_tokens)
        meta.update(result.diagnostics)
        attempts.append(meta)
        save_json(raw.with_suffix('.json'), {**meta, 'normalized': result.model_dump(exclude={'raw_output','docling_document'})})
        return result

    try:
        result = attempt(request, 'initial')
        for _ in range(cfg.recovery_retries):
            if result.status != 'FAILED':
                break
            truncated = result.diagnostics.get('done_reason') == 'length' or result.diagnostics.get('done') is False
            if truncated and cfg.split_enabled and cfg.split_after_first_truncation:
                break
            settings = cfg.model_copy(deep=True)
            if truncated:
                settings.max_generation_tokens = max(cfg.max_generation_tokens, cfg.recovery_output_tokens)
                settings.context_tokens = max(cfg.context_tokens, cfg.recovery_context_tokens)
            if 'out of memory' in result.diagnostics.get('error','').lower() or 'oom' in result.diagnostics.get('error','').lower():
                provider.runtime.close()
                settings.max_image_resolution = cfg.oom_image_resolution
            result = attempt(request, 'retry', settings)
        truncated = result.diagnostics.get('done_reason') == 'length' or result.diagnostics.get('done') is False
        fragmented = False
        if result.status == 'FAILED' and truncated and cfg.split_enabled:
            fragments = []
            with Image.open(request.image_path) as image:
                w,h = image.size
                extra = int(h * cfg.split_overlap / 2)
                regions = [(0,0,w,h//2+extra), (0,h//2-extra,w,h)]
                for i, region in enumerate(regions):
                    path = root / f'fragment-{i}.png'
                    with image.crop(region) as crop:
                        crop.save(path)
                    req = request.model_copy(update={'image_path':path,'image_hash':file_hash(path), 'fragment_id':str(i),'region':list(region)})
                    fragment_settings = cfg.model_copy(deep=True)
                    if cfg.split_after_first_truncation:
                        fragment_settings.max_generation_tokens = min(cfg.max_generation_tokens, 8192)
                    fragments.append(attempt(req, 'fragment', fragment_settings))
            if all(r.status != 'FAILED' for r in fragments):
                result = merge_fragments(fragments, cfg.split_overlap > 0)
                fragmented = True
        fallback_reason = None
        if result.status == 'FAILED' and cfg.fallback_enabled:
            fallback_reason = result.diagnostics.get('error', 'Qwen recovery exhausted')
            from .providers import ClassicOCRRecognitionProvider
            provider.runtime.close()  # release VLM allocation before loading OCR models
            provider.fallback = provider.fallback or ClassicOCRRecognitionProvider(provider.config)
            result = provider.fallback.recognize_page(request)
            raw = root / 'classic-fallback.raw'
            raw.write_text(result.raw_output, encoding='utf-8')
            save_json(raw.with_suffix('.json'), result.model_dump(exclude={'raw_output','docling_document'}))
            if result.status == 'FAILED' and all(a.get('failure_scope') == 'runtime' for a in attempts):
                from .runtime import RuntimeUnavailable
                raise RuntimeUnavailable('Qwen runtime and Classic fallback unavailable: ' + result.diagnostics.get('error','unknown'))
        result.diagnostics.update(attempts=attempts, original_qwen_attempts=len(attempts),
            finish_reason=attempts[-1].get('done_reason'),
            truncated=any(a.get('done_reason') == 'length' or a.get('done') is False for a in attempts),
            fragmented=fragmented, fallback_used=fallback_reason is not None, fallback_reason=fallback_reason,
            split_attempted=any(a['kind']=='fragment' for a in attempts),
            retried=any(a['kind']=='retry' for a in attempts))
        result.diagnostics.setdefault('reading_order_status','OK')
        result.duration_ms = (time.perf_counter()-started)*1000
        return result
    finally:
        if original is not None:
            provider.runtime.settings = original
