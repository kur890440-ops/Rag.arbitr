import json
import sqlite3
from pathlib import Path
import pytest
from rag_arbiter.config import Config, RecognitionConfig
from rag_arbiter.documents import file_hash, save_json
from rag_arbiter.recognition import (DocumentRecognitionProvider, RecognitionRequest,
    Qwen3VLRecognitionProvider, ClassicOCRRecognitionProvider)
from rag_arbiter.recognition.models import RuntimeResponse
from rag_arbiter.recognition.cache import RecognitionCache
from rag_arbiter.recognition.runtime import OllamaRuntime, RuntimeUnavailable
from rag_arbiter.storage import MetadataStore
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.retrieval import SemanticRetriever, validate_comparison
from rag_arbiter.chunking import StructureAwareChunker, coverage
from conftest import FakeEmbedding, WordTokenizer, make_scanned_pdf
from test_core import FakeOCR


def page_output(page=1):
    return {"page_number": page, "blocks": [
        {"type": "heading", "text": "Договор", "order": 0},
        {"type": "paragraph", "text": "Оплата через десять дней.", "order": 1}],
        "tables": [{"order": 2, "rows": [["Товар", "Цена"], ["Хлеб", "100"]]}]}


class FakeRuntime:
    def __init__(self, content=None):
        self.content = content
        self.calls = 0
        self.version = "sha256:test"
        self.done_reason = "stop"

    def preflight(self):
        return {"model": "hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M", "model_version": self.version, "runtime": "ollama",
                "runtime_version": "test", "quantization": "Q4_K_M", "effective_device": "gpu", "size_vram": 123}

    def generate(self, page_request, prompt, schema):
        self.calls += 1
        assert "never invent" in prompt and schema["properties"]["blocks"]
        content = self.content if self.content is not None else json.dumps(page_output(page_request.page_number), ensure_ascii=False)
        return RuntimeResponse(raw=json.dumps({"message": {"content": content}}, ensure_ascii=False), content=content,
                               device="gpu", diagnostics={"done": True, "done_reason": self.done_reason})

    def close(self):
        pass


@pytest.fixture
def page_request(config):
    from PIL import Image
    path = config.cache_path / "страница с пробелами.png"
    Image.new("RGB", (20, 30), "white").save(path)
    return RecognitionRequest(document_id="d", document_hash="hash", page_number=1,
        image_path=path, image_hash=file_hash(path), language_hints=["ru"], render_settings={"dpi": 200})


def test_default_and_contract(config, page_request):
    assert Config().recognition.provider == "qwen3_vl"
    provider = Qwen3VLRecognitionProvider(config, FakeRuntime())
    assert isinstance(provider, DocumentRecognitionProvider)
    result = provider.recognize_page(page_request)
    assert result.status == "SUCCESS" and result.provider == "qwen3_vl"
    assert result.blocks[0].type == "heading" and result.page_number == 1
    assert result.docling_document["schema_name"] == "DoclingDocument"
    assert result.tables and "Хлеб" in result.normalized_text
    assert all(b.bbox is None for b in result.blocks)


def test_classic_same_contract(config, page_request):
    provider = ClassicOCRRecognitionProvider(config, FakeOCR())
    assert isinstance(provider, DocumentRecognitionProvider)
    result = provider.recognize_page(page_request)
    assert result.status == "SUCCESS" and result.provider == "classic_ocr"
    assert result.page_number == page_request.page_number and result.normalized_text and result.raw_output


@pytest.mark.parametrize("kind", ["malformed", "truncated", "wrong_page"])
def test_invalid_response_keeps_raw(config, page_request, kind):
    output = page_output()
    if kind == "wrong_page":
        output["page_number"] = 99
    if kind == "duplicate_order":
        output["tables"][0]["order"] = 0
    runtime = FakeRuntime("{broken" if kind == "malformed" else json.dumps(output))
    if kind == "truncated":
        runtime.done_reason = "length"
    store = MetadataStore(config.sqlite_path)
    try:
        provider = Qwen3VLRecognitionProvider(config, runtime)
        cache = RecognitionCache(config, store, provider)
        key, result, metadata, reused = cache.recognize(page_request, provider.identity())
        assert result.status == "FAILED" and not reused
        assert Path(metadata["raw_output_path"]).read_text(encoding="utf-8") == result.raw_output
        assert Path(metadata["normalized_output_path"]).exists()
        assert "raw_output" not in store.get("recognition_metadata", key)
        cache.recognize(page_request, provider.identity())
        assert runtime.calls == 2  # Explicit repeat retries failed pages; each attempt remains archived.
    finally:
        store.close()


