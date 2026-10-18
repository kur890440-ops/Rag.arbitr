"""Explicit grants over canonical versions. No source-wide publication or new index."""
import copy
from uuid import uuid4
from ..documents import digest, now
from .views import ResultsService
from .rechunk import partitions


class PublicKnowledge:
    def __init__(self,runs):self.runs=runs

    def candidates(self):
        rows=[]
        with self.runs.db() as store:
            grants={g['key']:g for g in store.all('public_document_grants')}
            runs=store.all('processing_runs')
        for run in runs:
            if run.get('status')!='COMPLETED':continue
            try:snapshot=ResultsService(self.runs).snapshot(run['run_id'])
            except (KeyError,ValueError,OSError):continue
            for entry in snapshot.get('corpus',{}).get('documents',[]):
                did=entry['document_id'];version=entry.get('recognition_version')
                if not version:continue
                key=digest([did,version]);grant=grants.get(key,{})
                with self.runs.db() as store:document=store.get('documents',did) or {}
                rows.append(dict(key=key,document_id=did,recognition_version=version,run_id=run['run_id'],
                    title=document.get('file_name') or entry.get('file_name') or entry.get('title') or 'Опубликованный документ',public=grant.get('public',False)))
        return list({r['key']:r for r in rows}.values())

    def publish(self,key,public):
        candidate=next((r for r in self.candidates() if r['key']==key),None)
        if not candidate:raise KeyError(key)
        with self.runs.db() as store:
            previous=store.get('public_document_grants',key) or {}
            row={**candidate,'public':public,'public_id':previous.get('public_id') or uuid4().hex,
                 'revision':uuid4().hex,'updated_at':now()}
            store.put('public_document_grants',key,row)
        return row

    def grants(self):
        from ..network.notifications import source_access
        with self.runs.db() as store:
            origins=store.all('network_provenance');result={}
            for g in store.all('public_document_grants'):
                if not g['public']:continue
                sources={p['source_id'] for p in origins if p.get('document_id')==g['document_id']}
                if sources and not any(source_access(self.runs,store.get('network_sources',sid)) for sid in sources):continue
                result[g['document_id']]=g
            return result

    def snapshot(self):
        entries=[];parts=[];identities=[];grants={}
        views=ResultsService(self.runs)
        with self.runs.db() as store:
            for did,g in self.grants().items():
                from ..network.notifications import source_access
                origins=[p for p in store.all('network_provenance') if p.get('document_id')==did]
                if origins and not any(source_access(self.runs,store.get('network_sources',p['source_id'])) for p in origins):continue
                try:snap=views.snapshot(g['run_id'])
                except (KeyError,ValueError,OSError):continue
                entry=next((e for e in snap.get('corpus',{}).get('documents',[]) if e['document_id']==did and e.get('recognition_version')==g['recognition_version']),None)
                canonical=store.get('normalized_documents',g['recognition_version'])
                index=snap.get('indexes',{}).get('structure') or {}
                if not entry or not canonical or canonical.get('status')!='SUCCESS' or index.get('status') not in ('SUCCESS','PARTIAL'):continue
                valid=[]
                for part in partitions(index):
                    committed=store.get('index_runs',part['run_id']) or {}
                    if committed.get('status') not in ('SUCCESS','PARTIAL') or did not in part['document_ids']:continue
                    chunks=[k for k in part['chunk_ids'] if k in committed.get('chunk_ids',[]) and (store.get('chunks',k) or {}).get('document_id')==did]
                    if chunks:valid.append({**copy.deepcopy(part),'document_ids':[did],'chunk_ids':chunks})
                if not valid:continue
                entries.append({**entry,'processing_run_id':g['run_id']});parts.extend(valid);identities.append(index);grants[did]=g
        identity=digest([(e['document_id'],e['recognition_version']) for e in entries]);index=None
        if parts:
            first=identities[0]
            if any(any(i.get(k)!=first.get(k) for k in ('embedding','dimension','similarity')) for i in identities):raise ValueError('Несовместимые опубликованные индексы')
            index={**copy.deepcopy(first),'run_id':identity,'corpus_id':identity,'partitions':parts,'document_ids':list(grants),
                   'chunk_ids':list(dict.fromkeys(k for p in parts for k in p['chunk_ids']))}
            index['chunks']=len(index['chunk_ids'])
        return dict(corpus=dict(corpus_id=identity,documents=entries),indexes={'structure':index} if index else {},
                    document_runs={did:g['run_id'] for did,g in grants.items()},document_sources={did:None for did in grants}),grants

    def still_allowed(self,grants):
        current=self.grants()
        return all(d in current and current[d]['revision']==g['revision'] for d,g in grants.items())
