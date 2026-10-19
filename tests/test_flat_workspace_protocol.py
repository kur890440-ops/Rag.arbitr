import json
import pytest
from pydantic import ValidationError
from test_document_workspace import ws,draft
from test_workspace_stabilization import Script
from rag_arbiter.application.workspace_protocol import FlatToolInput
from rag_arbiter.application.document_workspace import ToolInput

@pytest.mark.parametrize('value',[
 {}, {'operation':'unknown'}, {'operation':'search_documents'},
 {'operation':'search_documents','query':1}, {'operation':'search_documents','query':'x','file_id':'x'},
 {'operation':'read_document','document_id':'d'}, {'operation':'read_document','file_id':''},
 {'operation':'read_document','file_id':'f','document_id':'d','version_id':'v'},
 {'operation':'analyze_documents','query':'q','document_ids':[]},
 {'operation':'create_document','format':'exe'}, {'operation':'create_document','content':5},
 {'operation':'edit_document','file_id':'f','append':' '},
 {'operation':'compare_documents','file_id':'a'}, {'operation':'validate_document'},
 {'operation':'save_workspace_result','file_id':2}, {'operation':'prepare_publication','file_id':'a'},
 {'operation':'confirm_publication','proposal_id':'p'}, {'operation':'finish_task','done':True},
 {'tool':{'operation':'search_documents','query':'x'}},
 {'operation':'search_documents','arguments':{'query':'x'}},
])
def test_strict_operation_schema(value):
    with pytest.raises(ValidationError):FlatToolInput.model_validate_json(json.dumps(value))

@pytest.mark.parametrize('raw',['{','not JSON','{} {}','{"operation":"finish_task"} trailing'])
def test_no_json_guessing(raw):
    with pytest.raises(ValidationError):FlatToolInput.model_validate_json(raw)

@pytest.mark.parametrize('op',['create_document','edit_document'])
def test_exact_mutation_idempotency(ws,op):
    service,admin,_,_=ws;task,file=draft(ws)
    req=ToolInput(operation=op,content='Once') if op=='create_document' else ToolInput(operation=op,file_id=file['id'],append='Once')
    a=service.call(admin,task['id'],req);b=service.call(admin,task['id'],req)
    assert a.data['id']==b.data['id']
    assert len(service.get('workspace_tasks',task['id'],admin)['files'])==2

def test_mocked_full_sequence_and_automatic_finalize(ws,monkeypatch):
    service,admin,_,_=ws
    service.knowledge.grants=lambda:{'d':dict(title='Contract',recognition_version='v')}
    with service.runs.db() as db:db.put('normalized_documents','v',dict(document_id='d',content_hash='hash',blocks=[dict(text='Evidence')]))
    def analyze(user,task,request,*args):
        assert request.document_ids==['d'] and task['sources'][0]['version_id']=='v'
        task['analysis']=dict(text='Evidence',citations=[],status='PARTIAL',documents=['d'])
        service.put('workspace_tasks',task);return task['analysis']
    monkeypatch.setattr(service,'analyze',analyze)
    monkeypatch.setattr(service,'validate_content',lambda user,key,*args:service.validate(user,key))
    provider=Script([dict(operation='search_documents',query='Contract'),dict(operation='read_document',document_id='d',version_id='v'),dict(operation='analyze_documents',query='Compare',document_ids=['d']),dict(operation='create_document',format='docx')])
    result=service.execute_goal(admin,'Compare and create DOCX',lambda cfg:provider,service.runs.config.llm)
    assert result['error_reason'] is None and len(provider.calls)==4
    assert [c['input']['operation'] for c in result['tools']]==['search_documents','read_document','analyze_documents','create_document']
    assert result['files']==result['saved_files'] and result['status']=='PARTIAL'
    with service.runs.db() as db:assert db.all('workspace_validations')[0]['structural_status']=='PASS'
    assert service.file_bytes(admin,result['files'][0])[1].startswith(b'PK')

def test_finish_cannot_replace_required_edit(ws):
    service,admin,_,_=ws
    provider=Script([dict(operation='create_document',content='wrong artifact'),dict(operation='finish_task')])
    result=service.execute_goal(admin,'Edit previous document',lambda cfg:provider,service.runs.config.llm)
    assert result['status']=='PARTIAL' and result['error_reason']=='EDIT_WORKFLOW_INCOMPLETE'

@pytest.mark.parametrize('goal,reason',[
 ('Find contracts and create DOCX','SEARCH_WORKFLOW_INCOMPLETE'),
 ('Create PDF','REQUESTED_FORMAT_MISSING'),
 ('Compare contracts and create DOCX','ANALYSIS_WORKFLOW_INCOMPLETE'),
])
def test_artifact_alone_does_not_prove_completion(ws,goal,reason):
    service,admin,_,_=ws
    p=Script([dict(operation='create_document',content='A draft'),dict(operation='finish_task')])
    result=service.execute_goal(admin,goal,lambda cfg:p,service.runs.config.llm)
    assert result['status']=='PARTIAL' and result['error_reason']==reason
