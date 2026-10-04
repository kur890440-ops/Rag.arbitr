"""Metadata-only migration for active indexes of a specified processing run."""
import argparse
from contextlib import closing
from collections import Counter
from types import SimpleNamespace
from rag_arbiter.config import Config
from rag_arbiter.documents import save_json
from rag_arbiter.storage import MetadataStore
from rag_arbiter.vectorstore import LocalVectorStore
from rag_arbiter.application.views import ResultsService
from rag_arbiter.application.document_metadata import enrich_documents,sync_payload


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
    cfg=Config.load()
    with closing(MetadataStore(cfg.sqlite_path)) as store:
        snapshot=ResultsService(SimpleNamespace(get=lambda key:store.get('processing_runs',key))).snapshot(args.run_id)
        indexes=list(snapshot.get('indexes',{}).values())
        ids=sorted({id for index in indexes for id in index['document_ids']})
        metadata=enrich_documents(store,ids,snapshot['corpus']['corpus_id'])
        vectors=LocalVectorStore(cfg.qdrant_path)
        try:
            for index in indexes:sync_payload(vectors,index,metadata)
        finally:vectors.close()
        report=dict(documents_total=len(ids),statuses=dict(Counter(m['classification_status'] for m in metadata.values())),
            types=dict(Counter(m['document_type'] for m in metadata.values())),dated=sum(m['document_year'] is not None for m in metadata.values()),
            documents=[dict(file_name=store.get('documents',id)['file_name'],**m) for id,m in metadata.items()],reembedded=False)
        save_json('data/day25/metadata_enrichment.json',report)
        print({k:v for k,v in report.items() if k!='documents'})


if __name__=='__main__':main()
