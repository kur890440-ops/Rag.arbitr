import asyncio
import json
import socket
import psutil
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Form, Query, HTTPException
from starlette.datastructures import UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from ..config import Config
from ..pipeline import Pipeline
from ..application.runs import RunService, TERMINAL, safe_error
from ..application.uploads import UploadService
from ..application.views import ResultsService
from ..application.status import SystemStatusService
from ..application.rechunk import RechunkService, ChunkSettings
from ..application.rag import RAGComparisonService, ControlQuestion
from ..llm import MiniMaxLLMProvider
from typing import Literal
from ..reranking import RAGPipelineMode
from ..scope import RAGScope

ROOT = Path(__file__).parent


class RequestSizeLimit:
    """Bound streamed multipart requests too, including chunked transfers."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.limit:
                raise HTTPException(413, "Upload request too large")
            return message

        await self.app(scope, limited_receive if scope["type"] == "http" else receive, send)


class StartRequest(BaseModel):
    upload_ids: list[str]
    recognition_provider: str | None = None


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    rag_scope: RAGScope = RAGScope.ALL_DOCUMENTS
    selected_document_id: str | None = None


class RAGRequest(BaseModel):
    claim_support_threshold: float | None = Field(None,ge=0,le=1,allow_inf_nan=False)
    rag_pipeline_mode: RAGPipelineMode = RAGPipelineMode.BASELINE
    rerank_threshold: float | None = Field(None,ge=0,le=1)
    question: str = Field('',max_length=4000)
    question_id: str | None = None
    document_id: str | None = None
    selected_document_id: str | None = None
    rag_scope: RAGScope = RAGScope.ALL_DOCUMENTS
    strategy: Literal['fixed','structure'] = 'structure'
    top_k: int | None = Field(None,ge=1,le=10,strict=True)
    candidate_top_n: int | None = Field(None,ge=1,le=100,strict=True)
    max_context_sources: int | None = Field(None,ge=1,le=20,strict=True)
    context_token_budget: int | None = Field(None,ge=256,le=64000,strict=True)
    minimum_candidate_score: float | None = Field(None,ge=-1,le=1)
    chunking_run_id: str | None = None


def create_app(config=None, pipeline_factory=Pipeline, llm_factory=MiniMaxLLMProvider):
    config = config or Config.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.runs = RunService(config, pipeline_factory)
        app.state.uploads = UploadService(app.state.runs)
        app.state.views = ResultsService(app.state.runs)
        app.state.system = SystemStatusService(config)
        app.state.rag = RAGComparisonService(app.state.runs,llm_factory)
        yield
        await run_in_threadpool(app.state.runs.close)

    app = FastAPI(title="rag.арбитр · Day 23", lifespan=lifespan, docs_url=None, redoc_url=None)
    request_limit = config.max_upload_request_size
    if config.max_upload_size and config.max_upload_files:
        request_limit = min(request_limit, config.max_upload_size * config.max_upload_files + 1024 * 1024)
    if request_limit:
        app.add_middleware(RequestSizeLimit, limit=request_limit)
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", *config.web_allowed_hosts}
    if config.web_host in {"0.0.0.0", "::"}:
        allowed_hosts.update({socket.gethostname(), socket.getfqdn()})
        for addresses in psutil.net_if_addrs().values():
            allowed_hosts.update(a.address for a in addresses if a.family == socket.AF_INET)
    else:
        allowed_hosts.add(config.web_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(allowed_hosts))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    from .markdown import answer_markdown
    templates.env.filters['answer_markdown'] = answer_markdown

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-rag-request") != "1" or (origin and origin != str(request.base_url).rstrip("/")):
                return JSONResponse({"detail": "Same-origin requests with X-RAG-Request required"}, status_code=403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            if request_limit and size > request_limit:
                return JSONResponse({"detail": "Upload request too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.endswith("/report") and request.method == "GET":
            response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; sandbox"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Запись или артефакт не найдены"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def failed(request, exc):
        return JSONResponse({"detail": safe_error(exc)}, status_code=500)

    def render(request, name, **context):
        return templates.TemplateResponse(request=request, name=name, context={"config": config, **context})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, run_id: str | None = None):
        runs = request.app.state.runs.list()
        # An explicitly created smoke corpus must not replace the user's corpus.
        default_run=next((r['run_id'] for r in runs if r.get('operation')!='analysis_fixture'),'')
        selected = run_id if any(r["run_id"] == run_id for r in runs) else default_run
        record = next((r for r in runs if r["run_id"] == selected), {})
        with request.app.state.runs.db() as store:
            uploads = [store.get("uploads", uid) for uid in record.get("upload_ids", [])]
        uploads = [u for u in uploads if u]
        return render(request, "index.html", runs=runs, selected=selected, uploads=uploads,
                      ids=json.dumps([u["upload_id"] for u in uploads]))

    def enriched_status(request):
        status=request.app.state.system.status()
        service=request.app.state.rag
        providers=list(service.rerankers.values())
        installed=all((Path(config.reranker.local_path)/name).is_file() for name in ('config.json','tokenizer.json','model.safetensors'))
        device=config.reranker.device
        if device=='auto':device='cuda' if status.get('torch_cuda_available') else 'cpu'
        available=installed and (device!='cuda' or status.get('torch_cuda_available'))
        status['reranker']=providers[-1].status() if providers else dict(model=config.reranker.model,device=device,status='READY' if available else 'ERROR',loaded=False)
        status['query_rewrite']=dict(model=config.llm.model,status='READY' if config.llm.resolved_key() else 'ERROR')
        return status

    @app.get("/api/system/status")
    def system(request: Request):
        return enriched_status(request)

    @app.get("/ui/system", response_class=HTMLResponse)
    def system_ui(request: Request, details: bool = False):
        return render(request, "system.html" if details else "system_strip.html", status=enriched_status(request))

    def save_uploads(request, files):
        if not files:
            raise ValueError("Выберите файлы для загрузки")
        if config.max_upload_files and len(files) > config.max_upload_files:
            raise ValueError("Превышено допустимое количество файлов")
        result = []
        for item in files:
            try:
                result.append(request.app.state.uploads.save(item.filename, item.content_type, item.file))
            finally:
                item.file.close()
        return result

    async def receive_uploads(request):
        # Explicitly override Starlette's implicit 1000-file multipart limit.
        # File contents spool to disk rather than accumulating in memory.
        async with request.form(max_files=config.max_upload_files or float("inf")) as form:
            files = form.getlist("files")
            if not all(isinstance(item, UploadFile) for item in files):
                raise ValueError("Ожидаются файлы в поле files")
            return await run_in_threadpool(save_uploads, request, files)

    @app.post("/api/uploads", status_code=201)
    async def upload(request: Request):
        return {"uploads": await receive_uploads(request)}

    @app.post("/ui/uploads", response_class=HTMLResponse)
    async def upload_ui(request: Request):
        uploads = await receive_uploads(request)
        return render(request, "uploads.html", uploads=uploads, ids=json.dumps([u["upload_id"] for u in uploads]))

    def start(request, data):
        cfg = request.app.state.uploads.run_config(data.upload_ids, data.recognition_provider)
        run_id = request.app.state.runs.create(cfg, upload_ids=data.upload_ids)
        return request.app.state.runs.public(request.app.state.runs.get(run_id))

    @app.post("/api/runs", status_code=202)
    def start_api(request: Request, data: StartRequest):
        return start(request, data)

    @app.post("/ui/runs", response_class=HTMLResponse)
    def start_ui(request: Request, upload_ids: str = Form("[]"), recognition_provider: str = Form("qwen3_vl")):
        try:
            ids = json.loads(upload_ids)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("Не удалось прочитать набор файлов. Загрузите документы заново.")
        if not ids:
            raise ValueError("Сначала выберите файлы и нажмите «Загрузить выбранные файлы», затем запускайте обработку.")
        if not isinstance(ids, list) or not all(isinstance(uid, str) for uid in ids):
            raise ValueError("Некорректный набор файлов. Загрузите документы заново.")
        run = start(request, StartRequest(upload_ids=ids, recognition_provider=recognition_provider))
        response = HTMLResponse("Обработка поставлена в очередь")
        response.headers["HX-Trigger"] = json.dumps({"runCreated": {"run_id": run["run_id"]}})
        return response

    @app.get("/api/runs")
    def runs_list(request: Request):
        return [request.app.state.runs.public(r) for r in request.app.state.runs.list()]

    @app.get("/api/runs/{run_id}")
    def run_status(request: Request, run_id: str):
        run = request.app.state.runs.public(request.app.state.runs.get(run_id))
        run["errors"] = request.app.state.views.errors(run_id)
        return run

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(request: Request, run_id: str):
        return request.app.state.runs.cancel(run_id)

    @app.post("/api/runs/{run_id}/retry")
    async def retry_pages(request: Request, run_id: str):
        old = request.app.state.runs.get(run_id)
        if old['status'] not in {'COMPLETED','PARTIAL','FAILED','CANCELLED','INTERRUPTED'}:
            raise ValueError('Дождитесь завершения запуска')
        data = await request.json()
        mode = data.get('mode', 'failed')
        if mode not in {'failed','fallback','force'}:
            raise ValueError('Unknown retry mode')
        doc_id, page = data.get('document_id'), data.get('page_number')
        known = request.app.state.views.pages(run_id)
        if doc_id and not any(p['document_id']==doc_id for p in known):
            raise ValueError('Document not in run')
        if page is not None and (not doc_id or not isinstance(page,int) or not any(p['document_id']==doc_id and p['page_number']==page for p in known)):
            raise ValueError('Page not in run')
        if mode == 'force' and not doc_id:
            raise ValueError('Выберите документ или страницу для принудительного повтора')
        cfg = request.app.state.uploads.run_config(old['upload_ids'], 'qwen3_vl')
        cfg.retry_mode, cfg.retry_document_id, cfg.retry_page_number = mode, doc_id, page
        new_id = request.app.state.runs.create(cfg, upload_ids=old['upload_ids'])
        return {'run_id':new_id}

    @app.get("/runs/{run_id}/events")
    @app.get("/api/runs/{run_id}/events")
    async def events(request: Request, run_id: str, after: int = Query(0, ge=0)):
        request.app.state.runs.get(run_id)
        try:
            after = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise ValueError("Invalid Last-Event-ID")
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                with request.app.state.runs.db() as store:
                    rows = store.events(run_id, cursor)
                    run = store.get("processing_runs", run_id)
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: {row['event_type']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if run["status"] not in ('QUEUED','RUNNING') and len(rows) < 200:
                    break
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/pages")
    def pages(request: Request, run_id: str):
        return request.app.state.views.pages(run_id)

    @app.get("/api/runs/{run_id}/pages/{recognition_id}")
    def page(request: Request, run_id: str, recognition_id: str):
        return request.app.state.views.page(run_id, recognition_id)

    @app.get("/api/runs/{run_id}/pages/{recognition_id}/image")
    def image(request: Request, run_id: str, recognition_id: str):
        return FileResponse(request.app.state.views.page_file(run_id, recognition_id, "image"), media_type="image/png")

    @app.get("/api/runs/{run_id}/pages/{recognition_id}/raw")
    def raw(request: Request, run_id: str, recognition_id: str):
        path = request.app.state.views.page_file(run_id, recognition_id, "raw")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @app.get("/ui/runs/{run_id}/pages/{recognition_id}", response_class=HTMLResponse)
    def viewer(request: Request, run_id: str, recognition_id: str):
        return render(request, "viewer.html", run_id=run_id, page=request.app.state.views.page(run_id, recognition_id))

    @app.get("/api/runs/{run_id}/chunks")
    def chunks(request: Request, run_id: str, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100), document_id: str | None = None, page: int | None = Query(None, ge=1), section: str | None = None):
        return request.app.state.views.chunks(run_id, offset, limit, document_id, page, section)

    @app.get("/ui/runs/{run_id}/chunks", response_class=HTMLResponse)
    def chunks_ui(request: Request, run_id: str, offset: int = Query(0, ge=0), document_id: str | None = None, page: int | None = Query(None, ge=1), section: str | None = None):
        return render(request, "chunks.html", run_id=run_id, data=request.app.state.views.chunks(run_id, offset, 5, document_id, page, section), offset=offset,
                      document_id=document_id, page_filter=page, section_filter=section)

    @app.get('/api/runs/{run_id}/chunks/{chunk_id}/embedding')
    def chunk_embedding(request: Request, run_id: str, chunk_id: str):
        return request.app.state.views.embedding(run_id, chunk_id)

    @app.get('/api/runs/{run_id}/files')
    def files_api(request: Request, run_id: str):
        return request.app.state.views.files(run_id)

    @app.get('/ui/runs/{run_id}/files', response_class=HTMLResponse)
    def files_ui(request: Request, run_id: str):
        return render(request, 'file_panel.html', run_id=run_id, files=request.app.state.views.files(run_id))

    def check_document(request, run_id, document_id):
        request.app.state.runs.get(run_id)
        if document_id and not any(f['document_id']==document_id for f in request.app.state.views.files(run_id)):
            raise ValueError('Document not in run')

    @app.get('/api/runs/{run_id}/chunk-settings')
    def read_settings(request: Request, run_id: str, document_id: str | None = None):
        check_document(request, run_id, document_id)
        with request.app.state.runs.db() as store:
            return ChunkSettings(config, store).read(document_id)

    @app.post('/api/runs/{run_id}/chunk-settings')
    async def save_settings(request: Request, run_id: str):
        data = await request.json()
        document_id = data.get('document_id')
        check_document(request, run_id, document_id)
        with request.app.state.runs.db() as store:
            return ChunkSettings(config, store).save(data.get('settings', {}), document_id, data.get('use_global', False))

    @app.post('/api/runs/{run_id}/rechunk-preview')
    async def rechunk_preview(request: Request, run_id: str):
        data = await request.json()
        _, preview = RechunkService(request.app.state.runs).prepare(run_id, data.get('document_id'), data.get('strategies'))
        return preview

    @app.post('/api/runs/{run_id}/rechunk', status_code=202)
    async def rechunk_start(request: Request, run_id: str):
        data = await request.json()
        if not data.get('document_id') and data.get('confirmed') is not True:
            raise ValueError('Confirm corpus recalculation; recognition will not run')
        new_id = RechunkService(request.app.state.runs).start(run_id, data.get('document_id'), data.get('strategies'))
        return {'run_id':new_id}

    @app.post('/api/runs/{run_id}/evaluate', status_code=202)
    def evaluate_active(request: Request, run_id: str):
        old = request.app.state.runs.get(run_id)
        if old['status'] not in TERMINAL:
            raise ValueError('Wait for processing to finish')
        cfg = Config(**old['config_json'])
        cfg.rechunk_source_run_id = run_id
        return {'run_id': request.app.state.runs.create(cfg, run_type='evaluate', upload_ids=old['upload_ids'])}

    @app.post("/api/runs/{run_id}/search")
    def search(request: Request, run_id: str, data: SearchRequest):
        return request.app.state.views.search(run_id, data.question, data.rag_scope, data.selected_document_id)

    @app.post("/ui/runs/{run_id}/search", response_class=HTMLResponse)
    def search_ui(request: Request, run_id: str, question: str = Form(...), rag_scope: RAGScope = Form(RAGScope.ALL_DOCUMENTS), selected_document_id: str = Form('')):
        return render(request, "search.html", run_id=run_id, results=request.app.state.views.search(run_id, question,rag_scope,selected_document_id or None))

    @app.get("/api/runs/{run_id}/evaluation")
    def evaluation(request: Request, run_id: str):
        return request.app.state.views.evaluation(run_id)

    @app.get('/api/rag/questions')
    def rag_questions(request: Request):
        return request.app.state.rag.questions.list()

    @app.post('/api/rag/questions',status_code=201)
    def rag_question_add(request: Request,data: ControlQuestion):
        return request.app.state.rag.questions.save(data.model_dump())

    @app.put('/api/rag/questions/{question_id}')
    def rag_question_edit(request: Request,question_id: str,data: ControlQuestion):
        return request.app.state.rag.questions.save(data.model_dump(),question_id)

    @app.delete('/api/rag/questions/{question_id}')
    def rag_question_delete(request: Request,question_id: str):
        request.app.state.rag.questions.delete(question_id)
        return {'status':'deleted'}

    @app.get('/ui/rag/questions',response_class=HTMLResponse)
    def rag_question_list(request: Request):
        return render(request,'rag_questions.html',questions=request.app.state.rag.questions.list())

    @app.post('/api/runs/{run_id}/rag/compare',status_code=202)
    def rag_compare(request: Request,run_id: str,data: RAGRequest):
        return {'comparison_run_id':request.app.state.rag.start(run_id,**data.model_dump())}

    @app.post('/api/runs/{run_id}/rag/batch',status_code=202)
    def rag_batch_start(request: Request,run_id: str,data: RAGRequest):
        return {'batch_id':request.app.state.rag.start_all(run_id,**data.model_dump(exclude={'question','question_id'}))}

    @app.post('/api/runs/{run_id}/rag/evaluate',status_code=202)
    def rag_modes_start(request: Request,run_id: str,data: RAGRequest):
        return {'batch_id':request.app.state.rag.start_all(run_id,compare_modes=True,**data.model_dump(exclude={'question','question_id'}))}

    @app.get('/ui/runs/{run_id}/rag/evaluation',response_class=HTMLResponse)
    def rag_modes_table(request: Request,run_id: str):
        service=request.app.state.rag
        with service.runs.db() as store:
            batches=[b for b in store.all('rag_batches') if b.get('day23_evaluation') and b['processing_run_id']==run_id]
        batch=batches[-1] if batches else None
        records=[service.get(k) for k in batch['comparison_ids']] if batch else []
        grouped={}
        for r in records:grouped.setdefault(r['question_text'],{})[r['rag_pipeline_mode']]=r
        from ..application.grounding import evaluate_grounding
        return render(request,'rag_evaluation.html',batch=batch,grouped=grouped,grounding=evaluate_grounding(records))

    @app.get('/api/rag/comparisons/{comparison_id}')
    def rag_result(request: Request,comparison_id: str):
        return request.app.state.rag.get(comparison_id)

    @app.get('/ui/rag/comparisons/{comparison_id}',response_class=HTMLResponse)
    def rag_result_ui(request: Request,comparison_id: str):
        return render(request,'rag_result.html',result=request.app.state.rag.get(comparison_id))

    @app.get('/api/rag/batches/{batch_id}')
    def rag_batch(request: Request,batch_id: str):
        return request.app.state.rag.get(batch_id,'rag_batches')

    @app.get('/ui/rag/batches/{batch_id}',response_class=HTMLResponse)
    def rag_batch_ui(request: Request,batch_id: str):
        batch=request.app.state.rag.get(batch_id,'rag_batches')
        return render(request,'rag_batch.html',batch=batch,results=[request.app.state.rag.get(k) for k in batch['comparison_ids']])

    @app.get('/ui/runs/{run_id}/rag/history',response_class=HTMLResponse)
    def rag_history(request: Request,run_id: str):
        request.app.state.runs.get(run_id)
        with request.app.state.runs.db() as store:
            history=[r for r in store.all('rag_comparison_runs') if r['processing_run_id']==run_id][-20:]
            batches=[b for b in store.all('rag_batches') if b.get('processing_run_id')==run_id][-10:]
        return render(request,'rag_history.html',history=list(reversed(history)),batches=list(reversed(batches)))

    @app.get("/api/runs/{run_id}/report")
    def report(request: Request, run_id: str, download: bool = False):
        path = request.app.state.views.report(run_id)
        return FileResponse(path, media_type="text/html", filename="day21.html" if download else None)

    @app.post("/api/runs/{run_id}/report")
    def report_generate(request: Request, run_id: str):
        request.app.state.views.report(run_id, regenerate=True)
        return {"url": f"/api/runs/{run_id}/report"}

    @app.get("/ui/runs/{run_id}/workspace", response_class=HTMLResponse)
    def workspace(request: Request, run_id: str, document_id: str | None = None):
        check_document(request, run_id, document_id)
        all_pages = request.app.state.views.pages(run_id) if document_id else []
        pages = [p for p in all_pages if p['document_id']==document_id] if document_id else []
        with request.app.state.runs.db() as store:
            settings = ChunkSettings(config, store).read(document_id)
            corpus_id = request.app.state.views.snapshot(run_id).get('corpus', {}).get('corpus_id')
            history = [r for r in store.all('index_runs') if r.get('corpus_id')==corpus_id and (not document_id or document_id in r.get('document_ids', []))][-30:]
        files = request.app.state.views.files(run_id)
        return render(request, "workspace.html", run_id=run_id, pages=pages, document_id=document_id,
                      active_indexes=request.app.state.views.snapshot(run_id).get('indexes',{}),
                      selected_file=next((f for f in files if f['document_id']==document_id), None), files=files, settings=settings, chunk_history=list(reversed(history)),
                      reliability=request.app.state.views.snapshot(run_id).get('recognition', {}).get('reliability', {}),
                      document_stats=request.app.state.views.snapshot(run_id).get('recognition', {}).get('document_stats', []),
                      evaluation=request.app.state.views.evaluation(run_id))

    return app
