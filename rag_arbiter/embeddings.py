import os
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
