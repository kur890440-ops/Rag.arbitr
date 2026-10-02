import json
from pathlib import Path
import pytest
from rag_arbiter.chunking import FixedSizeChunker, StructureAwareChunker, coverage, source
from rag_arbiter.documents import digest
from rag_arbiter.storage import MetadataStore
from rag_arbiter.ingestion import Ingestor, render_pages, freeze_corpus
from rag_arbiter.embeddings import EmbeddingCache
from rag_arbiter.vectorstore import LocalVectorStore
from rag_arbiter.retrieval import SemanticRetriever, validate_comparison
from rag_arbiter.evaluation import metrics, evaluate
from rag_arbiter.reporting import generate_report
from rag_arbiter.pipeline import Pipeline
from conftest import WordTokenizer, FakeEmbedding, make_scanned_pdf


def test_render_and_reuse(config):
    path = config.corpus_path / "scan.pdf"
    make_scanned_pdf(path)
    pages = render_pages(path, "doc", config.cache_path, 72)
    before = Path(pages[0].image_path).stat().st_mtime_ns
    again = render_pages(path, "doc", config.cache_path, 72)
    assert pages[0].width == 600 and pages[0].height == 700
    assert before == Path(again[0].image_path).stat().st_mtime_ns


def test_sqlite_models(config, doc):
    store = MetadataStore(config.sqlite_path)
    store.document(doc)
    assert store.get("documents", "doc")["page_count"] == 2
    assert {b["page_number"] for b in store.all("document_blocks")} == {1, 2}
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 8
    store.close()


@pytest.mark.parametrize("strategy", ["fixed", "structure"])
def test_chunk_determinism_coverage_provenance(doc, strategy):
    tokenizer = WordTokenizer()
    chunker = FixedSizeChunker(tokenizer, 8, 2, 8) if strategy == "fixed" else StructureAwareChunker(tokenizer, 8)
    chunks = chunker.chunk(doc)
    assert chunks == chunker.chunk(doc)
    assert all(c.token_count <= 8 and c.source_block_ids and c.page_start <= c.page_end for c in chunks)
    stats = coverage(doc, chunks, tokenizer)
    assert stats["unique_coverage"] == 1
    assert (stats["overlap_characters"] > 0) == (strategy == "fixed")
    with pytest.raises(ValueError, match="Content lost"):
        coverage(doc, chunks[:-1], tokenizer)


def test_structure_boundaries_and_table(doc):
    chunks = StructureAwareChunker(WordTokenizer(), 100).chunk(doc)
    table = next(c for c in chunks if "| Товар" in c.text)
    assert table.source_block_ids == ["2"] and table.page_start == 2
    assert len(chunks) == 3
    assert chunks != FixedSizeChunker(WordTokenizer(), 100, 10, 100).chunk(doc)


def test_oversized_unicode(doc):
    doc.blocks[1].text = "Слово. " * 100
    chunks = StructureAwareChunker(WordTokenizer(), 7).chunk(doc)
    assert max(c.token_count for c in chunks) <= 7
    assert coverage(doc, chunks, WordTokenizer())["unique_coverage"] == 1


def test_embedding_cache(config, doc):
    store = MetadataStore(config.sqlite_path)
    chunks = FixedSizeChunker(WordTokenizer(), 8, 2, 8).chunk(doc)
    cache = EmbeddingCache(config, store, FakeEmbedding())
    first, created, reused = cache.encode(chunks)
    second, created2, reused2 = cache.encode(chunks)
    assert created == len(chunks) and reused == 0
    assert created2 == 0 and reused2 == len(chunks)
    assert all((a == b).all() for a, b in zip(first, second))
    store.close()


def test_qdrant_persistence_topk_payload(config, doc):
    provider = FakeEmbedding()
    chunks = FixedSizeChunker(provider.tokenizer, 8, 2, 8).chunk(doc)
    vectors = LocalVectorStore(config.qdrant_path)
    for name in ("day21_fixed", "day21_structure"):
        vectors.create(name, provider.dimension)
    vectors.upsert("day21_fixed", chunks, provider.encode([c.text for c in chunks]), doc.file_name)
    vectors.close()
    vectors = LocalVectorStore(config.qdrant_path)
    hits = vectors.search("day21_fixed", provider.encode([chunks[0].text])[0], 3)
    assert len(hits) == 3 and hits[0].payload["chunk_id"] == chunks[0].chunk_id
    assert {"document_id", "strategy", "page_start", "page_end", "section", "token_count", "content_hash", "file_name"} <= hits[0].payload.keys()
    assert vectors.search("day21_structure", provider.encode(["q"])[0], 3) == []
    vectors.close()


