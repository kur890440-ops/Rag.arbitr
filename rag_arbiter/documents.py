import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Page(BaseModel):
    document_id: str
    page_number: int
    image_path: str
    width: int
    height: int
    ocr_text: str = ""
    status: str = "RENDERED"
    image_hash: str = ""
    recognition_id: str = ""
    recognition_provider: str = ""
    raw_output_path: str = ""
    normalized_output_path: str = ""
    reliability: dict = Field(default_factory=dict)


class Block(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    block_type: str
    text: str
    reading_order: int
    bbox: dict | None = None
    section: str = ""
    content_hash: str
    recognition_id: str = ""


class Document(BaseModel):
    document_id: str
    source_path: str
    file_name: str
    title: str
    content_type: str = "application/pdf"
    content_hash: str
    page_count: int
    parser_version: str
    ocr_engine: str
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    pages: list[Page] = []
    blocks: list[Block] = []
    cache_identity: str = ""
    recognition_provider: str = ""

    status: str = "SUCCESS"
    failed_pages: list[int] = []


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    strategy: str
    strategy_version: str = "1"
    chunk_index: int
    text: str
    token_count: int
    content_hash: str
    page_start: int
    page_end: int
    section: str
    source_block_ids: list[str]
    char_start: int
    char_end: int
    recognition_provider: str = ""
    recognition_ids: list[str] = []
    source: str = "local file"
    file_name: str = ""
    title: str = ""
