import json
import time
from pathlib import Path
from importlib.metadata import version
from .documents import Block, Document, Page, digest, file_hash, save_json, now
from .recognition import create_provider, ClassicOCRRecognitionProvider, RecognitionRequest
from .recognition.cache import RecognitionCache
from .progress import ConsoleReporter


def page_count(path):
    if path.suffix.lower() != ".pdf":
        return 1
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(path) as pdf:
        return len(pdf)


def iter_render_pages(path, document_id, cache, dpi, reporter=None):
    import pypdfium2 as pdfium
    from PIL import Image
    folder = cache / "pages" / digest([file_hash(path), dpi, version("pypdfium2")])
    folder.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() != ".pdf":
        from PIL import ImageOps
        if reporter:
            reporter.emit("page_started", "PAGE_RENDERING", path.name, current_page=1, document_pages=1)
        output = folder / "0001.png"
        if not output.exists():
            with Image.open(path) as original:
                with ImageOps.exif_transpose(original).convert("RGB") as rendered:
                    rendered.save(output)
        with Image.open(output) as image:
            width, height = image.size
        yield Page(document_id=document_id, page_number=1, image_path=str(output.resolve()),
                   width=width, height=height, image_hash=file_hash(output))
        return
    with pdfium.PdfDocument(path) as pdf:
        for i in range(len(pdf)):
            if reporter:
                reporter.emit("page_started", "PAGE_RENDERING", path.name, current_page=i+1, document_pages=len(pdf))
            output = folder / f"{i + 1:04}.png"
            if not output.exists():
                page = pdf[i]
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
                image.save(output)
                image.close()
                bitmap.close()
                page.close()
            with Image.open(output) as image:
                width, height = image.size
            yield Page(document_id=document_id, page_number=i + 1,
                       image_path=str(output.resolve()), width=width, height=height, image_hash=file_hash(output))


def render_pages(path, document_id, cache, dpi):
    return list(iter_render_pages(path, document_id, cache, dpi))


