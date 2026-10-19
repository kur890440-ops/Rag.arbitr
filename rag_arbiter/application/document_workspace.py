"""User-isolated draft files in the canonical SQLite, no archive writes here."""
import hashlib,json,time,uuid,copy,os,re
import psutil,portalocker
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from fastapi import HTTPException
from . import workspace_formats as formats
from .workspace_protocol import FlatToolInput, PLANNER_PROMPT
from .document_diff import structured_diff
from ..network.connector import no_links

TOOLS=('search_documents','read_document','analyze_documents','create_document','edit_document','compare_documents','validate_document','save_workspace_result','prepare_publication','confirm_publication')

def sha(data):return hashlib.sha256(data).hexdigest()
def uid():return uuid.uuid4().hex

def explicit_file_goal(question):
    """A direct user file command takes precedence over analytical subquestions.

    Only the current user message is inspected, never source text or model output.
    This routes to the existing bounded provider/tools; it grants no publication.
    """
    import re
    command=re.search(r'\b(?:создай(?:те)?|подготовь(?:те)?|сохрани(?:те)?|отредактируй(?:те)?|добавь(?:те)?|сделай(?:те)?)\b',question,re.I)
    artifact=re.search(r'\b(?:docx|word|pdf|txt|markdown|файл\w*|документ\w*|заключени\w*|редакци\w*)\b',question,re.I)
    return bool(command and artifact)

class SourceReference(BaseModel):
    document_id:str
    version_id:str
    content_hash:str

class ToolInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation:Literal['search_documents','read_document','analyze_documents','create_document','edit_document','compare_documents','validate_document','save_workspace_result','prepare_publication','confirm_publication']
    query:str=Field('',max_length=4000)
    document_id:str=''
    version_id:str=''
    document_ids:list[str]=Field(default_factory=list,max_length=1000)
    file_id:str=''
    other_file_id:str=''
    title:str=Field('Результат',max_length=150)
    format:Literal['docx','pdf','txt','md']='docx'
    content:str=Field('',max_length=100000)
    replacements:dict[str,str]=Field(default_factory=dict,max_length=100)
    append:str=Field('',max_length=50000)
    source_id:str=''
    target_path:str=Field('',max_length=2000)
    publication_operation:Literal['CREATE_NEW','REPLACE_EXISTING']='CREATE_NEW'
    proposal_id:str=''
    idempotency_key:str=Field('',max_length=100)
    accept_limitations:bool=False

    @model_validator(mode='after')
    def required_arguments(self):
        if self.operation=='read_document' and not (self.file_id or (self.document_id and self.version_id)):raise ValueError('READ_ID_AND_VERSION_REQUIRED')
        if self.operation in ('edit_document','validate_document','save_workspace_result','compare_documents','prepare_publication') and not self.file_id:raise ValueError('FILE_ID_REQUIRED')
        if self.operation=='edit_document' and not (self.append or self.replacements):raise ValueError('EDIT_REQUIRED')
        if self.operation=='compare_documents' and not self.other_file_id:raise ValueError('OTHER_FILE_REQUIRED')
        if self.operation=='prepare_publication' and not (self.source_id and self.target_path):raise ValueError('PUBLICATION_TARGET_REQUIRED')
        return self

class ToolOutput(BaseModel):
    operation:str
    status:Literal['SUCCESS','PARTIAL','FAILED','CANCELLED']
    data:dict=Field(default_factory=dict)
    limitations:list[str]=Field(default_factory=list)

