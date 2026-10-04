import time
import logging


FAIR_FIELDS = ("corpus_id", "corpus_hash", "normalized_hash", "embedding", "dimension", "similarity", "document_ids")


def validate_comparison(fixed, structure):
    if not fixed or not structure:
        raise ValueError("Build both indexes before comparison")
    if fixed["status"] != "SUCCESS" or structure["status"] != "SUCCESS":
        raise ValueError("Invalid comparison: incomplete corpus/index; inspect errors")
    for field in FAIR_FIELDS:
        if fixed[field] != structure[field]:
            raise ValueError(f"Invalid comparison: different {field}")


class SemanticRetriever:
    def __init__(self, provider, vectors, store):
        self.provider, self.vectors, self.store = provider, vectors, store

    def retrieve_vector(self, vector, index, top_k, document_id=None, policy=None):
        if index["embedding"] != self.provider.identity:
            raise ValueError("Query model/settings differ from indexed model")
        results = []
        from .application.rechunk import partitions
        points = []
        versions = {}
        for part in partitions(index):
            if document_id and document_id not in part['document_ids']:continue
            active = part.get('chunk_ids', [])
            if active:
                found=self.vectors.search(part['collection'], vector, top_k,
                    [document_id] if document_id else (part['document_ids'] if policy is not None else None), chunk_ids=active,
                    **({'policy':policy} if policy is not None else {}))
                points.extend(found)
                versions.update({p.payload['chunk_id']:part['run_id'] for p in found})
        points = sorted(points, key=lambda p: (-p.score, p.payload['chunk_id']))[:top_k]
        for rank, point in enumerate(points, 1):
            chunk = self.store.get("chunks", point.payload["chunk_id"])
            if not chunk:
                raise ValueError("Missing canonical chunk in SQLite")
            if policy is not None and (chunk['document_id']!=point.payload['document_id'] or
                    chunk['chunk_id'] not in index.get('chunk_ids',[])):
                logging.getLogger(__name__).error('RETRIEVAL_POLICY_VIOLATION stage=canonical_candidate chunk_id=%s',chunk['chunk_id'])
                continue
            results.append({**point.payload, "rank": rank, "score": point.score, "text": chunk["text"]})
            results[-1].update({k:chunk.get(k, '') for k in ('source','title')})
            results[-1].update(corpus_id=index.get('corpus_id'),chunking_run_id=versions[chunk['chunk_id']])
            if chunk.get("recognition_ids"):
                results[-1]["provenance"] = self.store.trace_chunk(chunk["chunk_id"])
        return policy.guard(results,'candidate') if policy is not None else results

    def compare(self, query, indexes, top_k, document_id=None):
        validate_comparison(indexes["fixed"], indexes["structure"])
        started = time.perf_counter()
        vector = self.provider.encode([query])[0]
        encoding = time.perf_counter() - started
        result = {}
        for strategy in ("fixed", "structure"):
            start = time.perf_counter()
            hits = self.retrieve_vector(vector, indexes[strategy], top_k, document_id)
            result[strategy] = {"hits": hits, "search_latency": time.perf_counter() - start,
                                "query_embedding_latency": encoding, "retrieved_tokens": sum(h["token_count"] for h in hits)}
        return result
