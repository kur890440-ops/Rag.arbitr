from pathlib import Path
import tomllib
from pydantic import BaseModel, Field, model_validator


class RecognitionConfig(BaseModel):
    provider: str = "qwen3_vl"
    runtime: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M"
    device: str = "auto"
    quantization: str = "Q4_K_M"
    max_image_resolution: int = 1280
    max_generation_tokens: int = 8192
    context_tokens: int = 16384
    timeout: float = 180
    temperature: float = 0
    seed: int = 42
    repeat_penalty: float = Field(default=1.1, ge=1.0, le=2.0)
    repeat_last_n: int = Field(default=1024, ge=0)
    keep_alive: str = "5m"
    language_hints: list[str] = ["ru", "en"]
    recovery_output_tokens: int = Field(default=12288, ge=1)
    recovery_context_tokens: int = Field(default=16384, ge=1)
    recovery_retries: int = Field(default=1, ge=0, le=2)
    split_enabled: bool = True
    split_after_first_truncation: bool = False
    split_overlap: float = Field(default=0.04, ge=0, le=0.1)
    fallback_enabled: bool = True
    oom_image_resolution: int = Field(default=896, ge=256)

    @model_validator(mode="after")
    def valid(self):
        if self.provider not in {"qwen3_vl", "classic_ocr"} or self.runtime != "ollama":
            raise ValueError("Supported recognition providers: qwen3_vl / classic_ocr; VLM runtime: ollama")
        if self.device not in {"auto", "cuda", "cpu"}:
            raise ValueError("Recognition device must be auto/cuda/cpu")
        if min(self.max_image_resolution, self.max_generation_tokens, self.context_tokens, self.timeout) <= 0:
            raise ValueError("Recognition limits must be positive")
        if self.max_generation_tokens >= self.context_tokens:
            raise ValueError("Generation budget must leave context space for image and prompt")
        if self.temperature != 0:
            raise ValueError("Recognition requires deterministic temperature=0")
        if self.recovery_output_tokens >= self.recovery_context_tokens:
            raise ValueError("Recovery output must leave room for the image and prompt")
        return self


class Config(BaseModel):
    recognition: RecognitionConfig = Field(default_factory=RecognitionConfig)
    corpus_path: Path = Path("data/corpus")
    cache_path: Path = Path("data/cache")
    sqlite_path: Path = Path("data/sqlite/metadata.db")
    qdrant_path: Path = Path("data/qdrant")
    report_path: Path = Path("data/reports/day21.html")
    manifest_path: Path = Path("data/corpus/manifest.json")
    queries_path: Path = Path("evaluation_queries.json")
    model: str = "BAAI/bge-m3"
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    device: str = "auto"
    batch_size: int = 8
    ocr_backend: str = "rapidocr"
    ocr_languages: list[str] = ["iso:ru"]
    render_dpi: int = 200
    fixed_tokens: int = 1000
    fixed_overlap: int = 125
    fixed_max_tokens: int = 1000
    structure_max_tokens: int = 1000
    top_k: int = 5
    preview_chars: int = 700
    similarity: str = "Cosine"
    web_port: int = 8765
    web_host: str = "127.0.0.1"
    web_allowed_hosts: list[str] = Field(default_factory=list)
    web_data_path: Path = Path("data/web")
    uploads_path: Path = Path("data/uploads")
    max_upload_size: int = 100 * 1024 * 1024
    max_upload_files: int = 0  # 0: no file-count limit.
    max_upload_request_size: int = 1001 * 1024 * 1024
    snapshot_path: Path | None = None
    retry_mode: str = "failed"
    retry_document_id: str | None = None
    retry_page_number: int | None = None
    rechunk_source_run_id: str | None = None
    rechunk_document_id: str | None = None
    rechunk_strategies: list[str] = Field(default_factory=lambda: ["fixed", "structure"])
    rechunk_settings: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_settings(self):
        if not 0 <= self.fixed_overlap < self.fixed_tokens <= self.fixed_max_tokens:
            raise ValueError("Require 0 <= overlap < target <= max")
        if min(self.batch_size, self.render_dpi, self.structure_max_tokens, self.top_k) < 1:
            raise ValueError("Sizes must be positive")
        if self.device not in {"auto", "cpu", "cuda"} or self.ocr_backend != "rapidocr":
            raise ValueError("Unsupported device or OCR backend")
        if self.similarity != "Cosine":
            raise ValueError("Day21 uses Cosine")
        if not 1 <= self.web_port <= 65535 or min(self.max_upload_size, self.max_upload_files) < 0:
            raise ValueError("Invalid web port/upload limits")
        if self.max_upload_request_size < 1:
            raise ValueError("Upload request limit must be positive")
        return self

    @classmethod
    def load(cls, path="config.toml"):
        p = Path(path)
        return cls(**tomllib.loads(p.read_text(encoding="utf-8"))) if p.exists() else cls()

    def prepare(self):
        for p in [self.corpus_path, self.cache_path, self.sqlite_path.parent,
                  self.qdrant_path, self.report_path.parent, self.manifest_path.parent]:
            p.mkdir(parents=True, exist_ok=True)