class WorkspaceService:
    def __init__(self,runs,auth,knowledge,rag=None,sync=None):
        self.runs,self.auth,self.knowledge,self.rag,self.sync=runs,auth,knowledge,rag,sync
        self.cfg=runs.config.workspace
        self.root=(runs.config.web_data_path/'document-workspaces').absolute()
        self.root.mkdir(parents=True,exist_ok=True)
        # Checkout ancestors may be virtualized. Private subtree itself must not be linked.
        if self.root.is_symlink() or getattr(self.root.stat(),'st_file_attributes',0)&0x400:raise ValueError('WORKSPACE_LINK')
        with self.runs.db() as db:
            for task in db.all('workspace_tasks'):
                if task['status'] not in ('PLANNED','RUNNING') or not task.get('owner_pid'):continue
                try:alive=psutil.Process(task['owner_pid']).create_time()==task['owner_started']
                except psutil.Error:alive=False
                if not alive:
                    task.update(status='PARTIAL',error_reason='PROCESS_INTERRUPTED',current_operation=None)
                    db.put('workspace_tasks',task['id'],task)

    def actor(self,user):
        with self.runs.db() as db:
            row=db.db.execute("SELECT * FROM auth_users WHERE id=? AND status='ACTIVE'",(user['id'],)).fetchone()
            columns=[c[1] for c in db.db.execute('PRAGMA table_info(auth_users)')]
        if not row:raise HTTPException(403,'Доступ отозван')
        return dict(zip(columns,row))

    def get(self,table,key,user):
        user=self.actor(user)
        with self.runs.db() as db:row=db.get(table,key)
        if not row or row['owner_id']!=user['id']:raise HTTPException(404,'Результат не найден')
        return row

    def put(self,table,row):
        with self.runs.db() as db:db.put(table,row['id'],row)
        return row

    def task(self,user,goal):
        user=self.actor(user)
        return self.put('workspace_tasks',dict(id=uid(),owner_id=user['id'],goal=goal,status='PLANNED',owner_pid=os.getpid(),owner_started=psutil.Process().create_time(),created_at=time.time(),tools=[],sources=[],files=[],limitations=[],cancelled=False))

    def cancel(self,user,key):
        with self.runs.mutation_lock:
            row=self.get('workspace_tasks',key,user);row['cancelled']=True
            return self.put('workspace_tasks',row)

    def read(self,user,document_id,version_id):
        self.actor(user);grant=self.knowledge.grants().get(document_id)
        if not grant or grant['recognition_version']!=version_id:raise HTTPException(403,'SOURCE_VERSION_NOT_ALLOWED')
        with self.runs.db() as db:doc=db.get('normalized_documents',version_id)
        if not doc or doc.get('document_id')!=document_id:raise ValueError('CANONICAL_DOCUMENT_MISSING')
        return dict(source=SourceReference(document_id=document_id,version_id=version_id,content_hash=doc['content_hash']).model_dump(),title=grant['title'],blocks=doc['blocks'])

    def file_bytes(self,user,key):
        row=self.get('workspace_files',key,user)
        for source in row['sources']:self.read(user,source['document_id'],source['version_id'])
        # Paths are generated from IDs, never trusted database/UI filenames.
        folder=self.root/row['owner_id'];path=folder/(row['id']+'.'+row['format'])
        for part in (folder,path):
            if part.is_symlink() or getattr(part.stat(),'st_file_attributes',0)&0x400:raise ValueError('WORKSPACE_LINK')
        data=path.read_bytes()
        if sha(data)!=row['sha256']:raise ValueError('DRAFT_CHANGED')
        return row,data

    def save(self,user,task,fmt,title,data,previous=None):
        if len(data)>self.cfg.max_file_bytes:raise ValueError('FILE_TOO_LARGE')
        parsed=formats.blocks(data,fmt)
        if not any(b['text'].strip() for b in parsed['blocks']):raise ValueError('EMPTY_DOCUMENT')
        row=dict(id=uid(),owner_id=user['id'],task_id=task['id'],format=fmt,title=title,sha256=sha(data),sources=task['sources'],created_at=time.time(),previous_id=previous,status='UNVERIFIED',limitations=list(dict.fromkeys(task['limitations']+['Содержательные выводы требуют проверки; Day33 не прошёл полную приёмку.'])))
        folder=self.root/user['id'];folder.mkdir(exist_ok=True)
        if folder.is_symlink() or getattr(folder.stat(),'st_file_attributes',0)&0x400:raise ValueError('WORKSPACE_LINK')
        with (folder/(row['id']+'.'+fmt)).open('xb') as stream:stream.write(data);stream.flush()
        self.put('workspace_files',row)
        self.put('workspace_revisions',{**row,'file_id':row['id']})
        task['files'].append(row['id']);self.put('workspace_tasks',task)
        if previous:
            old,olddata=self.file_bytes(user,previous)
            changes=structured_diff(previous,old['sha256'],row['sha256'],formats.blocks(olddata,old['format']),parsed)
            self.put('workspace_diffs',dict(id=row['id'],owner_id=user['id'],old_id=previous,new_id=row['id'],old_hash=old['sha256'],new_hash=row['sha256'],changes=changes))
        return row

    def validate(self,user,key):
        row,data=self.file_bytes(user,key);parsed=formats.blocks(data,row['format'])
        result=dict(id=uid(),owner_id=user['id'],file_id=key,sha256=row['sha256'],structural_status='PASS',semantic_status=row['status'],block_count=len(parsed['blocks']),limitations=row['limitations'],created_at=time.time())
        with self.runs.db() as db:
            prior=[v for v in db.all('workspace_validations') if v['file_id']==key and v['sha256']==row['sha256'] and 'claims' in v]
        if prior:
            latest=max(prior,key=lambda v:v['created_at'])
            for field in ('claims','semantic_status','coverage','failure','limitations','block_reviews','blocks_total','blocks_reviewed','failure_category','policy_hash'):
                if field in latest:result[field]=latest[field]
        self.put('workspace_validations',result)
        return result

    def validate_content(self,user,key,factory,config):
        from .workspace_validation import validate_content
        return validate_content(self,user,key,factory,config)

    def analyze(self,user,task,request,factory,config):
        from .exhaustive import ExhaustiveNoRAG
        from .contract_extraction import render_exhaustive_answer
        from .views import ResultsService
        from .retrieval_policy import RetrievalPolicyBuilder
        from .chat_models import TaskState
        selected=request.document_ids or [r['document_id'] for r in task['sources']]
        if not selected:raise ValueError('NO_PINNED_DOCUMENTS')
        pinned={r['document_id']:r for r in task['sources']}
        discovered={d['document_id']:d for call in task['tools'] if call['input']['operation']=='search_documents' for d in call['output']['data'].get('documents',[])}
        missing=[d for d in dict.fromkeys(selected) if d not in pinned]
        if any(d not in discovered for d in missing):raise ValueError('DOCUMENT_NOT_READ')
        if len(task['tools'])+len(missing)+1>self.cfg.max_tool_calls:raise ValueError('TOOL_CALL_LIMIT')
        for did in missing:
            found=discovered[did]
            data=self.read(user,did,found['version_id'])
            task['sources'].append(data['source']);pinned[did]=data['source']
            request_read=ToolInput(operation='read_document',document_id=did,version_id=found['version_id'])
            task['tools'].append(dict(input=request_read.model_dump(),output=ToolOutput(operation='read_document',status='SUCCESS',data=data).model_dump(),at=time.time(),initiator='BACKEND_REQUIRED_READ'))
            self.put('workspace_tasks',task)

        for did in selected:self.read(user,did,pinned[did]['version_id'])
        snapshot,grants=self.knowledge.snapshot();grants={d:g for d,g in grants.items() if d in selected}
        if set(grants)!=set(selected):raise ValueError('SOURCE_SCOPE_CHANGED')
        snapshot=ResultsService(self.runs).restrict_chat_snapshot(snapshot,grants)
        policy=RetrievalPolicyBuilder().build(TaskState(session_id=task['id']),'ALL_DOCUMENTS',snapshot['corpus']['corpus_id'],{d:{} for d in grants},source_scope='ALL_DOCUMENTS',source_document_ids=list(grants))
        rag=copy.copy(self.rag);rag.llm_factory=factory
        record=rag.prepare(next(iter(snapshot['document_runs'].values())),question=request.query or task['goal'],corpus_snapshot=snapshot,point_only=True,generation_mode='minimax')
        record.update(public_consult=True,retrieval_policy_json=policy.model_dump(mode='json'),status='RUNNING',full_document_context={},comparison_requested=True)
        self.rag.put(record)
        ExhaustiveNoRAG(self.runs,factory).run(record,config,self.rag.put)
        party_text='';party_citations=[];party_claims=[]
        if re.search(r'догов|contract',task['goal'],re.I):
            from .contract_extraction import render_contracts,party_confirmed
            party_record=rag.prepare(next(iter(snapshot['document_runs'].values())),question='Установи стороны и роли каждого выбранного договора по тексту, не по имени файла.',corpus_snapshot=snapshot,point_only=True,generation_mode='minimax')
            party_record.update(public_consult=True,retrieval_policy_json=policy.model_dump(mode='json'),status='RUNNING',full_document_context={},contract_extraction=True)
            self.rag.put(party_record)
            party_config=config.model_copy(deep=True);party_config.exhaustive_max_units_per_batch=0
            ExhaustiveNoRAG(self.runs,factory).run(party_record,party_config,self.rag.put)
            party_metrics=party_record.get('exhaustive',{})
            parties=party_metrics.get('contracts',[])
            party_text,party_citations,party_claims=render_contracts(parties,party_metrics)
            record['verified_contract_anchors']=[dict(document_id=p['document_id'],document_version_id=p['document_version_id'],party=p['subagent_name']) for p in parties if party_confirmed(p)]
            record['party_analysis_run_id']=party_record['comparison_run_id']
            self.rag.put(party_record)
        text,citations=render_exhaustive_answer(record,factory,config)
        if party_text:
            mapping={c['source_id']:'S'+str(len(citations)+i+1) for i,c in enumerate(party_citations)}
            party_text=re.sub(r'\[(S\d+)\]',lambda m:'['+mapping.get(m[1],m[1])+']',party_text)
            for c in party_citations:c['source_id']=mapping[c['source_id']]
            citations.extend(party_citations)
            text='# Стороны договоров\n'+party_text+'\n\n# Условия и сравнение\n'+text

        task.update(self.get('workspace_tasks',task['id'],user))
        for result in record.get('exhaustive',{}).get('document_results',[]):
            if result['processing_status']!='SUCCESS' or result['extraction_status']!='SUCCESS':
                task['limitations'].append(grants[result['document_id']]['title']+': '+result['processing_status']+' / '+result['extraction_status'])
        record.update(status='COMPLETED' if record.get('full_document_status')=='SUCCESS' else 'PARTIAL',claims_json=record.get('exhaustive',{}).get('verified_claims',[]))
        self.rag.put(record)
        task['analysis']=dict(text=text,citations=citations,status='PARTIAL',comparison_run_id=record['comparison_run_id'],documents=selected,metrics=record.get('exhaustive',{}))
        task['limitations'].append('Анализ частичный: сохранены только проверенные факты; полнота Day33 не гарантирована.')
        self.put('workspace_tasks',task)
        return task['analysis']

    def call(self,user,task_id,request,factory=None,config=None):
        self.get('workspace_tasks',task_id,user)
        locks=self.root/'task-locks';locks.mkdir(exist_ok=True)
        with portalocker.Lock(str(locks/(task_id+'.lock')),timeout=1):
            return self._call(user,task_id,request,factory,config)

    def _call(self,user,task_id,request,factory=None,config=None):
        user=self.actor(user);task=self.get('workspace_tasks',task_id,user)
        if task['cancelled']:raise ValueError('TASK_CANCELLED')
        if task['status']=='COMPLETED':raise ValueError('TASK_ALREADY_COMPLETED')
        op=request.operation
        if len(task['tools'])>=self.cfg.max_tool_calls:raise ValueError('TOOL_CALL_LIMIT')
        for previous in task['tools']:
            if previous['input']==request.model_dump() and op in ('create_document','edit_document','prepare_publication'):
                return ToolOutput.model_validate(previous['output'])
        task.update(current_operation=op,status='RUNNING');self.put('workspace_tasks',task)
        if op=='confirm_publication':raise HTTPException(403,'Только явное подтверждение ADMIN через интерфейс')
        if op=='search_documents':
            rows=[dict(document_id=d,version_id=g['recognition_version'],title=g['title']) for d,g in self.knowledge.grants().items() if not request.query or request.query.casefold() in g['title'].casefold()]
            data=dict(documents=rows,total=len(rows),search_kind='filename_inventory',complete=True)
        elif op=='read_document':
            if request.file_id:
                file,raw=self.file_bytes(user,request.file_id)
                data=dict(file_id=file['id'],workspace_text='\n'.join(b['text'] for b in formats.blocks(raw,file['format'])['blocks']),sources=file['sources'])
                task['sources']=file['sources'];task['limitations']=file['limitations']
                original_task=self.get('workspace_tasks',file['task_id'],user)
                if original_task.get('analysis'):task['analysis']=original_task['analysis']
                self.put('workspace_tasks',task)
            else:
                data=self.read(user,request.document_id,request.version_id)
                if any(r['document_id']==request.document_id and r['version_id']!=request.version_id for r in task['sources']):raise ValueError('PINNED_VERSION_CHANGED')
                if data['source'] not in task['sources']:task['sources'].append(data['source']);self.put('workspace_tasks',task)
        elif op=='analyze_documents':data=self.analyze(user,task,request,factory,config)
        elif op=='create_document':
            text=request.content
            if task.get('analysis'):
                analysis=task['analysis'];text=analysis['text']+'\n\n'+text
                text+='\n\nПодтверждающие фрагменты:\n'+'\n'.join('['+c['source_id']+'] '+c['file_title']+' / версия '+str(c.get('document_version_id'))+' / '+str(c.get('locator'))+'\n'+c['quote'] for c in analysis['citations'])
            if task['limitations']:text+='\n\nОграничения по документам:\n'+'\n'.join(task['limitations'])
            text='ОГРАНИЧЕНИЯ: частичный / непроверенный рабочий результат.\n'+text
            if task['sources']:text+='\n\nИсточники (закреплённые версии):\n'+'\n'.join(f"{r['document_id']} / {r['version_id']} / SHA-256 {r['content_hash']}" for r in task['sources'])
            data=self.save(user,task,request.format,request.title,formats.create(request.format,request.title,text))
        elif op=='edit_document':
            original,raw=self.file_bytes(user,request.file_id)
            task['sources']=original['sources'];task['limitations']=original['limitations']
            data=self.save(user,task,original['format'],original['title'],formats.edit(raw,original['format'],request.replacements,request.append),original['id'])
        elif op=='compare_documents':
            a,rawa=self.file_bytes(user,request.file_id);b,rawb=self.file_bytes(user,request.other_file_id)
            data=self.put('workspace_diffs',dict(id=uid(),owner_id=user['id'],old_id=a['id'],new_id=b['id'],old_hash=a['sha256'],new_hash=b['sha256'],changes=structured_diff(a['id'],a['sha256'],b['sha256'],formats.blocks(rawa,a['format']),formats.blocks(rawb,b['format']))))
        elif op=='validate_document':data=self.validate(user,request.file_id)
        elif op=='save_workspace_result':
            data=self.get('workspace_files',request.file_id,user)
            if request.file_id not in task['files']:raise ValueError('FILE_NOT_IN_TASK')
            self.validate(user,request.file_id)
            task.setdefault('saved_files',[])
            if request.file_id not in task['saved_files']:task['saved_files'].append(request.file_id)
            self.put('workspace_tasks',task)
        elif op=='prepare_publication':
            from .archive_publisher import ArchivePublisher
            data=ArchivePublisher(self).prepare(user,request)
        else:raise ValueError('UNKNOWN_TOOL')
        result=ToolOutput(operation=op,status='PARTIAL' if op=='analyze_documents' else 'SUCCESS',data=data)
        with self.runs.mutation_lock:
            current=self.get('workspace_tasks',task_id,user);current['tools'].append(dict(input=request.model_dump(),output=result.model_dump(),at=time.time()));self.put('workspace_tasks',current)
        return result

    def planner_state(self,task,recent):
        # Full inputs/results stay in SQLite; only bounded references go to planning.
        observed=[]
        last_read=next((c for c in reversed(task['tools']) if 'workspace_text' in c['output']['data']),None)
        for call in task['tools']:
            data=call['output']['data'];summary={k:data[k] for k in ('id','file_id','format','status','total','source','structural_status','semantic_status') if k in data}
            if 'documents' in data and call['input']['operation']=='search_documents':summary.update(documents=data['documents'][:100],omitted_count=max(0,len(data['documents'])-100))
            if call['input']['operation']=='analyze_documents':summary.update(document_count=len(data.get('documents',[])),coverage=data.get('metrics',{}).get('coverage_percent'),analysis_excerpt=data.get('text','')[:3500],excerpt_partial=len(data.get('text',''))>3500)
            if call is last_read:summary['excerpt']=data['workspace_text'][:12000];summary['excerpt_partial']=len(data['workspace_text'])>12000
            observed.append(dict(operation=call['input']['operation'],data=summary))
        return dict(goal=task['goal'],remaining_tool_calls=self.cfg.max_tool_calls-len(task['tools']),observed=observed,
                    recent_files=[{k:f[k] for k in ('id','title','format')} for f in recent],
                    analysis_available=bool(task.get('analysis')),error_reason=task.get('error_reason'),validation_errors=task.get('validation_errors',[]))

    def completion_error(self,user,task):
        """Check execution evidence before stopping planning; validation/save follow."""
        if not task['files']:return 'COMPLETION_WITHOUT_FILE'
        goal=task['goal'];calls=task['tools'];ops=[c['input']['operation'] for c in calls]
        editing=bool(re.search(r'редакци|отредакт|добавь|edit',goal,re.I))
        analytical=bool(re.search(r'сравн|анализ|compar|analy',goal,re.I))
        files=[self.file_bytes(user,key)[0] for key in task['files']]
        if editing and not any(f.get('previous_id') and any(c['input']['operation']=='read_document' and c['input'].get('file_id')==f['previous_id'] for c in calls) for f in files):
            return 'EDIT_WORKFLOW_INCOMPLETE'
        if re.search(r'найд|find|search',goal,re.I) and not editing and 'search_documents' not in ops:
            return 'SEARCH_WORKFLOW_INCOMPLETE'
        if analytical and not editing:
            analyses=[i for i,c in enumerate(calls) if c['input']['operation']=='analyze_documents']
            if (not task.get('analysis') or not task['sources'] or 'search_documents' not in ops or 'read_document' not in ops
                    or not analyses or not any(c['input']['operation']=='create_document' and c['output']['data'].get('id') in task['files'] for c in calls[analyses[-1]+1:])):
                return 'ANALYSIS_WORKFLOW_INCOMPLETE'
        requested=re.search(r'\b(docx|pdf|txt|md)\b',goal,re.I)
        if requested and not any(f['format']==requested[1].lower() for f in files):return 'REQUESTED_FORMAT_MISSING'
        if re.search(r'публик|publish',goal,re.I) and 'prepare_publication' not in ops:return 'PUBLICATION_APPROVAL_REQUIRED'
        return None

    def execute_goal(self,user,goal,factory,config,session_id=None,resume_task_id=None):
        from .dialogue import structured
        from .failure_diagnostics import capture
        if resume_task_id:
            task=self.get('workspace_tasks',resume_task_id,user)
            if task['goal']!=goal or task.get('session_id')!=session_id or task['status'] not in ('FAILED','PARTIAL') or task['cancelled']:raise ValueError('TASK_NOT_RESUMABLE')
            if task.get('attempt_count',1)>=2:raise ValueError('RESUME_LIMIT')
            task.setdefault('previous_failures',[]).append(dict(error_reason=task.get('error_reason'),failure=task.get('failure')))
            task.update(status='RUNNING',attempt_count=2,owner_pid=os.getpid(),owner_started=psutil.Process().create_time(),error_reason=None)
        else:
            task=self.task(user,goal);task.update(session_id=session_id,status='RUNNING',repair_count=0,provider_calls=0,attempt_count=1)
        self.put('workspace_tasks',task);deadline=time.monotonic()+self.cfg.timeout;original_factory=factory
        def factory(cfg):
            cfg=cfg.model_copy(deep=True);cfg.timeout=min(cfg.timeout,max(.01,deadline-time.monotonic()))
            provider=original_factory(cfg);workspace=self;task_id=task['id']
            class Guarded:
                def generate(self,request):
                    with workspace.runs.mutation_lock:
                        current=workspace.get('workspace_tasks',task_id,user)
                        if current['cancelled']:raise ValueError('TASK_CANCELLED')
                        if time.monotonic()>deadline:raise ValueError('WORKSPACE_TIMEOUT')
                        if len(request.question.encode('utf-8'))+cfg.max_output_tokens>cfg.full_document_context_budget:raise ValueError('CONTEXT_BUDGET_EXCEEDED')
                        current['provider_calls']+=1;workspace.put('workspace_tasks',current)
                    result=provider.generate(request)
                    current=workspace.get('workspace_tasks',task_id,user)
                    if current['cancelled']:raise ValueError('TASK_CANCELLED')
                    if time.monotonic()>deadline:raise ValueError('WORKSPACE_TIMEOUT')
                    if request.question.startswith('DAY34_FILE_TOOLS'):
                        current.setdefault('planner_responses',[]).append(dict(status=result.status,text=result.text[:20000],sha256=sha(result.text.encode()),finish_reason=result.finish_reason))
                        workspace.put('workspace_tasks',current)
                    return result
            return Guarded()
        with self.runs.db() as db:
            tasks={t['id']:t for t in db.all('workspace_tasks')}
            recent=sorted([f for f in db.all('workspace_files') if f['owner_id']==user['id'] and (session_id is None or tasks.get(f['task_id'],{}).get('session_id')==session_id)],key=lambda f:f['created_at'])[-5:]
        prompt=PLANNER_PROMPT
        while True:
            task=self.get('workspace_tasks',task['id'],user)
            task['remaining_budget']=dict(tool_calls=self.cfg.max_tool_calls-len(task['tools']),seconds=max(0,deadline-time.monotonic()),repairs=1-task['repair_count'])
            self.put('workspace_tasks',task)
            if task['cancelled'] or time.monotonic()>deadline:
                task.update(status='CANCELLED' if task['cancelled'] else 'PARTIAL' if task['files'] or task.get('analysis') else 'FAILED',error_reason='TASK_CANCELLED' if task['cancelled'] else 'WORKSPACE_TIMEOUT');break
            if task['files'] and self.completion_error(user,task) is None:
                task.update(error_reason=None,validation_errors=[],status='PARTIAL' if task['limitations'] else 'COMPLETED');break
            if len(task['tools'])>=self.cfg.max_tool_calls-2:
                task.update(status='PARTIAL',error_reason='TOOL_CALL_LIMIT');break
            try:
                payload=self.planner_state(task,recent)
                wire=prompt+'\n'+json.dumps({**payload,'schema':FlatToolInput.model_json_schema()},ensure_ascii=False)
                if len(wire.encode())+4096>config.full_document_context_budget:raise ValueError('CONTEXT_BUDGET_EXCEEDED')
                decision=structured(factory,config,prompt,payload,FlatToolInput).root
                if decision.operation=='finish_task':
                    reason=self.completion_error(user,task)
                    if reason:raise ValueError(reason)
                    task.update(error_reason=None,validation_errors=[],status='PARTIAL' if task['limitations'] else 'COMPLETED');break
                request=ToolInput.model_validate(decision.model_dump(exclude_none=True))
                if request.operation=='create_document' and not request.content.strip() and not task.get('analysis'):
                    raise ValueError('EMPTY_DOCUMENT')
                before=len(task['tools']);self.call(user,task['id'],request,factory,config)
                if len(self.get('workspace_tasks',task['id'],user)['tools'])==before:raise ValueError('REPEATED_COMPLETED_OPERATION')
            except Exception as exc:
                task=self.get('workspace_tasks',task['id'],user)
                task['failure']=capture(self.runs,exc,task['id'],'/chat/workspace')
                reason=getattr(exc,'code',None) or (str(exc) if type(exc) is ValueError and str(exc).isupper() else type(exc).__name__)
                task['error_reason']='INVALID_TOOL_CALL' if type(exc).__name__=='ValidationError' else reason
                if type(exc).__name__=='ValidationError':
                    task['validation_errors']=[dict(type=e['type'],loc=list(e['loc'])) for e in exc.errors(include_input=False,include_context=False,include_url=False)]
                # One bounded repair of a nonexecuted planner output; never replay tools.
                if type(exc).__name__ in ('ValidationError','ChatValidationError') and task['repair_count']==0 and time.monotonic()<deadline:
                    task['repair_count']=1;self.put('workspace_tasks',task);continue
                task['status']='CANCELLED' if task['cancelled'] else 'PARTIAL';task['limitations'].append(reason);break
        self.put('workspace_tasks',task)
        # Preserve partial work locally without another model request, including after timeout.
        if task.get('analysis') and not task['files'] and not task['cancelled'] and len(task['tools'])+2<=self.cfg.max_tool_calls:
            try:self.call(user,task['id'],ToolInput(operation='create_document',title='Частичный результат анализа'))
            except Exception:task['limitations'].append('PARTIAL_ARTIFACT_SAVE_FAILED');self.put('workspace_tasks',task)
        terminal=task['status'];task=self.get('workspace_tasks',task['id'],user)
        for key in task['files']:
            try:
                if time.monotonic()<deadline and not task['cancelled']:self.validate_content(user,key,factory,config)
                else:self.validate(user,key)
            except Exception:task['limitations'].append('STRUCTURAL_VALIDATION_FAILED');terminal='PARTIAL'
        task['provider_calls']=self.get('workspace_tasks',task['id'],user).get('provider_calls',task.get('provider_calls',0))
        task.update(status=terminal,current_operation=None,remaining_budget=dict(tool_calls=self.cfg.max_tool_calls-len(task['tools']),seconds=max(0,deadline-time.monotonic()),repairs=1-task['repair_count']))
        # Completion is a backend invariant, never a model assertion. Validation
        # and save are application-owned mandatory steps (no extra paid calls).
        if task['files'] and not task['cancelled']:
            for key in task['files']:
                try:
                    self.validate(user,key)
                    task.setdefault('saved_files',[])
                    if key not in task['saved_files']:task['saved_files'].append(key)
                except Exception:
                    task.update(status='PARTIAL',error_reason='MANDATORY_VALIDATION_FAILED')
            if task.get('analysis',{}).get('status')=='PARTIAL':task['status']='PARTIAL'
            with self.runs.db() as db:checks=[v for v in db.all('workspace_validations') if v['file_id'] in task['files']]
            if any(v.get('semantic_status')!='VERIFIED' for v in checks):task['status']='PARTIAL'
        elif task['status']=='COMPLETED':
            task.update(status='PARTIAL',error_reason='COMPLETION_WITHOUT_FILE')
        self.put('workspace_tasks',task)
        return task
