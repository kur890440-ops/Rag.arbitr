from urllib.parse import urlencode
from fastapi import Request,Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from ..application.catalog import CatalogService


class DiscussDocument(BaseModel):
    file_id:str
    scope:str='ALL_DOCUMENTS'
    source_id:str|None=None
    run_id:str|None=None


def register_catalog(app,render):
    def args(request):
        q=request.query_params
        return dict(scope=q.get('scope','ALL_DOCUMENTS'),source_id=q.get('source_id') or None,run_id=q.get('run_id') or None)

    def result(request):
        service=CatalogService(request.app.state.runs);filters=dict(request.query_params)
        info=service.snapshot(**args(request))
        pager=service.page(info,q=filters.get('q',''),format=filters.get('format',''),status=filters.get('status',''),folder=filters.get('folder',''),offset=int(filters.get('offset',0)),page_size=int(filters.get('page_size',25)))
        return render(request,'catalog_results.html',info=info,pager=pager,filters=filters,query=urlencode(filters))

    @app.get('/catalog')
    def catalog(request:Request):
        return RedirectResponse('/chat')

    @app.get('/api/chat/sessions/{sid}/documents')
    def selector(request:Request,sid:str,q:str='',offset:int=Query(0,ge=0),all_ready:bool=False):
        session=request.app.state.chat.load(sid)['session']
        service=CatalogService(request.app.state.runs)
        info=service.snapshot(scope=session['source_scope'],source_id=session['source_id'],run_id=session['processing_run_id'])
        # Match exactly the existing session corpus, including sessions without a manual run.
        from ..application.views import ResultsService
        active=ResultsService(request.app.state.runs).chat_snapshot(session['processing_run_id'],session['source_id'],source_scope=session['source_scope'])
        ready=set(active['document_runs'])
        for row in info['files']:
            if row.get('document_id') not in ready and row['availability']=='INDEXED':row['availability']='DISCOVERED'
        if all_ready:return dict(document_ids=sorted(ready))
        pager=service.page(info,q=q,page_size=25,offset=offset)
        with request.app.state.runs.db() as store:
            for row in pager['files']:
                doc=store.get('documents',row.get('document_id')) if row.get('document_id') else None
                row['units']=(doc or {}).get('page_count') or (doc or {}).get('block_count') or len((doc or {}).get('blocks') or [])
        return render(request,'document_selector.html',pager=pager)

    @app.get('/ui/catalog')
    def catalog_results(request:Request):return result(request)

    @app.get('/ui/catalog/tree')
    def tree(request:Request,folder:str='',offset:int=Query(0,ge=0)):
        service=CatalogService(request.app.state.runs);info=service.snapshot(**args(request));data=service.branches(info,folder,offset)
        scope=args(request)
        for node in data['nodes']:node['url']='/ui/catalog/tree?'+urlencode({k:v or '' for k,v in {**scope,'folder':node['path']}.items()})
        return render(request,'catalog_tree.html',tree=data,more_url='/ui/catalog/tree?'+urlencode({**{k:v or '' for k,v in scope.items()},'folder':folder,'offset':data['next_offset']}))

    @app.post('/api/catalog/discuss',status_code=201)
    def discuss(request:Request,body:DiscussDocument):
        row=CatalogService(request.app.state.runs).file(body.file_id,scope=body.scope,source_id=body.source_id,run_id=body.run_id)
        if not row:raise KeyError(body.file_id)
        if row['availability']!='INDEXED':raise ValueError('Документ ещё не доступен для RAG. Дождитесь успешной индексации.')
        session=request.app.state.chat.create(row['run_id'],row['document_id'],row['source_id'])
        return dict(session_id=session.session_id,url='/chat?session_id='+session.session_id)

    @app.get('/ui/catalog/viewer/{key}')
    def viewer(request:Request,key:str):
        row=CatalogService(request.app.state.runs).file(key,**args(request))
        if not row or not row.get('document_id') or not row.get('run_id'):raise KeyError(key)
        views=request.app.state.views;rid=row['run_id'];did=row['document_id']
        entry=next((e for e in views.snapshot(rid).get('corpus',{}).get('documents',[]) if e['document_id']==did),None)
        if not entry:raise KeyError(key)
        with request.app.state.runs.db() as store:doc=store.get('normalized_documents',entry['recognition_version'])
        links=[]
        if doc and doc.get('locator_kind')=='structure':
            links=[dict(label=f"Блок {i+1} · {b.get('section') or 'Без раздела'}",url=f"/ui/runs/{rid}/documents/{did}/blocks/{b['block_id']}") for i,b in enumerate(doc['blocks'])]
        else:
            links=[dict(label='Страница '+str(p['page_number']),url=f"/ui/runs/{rid}/pages/{p['recognition_id']}") for p in views.pages(rid) if p['document_id']==did]
        return render(request,'catalog_viewer.html',file=row,links=links)