def valid_index():
    return {"corpus_id": "id", "corpus_hash": "hash", "normalized_hash": "norm", "embedding": {"model": "test"},
            "dimension": 8, "similarity": "Cosine", "document_ids": ["doc"], "status": "SUCCESS"}


@pytest.mark.parametrize("field", ["corpus_id", "corpus_hash", "normalized_hash", "embedding", "dimension", "similarity", "document_ids", "status"])
def test_comparison_rejects_mismatch(field):
    a, b = valid_index(), valid_index()
    validate_comparison(a, b)
    b[field] = "mismatch"
    with pytest.raises(ValueError):
        validate_comparison(a, b)


def test_evaluation_metrics():
    hits = [{"rank": 1, "document_id": "other", "file_name": "other.pdf", "page_start": 1, "page_end": 1, "section": "S"},
            {"rank": 3, "document_id": "doc", "file_name": "doc.pdf", "page_start": 2, "page_end": 4, "section": "A / B"}]
    result = metrics(hits, {"expected_document": "doc.pdf", "expected_page": 3, "expected_section": "B"})
    assert result == {"Hit@1": 0, "Hit@3": 1, "Hit@5": 1, "rank": 3, "page_hit": 1, "page_rank": 3, "section_hit": 1, "section_rank": 3}


class FakeOCR:
    def __init__(self):
        self.calls = 0

    def identity(self):
        return {"parser": "test", "backend": "test", "version": "1"}

    def convert_page(self, path):
        from docling_core.types.doc import DoclingDocument, DocItemLabel, ProvenanceItem, BoundingBox
        self.calls += 1
        doc = DoclingDocument(name="test")
        doc.add_text(label=DocItemLabel.TEXT, text="Synthetic OCR fixture text for testing only.",
                     prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=100), charspan=(0, 43)))
        return doc


def test_ingest_cache_failure_and_change(config):
    good = config.corpus_path / "good.pdf"
    other = config.corpus_path / "other.pdf"
    bad = config.corpus_path / "bad.pdf"
    make_scanned_pdf(good)
    make_scanned_pdf(other)
    bad.write_bytes(b"not a PDF")
    store = MetadataStore(config.sqlite_path)
    provider = FakeOCR()
    ingest = Ingestor(config, store, provider)
    docs, stats = ingest.ingest([good, other, bad])
    assert len(docs) == 2 and len(stats["errors"]) == 1 and provider.calls == 2
    assert docs[0].pages[0].ocr_text and docs[0].blocks[0].page_number == 1
    ingest.ingest([good, other])
    assert provider.calls == 2
    good.write_bytes(good.read_bytes() + b"\n%changed")
    new_docs, stats = ingest.ingest([good, other])
    assert provider.calls == 3 and stats["reused_pages"] == 1
    assert new_docs[0].document_id != docs[0].document_id
    assert new_docs[1].cache_identity == docs[1].cache_identity
    store.close()


def test_pipeline_repeat_partial_and_report(config):
    good = config.corpus_path / "good.pdf"
    make_scanned_pdf(good)
    pipeline = Pipeline(config, FakeEmbedding(), FakeOCR())
    first = pipeline.day21()
    second = pipeline.day21()
    assert first["indexes"]["fixed"]["status"] == "SUCCESS"
    assert second["ocr"]["processed_pages"] == 0
    assert second["indexes"]["fixed"]["embeddings_created"] == 0
    assert second["indexes"]["fixed"]["embeddings_reused"] > 0
    assert first["indexes"]["fixed"]["collection"] != first["indexes"]["structure"]["collection"]
    html = config.report_path.read_text(encoding="utf-8")
    assert "FIXED" in html and "STRUCTURE" in html
    (config.corpus_path / "broken.pdf").write_bytes(b"broken")
    partial = pipeline.day21()
    assert partial["indexes"]["fixed"]["status"] == "PARTIAL"
    assert partial["evaluation"]["status"] == "QUERIES_REQUIRED"
    pipeline.close()


def test_empty_corpus_no_model_load(config):
    pipeline = Pipeline(config)
    result = pipeline.day21()
    assert result["status"] == "CORPUS REQUIRED" and pipeline.provider is None
    assert "No corpus documents found" in config.report_path.read_text(encoding="utf-8")
    pipeline.close()


def test_report_escapes_html(config):
    generate_report(config, {"status": "<script>alert(1)</script>"})
    assert "<script>" not in config.report_path.read_text(encoding="utf-8")


def test_independent_project():
    for path in Path("rag_arbiter").glob("*.py"):
        assert "workshop_agent" not in path.read_text(encoding="utf-8").lower()
