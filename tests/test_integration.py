import os
from pathlib import Path
import numpy as np
import pytest
from rag_arbiter.ingestion import Ingestor
from rag_arbiter.storage import MetadataStore
from rag_arbiter.embeddings import BgeM3EmbeddingProvider
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.documents import save_json
from rag_arbiter.chunking import FixedSizeChunker, StructureAwareChunker, coverage
from conftest import make_scanned_pdf

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv("RAG_INTEGRATION") != "1", reason="Set RAG_INTEGRATION=1 for real model downloads")]


def test_real_scanned_ocr_bge_and_comparison(config):
    config.recognition.provider = "classic_ocr"
    config.cache_path = Path("data/cache/integration")
    config.fixed_tokens = config.fixed_max_tokens = config.structure_max_tokens = 1000
    config.fixed_overlap = 125
    config.prepare()
    path = config.corpus_path / "synthetic_scan.pdf"
    make_scanned_pdf(path, pages=2)
    pipeline = Pipeline(config)
    try:
        docs, snapshot = pipeline.ingest()
        assert not snapshot["ocr"]["errors"], snapshot["ocr"]
        assert len(docs) == 1 and docs[0].pages[0].ocr_text
        assert len(docs[0].pages) == 2
        assert {b.page_number for b in docs[0].blocks} == {1, 2}
        text = docs[0].pages[0].ocr_text
        assert "HIDDEN_LAYER_POISON" not in text
        assert "договор" in text.lower() or "оплаты" in text.lower()
        fixed = pipeline.build_index(docs, snapshot, "fixed")
        structure = pipeline.build_index(docs, snapshot, "structure")
        assert fixed["status"] == structure["status"] == "SUCCESS", snapshot
        assert pipeline.provider.dimension == 1024
        vectors = pipeline.provider.encode(["Договор поставки", "Срок оплаты"])
        assert vectors.shape == (2, 1024)
        assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
        # Real BGE tokenizer: Unicode, whitespace, oversized structural unit.
        large = docs[0].model_copy(deep=True)
        large.blocks[0].text = ("Русский текст — проверка! 汉字🙂\n\n" * 100)
        for chunker in (FixedSizeChunker(pipeline.provider.tokenizer, 32, 5, 32),
                        StructureAwareChunker(pipeline.provider.tokenizer, 32)):
            chunks = chunker.chunk(large)
            assert max(c.token_count for c in chunks) <= 32
            assert coverage(large, chunks, pipeline.provider.tokenizer)["unique_coverage"] == 1
        save_json(config.queries_path, {"dataset_id": "synthetic-test-only", "corpus_hash": snapshot["corpus"]["corpus_hash"],
            "queries": [{"query_id": "test-1", "question": "Какой срок оплаты?", "expected_document": path.name,
                         "expected_page": 1, "notes": "SYNTHETIC TEST ONLY"}]})
        result = pipeline.evaluate(snapshot)
        assert result["aggregate"]["fixed"]["Hit@1"] == 1
        repeat = pipeline.day21()
        assert repeat["ocr"]["processed_pages"] == 0
        assert repeat["indexes"]["fixed"]["embeddings_created"] == 0
        save_json(Path("data/reports/integration-evidence.json"), {"fixture": "SYNTHETIC TEST ONLY", "snapshot": repeat})
    finally:
        pipeline.close()