@pytest.mark.parametrize("change", ["model", "settings", "image", "render", "language"])
def test_recognition_cache_identity(config, page_request, change):
    store = MetadataStore(config.sqlite_path)
    runtime = FakeRuntime()
    provider = Qwen3VLRecognitionProvider(config, runtime)
    cache = RecognitionCache(config, store, provider)
    try:
        first = cache.recognize(page_request, provider.identity())
        assert cache.recognize(page_request, provider.identity())[3]
        if change == "model":
            runtime.version = "sha256:changed"
            provider.preflight()
        elif change == "settings":
            config.recognition.max_image_resolution = 640
        elif change == "image":
            page_request.image_hash = "changed-image"
        elif change == "render":
            page_request.render_settings["dpi"] = 150
        else:
            page_request.language_hints = ["en"]
        second = cache.recognize(page_request, provider.identity())
        assert first[0] != second[0] and not second[3] and runtime.calls == 2
    finally:
        store.close()


def test_unknown_type_uncertain_and_bbox(config, page_request):
    output = page_output()
    output["blocks"][0].update(type="surprise", bbox={"invented": 1}, uncertain=True, text="[UNREADABLE]")
    result = Qwen3VLRecognitionProvider(config, FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.status == "UNCERTAIN" and result.blocks[0].type == "other"
    assert result.blocks[0].bbox is None and result.diagnostics["normalization"]


def test_fenced_json(config, page_request):
    result = Qwen3VLRecognitionProvider(config, FakeRuntime("```json\n" + json.dumps(page_output()) + "\n```")).recognize_page(page_request)
    assert result.status == "SUCCESS"


def test_shared_document_trace_and_switch(config):
    make_scanned_pdf(config.corpus_path / "с пробелом.pdf", pages=2)
    runtime = FakeRuntime()
    recognizer = Qwen3VLRecognitionProvider(config, runtime)
    pipeline = Pipeline(config, FakeEmbedding(), recognizer)
    try:
        docs, snapshot = pipeline.ingest()
        assert runtime.calls == 2 and len(docs[0].pages) == 2
        fixed = pipeline.build_index(docs, snapshot, "fixed")
        structure = pipeline.build_index(docs, snapshot, "structure")
        assert runtime.calls == 2
        validate_comparison(fixed, structure)
        assert fixed["normalized_hash"] == structure["normalized_hash"]
        assert fixed["embedding"]["model"] == "TEST_ONLY"  # Explicit embedding provider, never Qwen.
        table_chunks = StructureAwareChunker(WordTokenizer(), 100).chunk(docs[0])
        assert any(c.text.strip().startswith("|") and len(c.source_block_ids) == 1 for c in table_chunks)
        retriever = SemanticRetriever(pipeline.provider, pipeline.vectors, pipeline.store)
        hits = retriever.compare("Оплата", snapshot["indexes"], 5)["fixed"]["hits"]
        original_chunk = hits[0]["chunk_id"]
        for h in hits:
            assert h["recognition_provider"] == "qwen3_vl"
            for s in h["provenance"]["sources"]:
                assert Path(s["recognition"]["raw_output_path"]).exists()
                assert Path(s["recognition"]["normalized_output_path"]).exists()
                assert Path(s["page"]["image_path"]).exists()
                assert s["page"]["image_hash"] == s["recognition"]["image_hash"]
        repeated = pipeline.day21()
        assert runtime.calls == 2 and repeated["recognition"]["recognition_reused"] == 2
        pipeline.ocr = ClassicOCRRecognitionProvider(config, FakeOCR())
        switched = pipeline.day21()
        assert switched["indexes"]["fixed"]["status"] == "SUCCESS"
        assert switched["indexes"]["fixed"]["normalized_hash"] != fixed["normalized_hash"]
        # Historical provenance must still point to Qwen after classic overwrites current document.
        assert all(s["recognition"]["provider"] == "qwen3_vl" for s in pipeline.store.trace_chunk(original_chunk)["sources"])
    finally:
        pipeline.close()


def test_sqlite_v1_migrates_without_loss(config):
    db = sqlite3.connect(config.sqlite_path)
    db.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    db.execute("INSERT INTO documents VALUES ('legacy', '{}')")
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    store = MetadataStore(config.sqlite_path)
    assert store.get("documents", "legacy") == {}
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 10
    assert store.all("recognition_metadata") == []
    store.close()


@pytest.mark.parametrize("endpoint", ["https://api.example.com", "http://192.168.1.1:11434", "http://127.0.0.1.evil.com:11434", "http://user@localhost:11434", "http://localhost:11434/api"])
def test_no_remote_endpoints(endpoint):
    with pytest.raises(ValueError, match="loopback"):
        OllamaRuntime(RecognitionConfig(endpoint=endpoint))


def test_cpu_options():
    runtime = OllamaRuntime(RecognitionConfig(device="cpu"))
    assert runtime.options()["num_gpu"] == 0 and runtime.options()["temperature"] == 0


def test_missing_model_actionable():
    runtime = OllamaRuntime(RecognitionConfig())
    runtime.request = lambda path, *args, **kwargs: ({"version": "test"} if path == "/api/version" else {"models": []}, "")
    with pytest.raises(RuntimeUnavailable, match="ollama pull hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M"):
        runtime.preflight()


def test_stop_after_first_vlm_runtime_failure(config):
    make_scanned_pdf(config.corpus_path / "a.pdf", pages=2)
    make_scanned_pdf(config.corpus_path / "b.pdf")
    class BrokenRuntime(FakeRuntime):
        def generate(self, *args):
            self.calls += 1
            raise RuntimeUnavailable("Ollama unavailable")
    runtime = BrokenRuntime()
    pipeline = Pipeline(config, FakeEmbedding(), Qwen3VLRecognitionProvider(config, runtime))
    try:
        docs, snapshot = pipeline.ingest()
        assert docs == [] and runtime.calls == 3 and snapshot["recognition"]["errors"]
        assert len(pipeline.store.all("recognition_metadata")) == 3
    finally:
        pipeline.close()


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_page_order_failure_does_not_stop_batch(config, legacy_cache):
    make_scanned_pdf(config.corpus_path / "a.pdf", pages=2)
    make_scanned_pdf(config.corpus_path / "b.pdf")
    class FirstPageInvalid(FakeRuntime):
        def generate(self, request, *args):
            output = page_output(request.page_number)
            if self.calls == 0:
                output["tables"][0]["order"] = 0
            self.content = json.dumps(output)
            return super().generate(request, *args)
    runtime = FirstPageInvalid()
    pipeline = Pipeline(config, FakeEmbedding(), Qwen3VLRecognitionProvider(config, runtime))
    try:
        if legacy_cache:
            # Seed an old failed cache entry with no failure_scope field.
            from rag_arbiter.ingestion import Ingestor
            Ingestor(config, pipeline.store, pipeline.ocr).ingest([config.corpus_path / "a.pdf"])
            for metadata in pipeline.store.all("recognition_metadata"):
                path = Path(metadata["normalized_output_path"])
                data = json.loads(path.read_text(encoding="utf-8"))
                data["diagnostics"].pop("failure_scope", None)
                save_json(path, data)
        snapshot = pipeline.day21(do_evaluate=False)
        assert snapshot["status"] == "SUCCESS"
        assert runtime.calls == 3  # Failed first page, then the next document; no retry.
        assert len(snapshot["recognition"]["errors"]) == 0
        for index in snapshot["indexes"].values():
            assert index["documents"] == 2 and index["chunks"] > 0
        assert [d["status"] for d in snapshot["corpus"]["documents"]] == ["SUCCESS", "SUCCESS"]
    finally:
        pipeline.close()


def test_identical_text_new_recognition_has_distinct_chunk_ids(doc):
    chunker = StructureAwareChunker(WordTokenizer(), 100)
    doc.cache_identity = "recognition-A"
    first = chunker.chunk(doc)
    doc.cache_identity = "recognition-B"
    second = chunker.chunk(doc)
    assert [c.text for c in first] == [c.text for c in second]
    assert set(c.chunk_id for c in first).isdisjoint(c.chunk_id for c in second)


def test_fake_gpu_detection_does_not_override_runtime_cpu():
    runtime = OllamaRuntime(RecognitionConfig(device="cuda"))
    replies = {"/api/version": {"version": "test"}, "/api/tags": {"models": [{"name": runtime.settings.model, "digest": "modelhash"}]},
               "/api/show": {"details": {"family": "qwen3vl", "quantization_level": "Q4_K_M"},
                             "model_info": {"general.parameter_count": 2_000_000_000}, "capabilities": ["vision"]},
               "/api/generate": {}, "/api/ps": {"models": [{"name": runtime.settings.model, "size_vram": 0, "size": 1000}]}}
    runtime.request = lambda path, *args, **kwargs: (replies[path], "")
    with pytest.raises(RuntimeUnavailable, match="no GPU model allocation"):
        runtime.preflight()
    runtime.close()


def test_exact_duplicate_table_rows_preserve_one_table(config, page_request):
    output = page_output()
    output["blocks"].extend([{"type": "table", "text": "Товар Цена", "order": 2},
                             {"type": "table", "text": "Хлеб 100", "order": 3}])
    result = Qwen3VLRecognitionProvider(config, FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.status == "SUCCESS"
    assert len([b for b in result.blocks if b.type == "table"]) == 1
    assert result.tables[0].rows == output["tables"][0]["rows"]
    assert result.normalized_text.count("Хлеб") == 1
    assert len(result.diagnostics["normalization"]) == 2


def test_table_text_at_other_position_is_not_silently_dropped(config, page_request):
    output = page_output()
    output["blocks"].append({"type": "table", "text": "Хлеб 100", "order": 10})
    result = Qwen3VLRecognitionProvider(config, FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.status == "SUCCESS"
    assert result.normalized_text.count("Хлеб") == 2


def test_ambiguous_table_text_preserves_both_and_marks_uncertainty(config, page_request):
    output = page_output()
    output["blocks"].append({"type": "table", "text": "Дополнительные сведения и примечание", "order": 2})
    result = Qwen3VLRecognitionProvider(config, FakeRuntime(json.dumps(output))).recognize_page(page_request)
    assert result.status == "UNCERTAIN"
    assert "Дополнительные сведения" in result.normalized_text and "Хлеб" in result.normalized_text
    assert len(set(result.reading_order)) == len(result.blocks)
    table_block = next(b for b in result.blocks if b.type == "table")
    assert table_block.order == result.tables[0].order and table_block.uncertain
    assert result.tables[0].rows == output["tables"][0]["rows"]
    assert any("both preserved" in message for message in result.diagnostics["normalization"])


def test_repetition_window_is_explicit_and_part_of_recognition_identity(config):
    assert OllamaRuntime(config.recognition).options()["repeat_last_n"] == 1024
    first = Qwen3VLRecognitionProvider(config, FakeRuntime()).identity()
    changed = config.model_copy(deep=True)
    changed.recognition.repeat_last_n = 2048
    second = Qwen3VLRecognitionProvider(changed, FakeRuntime()).identity()
    assert first["settings"] != second["settings"]
    assert OllamaRuntime(changed.recognition).options()["repeat_penalty"] == 1.1


def test_failed_recognition_does_not_load_embeddings(config):
    make_scanned_pdf(config.corpus_path / "a.pdf")
    pipeline = Pipeline(config, ocr=Qwen3VLRecognitionProvider(config, FakeRuntime("bad json")))
    try:
        snapshot = pipeline.day21()
        assert snapshot["status"] == "FAILED" and pipeline.provider is None
        assert snapshot["recognition"]["backend"] == "qwen3_vl"
        assert config.report_path.exists()
    finally:
        pipeline.close()
