from fastapi import Request, Query, HTTPException
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
import ipaddress
from ..network.sync import SourceInput


class RootRequest(BaseModel):
    root_path: str = Field(min_length=1, max_length=4096)


def local_operator(request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    if not local or request.url.hostname not in {'localhost','127.0.0.1','::1'}:
        raise HTTPException(403, 'New roots can only be approved on this computer')


def register_sources(app, render):
    @app.post('/api/sources/check-path')
    def check_path(request: Request, body: RootRequest):
        local_operator(request)
        root = request.app.state.network.probe_root(body.root_path)
        return {'status':'ACCESS_OK','root_path':root,'connector_access':'read-only', 'os_write_permission':'NOT_TESTED'}

    @app.get('/ui/runs/{run_id}/documents/{document_id}/blocks/{block_id}')
    def fragment(request: Request, run_id: str, document_id: str, block_id: str):
        import json
        views = request.app.state.views
        entry = next((e for e in views.snapshot(run_id).get('corpus',{}).get('documents',[]) if e.get('document_id')==document_id), None)
        if not entry:
            raise KeyError('Document outside run')
        with request.app.state.runs.db() as store:
            doc = store.get('normalized_documents',entry['recognition_version'])
            block = next((b for b in (doc or {}).get('blocks',[]) if b['block_id']==block_id),None)
            if not block or block.get('locator_kind')!='structure':
                raise KeyError('Structural block outside document version')
            origins = [json.loads(r[0]) for r in store.db.execute("SELECT data FROM network_provenance WHERE json_extract(data,'$.document_id')=?",(document_id,))]
        return render(request,'structural_fragment.html',document=doc,block=block,provenance=origins,run_id=run_id)

    @app.get('/sources')
    def sources(request: Request):
        if request.headers.get("HX-Request") != "true":
            query = "?connect=1" if "connect" in request.query_params else ""
            return RedirectResponse("/" + query + "#sources")
        service = request.app.state.network
        return render(request, 'sources.html', sources=service.page('network_sources'), summaries=[service.discovery(s['id']) for s in service.page('network_sources')], roots=service.config.network_sources.allowed_roots)

    @app.post('/api/sources')
    def add(request: Request, data: SourceInput):
        return request.app.state.network.add(data.model_dump())

    @app.post('/ui/sources')
    async def add_form(request: Request):
        data = dict(await request.form())
        for key in ('enabled', 'recursive'):
            data[key] = key in data
        for key in ('include_patterns', 'exclude_patterns'):
            data[key] = [p.strip() for p in data.get(key, '').split(',') if p.strip()]
        approved = data.pop('authorize_root', '')
        data = SourceInput(**data).model_dump()
        if data.get('root_path') not in request.app.state.network.config.network_sources.allowed_roots:
            local_operator(request)
            if approved != 'yes':
                raise ValueError('Explicit read-only root approval is required')
            data['root_path'] = await run_in_threadpool(request.app.state.network.authorize_root, data['root_path'])
        await run_in_threadpool(request.app.state.network.add, data)
        service = request.app.state.network
        return render(request, 'sources.html', sources=service.page('network_sources'), summaries=[service.discovery(s['id']) for s in service.page('network_sources')], roots=service.config.network_sources.allowed_roots)

    @app.get('/sources/{source_id}')
    def detail(request: Request, source_id: str, offset: int = Query(0, ge=0)):
        if request.headers.get('HX-Request') != 'true':
            return RedirectResponse('/?' + urlencode({'source': source_id, 'offset': offset}) + '#sources')
        svc = request.app.state.network
        return render(request, 'source_detail.html', source=svc.source(source_id),
            info=svc.discovery(source_id,offset), files=svc.page('network_files', source_id, offset), history=svc.page('network_scans', source_id, limit=20), offset=offset)

    @app.get('/sources/{source_id}/tree')
    async def tree(request: Request, source_id: str, relative: str = '', offset: int = Query(0, ge=0)):
        svc = request.app.state.network
        listing = await svc.inspect(source_id, relative, offset)
        return render(request, 'source_tree.html', source=svc.source(source_id), relative=relative, listing=listing)

    @app.post('/api/sources/{source_id}/check')
    async def check(request: Request, source_id: str):
        return await request.app.state.network.check_access(source_id)

    @app.post('/api/sources/{source_id}/scan')
    def scan(request: Request, source_id: str):
        return {'scan_id': request.app.state.network.start(source_id)}

    @app.post('/api/sources/{source_id}/toggle')
    def toggle(request: Request, source_id: str):
        return request.app.state.network.toggle(source_id)

    @app.post('/api/source-scans/{scan_id}/cancel')
    def cancel(request: Request, scan_id: str):
        request.app.state.network.cancel(scan_id)
        return {'status': 'CANCELLATION_REQUESTED'}

    @app.get('/api/sources/{source_id}/files')
    def files(request: Request, source_id: str, offset: int = Query(0, ge=0)):
        return request.app.state.network.page('network_files', source_id, offset)

    @app.get('/api/sources/{source_id}/history')
    def history(request: Request, source_id: str, offset: int = Query(0, ge=0)):
        return request.app.state.network.page('network_scans', source_id, offset)
