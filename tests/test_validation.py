import pytest
from rag_arbiter.chunking import FixedSizeChunker, StructureAwareChunker, coverage, source
from rag_arbiter.documents import save_json
from rag_arbiter.evaluation import evaluate, metrics
from rag_arbiter.storage import MetadataStore
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.config import Config
from conftest import WordTokenizer, FakeEmbedding, make_scanned_pdf
from test_core import valid_index, FakeOCR


@pytest.mark.parametrize("settings", [{"fixed_overlap": 1000}, {"batch_size": 0}, {"device": "remote"}])
def test_invalid_config(settings):
    with pytest.raises(ValueError):
        Config(**settings)


def test_fixed_overlap_exact_tokens(doc):
    tokenizer = WordTokenizer()
    chunks = FixedSizeChunker(tokenizer, 8, 2, 8).chunk(doc)
    text, _ = source(doc)
    for a, b in zip(chunks, chunks[1:]):
        assert tokenizer.count(text[b.char_start:a.char_end]) == 2


def test_coverage_detects_altered_content(doc):
    chunks = StructureAwareChunker(WordTokenizer(), 8).chunk(doc)
    chunks[0].text = "LOST"
    with pytest.raises(ValueError, match="differs"):
        coverage(doc, chunks, WordTokenizer())


def test_empty_document(doc):
    doc.blocks = []
    assert FixedSizeChunker(WordTokenizer()).chunk(doc) == []
    assert StructureAwareChunker(WordTokenizer()).chunk(doc) == []
    assert coverage(doc, [], WordTokenizer())["unique_coverage"] == 1


@pytest.mark.parametrize("bad", ["hash", "top_k", "duplicate", "page"])
def test_evaluation_rejects_invalid_dataset(config, bad):
    q = {"query_id": "q", "question": "test", "expected_document": "test.pdf"}
    dataset = {"corpus_hash": "hash", "queries": [q]}
    if bad == "hash":
        dataset["corpus_hash"] = "other"
    elif bad == "top_k":
        config.top_k = 3
    elif bad == "duplicate":
        dataset["queries"].append(q.copy())
    elif bad == "page":
        q["expected_page"] = 0
    save_json(config.queries_path, dataset)
    store = MetadataStore(config.sqlite_path)
    try:
        with pytest.raises(ValueError):
            evaluate(config, {"fixed": valid_index(), "structure": valid_index()}, None, store)
    finally:
        store.close()


def test_no_relevant_hits():
    assert metrics([], {"expected_document": "missing"}) == {"Hit@1": 0, "Hit@3": 0, "Hit@5": 0, "rank": None}


def test_stale_corpus_rejected(config):
    make_scanned_pdf(config.corpus_path / "a.pdf")
    pipeline = Pipeline(config, FakeEmbedding(), FakeOCR())
    try:
        _, snapshot = pipeline.ingest()
        pipeline.validate_current_corpus(snapshot)
        (config.corpus_path / "new.pdf").write_bytes(b"changed")
        with pytest.raises(ValueError, match="Corpus changed"):
            pipeline.validate_current_corpus(snapshot)
    finally:
        pipeline.close()


def test_embedding_failure_isolated(config):
    make_scanned_pdf(config.corpus_path / "a.pdf")
    make_scanned_pdf(config.corpus_path / "b.pdf")
    pipeline = Pipeline(config, FakeEmbedding(), FakeOCR())
    try:
        docs, snapshot = pipeline.ingest()
        docs[0].blocks = []
        result = pipeline.build_index(docs, snapshot, "fixed")
        assert result["status"] == "PARTIAL" and result["documents"] == 1
        assert len(result["errors"]) == 1
    finally:
        pipeline.close()
