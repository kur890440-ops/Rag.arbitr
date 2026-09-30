import json
from .documents import digest, now
from .retrieval import validate_comparison


def metrics(hits, query):
    def document(h):
        return query["expected_document"] in (h["document_id"], h["file_name"])
    matches = [h for h in hits if document(h)]
    rank = min((h["rank"] for h in matches), default=None)
    result = {f"Hit@{k}": int(rank is not None and rank <= k) for k in (1, 3, 5)}
    result["rank"] = rank
    if query.get("expected_page") is not None:
        ranks = [h["rank"] for h in matches if h["page_start"] <= query["expected_page"] <= h["page_end"]]
        result.update(page_hit=int(bool(ranks)), page_rank=min(ranks, default=None))
    if query.get("expected_section"):
        ranks = [h["rank"] for h in matches if query["expected_section"] in h["section"].split(" / ")]
        result.update(section_hit=int(bool(ranks)), section_rank=min(ranks, default=None))
    return result


def evaluate(config, indexes, retriever, store, reporter=None):
    validate_comparison(indexes.get("fixed"), indexes.get("structure"))
    dataset = json.loads(config.queries_path.read_text(encoding="utf-8"))
    if not dataset.get("queries"):
        return {"status": "QUERIES_REQUIRED", "queries": []}
    if config.top_k < 5:
        raise ValueError("Evaluation Hit@5 requires top_k >= 5")
    if dataset.get("corpus_hash") != indexes["fixed"]["corpus_hash"]:
        raise ValueError("Evaluation dataset corpus_hash mismatch")
    ids = set()
    for q in dataset["queries"]:
        if reporter:
            reporter.check()
        if not all(q.get(k) for k in ("query_id", "question", "expected_document")) or q["query_id"] in ids:
            raise ValueError("Queries need unique IDs, question and expected_document")
        ids.add(q["query_id"])
        if q.get("expected_page") is not None and (type(q["expected_page"]) is not int or q["expected_page"] < 1):
            raise ValueError("expected_page must be a positive integer")
    fingerprint = digest(dataset)
    run = {"run_id": digest([fingerprint, now()]), "status": "SUCCESS", "dataset_hash": fingerprint,
           "top_k": config.top_k, "corpus_hash": dataset["corpus_hash"], "queries": [],
           "index_runs": {s: i["run_id"] for s, i in indexes.items()}}
    for q in dataset["queries"]:
        store.put("evaluation_queries", f"{fingerprint}:{q['query_id']}", q)
        if reporter:
            reporter.check()
        result = retriever.compare(q["question"], indexes, config.top_k)
        for strategy in ("fixed", "structure"):
            result[strategy]["metrics"] = metrics(result[strategy]["hits"], q)
        row = {"query": q, "results": result}
        run["queries"].append(row)
        store.put("retrieval_results", f"{run['run_id']}:{q['query_id']}", row)
    run["aggregate"] = {s: {f"Hit@{k}": sum(q["results"][s]["metrics"][f"Hit@{k}"] for q in run["queries"]) / len(run["queries"])
                                 for k in (1, 3, 5)} for s in ("fixed", "structure")}
    store.put("evaluation_runs", run["run_id"], run)
    return run
