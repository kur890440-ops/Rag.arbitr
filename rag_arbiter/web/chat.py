"""Chat routes share the existing application, middleware and templates."""
from fastapi import Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, PlainTextResponse
from ..application.chat_diagnostic import ChatDiagnosticService


class CreateChat(BaseModel):
    processing_run_id: str


class SendChat(BaseModel):
    content: str = Field(min_length=1,max_length=1500)
    expected_version: int = Field(ge=0)


class EditMemory(BaseModel):
    expected_version: int = Field(ge=0)
    field: str
    key: str
    value: str | None = None


def register_chat(app,render):
    @app.get('/api/chat/sessions/{sid}/diagnostic', response_class=PlainTextResponse)
    def diagnostic(request:Request,sid:str,turn_id:str|None=None,download:bool=False):
        report=ChatDiagnosticService(request.app.state.runs).build(sid,turn_id)
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        if download:headers['Content-Disposition']='attachment; filename="chat-diagnostic.md"'
        return PlainTextResponse(report,headers=headers)

    @app.get('/chat')
    def chat_page(request:Request,session_id:str|None=None):
        service=request.app.state.chat
        data=service.load(session_id) if session_id else None
        return render(request,'chat.html',data=data,sessions=service.recent(),runs=request.app.state.runs.list())

    @app.get('/ui/chat/{sid}')
    def chat_fragment(request:Request,sid:str):
        data=request.app.state.chat.load(sid)
        return render(request,'chat_thread.html',data=data)

    @app.get('/api/chat/sessions')
    def list_sessions(request:Request):return request.app.state.chat.recent()

    @app.post('/api/chat/sessions',status_code=201)
    def create_session(request:Request,body:CreateChat):
        return request.app.state.chat.create(body.processing_run_id).model_dump()

    @app.get('/api/chat/sessions/{sid}')
    def get_session(request:Request,sid:str):return request.app.state.chat.load(sid)

    @app.post('/api/chat/sessions/{sid}/messages',status_code=202)
    def send_message(request:Request,sid:str,body:SendChat):
        return request.app.state.chat.start(sid,body.content,body.expected_version).model_dump()

    @app.patch('/api/chat/sessions/{sid}/memory')
    def edit_memory(request:Request,sid:str,body:EditMemory):
        return request.app.state.chat.edit(sid,body.expected_version,body.field,body.key,body.value).model_dump()
