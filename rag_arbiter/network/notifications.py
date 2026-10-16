"""Sync completion notifications in the existing durable event journal."""
import json
import os
from .connector import authorized_root

STREAM = 'knowledge_base_updates'
DISCOVERY_STREAM = 'source_file_discovery'


def publish(store, scan):
    if scan['status'] in {'QUEUED', 'RUNNING'}:
        return
    files = [json.loads(r[0]) for r in store.db.execute("SELECT data FROM network_files WHERE json_extract(data,'$.first_indexed_scan')=?", (scan['scan_id'],))]
    count = len(files)
    if not count:
        return
    payload = json.dumps(dict(type='knowledge_base_updated', scan_id=scan['scan_id'], source_id=scan['source_id'], count=count, discovery_event_ids=[f.get('discovery_event_id') for f in files]))
    # Atomic insert-if-absent makes repeated finalization idempotent.
    with store.db:
        store.db.execute("INSERT INTO processing_run_events(run_id,data) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM processing_run_events WHERE run_id=? AND json_extract(data,'$.scan_id')=?)", (STREAM, payload, STREAM, scan['scan_id']))


def cursor(runs):
    with runs.db() as store:
        return store.db.execute('SELECT coalesce(max(id),0) FROM processing_run_events WHERE run_id=?', (STREAM,)).fetchone()[0]


def source_access(runs, source):
    if not source or source.get('connection_status') == 'ACCESS_DENIED':
        return False
    try:
        root = authorized_root(source['root_path'], runs.config.network_sources.allowed_roots)
        with os.scandir(root) as entries:
            next(entries, None)
        return True
    except (OSError, ValueError):
        return False


def record_discovery(store, row, scan_id=None):
    """Persist discovery before ingestion; retries with the same fingerprint reuse it."""
    from ..documents import digest, now
    fingerprint = digest([row.get('size'), row.get('mtime_ns')])
    if row.get('discovery_event_id') and row.get('discovery_fingerprint') == fingerprint:
        return
    row['discovery_event_at'] = now() if scan_id else row['discovered_at']
    payload = dict(type='source_file_discovered', source_id=row['source_id'], file_id=row['id'],
                   fingerprint=fingerprint, discovered_at=row['discovery_event_at'], scan_id=scan_id)
    row['discovery_event_id'] = store.event(DISCOVERY_STREAM, payload)
    row['discovery_fingerprint'] = fingerprint
    if not scan_id and row.get('indexing_status') == 'SUCCESS':
        row['indexed_discovery_event_id'] = row['discovery_event_id']


def updates(runs, after, session=None):
    with runs.db() as store:
        batch = store.events(STREAM, after)
        events = []
        selected = (session or {}).get('selected_document_ids') or (session or {}).get('document_id')
        inline_source = (session or {}).get('source_id') if selected and session.get('source_scope') == 'NETWORK' else None
        presented = {r['event_id'] for r in store.all('chat_discovery_decisions') if session and r['session_id'] == session['session_id']}
        for event in batch:
            # An inline proposal owns this source, even after accepting/dismissing it.
            if event['source_id'] == inline_source:
                continue
            ids = event.get('discovery_event_ids') or [r.get('discovery_event_id') for r in store.all('network_files') if r.get('first_indexed_scan') == event['scan_id']]
            count = event['count'] - sum(i in presented for i in ids)
            if count <= 0:
                continue
            source = store.get('network_sources', event['source_id'])
            if source_access(runs, source):
                events.append({'id': event['id'], 'count': count})
        return {'cursor': batch[-1]['id'] if batch else after, 'events': events}
