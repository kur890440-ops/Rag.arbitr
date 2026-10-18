"""Authentication endpoints and ADMIN-only user management."""
from fastapi import Request,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Literal
from ..application.auth import COOKIE,NONCE_COOKIE,CODE_TTL

class UserAction(BaseModel):
    action: Literal['approve','block','unblock','role','revoke']
    value: Literal['ADMIN','CLIENT'] | None = None


def register_auth(app,render):
    @app.post('/consult/auth/code')
    def code(request:Request):
        if request.url.scheme!='https':raise HTTPException(426,'Для входа требуется HTTPS.')
        auth=request.app.state.auth
        nonce,code=auth.create_code(request.client.host,request.cookies.get(NONCE_COOKIE))
        response=JSONResponse(dict(code=code,expires_in=CODE_TTL))
        response.set_cookie(NONCE_COOKIE,nonce,max_age=CODE_TTL,httponly=True,secure=True,samesite='strict',path='/consult/auth')
        return response

    @app.post('/consult/auth/poll')
    def poll(request:Request):
        if request.url.scheme!='https':raise HTTPException(426,'Для входа требуется HTTPS.')
        auth=request.app.state.auth;token=auth.poll(request.cookies.get(NONCE_COOKIE),request.client.host)
        response=JSONResponse(dict(authenticated=bool(token)))
        if token:
            response.set_cookie(COOKIE,token,max_age=auth.cfg.session_ttl,httponly=True,secure=True,samesite='strict',path='/')
            response.delete_cookie(NONCE_COOKIE,path='/consult/auth',secure=True,httponly=True,samesite='strict')
            # Remove the old anonymous cookie, never adopt its history.
            response.delete_cookie(COOKIE,path='/consult',secure=True,httponly=True,samesite='strict')
        return response

    @app.get('/consult/auth/session')
    def session(request:Request):
        request.app.state.auth.rate('session-poll:'+request.client.host,60)
        user=request.app.state.auth.authenticate(request.cookies.get(COOKIE))
        return dict(authenticated=True,role=user['role'])

    @app.post('/consult/auth/logout')
    def logout(request:Request):
        request.app.state.auth.logout(request.cookies.get(COOKIE))
        response=JSONResponse(dict(ok=True));response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='strict')
        return response

    @app.get('/users')
    def users(request:Request,status:str='PENDING'):
        if status not in ('PENDING','ACTIVE','BLOCKED'):raise HTTPException(400)
        auth=request.app.state.auth
        return render(request,'users.html',users=auth.list_users(request.state.user,status),status=status,bot_status=request.app.state.telegram.status)

    @app.get('/api/users')
    def user_list(request:Request,status:str|None=None):
        if status not in (None,'PENDING','ACTIVE','BLOCKED'):raise HTTPException(400)
        return request.app.state.auth.list_users(request.state.user,status)

    @app.post('/api/users/{user_id}')
    def change_user(request:Request,user_id:str,body:UserAction):
        return request.app.state.auth.change(request.state.user,user_id,body.action,body.value)
