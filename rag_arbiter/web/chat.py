"""Chat routes share the existing application, middleware and templates."""
from fastapi import Request, Query
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, PlainTextResponse
from ..application.chat_diagnostic import ChatDiagnosticService


class CreateChat(BaseModel):
    processing_run_id: str | None = None


class SelectSource(BaseModel):
    selected_document_ids: list[str] = Field(default_factory=list)
    scope: str
    source_id: str | None = None
    expected_version: int = Field(ge=0)


class SendChat(BaseModel):
    expected_scope_version: int | None = Field(default=None,ge=0)
    content: str = Field(min_length=1,max_length=1500)
    expected_version: int = Field(ge=0)


class EditMemory(BaseModel):
    expected_version: int = Field(ge=0)
    field: str
    key: str
    value: str | None = None


class DiscoveryDecision(BaseModel):
    action: str
    document_ids: list[str] = Field(default_factory=list, max_length=10000)
    event_ids: list[int] = Field(min_length=1, max_length=10000)
    expected_version: int = Field(ge=0)


def register_chat(app,render):
    @app.get('/api/chat/knowledge-updates')
    def knowledge_updates(request: Request, after: int = Query(ge=0), session_id: str | None = None):
        from ..network.notifications import updates
        from ..application.contextual_discovery import ContextualDiscovery
        service = ContextualDiscovery(request.app.state.chat)
        session = service.session(session_id) if session_id else None
        result = updates(request.app.state.runs, after, session)
        result['notice'] = service.snapshot(session_id) if session_id else None
        if result['notice']:
            service.presented(result['notice'])
        result['session'] = session
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @app.post('/api/chat/sessions/{sid}/discovery-decision')
    def discovery_decision(request: Request, sid: str, body: DiscoveryDecision):
        from ..application.contextual_discovery import ContextualDiscovery
        return ContextualDiscovery(request.app.state.chat).decide(sid, body.action, body.event_ids, body.expected_version, body.document_ids)


    @app.get('/api/chat/sessions/{sid}/diagnostic', response_class=PlainTextResponse)
    def diagnostic(request:Request,sid:str,turn_id:str|None=None,download:bool=False):
        report=ChatDiagnosticService(request.app.state.runs).build(sid,turn_id)
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        if download:headers['Content-Disposition']='attachment; filename="chat-diagnostic.md"'
        return PlainTextResponse(report,headers=headers)

    @app.get('/chat')
    def chat_page(request:Request,session_id:str|None=None):
        from ..network.notifications import cursor
        service=request.app.state.chat
        data=service.load(session_id,message_limit=40) if session_id else None
        return render(request,'chat.html',data=data,sessions=service.recent(),runs=request.app.state.runs.list(),sources=request.app.state.network.page('network_sources',limit=10000),notification_cursor=cursor(request.app.state.runs))

    @app.get('/ui/chat/{sid}')
    def chat_fragment(request:Request,sid:str,before:int|None=Query(None,ge=1),after:int|None=Query(None,ge=0)):
        data=request.app.state.chat.load(sid,message_limit=40,before=before,after=after)
        return render(request,'chat_thread.html',data=data)

    @app.patch('/api/chat/sessions/{sid}/source')
    def select_source(request:Request,sid:str,body:SelectSource):
        return request.app.state.chat.select_source(sid,body.scope,body.source_id,body.expected_version,body.selected_document_ids).model_dump()

    @app.get('/ui/chat/{sid}/discovery')
    def discovery(request:Request,sid:str,offset:int=Query(0,ge=0),q:str=Query('',max_length=200),page_size:int=Query(10)):
        if page_size not in (10,25,50,100):raise ValueError('Invalid page size')
        session=request.app.state.chat.load(sid)['session']
        svc=request.app.state.network
        from ..application.archive_status import ArchiveStatusProvider
        summary=ArchiveStatusProvider(request.app.state.runs).snapshot(session)
        query=q.strip().casefold()
        matching=sorted((f for f in summary['files'] if query in f['filename'].casefold()),key=lambda f:(f['filename'].casefold(),f['id']))
        total=len(matching);pages=max(1,(total+page_size-1)//page_size)
        offset=min(offset//page_size,pages-1)*page_size
        files=matching[offset:offset+page_size]
        pager=dict(total=total,all_total=len(summary['files']),offset=offset,size=page_size,page=offset//page_size+1,pages=pages,
            start=offset+1 if total else 0,end=min(offset+page_size,total))
        if session['source_scope']=='NETWORK' and session['source_id']:
            info=svc.discovery(session['source_id']);info['files']=files
            return render(request,'chat_discovery.html',info=info,session=session,pager=pager)
        source_info=[svc.discovery(s['id']) for s in summary['sources']]
        return render(request,'archive_discovery.html',summary=summary,source_info=source_info,files=files,pager=pager)

    @app.get('/api/chat/sessions')
    def list_sessions(request:Request):return request.app.state.chat.recent()

    @app.post('/api/chat/sessions',status_code=201)
    def create_session(request:Request,body:CreateChat):
        return request.app.state.chat.create(body.processing_run_id).model_dump()

    @app.get('/api/chat/sessions/{sid}')
    def get_session(request:Request,sid:str):return request.app.state.chat.load(sid)

    @app.post('/api/chat/sessions/{sid}/messages',status_code=202)
    def send_message(request:Request,sid:str,body:SendChat):
        return request.app.state.chat.start(sid,body.content,body.expected_version,body.expected_scope_version).model_dump()

    @app.patch('/api/chat/sessions/{sid}/memory')
    def edit_memory(request:Request,sid:str,body:EditMemory):
        return request.app.state.chat.edit(sid,body.expected_version,body.field,body.key,body.value).model_dump()
