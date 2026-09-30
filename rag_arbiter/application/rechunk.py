"""Chunk experiments over persisted normalized documents; no recognition dependency."""
import copy
import statistics
from pathlib import Path
from uuid import uuid4
from ..config import Config
from ..documents import Document, digest, now, save_json
from ..reporting import generate_report


class ChunkSettings:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def defaults(self):
        c = self.config
        return dict(fixed_tokens=c.fixed_tokens, fixed_overlap=c.fixed_overlap,
                    fixed_max_tokens=c.fixed_max_tokens, structure_max_tokens=c.structure_max_tokens)

    def read(self, document_id=None):
        global_settings = self.store.get('chunk_settings', 'global') or self.defaults()
        override = self.store.get('chunk_settings', document_id) if document_id else None
        return dict(global_settings=global_settings, override=override,
                    effective=override or global_settings, use_global=not bool(override))

    def save(self, values, document_id=None, use_global=False):
        if document_id and use_global:
            with self.store.db:
                self.store.db.execute('DELETE FROM chunk_settings WHERE id=?', (document_id,))
        else:
            if set(values) != set(self.defaults()):
                raise ValueError('Provide all four chunk parameters')
            if any(type(v) is not int for v in values.values()):
                raise ValueError('Chunk parameters must be integers')
            Config(**{**self.config.model_dump(), **values})
            self.store.put('chunk_settings', document_id or 'global', values)
        return self.read(document_id)


def partitions(index):
    return index.get('partitions') or [dict(collection=index['collection'],
        document_ids=index['document_ids'], chunk_ids=index.get('chunk_ids', []),
        run_id=index['run_id'], parameters_json=index.get('parameters_json', index.get('settings')))]


def active_parameters(index, document_id, strategy):
    if not index or not (index.get('collection') or index.get('partitions')):
        return None
    for part in partitions(index):
        if document_id in part['document_ids']:
            values = part.get('parameters_json')
            if isinstance(values, dict):
                return values.get(document_id)
            if isinstance(values, list):
                keys = ['fixed_tokens','fixed_overlap','fixed_max_tokens'] if strategy=='fixed' else ['structure_max_tokens']
                return dict(zip(keys, values))
    return None


