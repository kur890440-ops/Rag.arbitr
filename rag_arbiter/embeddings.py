import os
import threading
import time
from typing import Protocol
from importlib.metadata import version
import numpy as np
from .documents import digest
from .chunking import ModelTokenizer


class EmbeddingProvider(Protocol):
    identity: dict
    dimension: int
    tokenizer: ModelTokenizer
    def encode(self, texts: list[str]) -> np.ndarray: ...


class BgeM3EmbeddingProvider:
    def __init__(self, config):
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import torch
        from sentence_transformers import SentenceTransformer
        self._encode_lock = threading.RLock()
        self.config = config
        self.device = "cuda" if config.device == "auto" and torch.cuda.is_available() else config.device
        if self.device == "auto":
            self.device = "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        self.model = SentenceTransformer(config.model, revision=config.model_revision,
            device=self.device, cache_folder=str(config.cache_path / "models"), trust_remote_code=False)
        self.dimension = self.model.get_embedding_dimension()
        self.tokenizer = ModelTokenizer(self.model.tokenizer)
        resolved = getattr(self.model[0].auto_model.config, "_commit_hash", None)
        if not resolved:
            raise RuntimeError("Cannot resolve immutable model revision")
        self.identity = {"model": config.model, "revision": resolved, "normalization": "L2",
                         "library": version("sentence-transformers"), "dimension": self.dimension,
                         "device": self.device, "max_seq_length": self.model.max_seq_length}

    def encode(self, texts):
        with self._encode_lock:
            return self._encode(texts)

    def _encode(self, texts):
        for text in texts:
            if len(self.model.tokenizer.encode(text)) > self.model.max_seq_length:
                raise ValueError("Embedding input exceeds model capacity; refusing silent truncation")
        vectors = self.model.encode(texts, batch_size=self.config.batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        if vectors.shape != (len(texts), self.dimension) or not np.isfinite(vectors).all():
            raise ValueError("Invalid embedding output")
        return vectors


class EmbeddingCache:
    def __init__(self, config, store, provider):
        self.config, self.store, self.provider = config, store, provider
        self.folder = config.cache_path / "embeddings"
        self.folder.mkdir(parents=True, exist_ok=True)

    def encode(self, chunks):
        result = [None] * len(chunks)
        pending, created, reused = {}, 0, 0
        for i, chunk in enumerate(chunks):
            key = digest([chunk.content_hash, self.provider.identity])
            path = self.folder / f"{key}.npy"
            if path.exists():
                vector = np.load(path, allow_pickle=False)
                if vector.shape != (self.provider.dimension,) or not np.isfinite(vector).all():
                    raise ValueError(f"Invalid cached vector: {key}")
                result[i] = vector
                reused += 1
            else:
                pending.setdefault(key, []).append(i)
        keys = list(pending)
        for start in range(0, len(keys), self.config.batch_size):
            batch = keys[start:start + self.config.batch_size]
            vectors = self.provider.encode([chunks[pending[k][0]].text for k in batch])
            for key, vector in zip(batch, vectors):
                path = self.folder / f"{key}.npy"
                with path.with_suffix(".tmp").open("wb") as f:
                    np.save(f, vector)
                path.with_suffix(".tmp").replace(path)
                self.store.put("embeddings_metadata", key, {"content_hash": chunks[pending[key][0]].content_hash,
                    "model": self.provider.identity, "path": str(path), "dimension": len(vector)})
                for i in pending[key]:
                    result[i] = vector
                created += 1
                reused += len(pending[key]) - 1
        return result, created, reused


class ApplicationEmbeddingResource:
    """One lazy embedding model per RunService, not a process-wide singleton.

    SQLite and Qdrant remain operation-owned. Publish only a fully initialized
    model; a failed load leaves no ready object. A later request may retry once.
    """
    def __init__(self, factory=BgeM3EmbeddingProvider):
        self.factory=factory
        self._lock=threading.RLock()
        self._provider=None
        self._key=None
        self.initializations=0
        self.attempts=0
        self.last_event={}

    def get(self, config):
        key=(config.model,config.model_revision,config.device,str(config.cache_path.absolute()),config.batch_size)
        with self._lock:
            if self._provider is not None and self._key==key:return self._provider
            # Release previous configuration before loading another large model.
            self._provider=None;self._key=None
            import psutil
            process=psutil.Process();started=time.perf_counter();self.attempts+=1
            try:
                candidate=self.factory(config)
            except Exception as exc:
                self.last_event=dict(stage='EMBEDDING_INITIALIZATION',status='FAILED',error_type=type(exc).__name__,pid=os.getpid(),duration_ms=(time.perf_counter()-started)*1000)
                raise
            self._provider=candidate;self._key=key;self.initializations+=1
            memory=process.memory_info()
            self.last_event=dict(stage='EMBEDDING_INITIALIZATION',status='READY',pid=os.getpid(),threads=process.num_threads(),rss=memory.rss,peak_rss=getattr(memory,'peak_wset',None),duration_ms=(time.perf_counter()-started)*1000,initializations=self.initializations,attempts=self.attempts,identity=candidate.identity)
            return candidate

    def close(self):
        with self._lock:self._provider=None;self._key=None
