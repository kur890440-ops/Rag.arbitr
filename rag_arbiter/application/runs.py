import os
import logging
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from typing import Literal
import portalocker
import psutil
from pydantic import BaseModel, Field
from ..config import Config
from ..documents import now, digest
from ..storage import MetadataStore
from ..pipeline import Pipeline
from ..progress import RunCancelled

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED"}


class ProcessingRun(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    run_type: str = "day21"
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED"] = "QUEUED"
    stage: str = "UPLOAD_VALIDATION"
    created_at: str = Field(default_factory=now)
    started_at: str | None = None
    finished_at: str | None = None
    total_documents: int = 0
    processed_documents: int = 0
    total_pages: int = 0
    processed_pages: int = 0
    current_document_id: str | None = None
    current_page: int | None = None
    progress_percent: float = 0
    recognition_provider: str
    recognition_model: str
    embedding_model: str
    fixed_chunks: int = 0
    structure_chunks: int = 0
    embeddings_created: int = 0
    embeddings_reused: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    errors_count: int = 0
    last_message: str = "Ожидает запуска"
    metrics_json: dict = {}
    cancel_requested: bool = False
    owner_pid: int = Field(default_factory=os.getpid)
    owner_started: float = Field(default_factory=lambda: psutil.Process().create_time())
    config_json: dict
    upload_ids: list[str] = []
    index_run_ids: dict = {}
    evaluation_run_id: str | None = None


def safe_error(exc):
    text = str(exc).lower()
    if "truncated" in text or "generation/context budget" in text:
        return "Ответ Qwen оборвался по лимиту токенов. Увеличьте max_generation_tokens и context_tokens в config.toml и повторите запуск. Неполный ответ сохранён для диагностики."
    if "duplicate reading order" in text:
        return "Порядок блоков Qwen неоднозначен. Доступно постраничное восстановление; исходный ответ сохранён."
    for words, message in [(("out of memory", "oom"), "Недостаточно GPU-памяти. Уменьшите resolution/token budget или выберите CPU."),
        (("local model missing", "model not found"), "Модель Qwen не установлена. Выполните Ollama pull для модели из config."),
        (("ollama", "connection refused"), "Ollama недоступен или истёк timeout. Проверьте локальный runtime и установленную модель."),
        (("cuda",), "CUDA недоступна для выбранного runtime."),
        (("recognition failed", "json", "duplicate reading"), "Не удалось разобрать recognition response. Raw сохранён; проверьте Recognition Viewer."),
        (("qdrant", "already accessed"), "Qdrant занят или недоступен. Завершите другую операцию индексации."),
        (("sqlite", "database",), "Ошибка metadata DB. Проверьте доступ и свободное место."),
        (("pdf", "docling"), "Документ не удалось обработать. Проверьте формат и целостность файла.")]:
        if any(word in text for word in words):
            return message
    return "Операция не завершена. Проверьте локальную конфигурацию и диагностический журнал."


class StoredReporter:
    def __init__(self, service, run_id, cancellation, store):
        self.service, self.run_id, self.cancellation, self.store = service, run_id, cancellation, store

    def check(self):
        if self.cancellation.is_set():
            raise RunCancelled()

    def emit(self, event_type, stage="", message="", **metrics):
        self.check()
        with self.service.mutation_lock:
            run = self.store.get("processing_runs", self.run_id)
            if stage:
                run["stage"] = stage
            run["last_message"] = message
            for name, value in metrics.items():
                if name.endswith("_delta"):
                    field = name[:-6]
                    if field in run:
                        run[field] += value
                elif name in run:
                    run[name] = value
            extra = {k: v for k, v in metrics.items() if k not in run and not k.endswith("_delta") and k != "error"}
            run["metrics_json"].update(extra)
            if event_type == "recognition_completed":
                run["metrics_json"]["recognition_ms"] = run["metrics_json"].get("recognition_ms", 0) + metrics.get("duration_ms", 0)
                run["metrics_json"]["average_sec_per_page"] = run["metrics_json"]["recognition_ms"] / 1000 / max(1, run["cache_misses"])
            if event_type == "document_started":
                run["current_page"] = None
            if event_type == "index_completed":
                run[metrics["strategy"] + "_chunks"] = metrics["chunks"]
                run["index_run_ids"][metrics["strategy"]] = metrics["index_run_id"]
            if event_type == "evaluation_completed":
                run["evaluation_run_id"] = metrics.get("evaluation_run_id")
            if event_type == "document_error":
                run["errors_count"] += 1
                error = {"run_id": self.run_id, "stage": stage, "document": metrics.get("document"),
                         "page": run["current_page"], "message": safe_error(metrics.get("error", ""))}
                self.store.put("processing_run_errors", uuid4().hex, error)
                message = error["message"]
            resolved_or_failed = run["processed_pages"] + run['metrics_json'].get('reliability', {}).get('failed', 0)
            percent = 55 * resolved_or_failed / max(1, run["total_pages"])
            floor = {"FIXED_CHUNKING": 60, "STRUCTURE_CHUNKING": 80, "EVALUATION": 95, "REPORTING": 99}.get(stage, 0)
            run["progress_percent"] = min(99, max(run["progress_percent"], percent, floor))
            self.store.put("processing_runs", self.run_id, run)
            event_metrics = {**extra, **{key: run[key] for key in (
                "total_documents", "processed_documents", "total_pages", "processed_pages", "current_page",
                "cache_hits", "cache_misses", "embeddings_created", "embeddings_reused", "fixed_chunks", "structure_chunks")}}
            self.store.event(self.run_id, {"run_id": self.run_id, "event_type": event_type, "stage": stage,
                "progress": run["progress_percent"], "message": message, "timestamp": now(), "metrics": event_metrics})


class RunService:
    """Shared CLI/Web application boundary. A single background worker; DB is truth."""
    def __init__(self, config, pipeline_factory=Pipeline):
        self.config, self.pipeline_factory = config, pipeline_factory
        config.prepare()
        config.web_data_path.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="day21")
        self.mutation_lock = threading.RLock()
        self.cancellations = {}
        self.futures = {}
        self.recover()

    @contextmanager
    def db(self):
        store = MetadataStore(self.config.sqlite_path)
        try:
            yield store
        finally:
            store.close()

    def recover(self):
        with self.db() as store:
            for run in store.all("processing_runs"):
                if run["status"] not in {"RUNNING", "QUEUED"}:
                    continue
                try:
                    alive = psutil.Process(run["owner_pid"]).create_time() == run["owner_started"]
                except psutil.Error:
                    alive = False
                if not alive:
                    run.update(status="INTERRUPTED", finished_at=now(), last_message="application_restart")
                    store.put("processing_runs", run["run_id"], run)
                    store.event(run["run_id"], {"run_id": run["run_id"], "event_type": "run_failed", "stage": run["stage"],
                        "progress": run["progress_percent"], "message": "application_restart", "timestamp": now(), "metrics": {}})

    def get(self, run_id):
        with self.db() as store:
            run = store.get("processing_runs", run_id)
        if not run:
            raise KeyError("Run not found")
        return run

    def list(self):
        with self.db() as store:
            return list(reversed(store.all("processing_runs")))

    def public(self, run):
        return {k: v for k, v in run.items() if k not in {"config_json", "owner_pid", "owner_started"}}

    def create(self, config=None, run_type="day21", upload_ids=None, background=True):
        cfg = config or self.config
        run = ProcessingRun(run_type=run_type, recognition_provider=cfg.recognition.provider,
            recognition_model=cfg.recognition.model, embedding_model=cfg.model,
            config_json=cfg.model_dump(mode="json"), upload_ids=upload_ids or [])
        self.cancellations[run.run_id] = threading.Event()
        with self.db() as store:
            store.put("processing_runs", run.run_id, run.model_dump())
        if background:
            self.futures[run.run_id] = self.executor.submit(self.execute, run.run_id)
        return run.run_id

    @contextmanager
    def operation_lock(self, cancellation=None):
        path = self.config.qdrant_path.parent / (self.config.qdrant_path.name + ".application.lock")
        with path.open("a+b") as handle:
            while True:
                if cancellation and cancellation.is_set():
                    raise RunCancelled()
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    break
                except portalocker.exceptions.LockException:
                    if cancellation is None:
                        raise ValueError("Qdrant занят: дождитесь завершения активной операции")
                    cancellation.wait(0.1)
            try:
                yield
            finally:
                portalocker.unlock(handle)

    def cancel(self, run_id):
        with self.mutation_lock:
            run = self.get(run_id)
            if run["status"] in TERMINAL:
                return self.public(run)
            signal = self.cancellations.get(run_id)
            if not signal:
                raise ValueError("Run belongs to another live application process")
            signal.set()
            run.update(cancel_requested=True, last_message="Отмена запрошена; завершаем текущую страницу/batch")
            with self.db() as store:
                store.put("processing_runs", run_id, run)
            return self.public(run)

    def query(self, config, question, compare=True, strategy="fixed", snapshot=None):
        with self.operation_lock():
            pipeline = self.pipeline_factory(config)
            try:
                return pipeline.query(question, compare=compare, strategy=strategy, snapshot=snapshot)
            finally:
                pipeline.close()

    def execute(self, run_id):
        pipeline, result = None, None
        signal = self.cancellations[run_id]
        with self.db() as store:
            reporter = StoredReporter(self, run_id, signal, store)
            try:
                with self.operation_lock(signal):
                    reporter.check()
                    with self.mutation_lock:
                        run = store.get("processing_runs", run_id)
                        run.update(status="RUNNING", started_at=now())
                        store.put("processing_runs", run_id, run)
                    reporter.emit("run_started", "UPLOAD_VALIDATION", "Обработка запущена")
                    cfg = Config(**run["config_json"])
                    pipeline = self.pipeline_factory(cfg, reporter=reporter)
                    try:
                        if run["run_type"] == "rechunk":
                            from .rechunk import RechunkService
                            result = RechunkService(self).execute(pipeline)
                        elif run["run_type"] == "ingest":
                            _, result = pipeline.ingest()
                        elif run["run_type"] in {"evaluate", "report"}:
                            result = pipeline.read_snapshot()
                            if run["run_type"] == "evaluate":
                                pipeline.evaluate(result)
                            from ..reporting import generate_report
                            reporter.emit("stage_started", "REPORTING", "Создание отчёта")
                            generate_report(cfg, result)
                        else:
                            strategies = ("fixed", "structure") if run["run_type"] in {"day21", "index"} else (run["run_type"].split(":")[1],)
                            result = pipeline.day21(strategies, do_evaluate=run["run_type"] == "day21")
                        reporter.check()
                        status = {"FAILED": "FAILED", "PARTIAL": "PARTIAL", "CORPUS REQUIRED": "COMPLETED"}.get(result.get("status"), "COMPLETED")
                        if result.get("evaluation", {}).get("status") == "INVALID" and status == "COMPLETED":
                            status = "PARTIAL"
                    finally:
                        pipeline.close()
                        pipeline = None
                    reporter.check()
                    self.finish(store, run_id, status, "Обработка завершена" if status == "COMPLETED" else "Обработка завершена с ошибками", result)
            except RunCancelled:
                self.finish(store, run_id, "CANCELLED", "Обработка отменена. Готовый кеш сохранён.")
            except Exception as exc:
                logging.getLogger(__name__).exception("Processing run %s failed", run_id)
                message = safe_error(exc)
                run = store.get("processing_runs", run_id)
                store.put("processing_run_errors", uuid4().hex, {"run_id": run_id, "stage": run["stage"],
                    "document": run["current_document_id"], "page": run["current_page"], "message": message,
                    "error_type": type(exc).__name__, "fatal": True})
                self.finish(store, run_id, "FAILED", message, fatal=True)
            finally:
                if pipeline:
                    pipeline.close()
        return result

    def finish(self, store, run_id, status, message, snapshot=None, *, fatal=False):
        with self.mutation_lock:
            run = store.get("processing_runs", run_id)
            run.update(status=status, finished_at=now(), last_message=message)
            if status in {"COMPLETED", "PARTIAL"}:
                run.update(stage="DONE", progress_percent=100)
            if snapshot:
                run['fixed_chunks'] = snapshot.get('indexes', {}).get('fixed', {}).get('chunks', 0)
                run['structure_chunks'] = snapshot.get('indexes', {}).get('structure', {}).get('chunks', 0)
                run["index_run_ids"] = {k: v["run_id"] for k, v in snapshot.get("indexes", {}).items()}
                run["evaluation_run_id"] = snapshot.get("evaluation", {}).get("run_id")
                run["metrics_json"]["result_status"] = snapshot.get("status")
                for idx in snapshot.get("indexes", {}).values():
                    for error in idx.get("errors", []):
                        store.put("processing_run_errors", digest([run_id, error]), {"run_id": run_id, "stage": error.get("stage"),
                            "document": Path(error.get("document", "")).name, "page": None, "message": safe_error(error.get("error", ""))})
            errors = [e for e in store.all("processing_run_errors") if e["run_id"] == run_id]
            run["errors_count"] = len(errors)
            if status == "FAILED" and errors and not fatal:
                message = errors[0]["message"]
                run["last_message"] = message
                run["stage"] = errors[0].get("stage") or run["stage"]
            store.put("processing_runs", run_id, run)
            store.event(run_id, {"run_id": run_id, "event_type": "run_failed" if status in {"FAILED", "INTERRUPTED"} else "run_completed",
                "stage": run["stage"], "progress": run["progress_percent"], "message": message, "timestamp": now(), "metrics": {"status": status}})

    def close(self):
        for run_id, signal in self.cancellations.items():
            if self.get(run_id)["status"] not in TERMINAL:
                signal.set()
        self.executor.shutdown(wait=True)