class Ingestor:
    def __init__(self, config, store, provider=None, reporter=None):
        self.config, self.store = config, store
        self.reporter = reporter or ConsoleReporter()
        self.provider = provider or create_provider(config)
        if not hasattr(self.provider, "recognize_page"):
            self.provider = ClassicOCRRecognitionProvider(config, self.provider)

    def ingest(self, paths):
        try:
            return self._ingest(paths)
        finally:
            self.provider.close()

    def _ingest(self, paths):
        docs, errors = [], []
        stats = {"backend": self.config.ocr_backend, "processed_pages": 0, "reused_pages": 0,
                 "recognition_created": 0, "recognition_reused": 0, "cache_hits": 0, "cache_misses": 0}
        started = time.perf_counter()
        durations = []
        total_pages = 0
        for path in paths:
            self.reporter.check()
            try:
                total_pages += page_count(path)
            except Exception:
                pass  # Per-document ingestion records the precise failure.
        self.reporter.emit("stage_started", "RECOGNITION", "Проверка recognition runtime", total_documents=len(paths), total_pages=total_pages)
        # Validate model/runtime once before opening any PDF. No cloud or silent fallback.
        try:
            runtime_status = self.provider.preflight()
            identity = self.provider.identity()
        except Exception:
            self.provider.close()
            raise
        stats.update(identity=identity, provider=identity["provider"], model=identity["model"],
                     backend=self.config.ocr_backend if identity["provider"] == "classic_ocr" else identity["provider"],
                     runtime=identity["runtime"], device=runtime_status.get("effective_device", "unknown"),
                     quantization=identity["quantization"], runtime_status=runtime_status)
        self.reporter.emit("stage_completed", "RECOGNITION", "Recognition runtime готов",
                           recognition_provider=identity["provider"], recognition_model=identity["model"])
        cache = RecognitionCache(self.config, self.store, self.provider)
        print(f"Recognition: provider={identity['provider']} model={identity['model']} runtime={identity['runtime']} device={stats['device']}")
        reliability = {k: 0 for k in ('qwen_success','qwen_retry_success','qwen_fragment_success','classic_fallback','failed','cache_reused','order_repaired','order_uncertain','token_recovered','documents_success','documents_partial','documents_failed')}
        document_stats = []
        reliability.update(pages_total=total_pages, documents_total=len(paths))
        for path in paths:
            self.reporter.check()
            content_hash = file_hash(path)
            doc_id = digest([str(path.resolve()), content_hash])
            try:
                required = page_count(path)
            except Exception as exc:
                import pypdfium2 as pdfium
                if not isinstance(exc, pdfium.PdfiumError):
                    raise
                errors.append(dict(document=str(path), stage='render', error=str(exc)))
                document_stats.append(dict(document_id=doc_id, name=path.name, pages_total=0, status='FAILED',
                    qwen_success=0,qwen_retry_success=0,qwen_fragment_success=0,classic_fallback=0,failed=0))
                reliability['documents_failed'] += 1
                self.reporter.emit('document_error','PAGE_RENDERING','Document unreadable',error=str(exc),document=path.name)
                continue
            self.reporter.emit("document_started", "INGESTION", path.name, current_document_id=doc_id, current_file=path.name)
            pages, blocks, section, page_keys = [], [], "", []
            row = dict(document_id=doc_id, name=path.name, pages_total=required,
                       qwen_success=0, qwen_retry_success=0, qwen_fragment_success=0, classic_fallback=0, failed=0)
            for p in iter_render_pages(path, doc_id, self.config.cache_path, self.config.render_dpi, self.reporter):
                self.reporter.check()
                self.reporter.emit("stage_started", "RECOGNITION", f"{path.name}: {p.page_number}")
                request = RecognitionRequest(document_id=doc_id, document_hash=content_hash, page_number=p.page_number,
                    image_path=Path(p.image_path), image_hash=p.image_hash, language_hints=self.config.recognition.language_hints,
                    render_settings={"dpi": self.config.render_dpi, "pdfium": version("pypdfium2")})
                key, result, metadata, reused = cache.recognize(request, identity)
                self.reporter.emit("cache_hit" if reused else "recognition_completed", "RECOGNITION", "Page recognition saved",
                    recognition_id=key, cache_hits_delta=int(reused), cache_misses_delta=int(not reused), duration_ms=result.duration_ms)
                stats["recognition_reused" if reused else "recognition_created"] += 1
                stats["cache_hits" if reused else "cache_misses"] += 1
                stats["reused_pages" if reused else "processed_pages"] += 1
                reliability['cache_reused'] += int(reused)
                if not reused:
                    durations.append(result.duration_ms / 1000)
                p.recognition_id, p.recognition_provider = key, result.provider
                p.raw_output_path, p.normalized_output_path = metadata["raw_output_path"], metadata["normalized_output_path"]
                p.status, p.ocr_text, p.reliability = result.status, result.normalized_text, result.diagnostics
                page_keys.append(key)
                page_blocks = []
                category = ('failed' if result.status == 'FAILED' else 'classic_fallback' if result.diagnostics.get('fallback_used')
                            else 'qwen_fragment_success' if result.diagnostics.get('fragmented')
                            else 'qwen_retry_success' if result.diagnostics.get('retried') else 'qwen_success' if result.provider == 'qwen3_vl' else None)
                if category:
                    reliability[category] += 1
                    row[category] += 1
                reliability['order_repaired'] += int(result.diagnostics.get('reading_order_status') == 'REPAIRED')
                reliability['order_uncertain'] += int(result.diagnostics.get('reading_order_status') == 'UNCERTAIN')
                reliability['token_recovered'] += int(result.diagnostics.get('truncated', False) and result.status != 'FAILED')
                if result.status == 'FAILED':
                    error = dict(document=str(path), page=p.page_number, stage='recognition', error=result.diagnostics.get('error','Page recovery exhausted'))
                    errors.append(error)
                    self.reporter.emit('document_error','RECOGNITION','Page unresolved', error=error['error'],document=path.name)
                    section = ''
                else:
                    for recognized in sorted(result.blocks, key=lambda b: b.order):
                        if not recognized.text.strip():
                            continue
                        if recognized.type in {"heading", "title"}:
                            section = recognized.text
                        block = Block(block_id=digest([doc_id, key, recognized.order, recognized.text]), document_id=doc_id,
                            page_number=p.page_number, block_type=recognized.type, text=recognized.text, reading_order=len(blocks)+len(page_blocks),
                            bbox=recognized.bbox, section=section, content_hash=digest(recognized.text), recognition_id=key)
                        page_blocks.append(block)
                self.store.page(p, page_blocks)
                blocks.extend(page_blocks)
                pages.append(p)
                self.reporter.emit('page_completed','NORMALIZATION','Page committed',processed_pages_delta=int(result.status!='FAILED'),reliability=reliability)
            failed = [p.page_number for p in pages if p.status == 'FAILED']
            status = 'FAILED' if not any(p.status != 'FAILED' for p in pages) else 'PARTIAL' if failed else 'SUCCESS'
            key = digest([doc_id, identity, page_keys])
            doc = Document(document_id=doc_id, source_path=str(path.resolve()), file_name=path.name,
                title=path.stem, content_hash=content_hash, page_count=required, parser_version=identity['parser'],
                ocr_engine=identity['provider'], recognition_provider='mixed' if len({p.recognition_provider for p in pages})>1 else pages[0].recognition_provider if pages else identity['provider'],
                pages=pages, blocks=blocks, cache_identity=key, status=status, failed_pages=failed)
            self.store.document(doc)
            save_json(self.config.cache_path / 'parsed' / f'{key}.json', doc.model_dump())
            if status != 'FAILED':
                docs.append(doc)
            row['status'] = status
            document_stats.append(row)
            reliability['documents_'+status.lower()] += 1
            self.reporter.emit('document_completed','NORMALIZATION',path.name,processed_documents_delta=int(status!='FAILED'),reliability=reliability,document_stats=document_stats)
        stats.update(reliability=reliability, document_stats=document_stats)
        self.provider.close()  # Release recognition model before BGE-M3 can claim GPU memory.
        stats.update(errors=errors, duration=time.perf_counter() - started, identity=identity,
                     total_recognition_time=sum(durations), average_sec_per_page=sum(durations) / len(durations) if durations else 0,
                     min_sec_per_page=min(durations, default=0), max_sec_per_page=max(durations, default=0))
        print(f"Recognition: pages processed={stats['processed_pages']} cache reused={stats['reused_pages']} average sec/page={stats['average_sec_per_page']:.3f}")
        return docs, stats


