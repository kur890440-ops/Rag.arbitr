"""Incremental discovery orchestration; content processing belongs to RunService."""
import asyncio
import base64
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import psutil
from pydantic import BaseModel, Field
from ..documents import now, digest
from ..progress import RunCancelled
from .client import connect, call
from .connector import authorized_root


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(max_length=4096)
    enabled: bool = True
    recursive: bool = True
    scan_interval: int = Field(300, ge=1, le=31536000)
    include_patterns: list[str] = Field(default_factory=lambda: ['*'], max_length=100)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=100)


class SyncService:
    def __init__(self, runs):
        self.runs, self.config = runs, runs.config
        self.signals = {}
        self.closed = False
        self.scheduler_running = False
        from ..application.document_versions import backfill
        from ..application.document_review import DocumentReviewService
        backfill(runs)
        self.reviews = DocumentReviewService(runs)
        with runs.db() as store:
            from .notifications import record_discovery
            for row in store.all('network_files'):
                if not row.get('discovery_event_id'):
                    record_discovery(store, row)
                    store.put('network_files', row['id'], row)
            for grant in store.all('network_root_grants'):
                if grant['root_path'] not in self.config.network_sources.allowed_roots:
                    self.config.network_sources.allowed_roots.append(grant['root_path'])
            for scan in store.all('network_scans'):
                if scan['status'] in {'QUEUED', 'RUNNING'}:
                    try:
                        alive = psutil.Process(scan['owner_pid']).create_time() == scan['owner_started']
                    except psutil.Error:
                        alive = False
                    if not alive:
                        scan.update(status='INTERRUPTED', finished_at=now(), errors=['application_restart'])
                        store.put('network_scans', scan['scan_id'], scan)
                        source = store.get('network_sources', scan['source_id'])
                        if source:
                            source.update(status='INTERRUPTED', last_error='application_restart')
                            store.put('network_sources', source['id'], source)
            store.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS network_one_scan ON network_scans(json_extract(data,'$.source_id')) WHERE json_extract(data,'$.status') IN ('QUEUED','RUNNING')")
            store.db.execute("CREATE INDEX IF NOT EXISTS network_files_source ON network_files(json_extract(data,'$.source_id'))")
            store.db.execute("CREATE INDEX IF NOT EXISTS provenance_document ON network_provenance(json_extract(data,'$.document_id'))")
            store.db.commit()

    def probe_root(self, value):
        from .connector import no_links
        from .drive_paths import resolve_drive_path
        path = Path(resolve_drive_path(value))
        if not path.is_absolute() or path == Path(path.anchor):
            raise ValueError('Specify an absolute folder path, not a drive root')
        try:
            # Operator enrollment is local-only; never allow Windows system directories.
            if os.name == 'nt':
                system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
                if path.resolve().is_relative_to(system.resolve()):
                    raise ValueError('System folders cannot be document sources')
            no_links(path)
            if not path.is_dir():
                raise ValueError('PATH_NOT_FOUND')
            with os.scandir(path) as entries:
                next(entries, None)
            return str(path.absolute())
        except FileNotFoundError as exc:
            raise ValueError(
                'Папка не найдена или недоступна процессу приложения. '
                'Проверьте путь. Если это подключённый сетевой диск, укажите UNC-путь '
                '(\\\\сервер\\ресурс\\папка): буква диска может быть недоступна в сеансе сервера.'
            ) from exc
        except PermissionError as exc:
            raise ValueError('Нет доступа к папке. Проверьте права пользователя, под которым запущено приложение.') from exc
        except OSError as exc:
            code = getattr(exc, 'winerror', None) or exc.errno
            raise ValueError(f'Не удалось прочитать сетевую папку (код ОС: {code}). Проверьте подключение к серверу и доступность ресурса.') from exc

    def authorize_root(self, value):
        root = self.probe_root(value)
        with self.runs.mutation_lock, self.runs.db() as store:
            store.put('network_root_grants', digest(os.path.normcase(root)),
                dict(root_path=root, approved_at=now(), approved_via='local_operator_ui', access='read-only'))
            if root not in self.config.network_sources.allowed_roots:
                self.config.network_sources.allowed_roots.append(root)
        return root

    def add(self, data):
        row = SourceInput(**data).model_dump()
        row['root_path'] = str(authorized_root(row['root_path'], self.config.network_sources.allowed_roots))
        row.update(id=uuid4().hex, last_scan_at=None, last_success_at=None, status='READY', connection_status='NOT_CHECKED', last_error=None)
        with self.runs.db() as store:
            store.put('network_sources', row['id'], row)
        return row

    def source(self, source_id):
        with self.runs.db() as store:
            row = store.get('network_sources', source_id)
        if not row:
            raise KeyError(source_id)
        return row

    def toggle(self, source_id):
        with self.runs.mutation_lock, self.runs.db() as store:
            row = self.source(source_id)
            row['enabled'] = not row['enabled']
            store.put('network_sources', source_id, row)
        return row

    def page(self, table, source_id=None, offset=0, limit=100):
        if table not in {'network_sources', 'network_files', 'network_scans'}:
            raise ValueError('Invalid table')
        with self.runs.db() as store:
            where = "WHERE json_extract(data,'$.source_id')=?" if source_id else ''
            args = [source_id] if source_id else []
            return [json.loads(r[0]) for r in store.db.execute(
                f'SELECT data FROM {table} {where} ORDER BY rowid DESC LIMIT ? OFFSET ?', (*args, limit, offset))]

    def discovery(self, source_id, offset=0):
        from datetime import timedelta
        from ..application.views import ResultsService
        source=self.source(source_id)
        history=self.page('network_scans',source_id,limit=1)
        scan=history[0] if history else None
        with self.runs.db() as store:
            rows=[json.loads(r[0]) for r in store.db.execute("SELECT data FROM network_files WHERE json_extract(data,'$.source_id')=? ORDER BY rowid DESC",(source_id,))]
        snapshot=ResultsService(self.runs).chat_snapshot(None,source_id)
        indexed=set(snapshot['document_runs'])
        counts={'discovered':len(rows),'new':scan['new_count'] if scan else 0,'modified':scan['modified_count'] if scan else 0,'indexed':0,'processing':0,'queued':0,'failed':0}
        for row in rows:
            from ..application.archive_status import readiness
            status=readiness(row,indexed)
            row['availability']=status
            if status=='INDEXED':counts['indexed']+=1
            if status=='PROCESSING':counts['processing']+=1
            if status=='QUEUED':counts['queued']+=1
            if status=='FAILED':counts['failed']+=1
        automatic=self.scheduler_running and not self.closed and self.config.network_sources.enabled and source['enabled']
        active=bool(scan and scan['status'] in ('QUEUED','RUNNING'))
        next_scan=None
        if automatic and not active:
            next_scan=(datetime.fromisoformat(source['last_scan_at'])+timedelta(seconds=source['scan_interval'])).isoformat() if source.get('last_scan_at') else now()
        return dict(source=source,scan=scan,files=rows[offset:offset+100],counts=counts,automatic=automatic,
            next_scan=next_scan,active=active,offset=offset,has_more=len(rows)>offset+100)

    def start(self, source_id):
        self.source(source_id)
        if self.closed:
            raise ValueError('Service stopping')
        scan_id = uuid4().hex
        scan = dict(scan_id=scan_id, source_id=source_id, started_at=now(), finished_at=None, status='QUEUED',
            files_discovered=0, new_count=0, modified_count=0, unchanged_count=0, missing_count=0,
            deferred_count=0, error_count=0, ingestion_queued=0, ingestion_completed=0, ingestion_failed=0,
            errors=[], owner_pid=os.getpid(), owner_started=psutil.Process().create_time())
        with self.runs.mutation_lock, self.runs.db() as store:
            store.db.execute('BEGIN IMMEDIATE')
            active = store.db.execute("SELECT id FROM network_scans WHERE json_extract(data,'$.source_id')=? AND json_extract(data,'$.status') IN ('QUEUED','RUNNING')", (source_id,)).fetchone()
            if active:
                return active[0]
            store.put('network_scans', scan_id, scan)
            self.signals[scan_id] = threading.Event()
            self.runs.executor.submit(self.execute, scan_id)
        return scan_id

    def cancel(self, scan_id):
        if scan_id not in self.signals:
            raise ValueError('Scan belongs to another process or is finished')
        self.signals[scan_id].set()
        with self.runs.db() as store:
            scan = store.get('network_scans', scan_id)
        if scan.get('current_run_id'):
            self.runs.cancel(scan['current_run_id'])

    def close(self):
        self.closed = True
        self.scheduler_running = False
        for signal in list(self.signals.values()):
            signal.set()

    async def schedule(self):
        self.scheduler_running = True
        try:
            while not self.closed:
                if self.config.network_sources.enabled:
                    for row in self.page('network_sources', limit=1000):
                        last = row['last_scan_at']
                        elapsed = (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() if last else float('inf')
                        if row['enabled'] and elapsed >= row['scan_interval']:
                            self.start(row['id'])
                await asyncio.sleep(1)
        finally:
            self.scheduler_running = False

    async def inspect(self, source_id, relative='', offset=0):
        async with connect(self.config) as session:
            return await call(session, 'list_directory', source_id=source_id, relative_path=relative, offset=offset)

    async def check_access(self, source_id):
        try:
            await self.inspect(source_id)
            status = 'ACCESS_OK'
        except Exception as exc:
            status = self.code(exc)
        with self.runs.db() as store:
            source = self.source(source_id)
            source.update(connection_status=status, last_checked_at=now())
            store.put('network_sources',source_id,source)
        if status != 'ACCESS_OK':
            raise ValueError(status)
        return {'status':status, 'connector_access':'read-only', 'os_write_permission':'NOT_TESTED'}

    def execute(self, scan_id):
        with self.runs.db() as store:
            scan = store.get('network_scans', scan_id)
        signal = self.signals[scan_id]
        try:
            self.check(signal)
            scan['status'] = 'RUNNING'
            self.save_scan(scan)
            asyncio.run(self.sync(scan, signal))
            scan['status'] = 'ERROR' if scan['error_count'] else 'COMPLETED'
        except RunCancelled:
            scan.update(status='CANCELLED')
            scan['errors'].append('SCAN_CANCELLED')
        except (Exception, BaseExceptionGroup) as exc:
            if signal.is_set():
                scan.update(status='CANCELLED')
                scan['errors'].append('SCAN_CANCELLED')
            else:
                scan.update(status='ERROR')
                scan['error_count'] += 1
                scan['errors'].append(self.code(exc))
        finally:
            scan['finished_at'] = now()
            self.save_scan(scan)
            with self.runs.db() as store:
                if scan['status'] == 'CANCELLED':
                    for (key,) in store.db.execute("SELECT id FROM network_files WHERE json_extract(data,'$.seen_scan')=? AND json_extract(data,'$.ingestion_status')='PROCESSING'", (scan_id,)):
                        row = store.get('network_files', key)
                        row.update(status='DEFERRED', ingestion_status='CANCELLED', error='SCAN_CANCELLED')
                        store.put('network_files', key, row)
                source = store.get('network_sources', scan['source_id'])
                network_error = next((c for c in scan['errors'] if c in {'NETWORK_UNAVAILABLE','ACCESS_DENIED','PATH_NOT_FOUND'}),None)
                if network_error:
                    source['connection_status'] = network_error
                elif scan['status']=='COMPLETED':
                    source['connection_status'] = 'ACCESS_OK'
                source.update(last_scan_at=scan['started_at'], status=scan['status'], last_error='; '.join(scan['errors'][-10:]) or None)
                if scan['status'] == 'COMPLETED':
                    source['last_success_at'] = scan['finished_at']
                store.put('network_sources', source['id'], source)
            self.signals.pop(scan_id, None)

    @staticmethod
    def code(exc):
        if isinstance(exc, BaseExceptionGroup):
            return SyncService.code(exc.exceptions[0])
        if isinstance(exc, TimeoutError) or 'Timed out while waiting for response' in str(exc):
            return 'MCP_TIMEOUT'
        codes = ['FILE_UNSTABLE','UNSUPPORTED_FORMAT','FILE_TOO_LARGE','ACCESS_DENIED','PATH_NOT_FOUND','SCAN_LIMIT','DEPTH_LIMIT','NETWORK_UNAVAILABLE']
        return next((c for c in codes if c in str(exc)), 'READ_ERROR')

    def save_scan(self, scan):
        with self.runs.db() as store:
            store.put('network_scans', scan['scan_id'], scan)
            from .notifications import publish
            publish(store, scan)

    @staticmethod
    def check(signal):
        if signal.is_set():
            raise RunCancelled()

    async def download(self, session, sid, relative, signal):
        cfg = self.config.network_sources
        folder = self.config.web_data_path / 'network' / 'staging'
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / uuid4().hex
        try:
            for attempt in range(cfg.stability_retries+1):
                self.check(signal)
                first = await call(session, 'get_file_metadata', source_id=sid, relative_path=relative)
                await asyncio.sleep(cfg.stability_seconds)
                second = await call(session, 'get_file_metadata', source_id=sid, relative_path=relative)
                if first != second:
                    continue
                if first['size'] > cfg.max_file_size:
                    raise ValueError('FILE_TOO_LARGE')
                offset, hasher = 0, hashlib.sha256()
                try:
                    with temp.open('wb') as out:
                        while True:
                            self.check(signal)
                            part = await call(session, 'read_file_chunk', source_id=sid, relative_path=relative, offset=offset)
                            if any(part[k] != first[k] for k in ('size', 'mtime_ns')):
                                raise ValueError('FILE_UNSTABLE')
                            data = base64.b64decode(part['data'], validate=True)
                            if not data and not part['eof']:
                                raise ValueError('READ_ERROR')
                            offset += len(data)
                            if offset > cfg.max_file_size:
                                raise ValueError('FILE_TOO_LARGE')
                            out.write(data)
                            hasher.update(data)
                            if part['eof']:
                                break
                    final = await call(session, 'get_file_metadata', source_id=sid, relative_path=relative)
                    if final != first or offset != first['size']:
                        raise ValueError('FILE_UNSTABLE')
                    return temp, hasher.hexdigest(), first
                except ValueError as exc:
                    if 'FILE_UNSTABLE' not in str(exc):
                        raise
            raise ValueError('FILE_UNSTABLE')
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    async def sync(self, scan, signal):
        sid, cursor, complete = scan['source_id'], '', True
        with self.runs.db() as store:
            source = store.get('network_sources', sid)
            source.update(last_scan_at=scan['started_at'], status='RUNNING', last_error=None)
            store.put('network_sources', sid, source)
        async with connect(self.config) as session:
            while True:
                self.check(signal)
                batch = await call(session, 'scan_source', source_id=sid, cursor=cursor)
                for metadata in batch['files']:
                    self.check(signal)
                    relative = metadata['relative_path']
                    key = digest([sid, os.path.normcase(relative)])
                    with self.runs.db() as store:
                        old = store.get('network_files', key) or {}
                    row = {**old, **metadata, 'id': key, 'source_id': sid, 'filename': Path(relative).name, 'format':Path(relative).suffix.lstrip('.').upper(),
                           'seen_scan': scan['scan_id'], 'last_sync_at': now(), 'discovered_at': old.get('discovered_at', now()), 'error': None}
                    scan['files_discovered'] += 1
                    row.setdefault('status','DISCOVERED')
                    row.setdefault('ingestion_status','PENDING')
                    with self.runs.db() as store:
                        from .notifications import record_discovery
                        record_discovery(store, row, scan['scan_id'])
                        store.put('network_files',key,row)
                    self.save_scan(scan)
                    temp = None
                    try:
                        if metadata.get('error'):
                            complete = False
                            raise ValueError(metadata['error'])
                        unchanged = all(old.get(k) == metadata[k] for k in ('size', 'mtime_ns')) and old.get('indexing_status', old.get('ingestion_status')) == 'SUCCESS'
                        if unchanged:
                            row['status'] = 'UNCHANGED'
                            scan['unchanged_count'] += 1
                        else:
                            temp, content_hash, stable = await self.download(session, sid, relative, signal)
                            row.update(stable)
                            row['content_hash'] = content_hash
                            if old.get('content_hash') == content_hash and old.get('indexing_status', old.get('ingestion_status')) == 'SUCCESS':
                                row['status'] = 'UNCHANGED'
                                scan['unchanged_count'] += 1
                            else:
                                row['status'] = 'MODIFIED' if old.get('content_hash') else 'NEW'
                                scan['modified_count' if row['status']=='MODIFIED' else 'new_count'] += 1
                                self.ingest(row, temp, scan, signal)
                                if (row.get('ingestion_status') == row.get('indexing_status') == 'SUCCESS'
                                        and not old.get('last_indexed') and not old.get('first_indexed_scan')
                                        and old.get('indexing_status') != 'SUCCESS'):
                                    row['first_indexed_scan'] = scan['scan_id']
                        if row.get('indexing_status') == 'SUCCESS':
                            row['indexed_discovery_event_id'] = row['discovery_event_id']
                    except RunCancelled:
                        raise
                    except Exception as exc:
                        row['error'] = self.code(exc)
                        row['status'] = 'DEFERRED' if row['error'] == 'FILE_UNSTABLE' else 'ERROR'
                        scan['deferred_count' if row['status']=='DEFERRED' else 'error_count'] += 1
                        if len(scan['errors']) < 100:
                            scan['errors'].append(row['error'])
                        # Preserve the accepted fingerprint so failures retry on the next scan.
                        row['size'], row['mtime_ns'] = old.get('size'), old.get('mtime_ns')
                    finally:
                        if temp:
                            temp.unlink(missing_ok=True)
                    with self.runs.db() as store:
                        store.put('network_files', key, row)
                    self.save_scan(scan)
                cursor = batch['cursor']
                if not cursor:
                    break
        # Only an exhaustive, error-free enumeration is evidence of absence.
        if complete:
            with self.runs.db() as store:
                ids = store.db.execute("SELECT id FROM network_files WHERE json_extract(data,'$.source_id')=? AND json_extract(data,'$.seen_scan') != ?", (sid, scan['scan_id']))
                for (key,) in ids:
                    self.check(signal)
                    row = store.get('network_files', key)
                    row.update(status='MISSING', last_sync_at=now())
                    store.put('network_files', key, row)
                    scan['missing_count'] += 1

    def ingest(self, row, temp, scan, signal):
        self.check(signal)
        from ..application.document_versions import capture
        with self.runs.db() as store:
            previous=store.get('network_files',row['id']) or {}
            # The current registry metadata already contains the new hash; previous provenance owns the old one.
            old_origin=next((p for p in reversed(store.all('network_provenance')) if p.get('source_id')==row['source_id'] and p.get('relative_path')==row['relative_path'] and p.get('document_id')==previous.get('document_id')),None)
            old_version=capture(store,{**previous,**old_origin}) if old_origin else None
            from ..application.document_versions import ready
            if old_version and not ready(store,old_version):
                old_version=next((v for v in reversed(store.all('document_versions')) if v['document_id']==row['id'] and ready(store,v)),None)
        # Immutable path + hash matches the existing Ingestor document identity.
        folder = self.config.web_data_path / 'network' / 'versions' / row['id'] / row['content_hash']
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / row['filename']
        if not path.exists():
            temp.replace(path)
        cfg = self.config.model_copy(deep=True)
        cfg.corpus_path, cfg.manifest_path = folder, folder / 'manifest.json'
        output = self.config.web_data_path / 'network' / 'runs' / uuid4().hex
        output.mkdir(parents=True)
        cfg.snapshot_path, cfg.report_path = output / 'snapshot.json', output / 'report.html'
        rid = self.runs.create(cfg, run_type='index', background=False)
        # Share cancellation with the existing page/batch reporter.
        self.runs.cancellations[rid] = signal
        if row.get('active_run_id'):
            row['previous_run_id'] = row.pop('active_run_id')
        row.update(run_id=rid, ingestion_status='PROCESSING', chunking_status='PENDING', indexing_status='PENDING')
        scan.update(current_run_id=rid, ingestion_queued=scan['ingestion_queued']+1)
        self.save_scan(scan)
        with self.runs.db() as store:
            store.put('network_files', row['id'], row)
        self.runs.execute(rid)
        self.check(signal)
        result = self.runs.get(rid)
        row['ingestion_status'] = {'COMPLETED':'SUCCESS', 'PARTIAL':'PARTIAL'}.get(result['status'], 'FAILED')
        did = digest([str(path.resolve()), row['content_hash']])
        row['document_id'] = did
        with self.runs.db() as store:
            doc = store.get('documents', did)
            indexes = [store.get('index_runs', i) for i in result.get('index_run_ids', {}).values()]
        indexes = [i for i in indexes if i]
        row['ingestion_status'] = doc['status'] if doc else 'FAILED'
        row['chunk_count'] = sum(i.get('chunks',0) for i in indexes)
        row['chunking_status'] = 'SUCCESS' if row['chunk_count'] else 'FAILED'
        row['indexing_status'] = 'SUCCESS' if len(indexes)==2 and all(i['status']=='SUCCESS' for i in indexes) else 'PARTIAL' if any(i['status'] in {'SUCCESS','PARTIAL'} for i in indexes) else 'FAILED'
        if row['ingestion_status'] == row['indexing_status'] == 'SUCCESS':
            row.update(last_indexed=now(), active_run_id=rid)
            scan['ingestion_completed'] += 1
        else:
            row['error'] = 'INGESTION_FAILED'
            scan['ingestion_failed'] += 1
            scan['error_count'] += 1
            if len(scan['errors']) < 100:
                scan['errors'].append('INGESTION_FAILED')
        provenance = {k: row.get(k) for k in ('source_id','relative_path','content_hash','discovered_at','last_sync_at','document_id','run_id')}
        with self.runs.db() as store:
            store.put('network_provenance', digest([row['id'], row['content_hash']]), provenance)
        # A durable review failure must not roll back successful indexing.
        try:
            self.reviews.schedule(row,old_version,modified=row.get('status')=='MODIFIED' and bool(old_version or previous.get('last_indexed')))
        except Exception:
            row['review_error']='REVIEW_GENERATION_FAILED'
