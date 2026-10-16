"""Read-only discovery projection and explicit per-session decisions."""
from ..documents import digest, now
from ..network.notifications import source_access
from .chat_models import ChatValidationError
from .views import ResultsService


class ContextualDiscovery:
    def __init__(self, chat):
        self.chat, self.runs = chat, chat.runs

    def session(self, sid):
        with self.runs.db() as store:
            session = store.get('chat_sessions', sid)
        if not session:
            raise KeyError(sid)
        return session

    def snapshot(self, sid):
        session = self.session(sid)
        selected = session.get('selected_document_ids') or ([session['document_id']] if session.get('document_id') else [])
        result = dict(session_id=sid, scope_version=session.get('scope_version', 0), source_id=session.get('source_id'),
                      source_name='', files=[], ready_count=0)
        if not selected or session['source_scope'] != 'NETWORK' or not session.get('source_id'):
            return result
        with self.runs.db() as store:
            source = store.get('network_sources', session['source_id'])
            if not source_access(self.runs, source):
                return result
            result['source_name'] = source['name']
            decisions = {r['event_id'] for r in store.all('chat_discovery_decisions') if r['session_id'] == sid and r['action'] in ('add', 'dismiss')}
            since = session.get('discovery_since') or session['created_at']
            rows = [r for r in store.all('network_files') if r['source_id'] == source['id']
                    and r.get('discovery_event_id') and r['discovery_event_id'] not in decisions
                    and r.get('discovery_event_at', r.get('discovered_at', '')) > since and r.get('status') != 'MISSING'
                    and r.get('document_id') not in selected]
        if not rows:
            return result
        active = ResultsService(self.runs).chat_snapshot(session.get('processing_run_id'), source['id'], source_scope='NETWORK')
        for row in rows:
            ready = row.get('document_id') in active['document_runs'] and row.get('indexing_status') == 'SUCCESS' and row.get('indexed_discovery_event_id') == row['discovery_event_id']
            status = ('INDEXED' if ready else 'PROCESSING' if row.get('ingestion_status') in ('PROCESSING', 'QUEUED')
                      else 'FAILED' if row.get('error') or row.get('ingestion_status') in ('FAILED', 'CANCELLED')
                      or row.get('indexing_status') in ('FAILED', 'PARTIAL') else 'NEW')
            result['files'].append(dict(event_id=row['discovery_event_id'], file_id=row['id'],
                source_id=row['source_id'], document_id=row.get('document_id'),
                fingerprint=row['discovery_fingerprint'], filename=row['filename'], status=status))
        result['files'].sort(key=lambda f: (f['event_id'], f['file_id']))
        result['ready_count'] = sum(f['status'] == 'INDEXED' for f in result['files'])
        return result

    def presented(self, notice):
        """Remember inline delivery without acknowledging or hiding the proposal."""
        import json
        with self.runs.db() as store, store.db:
            for file in notice['files']:
                key = digest([notice['session_id'], file['event_id']])
                decision = dict(session_id=notice['session_id'], event_id=file['event_id'],
                    source_id=file['source_id'], document_id=file['document_id'],
                    fingerprint=file['fingerprint'], action='presented', decided_at=now())
                store.db.execute('INSERT OR IGNORE INTO chat_discovery_decisions(id,data) VALUES (?,?)',
                                 (key, json.dumps(decision, ensure_ascii=False)))

    def decide(self, sid, action, event_ids, expected_version, document_ids=None):
        if action not in ('add', 'dismiss') or not event_ids:
            raise ChatValidationError('Некорректное решение по новым документам.')
        # Serialize decisions in this process; select_source also enforces a DB scope-version check.
        with self.runs.mutation_lock:
            notice = self.snapshot(sid)
            session = self.session(sid)
            if notice['scope_version'] != expected_version or session['scope_version'] != expected_version:
                raise ChatValidationError('Область поиска изменилась. Обновите предложение.')
            files = {f['event_id']: f for f in notice['files']}
            requested = set(event_ids)
            if not requested <= files.keys():
                raise ChatValidationError('Список новых документов изменился. Обновите предложение.')
            ready = {files[i]['document_id'] for i in event_ids if files[i]['status'] == 'INDEXED'}
            confirmed = list(dict.fromkeys(document_ids or []))
            if action == 'add' and (not confirmed or not set(confirmed) <= ready):
                raise ChatValidationError('Подтверждённые документы ещё не готовы или их версия изменилась. Обновите предложение.')
            decisions = {}
            for event_id in requested:
                file = files[event_id]
                decisions[digest([sid, event_id])] = dict(session_id=sid, event_id=event_id, source_id=file['source_id'],
                    document_id=file['document_id'], fingerprint=file['fingerprint'],
                    action='add' if action == 'add' and file['document_id'] in confirmed else 'dismiss', decided_at=now())
            if action == 'add':
                selected = session.get('selected_document_ids') or [session['document_id']]
                # Selection and acknowledgement commit atomically in the established scope transaction.
                session = self.chat.select_source(sid, session['source_scope'], session['source_id'], expected_version,
                    list(dict.fromkeys([*selected, *confirmed])), preserve_discovery_since=True,
                    discovery_decisions=decisions).model_dump()
            else:
                with self.runs.db() as store, store.db:
                    store.db.execute('BEGIN IMMEDIATE')
                    current = store.get('chat_sessions', sid)
                    if current['scope_version'] != expected_version:
                        raise ChatValidationError('Область поиска изменилась. Обновите предложение.')
                    for key, decision in decisions.items():
                        self.chat.write(store, 'chat_discovery_decisions', key, decision)
            return dict(session=session, notice=self.snapshot(sid))
