"""Anonymous LAN-restricted consultation; administrative routes stay loopback-only."""
import asyncio
from ..application.public_knowledge import PublicKnowledge
from fastapi import Request,HTTPException
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool

class ConsultQuestion(BaseModel):
    question: str=Field(min_length=1,max_length=4000)
class PublicationDecision(BaseModel):
    public: bool

def register_consult(app,render):
    def service(request):
        if not request.app.state.runs.config.consult.enabled:raise HTTPException(404)
        return request.app.state.consult

    @app.get('/consult')
    def consult(request:Request):
        if not getattr(request.state,'user',None):
            return render(request,'consult_login.html',configured=bool(request.app.state.telegram.token))
        s=service(request);token,v=s.visitor(request.cookies.get('rag_consult'),create=True)
        response=render(request,'consult.html',messages=s.history(v),max_input=s.cfg.max_input_chars,timeout_ms=int((max(s.cfg.timeout,s.cfg.exhaustive_timeout)+5)*1000))

        return response

    @app.post('/consult/messages')
    async def message(request:Request):
        s=service(request)
        try:
            data=await request.json() if 'application/json' in request.headers.get('content-type','') else dict(await request.form())
            body=ConsultQuestion.model_validate(data)
        except Exception:raise HTTPException(400,'Введите вопрос допустимой длины.') from None
        work=asyncio.create_task(run_in_threadpool(s.answer,request.cookies.get('rag_consult'),request.client.host if request.client else '',body.question))
        try:result,metrics=await asyncio.wait_for(asyncio.shield(work),timeout=max(s.cfg.timeout,s.cfg.exhaustive_timeout))
        except asyncio.TimeoutError:
            work.add_done_callback(lambda done:done.exception() if not done.cancelled() else None)
            raise HTTPException(504,'Время ожидания ответа истекло. Попробуйте позже.') from None
        return render(request,'consult_message.html',message={'role':'ASSISTANT',**result})

    @app.get('/consult/diagnostic')
    def consult_diagnostic(request:Request):
        from fastapi.responses import PlainTextResponse
        from ..application.chat_diagnostic import redact
        import json
        s=service(request);_,v=s.visitor(request.cookies.get('rag_consult'))
        s.history(v)
        return PlainTextResponse('# Consultant diagnostic\n\n```json\n'+json.dumps(redact(v.diagnostics),ensure_ascii=False,indent=2)+'\n```\n',
            headers={'Content-Disposition':'attachment; filename="consult-diagnostic.md"','Cache-Control':'no-store'})

    @app.get('/consult/sources/{citation_id}')
    def source(request:Request,citation_id:str):
        s=service(request);_,v=s.visitor(request.cookies.get('rag_consult'))
        s.history(v);entry=v.citations.get(citation_id)
        if not entry or not s.knowledge.still_allowed({entry['grant']['document_id']:entry['grant']}):raise HTTPException(404)
        return render(request,'consult_source.html',citation=entry['citation'])

    @app.get('/publications')
    def publications(request:Request):
        return render(request,'publications.html',documents=PublicKnowledge(service(request).runs).candidates())

    @app.patch('/api/publications/{key}')
    def publish(request:Request,key:str,body:PublicationDecision):
        PublicKnowledge(service(request).runs).publish(key,body.public)
        return {'public':body.public}


def create_public_app():
    """Compatibility factory for a consultant-only, LAN-restricted listener."""
    from ..config import Config
    from .app import create_app
    config=Config.load().model_copy(deep=True)
    config.consult.public_only=True
    config.web_host="127.0.0.1"
    config.network_sources.enabled=False
    return create_app(config)
