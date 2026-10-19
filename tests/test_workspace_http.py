import json
from test_rag import ragweb
from test_consult_auth_integration import login
from rag_arbiter.application.document_workspace import ToolInput
from rag_arbiter.llm import LLMResult

def test_workspace_chat_http_and_client_idor_csrf(ragweb):
    c,rid,llm,_=ragweb;ws=c.app.state.workspace;auth=c.app.state.auth;admin=auth.user(101)
    client=auth.register(99903);auth.change(admin,client['id'],'approve')
    task=ws.task(admin,'Synthetic draft');file=ws.call(admin,task['id'],ToolInput(operation='create_document',content='Synthetic evidence')).data
    assert c.get('/chat/workspace/files/'+file['id']).status_code==200
    assert c.get('/chat/workspace/tasks').status_code==200
    assert c.post('/chat/workspace/tasks/'+task['id']+'/cancel',headers={'X-RAG-Request':''}).status_code==403
    assert c.post('/chat/workspace/tasks/'+task['id']+'/cancel',headers={'Origin':'https://evil.invalid'}).status_code==403
    login(c,auth,99903)
    assert c.get('/chat/workspace/files/'+file['id']).status_code==403
    assert c.get('/chat/workspace/tasks/'+task['id']).status_code==403
    own=ws.task(client,'own');ownfile=ws.call(client,own['id'],ToolInput(operation='create_document',content='Own')).data
    assert c.post('/chat/workspace/files/'+ownfile['id']+'/prepare',data={'source_id':'x','target_path':'x.docx'}).status_code==403

def test_autonomous_file_tools_use_existing_provider_and_chat(ragweb):
    c,rid,llm,_=ragweb;steps=[]
    def generate(req):
        if req.question.startswith('DAY33_PUBLIC_ROUTER'):
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='WORKSPACE',question='Create document',topic='draft')))
        assert req.question.startswith('DAY34_FILE_TOOLS')
        steps.append(req)
        assert len(steps)==1, 'Backend must finalize without another planner call'
        decision=dict(operation='create_document',content='Synthetic 456')
        return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(decision))
    llm.generate=generate
    sid=c.post('/api/chat/sessions',json={'processing_run_id':rid}).json()['session_id']
    response=c.post('/api/chat/sessions/'+sid+'/messages',json={'content':'Подготовь документ DOCX','expected_version':0})
    assert response.status_code==202,response.text
    c.app.state.chat.futures[response.json()['turn_id']].result(timeout=30)
    page=c.get('/chat/workspace/tasks');assert 'Скачать' in page.text
    with c.app.state.runs.db() as db:tasks=db.all('workspace_tasks')
    assert len(tasks)==1 and len(tasks[0]['tools'])==1 and len(tasks[0]['files'])==1

def test_workspace_analysis_reuses_exhaustive_full_pinned_scope(ragweb):
    c,_,_,_=ragweb;ws=c.app.state.workspace;admin=c.app.state.auth.user(101)
    task=ws.task(admin,'Compare reporting');grants=ws.knowledge.grants();assert len(grants)==2
    for did,g in grants.items():ws.call(admin,task['id'],ToolInput(operation='read_document',document_id=did,version_id=g['recognition_version']))
    class NoEvidence:
        def generate(self,req):return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(relevant=False,findings=[],limitations=['No relevant evidence'])))
    result=ws.call(admin,task['id'],ToolInput(operation='analyze_documents',query='Compare reporting',document_ids=list(grants)),lambda cfg:NoEvidence(),ws.runs.config.llm)
    assert result.status=='PARTIAL' and set(result.data['documents'])==set(grants)
    assert result.data['metrics']['documents_total']==2


def test_consult_no_file_routes(ragweb):
    c,rid,llm,_=ragweb
    assert c.get('/consult/workspace/tasks').status_code==404
    assert 'workspace-events' not in c.get('/consult').text
    assert '/static/chat-workspace.js' in c.get('/chat').text


def test_content_validation_uses_existing_grounding_and_semantic_review(ragweb):
    c,_,_,_=ragweb;ws=c.app.state.workspace;admin=c.app.state.auth.user(101)
    grants=ws.knowledge.grants();did,g=next(iter(grants.items()))
    task=ws.task(admin,'Verify obligation');doc=ws.call(admin,task['id'],ToolInput(operation='read_document',document_id=did,version_id=g['recognition_version'])).data
    text=doc['blocks'][0]['text'];file=ws.call(admin,task['id'],ToolInput(operation='create_document',content=text)).data
    calls=[]
    class Provider:
        def generate(self,req):
            calls.append(req.question)
            if req.question.startswith('DAY34_ALL_DRAFT_CLAIMS'):
                payload=json.loads(req.question[req.question.index('{'):])
                body=dict(blocks=[dict(block_id=b['id'],kind='FACTUAL' if b['content']==text else 'EDITORIAL',reason='Fixture block',subject='fixture subject',predicate='fixture predicate',object='fixture object') for b in payload['fragments']])
            else:
                assert req.question.startswith('DAY33_EVIDENCE_CLAIMS')
                body=dict(decisions=[dict(id='I1',status='SUPPORTED',evidence=['E1_1'],reason='Fixture canonical evidence')])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(body))
    validation=ws.validate_content(admin,file['id'],lambda cfg:Provider(),ws.runs.config.llm)
    assert validation.get('failure') is None,validation
    assert validation['claims'] and len(calls)==2
    assert validation['coverage']=='FULL' and validation['blocks_reviewed']==validation['blocks_total']
    assert validation['semantic_status']=='VERIFIED'
    assert validation['claims'][0]['evidence_blocks']
