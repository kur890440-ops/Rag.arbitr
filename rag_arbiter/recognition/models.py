"""Page recognition transport types; Document/Page/Block remain in documents.py."""
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from pydantic import BaseModel, Field


class RecognitionRequest(BaseModel):
    document_id: str
    document_hash: str
    page_number: int = Field(ge=1)
    image_path: Path
    image_hash: str
    language_hints: list[str]
    render_settings: dict
    attempt_dir: Path | None = None
    fragment_id: str | None = None
    region: list[int] | None = None


class RecognizedBlock(BaseModel):
    type: str
    text: str
    order: int = Field(ge=0)
    bbox: dict | None = None
    uncertain: bool = False


class RecognizedTable(BaseModel):
    order: int = Field(ge=0)
    rows: list[list[str]] = []
    markdown: str = ""
    uncertain: bool = False
    bbox: dict | None = None


class PageOutput(BaseModel):
    page_number: int = Field(ge=1)
    blocks: list[RecognizedBlock]
    tables: list[RecognizedTable]


class ContentBlock(BaseModel):
    type: str
    text: str


class ContentOutput(BaseModel):
    blocks: list[ContentBlock]


class RecognitionResult(BaseModel):
    provider: str
    model: str
    model_version: str
    runtime: str
    device: str
    quantization: str
    page_number: int
    raw_output: str
    normalized_text: str = ""
    blocks: list[RecognizedBlock] = []
    tables: list[RecognizedTable] = []
    reading_order: list[int] = []
    status: Literal["SUCCESS", "UNCERTAIN", "EMPTY", "FAILED"] = "SUCCESS"
    duration_ms: float = 0
    diagnostics: dict = Field(default_factory=dict)
    docling_document: dict | None = None


@runtime_checkable
class DocumentRecognitionProvider(Protocol):
    def preflight(self) -> dict: ...
    def identity(self) -> dict: ...
    def recognize_page(self, request: RecognitionRequest) -> RecognitionResult: ...
    def close(self) -> None: ...


class RuntimeResponse(BaseModel):
    raw: str
    content: str
    device: str
    diagnostics: dict = Field(default_factory=dict)


class VisionModelRuntime(Protocol):
    def preflight(self) -> dict: ...
    def generate(self, request: RecognitionRequest, prompt: str, schema: dict) -> RuntimeResponse: ...
    def close(self) -> None: ...
