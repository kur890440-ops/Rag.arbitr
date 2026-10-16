"""File catalog over the existing archive projection, without filesystem access."""
from pathlib import PurePosixPath
from .archive_status import ArchiveStatusProvider


class CatalogService:
    def __init__(self,runs):self.runs=runs

    def snapshot(self,scope='ALL_DOCUMENTS',source_id=None,run_id=None):
        if scope not in ('ALL_DOCUMENTS','NETWORK','MANUAL'):raise ValueError('Invalid scope')
        if scope=='MANUAL':source_id=None
        if source_id:
            with self.runs.db() as store:
                if not store.get('network_sources',source_id):raise KeyError(source_id)
            scope='NETWORK'
        info=ArchiveStatusProvider(self.runs).snapshot(dict(source_scope=scope,source_id=source_id,processing_run_id=run_id))
        if not run_id and scope!='NETWORK':
            # Reuse each manual run's canonical projection; deduplicate repeated runs.
            # Network runs have no upload_ids and remain owned by the source registry.
            known={f['id'] for f in info['files']}
            for run in self.runs.list():
                if not run.get('upload_ids'):continue
                manual=ArchiveStatusProvider(self.runs).snapshot(dict(source_scope='MANUAL',source_id=None,processing_run_id=run['run_id']))
                for file in manual['files']:
                    if file['id'] not in known:info['files'].append(file);known.add(file['id'])
            from collections import Counter
            counts=Counter(f['availability'] for f in info['files'])
            info['counts']=dict(discovered=len(info['files']),indexed=counts['INDEXED'],queued=counts['QUEUED'],processing=counts['PROCESSING'],pending=counts['DISCOVERED']+counts['QUEUED']+counts['PROCESSING'],failed=counts['FAILED'],missing=counts['MISSING'])
        names={s['id']:s['name'] for s in info['sources']}
        for f in info['files']:
            f['source_name']=names.get(f['source_id'],'Загруженные вручную')
            f['relative_path']=(f.get('relative_path') or f['filename']).replace('\\','/')
        return info

    @staticmethod
    def page(info,q='',format='',status='',folder='',offset=0,page_size=25):
        if page_size not in (10,25,50,100):raise ValueError('Invalid page size')
        if offset<0:raise ValueError('Invalid offset')
        folder=folder.strip('/')
        rows=[f for f in info['files'] if q.strip().casefold() in f['filename'].casefold()
            and (not format or f['format']==format)
            and (not status or status in (f['availability'],f['status']))
            and (not folder or f['relative_path'].startswith(folder+'/'))]
        rows.sort(key=lambda f:(f['filename'].casefold(),f['source_name'].casefold(),f['id']))
        total=len(rows);pages=max(1,(total+page_size-1)//page_size);offset=min(offset//page_size,pages-1)*page_size
        return dict(files=rows[offset:offset+page_size],total=total,all_total=len(info['files']),offset=offset,size=page_size,page=offset//page_size+1,pages=pages,start=offset+1 if total else 0,end=min(offset+page_size,total))

    @staticmethod
    def branches(info,folder='',offset=0):
        prefix=folder.strip('/')
        prefix=prefix+'/' if prefix else ''
        children=set()
        for f in info['files']:
            path=f['relative_path']
            if path.startswith(prefix):
                tail=path[len(prefix):]
                if '/' in tail:children.add(tail.split('/',1)[0])
        children=sorted(children,key=str.casefold)
        return dict(nodes=[dict(name=n,path=prefix+n) for n in children[offset:offset+100]],more=offset+100<len(children),next_offset=offset+100)

    def file(self,key,**scope):
        return next((f for f in self.snapshot(**scope)['files'] if f['id']==key),None)
