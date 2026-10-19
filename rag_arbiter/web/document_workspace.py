"""Workspace results are rendered inside the existing consultant conversation."""
import json
from fastapi import Request,HTTPException
from fastapi.responses import Response,JSONResponse
from ..application.document_workspace import ToolInput
from ..application.archive_publisher import ArchivePublisher

MIME={'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','pdf':'application/pdf','txt':'text/plain; charset=utf-8','md':'text/markdown; charset=utf-8'}

def register_workspace(app,render):
    def card(request,task_id):
        ws=request.app.state.workspace;user=request.state.user;task=ws.get('workspace_tasks',task_id,user)
        files=[ws.file_bytes(user,key)[0] for key in task['files']]
        with ws.runs.db() as db:
            proposals=[p for p in db.all('workspace_proposals') if p['task_id']==task_id and p['owner_id']==user['id']]
            publications=[p for p in db.all('workspace_publications') if p['task_id']==task_id and p['owner_id']==user['id']]
            diffs=[d for d in db.all('workspace_diffs') if d['owner_id']==user['id'] and (d.get('new_id') in task['files'] or d['id'] in [p['diff_revision'] for p in proposals])]
            sources=[s for s in db.all('network_sources') if s.get('enabled')]
            validations=[v for v in db.all('workspace_validations') if v['owner_id']==user['id']]
        for file in files:file['validation']=next((v for v in reversed(validations) if v['file_id']==file['id']),None)
        publications=[ArchivePublisher(ws).status(user,p['id']) for p in publications]
        return render(request,'document_workspace_card.html',task=task,files=files,proposals=proposals,publications=publications,diffs=diffs,sources=sources,is_admin=user['role']=='ADMIN')

    @app.get('/chat/workspace/tasks')
    def tasks(request:Request,session_id:str|None=None):
        ws=request.app.state.workspace;user=ws.actor(request.state.user)
        with ws.runs.db() as db:rows=[t for t in db.all('workspace_tasks') if t['owner_id']==user['id'] and (session_id is None or t.get('session_id')==session_id)]
        rows=sorted(rows,key=lambda t:t['created_at'])[-3:]
        return Response(''.join(card(request,t['id']).body.decode() for t in rows),media_type='text/html')

    @app.get('/chat/workspace/tasks/{key}')
    def task(request:Request,key:str):return card(request,key)

    @app.post('/chat/workspace/tasks/{key}/cancel')
    def cancel(request:Request,key:str):
        request.app.state.workspace.cancel(request.state.user,key);return card(request,key)

    @app.get('/chat/workspace/files/{key}')
    def download(request:Request,key:str):
        row,data=request.app.state.workspace.file_bytes(request.state.user,key)
        return Response(data,media_type=MIME[row['format']],headers={'Content-Disposition':f'attachment; filename="result-{key}.{row["format"]}"','Cache-Control':'no-store'})

    @app.get('/chat/workspace/diffs/{key}')
    def diff(request:Request,key:str):
        ws=request.app.state.workspace;row=ws.get('workspace_diffs',key,request.state.user)
        for name in ('old_id','new_id'):
            if row.get(name):ws.file_bytes(request.state.user,row[name])
        if not row.get('new_id'):
            proposal=ws.get('workspace_proposals',key,request.state.user);ws.file_bytes(request.state.user,proposal['file_id'])
        return JSONResponse(row,headers={'Content-Disposition':'attachment; filename="diff.json"'})

    @app.post('/chat/workspace/files/{key}/prepare')
    async def prepare(request:Request,key:str):
        data=dict(await request.form());ws=request.app.state.workspace
        proposal=ArchivePublisher(ws).prepare(request.state.user,ToolInput(operation='prepare_publication',file_id=key,source_id=data.get('source_id',''),target_path=data.get('target_path',''),publication_operation=data.get('operation','CREATE_NEW')))
        return card(request,proposal['task_id'])

    @app.post('/chat/workspace/proposals/{key}/confirm')
    async def confirm(request:Request,key:str):
        data=dict(await request.form());ws=request.app.state.workspace
        result=ArchivePublisher(ws).confirm(request.state.user,key,data.get('idempotency_key',''),data.get('accept_limitations')=='yes')
        return card(request,result['task_id'])
