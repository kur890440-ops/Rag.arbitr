"""Immutable logical network-document versions over the existing canonical pipeline."""
import json
from pathlib import Path
from ..documents import digest, now


def insert_immutable(store, table, key, data):
    old=store.get(table,key)
    if old is not None:
        return old
    store.db.execute(f'INSERT OR IGNORE INTO {table}(id,data) VALUES (?,?)',(key,json.dumps(data,ensure_ascii=False)))
    store.db.commit()
    return store.get(table,key)


def capture(store, row, run_id=None):
    did=row.get('document_id');doc=store.get('documents',did) if did else None
    if not doc or doc.get('status')!='SUCCESS':return None
    run=store.get('processing_runs',run_id or row.get('active_run_id') or row.get('run_id') or '') or {}
    path=run.get('config_json',{}).get('snapshot_path')
    entry=None
    if path and Path(path).is_file():
        snapshot=json.loads(Path(path).read_text(encoding='utf-8'))
        entry=next((e for e in snapshot.get('corpus',{}).get('documents',[]) if e['document_id']==did),None)
    canonical=store.get('normalized_documents',(entry or {}).get('recognition_version') or doc.get('cache_identity',''))
    if not canonical or canonical.get('status')!='SUCCESS':return None
    vid=digest([row['id'],row['content_hash'],canonical['cache_identity']])
    version=dict(document_id=row['id'],version_id=vid,canonical_document_id=did,
        source_id=row['source_id'],relative_path=row['relative_path'],document_name=row['filename'],
        content_hash=row['content_hash'],source_fingerprint=row.get('discovery_fingerprint') or digest([row.get('size'),row.get('mtime_ns')]),
        created_at=row.get('last_sync_at') or now(),canonical_content_reference=vid,
        recognition_version=canonical['cache_identity'],ingestion_status=canonical['status'])
    insert_immutable(store,'document_version_contents',vid,canonical)
    version=insert_immutable(store,'document_versions',vid,version)
    if row.get('indexing_status')=='SUCCESS' and row.get('active_run_id'):
        store.put('document_version_indexes',vid,dict(version_id=vid,run_id=row['active_run_id'],status='SUCCESS'))
    return version


def backfill(runs):
    with runs.db() as store:
        rows=store.all('network_files')
        for row in rows:
            # Historical provenance retains each content-addressed file/run, not only latest.
            for origin in store.all('network_provenance'):
                if origin.get('source_id')!=row['source_id'] or origin.get('relative_path')!=row['relative_path']:continue
                rid=origin.get('run_id');run=store.get('processing_runs',rid) if rid else None
                if run and run.get('status')=='COMPLETED':
                    capture(store,{**row,**origin,'discovery_fingerprint':'content-sha256:'+origin['content_hash'],'indexing_status':'SUCCESS','active_run_id':rid})


def ready(store, version):
    index=store.get('document_version_indexes',version['version_id'])
    if not index or index['status']!='SUCCESS':return False
    run=store.get('processing_runs',index['run_id']) or {}
    indexes=[store.get('index_runs',i) for i in run.get('index_run_ids',{}).values()]
    return len(indexes)==2 and all(i and i['status']=='SUCCESS' for i in indexes)