def freeze_corpus(config, paths, docs):
    known = {d.source_path: d for d in docs}
    entries = []
    for p in paths:
        d = known.get(str(p.resolve()))
        try:
            count = d.page_count if d else page_count(p)
        except Exception:
            count = 0  # Precise corrupt-document error already recorded by ingestion.
        entries.append({"source_path": str(p.resolve()), "file_name": p.name,
                        "content_hash": file_hash(p), "document_id": d.document_id if d else None,
                        "page_count": count, "recognition_version": d.cache_identity if d else None,
                        "status": d.status if d else "FAILED", "failed_pages": d.failed_pages if d else list(range(1, count+1))})
    corpus_hash = digest([{k: e[k] for k in ("source_path", "content_hash")} for e in entries])
    manifest = {"corpus_id": f"frozen-v1-{corpus_hash[:16]}", "name": "FROZEN CORPUS V1",
                "documents": entries, "page_count": sum(e['page_count'] for e in entries),
                "corpus_hash": corpus_hash, "created_at": now()}
    if config.manifest_path.exists():
        previous = json.loads(config.manifest_path.read_text(encoding="utf-8"))
        if previous["corpus_hash"] == corpus_hash:
            manifest["created_at"] = previous["created_at"]
    save_json(config.manifest_path, manifest)
    save_json(config.cache_path / "corpora" / f"{corpus_hash}.json", manifest)
    return manifest
