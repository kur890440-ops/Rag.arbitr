import json
import statistics
from collections import Counter
from ..documents import digest
from pathlib import Path
from ..config import Config
from ..reporting import generate_report
from .uploads import contained
from .runs import TERMINAL


class ResultsService:
    def __init__(self, runs):
        self.runs = runs

    def snapshot(self, run_id):
        run = self.runs.get(run_id)
        cfg = Config(**run["config_json"])
        path = cfg.snapshot_path or cfg.cache_path / "latest.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if cfg.rechunk_source_run_id:
            return self.snapshot(cfg.rechunk_source_run_id)
        return {}

    def recognition_ids(self, run_id):
        run = self.runs.get(run_id)
        source = run.get('config_json', {}).get('rechunk_source_run_id')
        if source:
            return self.recognition_ids(source)
        with self.runs.db() as store:
            rows = store.db.execute("SELECT data FROM processing_run_events WHERE run_id=? AND json_extract(data,'$.event_type') IN ('cache_hit','recognition_completed') ORDER BY id", (run_id,)).fetchall()
        return list(dict.fromkeys(json.loads(row[0])["metrics"]["recognition_id"] for row in rows))

    def pages(self, run_id):
        result = []
        ids = self.recognition_ids(run_id)
        names = {}
        with self.runs.db() as store:
            for key in ids:
                meta = store.get("recognition_metadata", key)
                if not meta:
                    continue
                if meta['document_id'] not in names:
                    doc = store.get('documents', meta['document_id'])
                    names[meta['document_id']] = doc['file_name'] if doc else meta['document_id'][:12]
                result.append({"recognition_id": key, "document_id": meta["document_id"], "page_number": meta["page_number"],
                               "file_name": names[meta['document_id']], "status": meta["status"]})
        return result

    def page(self, run_id, recognition_id):
        if recognition_id not in self.recognition_ids(run_id):
            raise KeyError("Page not in run")
        with self.runs.db() as store:
            metadata = store.get("recognition_metadata", recognition_id)
            events = store.db.execute("SELECT data FROM processing_run_events WHERE run_id=? AND json_extract(data,'$.metrics.recognition_id')=? ORDER BY id DESC LIMIT 1", (run_id, recognition_id)).fetchone()
        root = self.runs.config.cache_path
        normalized_path = contained(metadata["normalized_output_path"], root)
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        normalized.pop("raw_output", None)
        hit = events and json.loads(events[0])["event_type"] == "cache_hit"
        public = {k: metadata[k] for k in ("provider", "model", "runtime", "duration_ms", "status", "device", "page_number", "document_id")}
        return {"recognition_id": recognition_id, "metadata": public, "cache": "HIT" if hit else "MISS", "normalized": normalized}

    def page_file(self, run_id, recognition_id, kind):
        self.page(run_id, recognition_id)
        with self.runs.db() as store:
            meta = store.get("recognition_metadata", recognition_id)
        key = {"image": "image_path", "raw": "raw_output_path"}[kind]
        return contained(meta[key], self.runs.config.cache_path)

    def files(self, run_id):
        run = self.runs.get(run_id)
        cfg = Config(**run['config_json'])
        snapshot = self.snapshot(run_id)
        counts = {s: Counter() for s in ('fixed','structure')}
        rows = []
        with self.runs.db() as store:
            metas = [store.get('recognition_metadata', k) for k in self.recognition_ids(run_id)]
            by_doc = {}
            for m in metas:
                if m:
                    by_doc.setdefault(m['document_id'], {})[m['page_number']] = m
            for s in counts:
                for key in snapshot.get('indexes', {}).get(s, {}).get('chunk_ids', []):
                    c = store.get('chunks', key)
                    if c: counts[s][c['document_id']] += 1
            uploads = [store.get('uploads', uid) for uid in run.get('upload_ids', [])]
            entries = []
            for u in uploads:
                if not u: continue
                path = cfg.corpus_path / u['upload_id'] / u['filename']
                entries.append(dict(document_id=digest([str(path.resolve()), u['content_hash']]), file_name=u['filename'], page_count=u['pages']))
            if not entries:
                entries = snapshot.get('corpus', {}).get('documents', [])
            for e in entries:
                did = e['document_id']
                pages = list(by_doc.get(did, {}).values())
                q = sum(m['status']!='FAILED' and m['provider']=='qwen3_vl' for m in pages)
                o = sum(m['status']!='FAILED' and m['provider']=='classic_ocr' for m in pages)
                errors = sum(m['status']=='FAILED' for m in pages)
                usable = q+o
                status = 'SUCCESS' if usable==e['page_count'] and usable else 'PARTIAL' if usable else 'FAILED' if errors else 'NOT_PROCESSED'
                if run['status'] in {'RUNNING','QUEUED'} and (run.get('current_document_id')==did or run['run_type']=='rechunk' and (not cfg.rechunk_document_id or cfg.rechunk_document_id==did)):
                    status = 'PROCESSING'
                rows.append(dict(document_id=did, name=e['file_name'], pages=e['page_count'], q=q, o=o, errors=errors,
                                 processed=bool(usable), status=status,
                                 fixed=counts['fixed'].get(did), structure=counts['structure'].get(did)))
        return sorted(rows, key=lambda r:(not r['processed'], r['name'].casefold(), r['document_id']))

    def chunks(self, run_id, offset=0, limit=10, document_id=None, page=None, section=None):
        snapshot = self.snapshot(run_id)
        output = {}
        with self.runs.db() as store:
            for strategy in ("fixed", "structure"):
                index = snapshot.get("indexes", {}).get(strategy, {})
                ids = index.get("chunk_ids", [])
                all_chunks = [store.get('chunks', k) for k in ids]
                scoped = [c for c in all_chunks if c and (not document_id or c['document_id']==document_id)]
                filtered = [c for c in scoped if (not page or c['page_start']<=page<=c['page_end']) and (not section or section in c['section'])]
                chunks = filtered[offset:offset+limit]
                for chunk in chunks:
                    doc = store.get("documents", chunk["document_id"])
                    chunk["file_name"] = doc["file_name"] if doc else chunk["document_id"]
                    chunk.setdefault('title', (doc or {}).get('title') or chunk['file_name'])
                    chunk.setdefault('source', 'uploaded document' if self.runs.get(run_id).get('upload_ids') else 'local file')
                stats = {k: index.get(k) for k in
                    ("chunks", "avg_tokens", "median_tokens", "min_tokens", "max_tokens", "duration", "status")}
                counts = [c['token_count'] for c in scoped]
                # Active chunk_ids are recorded only after successful vector upsert:
                # one indexed dense vector per chunk, including cache-reused vectors.
                stats.update(chunks=len(scoped), embeddings=len(scoped), avg_tokens=statistics.mean(counts) if counts else 0,
                             median_tokens=statistics.median(counts) if counts else 0, min_tokens=min(counts, default=0), max_tokens=max(counts, default=0))
                active_id = index.get('run_id')
                parameters = index.get('parameters_json', index.get('settings'))
                if document_id:
                    stats['duration'] = None  # Legacy corpus timing cannot be attributed to one file.
                    for part in index.get('partitions', []):
                        if document_id in part['document_ids']:
                            active_id = part['run_id']
                            part_run = store.get('index_runs', active_id) or {}
                            if part_run.get('scope_type') == 'DOCUMENT':
                                stats['duration'] = part_run.get('duration')
                            parameters = part.get('parameters_json')
                    if isinstance(parameters, dict):
                        parameters = parameters.get(document_id, parameters)
                output[strategy] = {"chunks": chunks, "total": len(filtered), "stats": stats, 'active_run':index.get('run_id'),
                                    'document_active_run':active_id, 'parameters':parameters, 'partitions':index.get('partitions', [])}
        return output

    def search(self, run_id, question):
        if not question.strip() or len(question) > 4000:
            raise ValueError("Введите вопрос длиной от 1 до 4000 символов")
        run = self.runs.get(run_id)
        if run["status"] not in TERMINAL:
            raise ValueError("Дождитесь завершения run")
        cfg = Config(**run["config_json"])
        results = self.runs.query(cfg, question, snapshot=self.snapshot(run_id))
        for group in results.values():
            for hit in group["hits"]:
                hit["source_pages"] = [{"recognition_id": s["page"]["recognition_id"], "page_number": s["page"]["page_number"]}
                    for s in hit.get("provenance", {}).get("sources", [])]
                hit.pop("provenance", None)  # Browser receives internal IDs, never file paths.
        return results

    def evaluation(self, run_id):
        evaluation = self.snapshot(run_id).get("evaluation", {})
        rows = evaluation.get("queries", [])
        result = {"status": evaluation.get("status", "QUERIES_REQUIRED"), "query_count": len(rows), "strategies": {}}
        for strategy in ("fixed", "structure"):
            metrics = dict(evaluation.get("aggregate", {}).get(strategy, {}))
            for key in ("page_hit", "section_hit"):
                values = [q["results"][strategy]["metrics"][key] for q in rows if key in q["results"][strategy]["metrics"]]
                metrics[key] = sum(values)/len(values) if values else None
            latencies = [q["results"][strategy]["search_latency"] + q["results"][strategy]["query_embedding_latency"] for q in rows]
            metrics["latency"] = sum(latencies)/len(latencies) if latencies else None
            result["strategies"][strategy] = metrics
        return result

    def embedding(self, run_id, chunk_id):
        """Read the saved vector for an active chunk, without loading a model."""
        import numpy as np
        snapshot = self.snapshot(run_id)
        index = next((i for i in snapshot.get('indexes', {}).values()
                      if chunk_id in i.get('chunk_ids', [])), None)
        if index is None:
            raise KeyError('Chunk not in active run')
        with self.runs.db() as store:
            chunk = store.get('chunks', chunk_id)
            if not chunk:
                raise KeyError('Chunk unavailable')
            for part in index.get('partitions', []):
                if chunk_id in part.get('chunk_ids', []):
                    index = store.get('index_runs', part['run_id']) or index
                    break
        identity = index['embedding']
        cfg = Config(**self.runs.get(run_id)['config_json'])
        key = digest([chunk['content_hash'], identity])
        path = cfg.cache_path / 'embeddings' / f'{key}.npy'
        if not path.is_file():
            raise KeyError('Saved embedding is unavailable')
        vector = np.load(path, allow_pickle=False)
        if vector.shape != (index['dimension'],) or not np.isfinite(vector).all():
            raise ValueError('Invalid saved embedding')
        return dict(chunk_id=chunk_id, model=identity['model'], dimension=len(vector),
                    normalization=identity.get('normalization'), vector=vector.tolist())

    def report(self, run_id, regenerate=False):
        run = self.runs.get(run_id)
        if run["status"] not in TERMINAL:
            raise ValueError("Отчёт доступен после завершения обработки")
        cfg = Config(**run["config_json"])
        path = cfg.report_path.resolve()
        if path != self.runs.config.report_path.resolve():
            path = contained(path, self.runs.config.web_data_path)
        if regenerate:
            with self.runs.operation_lock():
                generate_report(cfg, self.snapshot(run_id))
        # Web-owned reports or the explicitly configured CLI report; never an input path.
        if not path.exists():
            raise KeyError("Report not generated")
        return path

    def errors(self, run_id):
        with self.runs.db() as store:
            return [e for e in store.all("processing_run_errors") if e["run_id"] == run_id]
