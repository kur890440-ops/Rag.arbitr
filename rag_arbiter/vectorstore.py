from uuid import UUID
from qdrant_client import QdrantClient, models


class LocalVectorStore:
    def __init__(self, path):
        self.client = QdrantClient(path=str(path))

    def create(self, name, dimension):
        if not self.client.collection_exists(name):
            self.client.create_collection(name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
        params = self.client.get_collection(name).config.params.vectors
        if params.size != dimension or params.distance != models.Distance.COSINE:
            raise ValueError("Collection vector configuration mismatch")

    def upsert(self, name, chunks, vectors, file_name):
        points = []
        for c, vector in zip(chunks, vectors):
            payload = {k: getattr(c, k) for k in ("chunk_id", "document_id", "strategy", "page_start", "page_end",
                                                          "section", "token_count", "content_hash")}
            payload["file_name"] = file_name
            payload["source"] = c.source
            payload["title"] = c.title or file_name
            payload["recognition_provider"] = c.recognition_provider
            payload["recognition_ids"] = c.recognition_ids
            points.append(models.PointStruct(id=str(UUID(c.chunk_id[:32])), vector=vector.tolist(), payload=payload))
        if points:
            self.client.upsert(name, points=points, wait=True)

    def search(self, name, vector, top_k, document_ids=None, chunk_ids=None):
        conditions = []
        if document_ids is not None:
            conditions.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids)))
        if chunk_ids is not None:
            conditions.append(models.FieldCondition(key="chunk_id", match=models.MatchAny(any=chunk_ids)))
        query_filter = models.Filter(must=conditions) if conditions else None
        return self.client.query_points(name, query=vector.tolist(), limit=top_k, query_filter=query_filter).points

    def close(self):
        self.client.close()
