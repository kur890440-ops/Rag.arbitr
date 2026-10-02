import json
import statistics
import time
from uuid import uuid4
from .documents import digest, now, save_json, file_hash
from .storage import MetadataStore
from .ingestion import Ingestor, freeze_corpus
from .chunking import FixedSizeChunker, StructureAwareChunker, coverage
from .embeddings import BgeM3EmbeddingProvider, EmbeddingCache
from .vectorstore import LocalVectorStore
from .retrieval import SemanticRetriever
from .evaluation import evaluate
from .reporting import generate_report
from .progress import ConsoleReporter


class Pipeline:
    def __init__(self, config, provider=None, ocr=None, reporter=None):
        self.config = config
        config.prepare()
        self.store = MetadataStore(config.sqlite_path)
        self.provider, self.ocr, self.vectors = provider, ocr, None
        self.reporter = reporter or ConsoleReporter()
        self.snapshot_path = config.snapshot_path or config.cache_path / "latest.json"

    def close(self):
        if self.vectors:
            self.vectors.close()
        self.store.close()

    def paths(self):
        return sorted((p for p in self.config.corpus_path.rglob("*") if p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}), key=lambda p: str(p).lower())

    def read_snapshot(self):
        if not self.snapshot_path.exists():
            raise ValueError("Run day21 or index first")
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def empty(self):
        print("No corpus documents found. Положите scanned PDFs в data/corpus/.")
        snapshot = {"status": "CORPUS REQUIRED", "corpus": {"documents": 0, "pages": 0}, "indexes": {}}
        save_json(self.snapshot_path, snapshot)
        generate_report(self.config, snapshot)
        return snapshot

    def validate_current_corpus(self, snapshot):
        current = [{"source_path": str(p.resolve()), "content_hash": file_hash(p)} for p in self.paths()]
        if digest(current) != snapshot.get("corpus", {}).get("corpus_hash"):
            raise ValueError("Corpus changed since indexing; run day21 again")

    def ingest(self):
        self.reporter.emit("stage_started", "INGESTION", "Подготовка корпуса")
        paths = self.paths()
        if not paths:
            return [], self.empty()
        docs, ocr = Ingestor(self.config, self.store, self.ocr, self.reporter).ingest(paths)
        manifest = freeze_corpus(self.config, paths, docs)
        self.store.put("corpora", manifest["corpus_id"], manifest)
        for entry in manifest["documents"]:
            self.store.put("corpus_documents", digest([manifest["corpus_id"], entry["source_path"]]), entry)
        snapshot = {"status": "INGESTED" if not ocr["errors"] else "PARTIAL", "corpus": manifest,
                    "ocr": ocr, "recognition": ocr, "indexes": {}}
        if self.snapshot_path.exists():
            previous = self.read_snapshot()
            if previous.get("corpus", {}).get("corpus_hash") == manifest["corpus_hash"]:
                snapshot["indexes"] = previous.get("indexes", {})
        save_json(self.snapshot_path, snapshot)
        print(f"Corpus: {len(paths)} documents / {manifest['page_count']} pages; parsed {len(docs)}")
        self.reporter.emit("stage_completed", "INGESTION", "Корпус распознан")
        return docs, snapshot

    def resources(self):
        self.reporter.check()
        if self.provider is None:
            self.provider = BgeM3EmbeddingProvider(self.config)
        if self.vectors is None:
            self.vectors = LocalVectorStore(self.config.qdrant_path)

    def build_index(self, docs, snapshot, strategy):
        self.reporter.emit("stage_started", f"{strategy.upper()}_CHUNKING", f"{strategy}: разбиение")
        self.resources()
        cfg = self.config
        from .application.rechunk import ChunkSettings
        settings_service = ChunkSettings(cfg, self.store)
        effective = {d.document_id: settings_service.read(d.document_id)['effective'] for d in docs}
        if cfg.rechunk_settings:
            effective.update(cfg.rechunk_settings)
        chunker = FixedSizeChunker(self.provider.tokenizer, cfg.fixed_tokens, cfg.fixed_overlap, cfg.fixed_max_tokens) if strategy == "fixed" else StructureAwareChunker(self.provider.tokenizer, cfg.structure_max_tokens)
        settings = [cfg.fixed_tokens, cfg.fixed_overlap, cfg.fixed_max_tokens] if strategy == "fixed" else [cfg.structure_max_tokens]
        corpus = snapshot["corpus"]
        normalized_hash = digest([(d.document_id, d.cache_identity, [b.model_dump() for b in d.blocks]) for d in docs])
        identity = digest([corpus["corpus_hash"], normalized_hash, strategy, "1", effective, self.provider.identity])
        collection = f"day21_{strategy}_{identity[:20]}"
        run = {"run_id": str(uuid4()), "corpus_id": corpus["corpus_id"], "corpus_hash": corpus["corpus_hash"],
               "normalized_hash": normalized_hash, "strategy": strategy, "strategy_version": "1", "settings": settings,
               "recognition": snapshot.get("recognition", {}).get("identity", {}),
               "embedding_model": cfg.model, "embedding": self.provider.identity, "dimension": self.provider.dimension,
               "similarity": cfg.similarity, "collection": collection, "started_at": now(), "finished_at": None,
               "status": "FAILED", "documents": 0, "pages": 0, "chunks": 0, "document_ids": [],
               "embeddings_created": 0, "embeddings_reused": 0, "errors": list(snapshot["ocr"]["errors"]), "coverage": {}}
        run["chunk_ids"] = []
        run.update(chunking_run_id=run['run_id'], scope_type='CORPUS', scope_id=corpus['corpus_id'],
                   parameters_json={d.document_id: ({k:v for k,v in effective[d.document_id].items() if k.startswith('fixed_')} if strategy=='fixed' else {'structure_max_tokens':effective[d.document_id]['structure_max_tokens']}) for d in docs},
                   source_recognition_version={d.document_id:d.cache_identity for d in docs}, created_at=now())
        self.store.put("index_runs", run["run_id"], run)
        started = time.perf_counter()
        counts = []
        try:
            self.vectors.create(collection, self.provider.dimension)
            cache = EmbeddingCache(cfg, self.store, self.provider)
            for doc in docs:
                values = effective[doc.document_id]
                chunker = FixedSizeChunker(self.provider.tokenizer, values['fixed_tokens'], values['fixed_overlap'], values['fixed_max_tokens']) if strategy == 'fixed' else StructureAwareChunker(self.provider.tokenizer, values['structure_max_tokens'])
                self.reporter.check()
                stage = "chunking"
                try:
                    self.reporter.emit("stage_started", f"{strategy.upper()}_CHUNKING", f"{strategy}: {doc.file_name}")
                    chunks = chunker.chunk(doc)
                    if not chunks:
                        raise ValueError("No OCR text blocks; document cannot be indexed")
                    run["coverage"][doc.document_id] = coverage(doc, chunks, self.provider.tokenizer)
                    stage = "embeddings"
                    for start in range(0, len(chunks), cfg.batch_size):
                        self.reporter.emit("stage_started", "EMBEDDINGS", f"{strategy}: embeddings")
                        batch = chunks[start:start + cfg.batch_size]
                        vectors, created, reused = cache.encode(batch)
                        run["embeddings_created"] += created
                        run["embeddings_reused"] += reused
                        self.reporter.emit("embedding_progress", "EMBEDDINGS", f"{strategy}: batch готов", embeddings_created_delta=created, embeddings_reused_delta=reused)
                        for c in batch:
                            self.store.put("chunks", c.chunk_id, c.model_dump())
                        self.reporter.emit("stage_started", "VECTOR_INDEXING", f"{strategy}: Qdrant")
                        self.vectors.upsert(collection, batch, vectors, doc.file_name)
                    counts.extend(c.token_count for c in chunks)
                    run["chunk_ids"].extend(c.chunk_id for c in chunks)
                    run["documents"] += 1
                    run["pages"] += sum(p.status != 'FAILED' for p in doc.pages)
                    run["document_ids"].append(doc.document_id)
                except Exception as exc:
                    run["errors"].append({"document": doc.source_path, "stage": stage, "error": str(exc)})
            run["status"] = "PARTIAL" if run["errors"] and run["documents"] else "FAILED" if run["errors"] or not run["documents"] else "SUCCESS"
        except Exception as exc:
            run["errors"].append({"stage": "qdrant", "error": str(exc)})
        run.update(finished_at=now(), duration=time.perf_counter() - started, chunks=len(counts),
                   avg_tokens=statistics.mean(counts) if counts else 0, median_tokens=statistics.median(counts) if counts else 0,
                   min_tokens=min(counts, default=0), max_tokens=max(counts, default=0))
        self.store.put("index_runs", run["run_id"], run)
        snapshot["indexes"][strategy] = run
        run.update(duration_ms=run['duration']*1000, chunks_count=run['chunks'],
                   examples=[self.store.get('chunks', k) for k in run['chunk_ids'][:2]])
        self.store.put('index_runs', run['run_id'], run)
        snapshot["embedding"] = self.provider.identity
        snapshot["status"] = run["status"]
        save_json(self.snapshot_path, snapshot)
        print(f"{strategy}: {run['status']} / {run['chunks']} chunks; embeddings created {run['embeddings_created']}, reused {run['embeddings_reused']}; Qdrant {collection}")
        self.reporter.emit("index_completed", "VECTOR_INDEXING", f"{strategy}: {run['status']}", strategy=strategy, chunks=run["chunks"], index_run_id=run["run_id"])
        return run

    def evaluate(self, snapshot):
        self.reporter.emit("stage_started", "EVALUATION", "Проверка retrieval")
        self.validate_current_corpus(snapshot)
        self.resources()
        result = evaluate(self.config, snapshot["indexes"], SemanticRetriever(self.provider, self.vectors, self.store), self.store, self.reporter)
        snapshot["evaluation"] = result
        save_json(self.snapshot_path, snapshot)
        self.reporter.emit("evaluation_completed", "EVALUATION", result["status"], evaluation_run_id=result.get("run_id"))
        return result

    def query(self, question, compare=True, strategy="fixed", snapshot=None, document_id=None):
        snapshot = snapshot or self.read_snapshot()
        self.validate_current_corpus(snapshot)
        self.resources()
        retriever = SemanticRetriever(self.provider, self.vectors, self.store)
        if compare:
            return retriever.compare(question, snapshot["indexes"], self.config.top_k, document_id)
        return {strategy: {"hits": retriever.retrieve_vector(self.provider.encode([question])[0], snapshot["indexes"][strategy], self.config.top_k, document_id)}}

    def day21(self, strategies=("fixed", "structure"), do_evaluate=True):
        docs, snapshot = self.ingest()
        if snapshot["status"] == "CORPUS REQUIRED":
            return snapshot
        if not docs:
            snapshot["status"] = "FAILED"
            snapshot["evaluation"] = {"status": "NOT_RUN", "reason": "No successfully recognized documents"}
            save_json(self.snapshot_path, snapshot)
            print(f"Report: {generate_report(self.config, snapshot).resolve()}")
            return snapshot
        for strategy in strategies:
            try:
                self.build_index(docs, snapshot, strategy)
            except Exception as exc:
                run = {"run_id": str(uuid4()), "strategy": strategy, "status": "FAILED",
                       "corpus_id": snapshot["corpus"]["corpus_id"], "corpus_hash": snapshot["corpus"]["corpus_hash"],
                       "started_at": now(), "finished_at": now(),
                       "errors": [{"stage": "model/setup", "error": str(exc)}]}
                self.store.put("index_runs", run["run_id"], run)
                snapshot["indexes"][strategy] = run
                print(f"{strategy}: FAILED model/setup: {exc}")
        if do_evaluate:
            if self.config.queries_path.exists():
                try:
                    self.evaluate(snapshot)
                except Exception as exc:
                    snapshot["evaluation"] = {"status": "INVALID", "error": str(exc), "queries": []}
            else:
                snapshot["evaluation"] = {"status": "QUERIES_REQUIRED", "queries": []}
        statuses = [i["status"] for i in snapshot["indexes"].values()]
        snapshot["status"] = "SUCCESS" if statuses and all(s == "SUCCESS" for s in statuses) else "PARTIAL" if "SUCCESS" in statuses or "PARTIAL" in statuses else "FAILED"
        save_json(self.snapshot_path, snapshot)
        print(f"Evaluation: {snapshot.get('evaluation', {}).get('status', 'NOT_RUN')}")
        self.reporter.emit("stage_started", "REPORTING", "Создание HTML-отчёта")
        print(f"Report: {generate_report(self.config, snapshot).resolve()}")
        self.reporter.emit("stage_completed", "REPORTING", "Отчёт сохранён")
        return snapshot
