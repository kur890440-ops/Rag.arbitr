from importlib.metadata import version
from ..recognition.runtime import OllamaRuntime, gpu_diagnostics


class SystemStatusService:
    def __init__(self, config):
        self.config = config

    def status(self):
        from ..llm import llm_status
        import torch
        result = {"gpu": gpu_diagnostics(), "torch_cuda_available": torch.cuda.is_available(),
            "recognition_provider": self.config.recognition.provider, "recognition_model": self.config.recognition.model,
            "runtime": self.config.recognition.runtime, "embedding_model": self.config.model,
            "embedding_device": "cuda" if self.config.device == "auto" and torch.cuda.is_available() else "cpu" if self.config.device == "auto" else self.config.device,
            "docling": version("docling"), "qdrant": {"mode": "Local", "version": version("qdrant-client"), "directory_exists": self.config.qdrant_path.exists()},
            "sqlite": {"exists": self.config.sqlite_path.exists()}}
        try:
            import sqlite3
            with sqlite3.connect(f"file:{self.config.sqlite_path.resolve().as_posix()}?mode=ro", uri=True) as db:
                result["sqlite"].update(schema_version=db.execute("PRAGMA user_version").fetchone()[0], status="available")
        except Exception:
            result["sqlite"]["status"] = "unavailable"
        try:
            runtime = OllamaRuntime(self.config.recognition)
            result["runtime_status"] = runtime.request("/api/version", timeout=2)[0]
            tags = runtime.request("/api/tags", timeout=2)[0]
            result["model_installed"] = any(m["name"] == self.config.recognition.model for m in tags.get("models", []))
            result["placement"] = runtime.placement()  # Read only; status never loads a model.
        except Exception:
            result.update(runtime_status={"status": "unavailable"}, model_installed=None, placement={"effective_device": "unknown"})
        result['llm'] = llm_status(self.config.llm)
        from ..local_llm import LocalLLMProvider
        local = self.config.llm.local
        result['local_generation'] = dict(enabled=local.enabled, base_url=local.base_url, model=local.model)
        try:
            result['local_generation'].update(LocalLLMProvider(local).inspect(), status='READY')
        except Exception:
            result['local_generation']['status'] = 'LOCAL_GENERATION_UNAVAILABLE'
        if result['llm']['status']=='READY':
            try:
                with sqlite3.connect(f"file:{self.config.sqlite_path.resolve().as_posix()}?mode=ro",uri=True) as db:
                    row=db.execute("SELECT json_extract(data,'$.no_rag_result.status'),json_extract(data,'$.rag_result.status') FROM rag_comparison_runs WHERE COALESCE(json_extract(data,'$.manual_experiment'),0)=0 ORDER BY rowid DESC LIMIT 1").fetchone()
                    if row and 'ERROR' in row:result['llm']['status']='ERROR'
            except Exception:
                pass
        return result
