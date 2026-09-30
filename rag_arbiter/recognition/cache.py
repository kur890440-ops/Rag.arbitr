import json
from pathlib import Path
from uuid import uuid4
from ..documents import digest, now, save_json
from .models import RecognitionResult


class RecognitionCache:
    def __init__(self, config, store, provider):
        self.config, self.store, self.provider = config, store, provider

    def recognize(self, request, identity):
        base_key = digest([request.document_id, request.document_hash, request.page_number, request.image_hash,
                      request.render_settings, request.language_hints, identity])
        pointer = self.store.get("recognition_cache", base_key)
        metadata = self.store.get("recognition_metadata", pointer["recognition_id"] if pointer else base_key)
        selected = (not self.config.retry_document_id or self.config.retry_document_id == request.document_id) and (
            not self.config.retry_page_number or self.config.retry_page_number == request.page_number)
        force = selected and self.config.retry_mode == "force"
        if metadata and selected and self.config.retry_mode == "fallback" and metadata.get("diagnostics", {}).get("fallback_used"):
            force = True
        if not metadata:
            # Upgrade legacy successful page records without re-inference. Old schema
            # content remains immutable; only equivalent runtime/render settings qualify.
            rows = self.store.db.execute("SELECT data FROM recognition_metadata WHERE json_extract(data,'$.document_id')=? AND json_extract(data,'$.page_number')=? AND json_extract(data,'$.image_hash')=? AND json_extract(data,'$.status')!='FAILED' ORDER BY rowid DESC", (request.document_id, request.page_number, request.image_hash))
            for row in rows:
                old = json.loads(row[0])
                old_identity = old.get("settings", {})
                def content_identity(value):
                    value = dict(value)
                    settings = dict(value.get('settings', {}))
                    # Output caps/recovery routing cannot invalidate already-complete text.
                    # All model, prompt, input and sampling settings remain significant.
                    for name in ('max_generation_tokens','recovery_output_tokens','split_after_first_truncation'):
                        settings.pop(name, None)
                    value['settings'] = settings
                    return value
                compatible_budget = (old_identity.get('normalizer') == 'recognition-4'
                    and content_identity(old_identity) == content_identity(identity)
                    and old.get('render_settings') == request.render_settings
                    and old.get('language_hints', old_identity.get('settings',{}).get('language_hints')) == request.language_hints)
                if compatible_budget:
                    metadata = old
                    break
                if (old.get("status") != "FAILED" and old.get("document_id") == request.document_id
                    and old.get("page_number") == request.page_number and old.get("image_hash") == request.image_hash
                    and all(old_identity.get(k) == identity.get(k) for k in ("provider","model","model_version"))
                    and old_identity.get("normalizer") == "recognition-3"
                    and all(identity.get("settings", {}).get(k) == v for k,v in old_identity.get("settings",{}).items()
                            if k not in ('max_generation_tokens','recovery_output_tokens','split_after_first_truncation'))):
                    metadata = old
                    break
        if metadata and selected and self.config.retry_mode == 'fallback' and metadata.get('diagnostics', {}).get('fallback_used'):
            force = True
        previous = None
        previous_metadata = metadata
        if metadata:
            normalized = Path(metadata["normalized_output_path"])
            raw = Path(metadata["raw_output_path"])
            if normalized.exists() and raw.exists():
                previous = RecognitionResult.model_validate_json(normalized.read_text(encoding="utf-8"))
                previous.raw_output = raw.read_text(encoding="utf-8")
                outside_scope = not selected or (self.config.retry_mode == 'fallback' and not previous.diagnostics.get('fallback_used'))
                if (previous.status != "FAILED" and not force) or (outside_scope and (self.config.retry_document_id or self.config.retry_mode == 'fallback')):
                    key = metadata.get("recognition_id") or normalized.parent.name
                    if previous.status != 'FAILED':
                        self.store.put('recognition_cache', base_key, {'recognition_id':key,'status':previous.status})
                    return key, previous, metadata, previous.status != 'FAILED'
        key = digest([base_key, uuid4().hex])
        folder = self.config.cache_path / "recognition" / request.document_id / f"page_{request.page_number:04}" / key
        raw_path, normalized_path = folder / "recognition.raw", folder / "normalized.json"
        request = request.model_copy(update={"attempt_dir":folder / "attempts"})
        if (not selected and self.config.retry_document_id) or (self.config.retry_mode == 'fallback' and not metadata):
            result = RecognitionResult(provider=identity['provider'], model=identity['model'], model_version=identity['model_version'],
                runtime=identity['runtime'], device='not_run', quantization=identity['quantization'], page_number=request.page_number,
                raw_output='', status='FAILED', diagnostics={'error':'Missing page outside selected retry scope','skipped':True})
        else:
            result = self.provider.recognize_page(request)
        folder.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(result.raw_output, encoding="utf-8")
        normalized = result.model_dump(exclude={"raw_output", "docling_document"})
        normalized["raw_output"] = ""  # Large raw content is referenced by file, not duplicated.
        save_json(normalized_path, normalized)
        if result.docling_document is not None:
            save_json(folder / "docling.json", result.docling_document)
        metadata = {"recognition_id": key, "document_id": request.document_id, "page_number": request.page_number,
                    "document_hash": request.document_hash, "render_settings": request.render_settings,
                    "language_hints": request.language_hints,
                    "provider": result.provider, "model": result.model, "model_version": result.model_version,
                    "runtime": result.runtime, "device": result.device, "quantization": result.quantization,
                    "image_hash": request.image_hash, "settings_hash": digest(identity), "settings": identity,
                    "image_path": str(request.image_path.resolve()), "raw_output_path": str(raw_path.resolve()),
                    "normalized_output_path": str(normalized_path.resolve()), "duration_ms": result.duration_ms,
                    "status": result.status, "created_at": now(), "diagnostics": result.diagnostics}
        self.store.put("recognition_metadata", key, metadata)
        # A failed forced replacement never invalidates the previous usable page.
        if result.status == "FAILED" and previous and previous.status != "FAILED":
            old = previous_metadata
            if old:
                return old.get('recognition_id') or Path(old['normalized_output_path']).parent.name, previous, old, True
        self.store.put("recognition_cache", base_key, {"recognition_id":key, "status":result.status})
        return key, result, metadata, False
