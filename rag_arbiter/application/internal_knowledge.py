"""Consult-only access policy over the existing active registry and indexes."""
from collections import Counter
import re
from pathlib import PurePath
from ..documents import digest
from ..network.notifications import source_access
from .public_knowledge import PublicKnowledge
from .views import ResultsService
from .catalog import CatalogService


class InternalKnowledge(PublicKnowledge):
    def grants(self):
        views = ResultsService(self.runs)
        snapshots = [views.chat_snapshot(None)]
        for run in self.runs.list():
            if run.get('upload_ids') and run.get('status') in ('COMPLETED', 'PARTIAL'):
                snapshots.append(views.chat_snapshot(run['run_id'], source_scope='MANUAL'))
        result = {}
        with self.runs.db() as store:
            sources = {s['id']: s for s in store.all('network_sources')}
            access = {sid: source_access(self.runs, s) for sid, s in sources.items()}
            for snapshot in snapshots:
                for entry in snapshot['corpus']['documents']:
                    did = entry['document_id']; sid = entry.get('source_id')
                    if sid and not access.get(sid): continue
                    version = entry.get('recognition_version')
                    canonical = store.get('normalized_documents', version) if version else None
                    if not canonical or canonical.get('status') != 'SUCCESS': continue
                    doc = store.get('documents', did) or {}
                    index = snapshot['indexes']['structure']
                    result.setdefault(did, dict(document_id=did, recognition_version=version,
                        run_id=entry['processing_run_id'], title=doc.get('file_name') or entry.get('file_name') or 'Документ',
                        revision=digest([version, sorted(p['run_id'] for p in index['partitions'] if did in p['document_ids'])]),
                        source_id=sid, source_name=sources[sid]['name'] if sid else 'Загруженные вручную'))
        return result

    def archive(self, grants, name_filter=''):
        catalog = CatalogService(self.runs).snapshot()
        by_id = {f.get('document_id'): f for f in catalog['files']}
        rows = []
        for did, g in grants.items():
            if name_filter and not re.search(r'(?<!\w)'+re.escape(name_filter), g['title'], re.IGNORECASE): continue
            f = by_id.get(did, {})
            rows.append(dict(title=g['title'], source=g['source_name'],
                format=f.get('format') or PurePath(g['title']).suffix.lstrip('.').upper(),
                date=f.get('discovered_at') or 'не указана в реестре'))
        rows.sort(key=lambda r: r['title'].casefold())
        lines = [f'Доступно для поиска: {len(grants)} документов. По запросу: {len(rows)}.',
                 'Состав по форматам: '+', '.join(f'{k}: {v}' for k,v in sorted(Counter(r['format'] for r in rows).items()))+'.']
        lines += [f"- {r['title']} · {r['source']} · {r['format']} · INDEXED · дата появления: {r['date']}" for r in rows[:12]]
        if len(rows)>12: lines.append(f'Показаны первые 12 из {len(rows)}. Уточните название документа.')
        return '\n\n'.join(lines)