class RechunkService:
    def __init__(self, runs):
        self.runs = runs

    def prepare(self, source_run_id, document_id=None, strategies=None):
        from .views import ResultsService
        from .runs import TERMINAL
        run = self.runs.get(source_run_id)
        if run['status'] not in TERMINAL:
            raise ValueError('Wait for processing to finish')
        strategies = strategies or ['fixed', 'structure']
        if not strategies or len(set(strategies)) != len(strategies) or set(strategies) - {'fixed', 'structure'}:
            raise ValueError('Unknown chunk strategy')
        snapshot = ResultsService(self.runs).snapshot(source_run_id)
        entries = snapshot.get('corpus', {}).get('documents', [])
        if not isinstance(entries, list) or not entries:
            raise ValueError('No completed normalized corpus; finish recognition first')
        selected = [e for e in entries if e.get('document_id') and (not document_id or e['document_id'] == document_id)]
        if not selected:
            raise ValueError('Document not in normalized corpus')
        cfg = Config(**run['config_json'])
        with self.runs.db() as store:
            settings = ChunkSettings(self.runs.config, store)
            effective = {e['document_id']: settings.read(e['document_id'])['effective'] for e in selected}
        cfg.rechunk_source_run_id, cfg.rechunk_document_id = source_run_id, document_id
        cfg.rechunk_strategies, cfg.rechunk_settings = strategies, effective
        affected = [e for e in selected if any(
            active_parameters(snapshot.get('indexes', {}).get(s, {}), e['document_id'], s) !=
            {k:v for k,v in effective[e['document_id']].items() if k.startswith('fixed_') == (s=='fixed')}
            for s in strategies)]
        folder = self.runs.config.web_data_path / 'runs' / uuid4().hex
        cfg.snapshot_path, cfg.report_path = folder / 'snapshot.json', folder / 'report.html'
        return cfg, dict(documents=len(selected), pages=sum(e['page_count'] for e in selected),
                         affected_documents=len(affected), affected_pages=sum(e['page_count'] for e in affected),
                         recognition=False, keep_overrides=True, strategies=strategies, parameters=effective)

    def start(self, source_run_id, document_id=None, strategies=None, background=True):
        cfg, _ = self.prepare(source_run_id, document_id, strategies)
        return self.runs.create(cfg, run_type='rechunk',
            upload_ids=self.runs.get(source_run_id)['upload_ids'], background=background)

    def execute(self, pipeline):
        from .views import ResultsService
        cfg, store = pipeline.config, pipeline.store
        snapshot = copy.deepcopy(ResultsService(self.runs).snapshot(cfg.rechunk_source_run_id))
        entries = snapshot['corpus']['documents']
        selected = [e for e in entries if e.get('document_id') and (not cfg.rechunk_document_id or e['document_id'] == cfg.rechunk_document_id)]
        docs = []
        for entry in selected:
            saved = store.get('normalized_documents', entry.get('recognition_version') or '')
            saved = saved or store.get('documents', entry['document_id'])
            if not saved or not saved.get('blocks'):
                raise ValueError('Usable normalized document is missing')
            doc = Document(**saved)
            if entry.get('recognition_version') and entry['recognition_version'] != doc.cache_identity:
                raise ValueError('Recognition version changed; select its processing run')
            # Legacy runs: pin only when all page versions belong to the source run.
            if not entry.get('recognition_version'):
                known = set(ResultsService(self.runs).recognition_ids(cfg.rechunk_source_run_id))
                if any(p.recognition_id and p.status != 'FAILED' and p.recognition_id not in known for p in doc.pages):
                    raise ValueError('Legacy recognition has changed; select the newer run')
                entry['recognition_version'] = doc.cache_identity
                store.put('normalized_documents', doc.cache_identity, doc.model_dump())
            docs.append(doc)
        pipeline.reporter.emit('stage_started', 'FIXED_CHUNKING', 'Пересчёт из сохранённого текста; recognition не запускается',
                               total_documents=len(docs), total_pages=sum(d.page_count for d in docs))
        changed = False
        original_cfg = pipeline.config
        target_snapshot = pipeline.snapshot_path
        pipeline.snapshot_path = target_snapshot.with_name('building.json')
        for strategy in cfg.rechunk_strategies:
            old = copy.deepcopy(snapshot.get('indexes', {}).get(strategy, {}))
            active_parts = copy.deepcopy(partitions(old)) if old.get('collection') or old.get('partitions') else []
            new_runs = []
            for doc in docs:
                pipeline.reporter.check()
                desired = {k:v for k,v in cfg.rechunk_settings[doc.document_id].items() if k.startswith('fixed_') == (strategy=='fixed')}
                previous = old.get('parameters_json', {}).get(doc.document_id) if isinstance(old.get('parameters_json'), dict) else None
                for part in active_parts:
                    if doc.document_id in part['document_ids']:
                        values = part.get('parameters_json')
                        if isinstance(values, dict):
                            previous = values.get(doc.document_id, previous)
                        elif isinstance(values, list):
                            keys = ['fixed_tokens','fixed_overlap','fixed_max_tokens'] if strategy=='fixed' else ['structure_max_tokens']
                            previous = dict(zip(keys, values))
                if previous == desired:
                    continue
                pipeline.config = Config(**{**cfg.model_dump(), **cfg.rechunk_settings[doc.document_id]})
                working = copy.deepcopy(snapshot)
                result = pipeline.build_index([doc], working, strategy)
                if result['status'] not in {'SUCCESS', 'PARTIAL'} or not result.get('chunks'):
                    raise ValueError('Chunk/index rebuild failed; previous active index retained')
                result.update(scope_type='DOCUMENT', scope_id=doc.document_id,
                              source_recognition_version=doc.cache_identity)
                store.put('index_runs', result['run_id'], result)
                new_runs.append(result)
                for part in active_parts:
                    part['document_ids'] = [d for d in part['document_ids'] if d != doc.document_id]
                    part['chunk_ids'] = [k for k in part['chunk_ids'] if store.get('chunks', k)['document_id'] != doc.document_id]
                active_parts = [p for p in active_parts if p['document_ids']]
                active_parts.extend(partitions(result))
            if not new_runs:
                continue
            changed = True
            index = copy.deepcopy(old or new_runs[0])
            index.update(run_id=uuid4().hex, chunking_run_id=None, partitions=active_parts,
                         scope_type='DOCUMENT' if cfg.rechunk_document_id else 'CORPUS',
                         scope_id=cfg.rechunk_document_id or snapshot['corpus']['corpus_id'],
                         created_at=now(), started_at=new_runs[0]['started_at'], finished_at=now(),
                         duration=sum(r['duration'] for r in new_runs),
                         parameters_json={p['run_id']: p['parameters_json'] for p in active_parts},
                         source_recognition_version={e['document_id']:e.get('recognition_version') for e in entries if e.get('document_id')})
            index['chunking_run_id'] = index['run_id']
            index['chunk_ids'] = [k for p in active_parts for k in p['chunk_ids']]
            index['document_ids'] = sorted({d for p in active_parts for d in p['document_ids']})
            if not old:
                index['normalized_hash'] = digest([(d.document_id, d.cache_identity, [b.model_dump() for b in d.blocks]) for d in docs])
            # Preserve original order for fair corpus comparison.
            if old.get('document_ids') and set(old['document_ids']) == set(index['document_ids']):
                index['document_ids'] = old['document_ids']
            counts = [store.get('chunks', k)['token_count'] for k in index['chunk_ids']]
            index.update(chunks=len(counts), chunks_count=len(counts), avg_tokens=statistics.mean(counts) if counts else 0,
                         median_tokens=statistics.median(counts) if counts else 0, min_tokens=min(counts, default=0), max_tokens=max(counts, default=0),
                         duration_ms=index['duration']*1000, documents=len(index['document_ids']),
                         pages=sum(e['page_count'] for e in entries if e.get('document_id') in index['document_ids']), status=old.get('status', 'SUCCESS'))
            index['examples'] = [store.get('chunks', k) for k in index['chunk_ids'][:2]]
            store.put('index_runs', index['run_id'], index)
            snapshot['indexes'][strategy] = index
        pipeline.config = original_cfg
        pipeline.snapshot_path = target_snapshot
        if changed:
            snapshot['evaluation'] = {'status': 'STALE', 'queries': [], 'reason': 'Active chunk runs changed'}
        snapshot['status'] = 'SUCCESS' if all(i['status']=='SUCCESS' for i in snapshot['indexes'].values()) else 'PARTIAL'
        save_json(pipeline.snapshot_path, snapshot)
        generate_report(cfg, snapshot)
        return snapshot
