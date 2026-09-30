"""One real local VLM call on one synthetic scanned page, then existing indexing services."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import make_scanned_pdf
from rag_arbiter.config import Config
from rag_arbiter.documents import save_json
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.recognition import Qwen3VLRecognitionProvider
from rag_arbiter.recognition.runtime import gpu_diagnostics
from rag_arbiter.embeddings import BgeM3EmbeddingProvider


def main():
    root = Path("data/recognition-smoke")
    config = Config(corpus_path=root / "corpus", cache_path=root / "cache", sqlite_path=root / "metadata.db",
                    qdrant_path=root / "qdrant", manifest_path=root / "manifest.json", queries_path=root / "queries.json",
                    report_path=root / "report.html")
    config.prepare()
    fixture = config.corpus_path / "synthetic-russian-scan.pdf"
    if not fixture.exists():
        make_scanned_pdf(fixture, pages=1)
    provider = Qwen3VLRecognitionProvider(config)
    pipeline = Pipeline(config, ocr=provider)
    evidence = {"fixture": "SYNTHETIC SINGLE PAGE ONLY", "stage": "preflight", "gpu_before": gpu_diagnostics()}
    try:
        evidence["runtime"] = provider.preflight()
        evidence["stage"] = "recognition"
        docs, snapshot = pipeline.ingest()
        evidence["recognition"] = snapshot["recognition"]
        if not docs or snapshot["recognition"]["errors"]:
            raise RuntimeError("Single-page recognition failed; inspect recognition errors and raw artifact")
        doc = docs[0]
        text = doc.pages[0].ocr_text
        evidence["russian_recognized"] = "договор" in text.lower() and "оплат" in text.lower()
        evidence["structure_types"] = sorted({b.block_type for b in doc.blocks})
        evidence["raw_output_path"] = doc.pages[0].raw_output_path
        evidence["normalized_output_path"] = doc.pages[0].normalized_output_path
        evidence["image_path"] = doc.pages[0].image_path
        if not evidence["russian_recognized"] or "HIDDEN_LAYER_POISON" in text:
            raise RuntimeError("Single-page Russian/visible-image verification failed")
        if "table" not in evidence["structure_types"]:
            raise RuntimeError("Single-page fixture table was not preserved")
        evidence["single_page_status"] = "SUCCESS"
        save_json(root / "evidence.json", evidence)
        # Recognition succeeded. Only now use the same normalized document for both chunkers.
        evidence["stage"] = "indexing"
        embedding_config = config.model_copy(update={"cache_path": Path("data/cache/integration")})
        pipeline.provider = BgeM3EmbeddingProvider(embedding_config)
        for strategy in ("fixed", "structure"):
            run = pipeline.build_index(docs, snapshot, strategy)
            if run["status"] != "SUCCESS":
                raise RuntimeError(f"Index {strategy} failed: {run['errors']}")
        save_json(config.queries_path, {"dataset_id": "single-page-synthetic-only", "corpus_hash": snapshot["corpus"]["corpus_hash"],
            "queries": [{"query_id": "test", "question": "Каков срок оплаты?", "expected_document": fixture.name, "expected_page": 1}]})
        pipeline.evaluate(snapshot)
        for row in snapshot["evaluation"]["queries"]:
            for strategy in ("fixed", "structure"):
                assert row["results"][strategy]["hits"]
                for hit in row["results"][strategy]["hits"]:
                    assert all(Path(s["recognition"]["raw_output_path"]).exists() for s in hit["provenance"]["sources"])
        from rag_arbiter.reporting import generate_report
        generate_report(config, snapshot)
        evidence.update(status="SUCCESS", stage="complete", snapshot=snapshot, gpu_after=gpu_diagnostics())
        print(json.dumps({k: v for k, v in evidence.items() if k not in {"snapshot", "recognition"}}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        evidence.update(status="FAILED", error=str(exc), gpu_after=gpu_diagnostics())
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 2
    finally:
        provider.close()
        pipeline.close()
        save_json(root / "evidence.json", evidence)


if __name__ == "__main__":
    raise SystemExit(main())
