"""Read-only archive projections; never scan, ingest or ask an LLM for counts."""
from collections import Counter
from pathlib import PurePosixPath
from ..documents import now
from .views import ResultsService


def readiness(row, indexed):
    if row.get('status') == 'MISSING': return 'MISSING'
    if row.get('document_id') in indexed: return 'INDEXED'
    if row.get('error') or row.get('ingestion_status') == 'FAILED' or row.get('indexing_status') == 'FAILED': return 'FAILED'
    if row.get('ingestion_status') == 'PROCESSING': return 'PROCESSING'
    if row.get('ingestion_status') in ('QUEUED','PENDING') and row.get('run_id'): return 'QUEUED'
    return 'DISCOVERED'


class ArchiveStatusProvider:
    def __init__(self, runs): self.runs = runs

    def snapshot(self, session):
        scope, sid = session['source_scope'], session.get('source_id')
        views = ResultsService(self.runs)
        active = views.chat_snapshot(session.get('processing_run_id'),sid if scope=='NETWORK' else None,source_scope=scope)
        indexed = set(active['document_runs'])
        with self.runs.db() as store:
            sources = [s for s in store.all('network_sources') if scope != 'MANUAL' and (scope != 'NETWORK' or not sid or s['id'] == sid)]
            source_ids = {s['id'] for s in sources}
            rows = [r for r in store.all('network_files') if r['source_id'] in source_ids]
            scans = [s for s in store.all('network_scans') if s['source_id'] in source_ids]
            manual = []
            rid = session.get('processing_run_id')
            network_runs = {r.get('run_id') for r in store.all('network_files')} | {r.get('run_id') for r in store.all('network_provenance')}
            if scope != 'NETWORK' and rid and rid not in network_runs:
                run = store.get('processing_runs',rid) or {}
                base = views.snapshot(rid)
                entries = base.get('corpus',{}).get('documents',[])
                # Existing run manifest, including failures, is authoritative for this manual corpus.
                represented_uploads=set()
                for entry in entries:
                    did=entry.get('document_id')
                    if not did:continue  # Failed legacy manifest entries are represented by uploads below.
                    doc=store.get('documents',did) or {}
                    from pathlib import Path
                    represented_uploads.update(uid for uid in run.get('upload_ids',[]) if uid in Path(doc.get('source_path','')).parts)
                    manual.append(dict(id=did,document_id=did,filename=doc.get('file_name') or entry.get('file_name') or entry.get('name') or did,
                        source_id=None,run_id=rid,relative_path=doc.get('file_name'),size=next(((store.get('uploads',uid) or {}).get('size') for uid in represented_uploads if uid in Path(doc.get('source_path','')).parts),None),status='DISCOVERED',ingestion_status=doc.get('status','PENDING'),
                        indexing_status='SUCCESS' if did in indexed else 'PENDING'))
                for uid in run.get('upload_ids',[]):
                    if uid in represented_uploads:continue
                    upload=store.get('uploads',uid)
                    if upload:manual.append(dict(id=uid,filename=upload['filename'],source_id=None,run_id=rid,
                        status='DISCOVERED',ingestion_status='PROCESSING' if run.get('status')=='RUNNING' else 'FAILED' if run.get('status') in ('FAILED','PARTIAL') else 'QUEUED'))
        source_views=[];latest={}
        for source in sources:
            history=sorted((s for s in scans if s['source_id']==source['id']),key=lambda s:s['started_at'],reverse=True)
            scan=history[0] if history else None
            if scan:latest[source['id']]=scan['scan_id']
            source_views.append(dict(id=source['id'],name=source['name'],access_status=source.get('connection_status','NOT_CHECKED'),
                status=source.get('status'),enabled=source.get('enabled'),last_error=source.get('last_error'),
                scan={k:scan.get(k) for k in ('scan_id','started_at','finished_at','status','files_discovered','new_count','modified_count','unchanged_count','error_count','errors')} if scan else None))
        files=[];folders=set()
        for row in rows+manual:
            rel=(row.get('relative_path') or row['filename']).replace('\\','/')
            for parent in PurePosixPath(rel).parents:
                if str(parent)!='.':folders.add((row['source_id'],str(parent)))
            files.append({**{k:row.get(k) for k in ('id','filename','relative_path','size','modified_at','source_id','document_id','run_id','status','ingestion_status','indexing_status','error','discovered_at','seen_scan')},
                'format':row.get('format') or PurePosixPath(row['filename']).suffix.lstrip('.').upper(),
                'availability':readiness(row,indexed),'last_scan':bool(row.get('seen_scan') and row.get('seen_scan')==latest.get(row['source_id']))})
        selected=session.get('selected_document_ids') or ([session['document_id']] if session.get('document_id') else [])
        if selected:
            files=[f for f in files if f.get('document_id') in selected]
            folders=[]
        counts=Counter(f['availability'] for f in files)
        return dict(as_of=now(),scope=scope,source_id=sid,sources=source_views,files=files,
            folders=[dict(source_id=s,path=p) for s,p in sorted(folders)],
            counts=dict(discovered=len(files),indexed=counts['INDEXED'],queued=counts['QUEUED'],processing=counts['PROCESSING'],
                pending=counts['DISCOVERED']+counts['QUEUED']+counts['PROCESSING'],failed=counts['FAILED'],missing=counts['MISSING']))

    @staticmethod
    def answer(info, view='OVERVIEW', help_intro=False):
        scope={'ALL_DOCUMENTS':'все документы выбранного корпуса и сетевых источников','NETWORK':'сетевые источники','MANUAL':'загруженные вручную документы выбранного корпуса'}[info['scope']]
        if info['scope']=='NETWORK' and info['source_id']:scope=next((s['name'] for s in info['sources']),'Источник недоступен')
        c=info['counts'];lines=[]
        if help_intro:lines.append('Я помогаю находить положения документов, сравнивать их и проверять состояние архива. Ответы по содержанию опираются на проверенные цитаты. Полный охват выполняется отдельным режимом полного просмотра.')
        lines.extend([f'Область: {scope}.',f"Обнаружено: {c['discovered']}. Проиндексировано: {c['indexed']}. Ожидают или обрабатываются: {c['pending']} (в очереди: {c['queued']}, обрабатываются: {c['processing']}). Ошибок файлов: {c['failed']}. Отсутствуют: {c['missing']}."])
        for s in info['sources']:
            scan=s['scan'];lines.append(f"Источник «{s['name']}»: доступ {s['access_status']}; последний scan: {scan['status']+' · '+scan['started_at'] if scan else 'не запускался'}.")
            if scan:lines.append(f"Последний scan: новых {scan['new_count']}, изменённых {scan['modified_count']}, без изменений {scan['unchanged_count']}; ошибок {scan['error_count']}.")
            errors=(scan or {}).get('errors') or ([s['last_error']] if s.get('last_error') else [])
            if errors:lines.append('Ошибки источника: '+', '.join(sorted(set(errors)))+'. Ошибочный scan не подтверждает отсутствие документов.')
        rows=info['files']
        if view=='NEW':rows=[f for f in rows if f['last_scan'] and f['status']=='NEW']
        elif view=='LAST_SCAN':rows=[f for f in rows if f['last_scan']]
        elif view=='PENDING':rows=[f for f in rows if f['availability'] in ('DISCOVERED','QUEUED','PROCESSING')]
        elif view=='ERRORS':rows=[f for f in rows if f['availability']=='FAILED' or f.get('error')]
        if view=='FOLDERS':
            lines.append('Известные папки по зарегистрированным файлам (пустые и ещё не просканированные папки неизвестны):')
            lines.extend('- '+f['path'] for f in info['folders'][:30])
            lines.append(f"Всего известных папок: {len(info['folders'])}.")
            rows=[]
        elif view!='OVERVIEW':lines.append(f'Файлов по запросу: {len(rows)}.')
        links=[]
        for f in rows[:20]:
            lines.append(f"- {f['filename']} · {f['format']} · {f['availability']}")
            if f.get('run_id') and f.get('document_id'):
                links.append(dict(name=f['filename'],document_id=f['document_id'],source_id=f['source_id'],run_id=f['run_id']))
        if len(rows)>20:lines.append(f'Показаны первые 20 из {len(rows)}. Остальные доступны в каталоге документов.')
        if help_intro:lines.extend(['Примеры:','/help Какие обязанности предусмотрены договором?','/help Сравни условия договоров.','/help Какие документы обнаружены при последнем scan?','/help Какие файлы ещё не обработаны?'])
        return '\n\n'.join(lines),links
