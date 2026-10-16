"""Day25 orchestration: existing POINT RAG grounding or policy-scoped full coverage."""
import json
import os
import re
import time
import psutil
from .chat_models import ChatValidationError, ChatSession, ChatMessage, ChatTurn, TaskState, TaskStatePatch, ChatResponse
from .chat_memory import TaskMemoryUpdater, ContextualQueryBuilder, ChatContextBuilder
from ..documents import now
from .views import ResultsService
from .document_metadata import enrich_documents
from .retrieval_policy import RetrievalPolicyBuilder, legacy_predicates
from .dialogue import DialogueWorkingContext, WorkingContextService, ReferenceResolver, AnswerModeRouter
from .exhaustive import ExhaustiveNoRAG
from .reference_retrieval import ReferenceAwareRetrievalService
from .diagnostic_trace import observed_chat


class ChatSessionService:
    def __init__(self,runs,rag):
        self.runs,self.rag=runs,rag
        self.memory=TaskMemoryUpdater(rag.llm_factory,runs.config.llm)
        self.resolver=ContextualQueryBuilder(rag.llm_factory,runs.config.llm)
        self.references=ReferenceResolver(rag.llm_factory,runs.config.llm)
        self.router=AnswerModeRouter(rag.llm_factory,runs.config.llm)
        self.futures={}
        self.recover()

    @staticmethod
    def write(store,table,key,value):
        # Use the caller's transaction, unlike MetadataStore.put's standalone commit.
        data=value.model_dump() if hasattr(value,'model_dump') else value
        store.db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)',(key,json.dumps(data,ensure_ascii=False)))

    def recover(self):
        with self.runs.db() as store:
            for t in store.all('chat_turns'):
                if t['status'] not in ('QUEUED','RUNNING'):continue
                try:alive=psutil.Process(t['owner_pid']).create_time()==t['owner_started']
                except psutil.Error:alive=False
                if not alive:
                    t.update(status='INTERRUPTED',stage='done')
                    t['trace']['error']='Сервер остановлен во время ответа. Сообщение сохранено; повторите запрос.'
                    store.put('chat_turns',t['turn_id'],t)

    def create(self,run_id,document_id=None,source_id=None):
        if run_id:self.runs.get(run_id)
        s=ChatSession(processing_run_id=run_id)
        if document_id:
            scope='NETWORK' if source_id else 'MANUAL'
            snapshot=ResultsService(self.runs).chat_snapshot(run_id,source_id,source_scope=scope)
            if snapshot['document_runs'].get(document_id)!=run_id:
                raise ChatValidationError('Документ не доступен в активном индексе. Дождитесь индексации.')
            with self.runs.db() as store:name=store.get('documents',document_id)['file_name']
            s.source_scope,s.source_id=scope,source_id
            s.document_id,s.document_name=document_id,name
            s.selected_document_ids=[document_id]
            s.title='Обсуждение: '+name
        state=TaskState(session_id=s.session_id)
        with self.runs.db() as store,store.db:
            store.db.execute('BEGIN IMMEDIATE')
            self.write(store,'chat_sessions',s.session_id,s)
            self.write(store,'task_states',s.session_id,state)
            self.write(store,'dialogue_working_contexts',s.session_id,DialogueWorkingContext(session_id=s.session_id))
        return s

    def recent(self):
        with self.runs.db() as store:rows=store.all('chat_sessions')
        return sorted(rows,key=lambda r:r['updated_at'],reverse=True)[:30]

    def load(self,sid,*,message_limit=None,before=None,after=None):
        with self.runs.db() as store:
            s=store.get('chat_sessions',sid)
            if not s:raise KeyError(sid)
            s=ChatSession.model_validate(s).model_dump()
            if message_limit is None:
                messages=[json.loads(r[0]) for r in store.db.execute("SELECT data FROM chat_messages WHERE json_extract(data,'$.session_id')=? ORDER BY json_extract(data,'$.sequence')",(sid,))]
                turns=[json.loads(r[0]) for r in store.db.execute("SELECT data FROM chat_turns WHERE json_extract(data,'$.session_id')=? ORDER BY rowid",(sid,))]
                has_older=False
            else:
                # Bounded presentation reads only; analysis continues to use the unchanged full load.
                limit=max(1,min(message_limit,100));where="json_extract(data,'$.session_id')=?";params=[sid]
                if before is not None:where+=" AND json_extract(data,'$.sequence')<?";params.append(before)
                if after is not None:where+=" AND json_extract(data,'$.sequence')>?";params.append(after)
                order='ASC' if after is not None else 'DESC'
                messages=[json.loads(r[0]) for r in store.db.execute(f"SELECT data FROM chat_messages WHERE {where} ORDER BY json_extract(data,'$.sequence') {order} LIMIT ?",(*params,limit))]
                messages.sort(key=lambda m:m['sequence']);keys=[m['message_id'] for m in messages]
                placeholders=','.join('?' for _ in keys) or "NULL"
                turns=[json.loads(r[0]) for r in store.db.execute(f"SELECT data FROM chat_turns WHERE json_extract(data,'$.session_id')=? AND (json_extract(data,'$.user_message_id') IN ({placeholders}) OR json_extract(data,'$.assistant_message_id') IN ({placeholders}) OR id=(SELECT id FROM chat_turns WHERE json_extract(data,'$.session_id')=? ORDER BY rowid DESC LIMIT 1)) ORDER BY rowid",(sid,*keys,*keys,sid))]
                first=messages[0]['sequence'] if messages else 0
                has_older=bool(first and store.db.execute("SELECT 1 FROM chat_messages WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.sequence')<? LIMIT 1",(sid,first)).fetchone())
            state=store.get('task_states',sid)
            working=WorkingContextService.load(store,sid)
            if not store.get('dialogue_working_contexts',sid):store.put('dialogue_working_contexts',sid,working.model_dump())
            typed=TaskState.model_validate(state)
            execution={i.key:('фильтр активен' if any(p.type!='SEMANTIC_CONSTRAINT' for p in legacy_predicates(i)) else
                'semantic' if i.predicates else 'не скомпилировано · semantic') if i.active else 'неактивно' for i in typed.constraints_json}
            return dict(session=s,messages=messages,task_state=state,turns=turns,memory_execution=execution,dialogue_context=working.model_dump(),has_older=has_older)

    def select_source(self, sid, scope, source_id, expected_version, selected_document_ids=None, *, preserve_discovery_since=False, discovery_decisions=None):
        if scope not in ('ALL_DOCUMENTS', 'NETWORK', 'MANUAL'):
            raise ChatValidationError('Недопустимая область поиска')
        if scope != 'NETWORK':source_id = None
        selected_document_ids=list(dict.fromkeys(selected_document_ids or []))
        current=self.load(sid)['session']
        if selected_document_ids:
            active=ResultsService(self.runs).chat_snapshot(current['processing_run_id'],source_id,source_scope=scope)
            if not set(selected_document_ids)<=set(active['document_runs']):
                raise ChatValidationError('Выбранные документы недоступны в активном индексе этой области. Обновите выбор.')
        with self.runs.db() as store, store.db:
            store.db.execute('BEGIN IMMEDIATE')
            raw = store.get('chat_sessions', sid)
            if not raw:raise KeyError(sid)
            session = ChatSession.model_validate(raw)
            if session.scope_version != expected_version:
                raise ChatValidationError('Область поиска изменилась. Обновите страницу.')
            if scope == 'NETWORK' and source_id and not store.get('network_sources', source_id):
                raise ChatValidationError('Выберите существующий сетевой источник')
            if store.db.execute("SELECT 1 FROM chat_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.status') IN ('QUEUED','RUNNING')", (sid,)).fetchone():
                raise ChatValidationError('Дождитесь текущего ответа перед сменой источника')
            if session.source_scope != scope or session.source_id != source_id or session.document_id or session.selected_document_ids != selected_document_ids:
                if not preserve_discovery_since:
                    session.discovery_since = now()
                session.source_scope, session.source_id = scope, source_id
                session.document_id=session.document_name=None
                session.selected_document_ids=selected_document_ids
                if len(selected_document_ids)==1:
                    session.document_id=selected_document_ids[0]
                    session.document_name=(store.get("documents",session.document_id) or {}).get("file_name")
                session.scope_version += 1
                session.updated_at = now()
                self.write(store, 'chat_sessions', sid, session)
                # Preserve explicit task constraints, but do not reuse evidence from another scope.
                self.write(store, 'dialogue_working_contexts', sid, DialogueWorkingContext(session_id=sid))
            for key, decision in (discovery_decisions or {}).items():
                self.write(store, 'chat_discovery_decisions', key, decision)
        return session

    def start(self,sid,content,expected_version,expected_scope_version=None):
        if not isinstance(content,str) or not content.strip() or len(content)>1500:
            raise ChatValidationError('Введите сообщение длиной 1–1500 символов.')
        with self.runs.db() as store,store.db:
            store.db.execute('BEGIN IMMEDIATE')
            session=store.get('chat_sessions',sid)
            if not session:raise KeyError(sid)
            if expected_scope_version is not None and session.get('scope_version',0)!=expected_scope_version:
                raise ChatValidationError('Область поиска изменилась в другой вкладке. Обновите страницу.')
            state=TaskState.model_validate(store.get('task_states',sid))
            if state.version!=expected_version:raise ChatValidationError('Память изменилась. Обновите страницу.')
            busy=store.db.execute("SELECT 1 FROM chat_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.status') IN ('RUNNING','QUEUED')",(sid,)).fetchone()
            if busy:raise ChatValidationError('Дождитесь текущего ответа.')
            seq=store.db.execute("SELECT COALESCE(MAX(json_extract(data,'$.sequence')),0) FROM chat_messages WHERE json_extract(data,'$.session_id')=?",(sid,)).fetchone()[0]+1
            m=ChatMessage(session_id=sid,role='USER',content=content.strip(),sequence=seq)
            t=ChatTurn(session_id=sid,user_message_id=m.message_id,original_question=m.content,task_state_version=state.version,
                owner_pid=os.getpid(),owner_started=psutil.Process().create_time(),trace={'task_state_before':state.model_dump(), 'source_selection': {k:session.get(k, default) for k,default in [('source_scope','ALL_DOCUMENTS'),('source_id',None),('scope_version',0),('document_id',None),('document_name',None),('selected_document_ids',[])]}})
            self.write(store,'chat_messages',m.message_id,m)
            self.write(store,'chat_turns',t.turn_id,t)
            if seq==1:session['title']=m.content[:70]
            session.update(updated_at=now(),last_message_at=m.created_at)
            self.write(store,'chat_sessions',sid,session)
        self.futures[t.turn_id]=self.runs.executor.submit(self.execute,t.turn_id)
        return t

    @observed_chat
    def execute(self,tid):
        started=time.perf_counter()
        with self.runs.db() as store:t=ChatTurn.model_validate(store.get('chat_turns',tid))
        from .diagnostic_trace import events
        t.trace['diagnostic_events']=events.get()
        from .chat_diagnostic import code_version
        t.trace['code_version']=code_version()
        t.trace['max_reference_claims']=self.runs.config.max_reference_claims
        def save(stage):
            t.stage=stage;t.status='RUNNING'
            t.trace.setdefault('stages',[]).append(dict(stage=stage,elapsed_ms=(time.perf_counter()-started)*1000))
            with self.runs.db() as store:store.put('chat_turns',tid,t.model_dump())
        try:
            save('memory')
            data=self.load(t.session_id)
            state=TaskState.model_validate(data['task_state'])
            selection=t.trace.get('source_selection', {})
            t.scope=selection.get('source_scope','ALL_DOCUMENTS')
            # Parse the command only; semantic routing uses the existing typed router/provider.
            parts=t.original_question.strip().split(maxsplit=1)
            help_command=parts[0].casefold()=='/help'
            question=parts[1].strip() if help_command and len(parts)>1 else '' if help_command else t.original_question
            intent=None
            if help_command and question:
                save('assistant_routing')
                try:intent=self.router.assistant_intent(question)
                except (ValueError,TypeError):
                    return self.clarify(t,started,'Не удалось определить тип вопроса. Уточните: содержание документов или состояние архива?','ASSISTANT_INTENT_INVALID')
                t.trace['assistant_intent']=intent.model_dump()
                if intent.confidence<0.6:
                    return self.clarify(t,started,'Уточните: вопрос о содержании документов или о состоянии архива?','ASSISTANT_INTENT_UNCERTAIN')
            if help_command and (not question or intent.target=='ARCHIVE_STATUS'):
                from .archive_status import ArchiveStatusProvider
                save('archive_status')
                info=ArchiveStatusProvider(self.runs).snapshot(data['session'])
                text,links=ArchiveStatusProvider.answer(info,intent.status_view if intent else 'OVERVIEW',help_intro=not question)
                t.trace.update(assistant_target='ARCHIVE_STATUS',archive_status=info,archive_links=links)
                t.resolved_question=question;t.status='COMPLETED';t.grounding_status='NOT_APPLICABLE'
                t.response=ChatResponse(answer_text=text,task_state_version=state.version,resolved_question=question,status='ARCHIVE_STATUS')
                return self.finish(t,started)
            if help_command:t.trace['assistant_target']='DOCUMENT_CONTENT'
            snapshot=ResultsService(self.runs).chat_snapshot(data['session']['processing_run_id'],selection.get('source_id') if t.scope=='NETWORK' else None,source_scope=t.scope)
            sources=snapshot['document_sources']
            allowed=[did for did, source_id in sources.items() if t.scope=='ALL_DOCUMENTS' or
                (t.scope=='MANUAL' and source_id is None) or (t.scope=='NETWORK' and source_id is not None and (not selection.get('source_id') or source_id==selection.get('source_id')))]
            selected_ids=selection.get('selected_document_ids') or ([selection['document_id']] if selection.get('document_id') else [])
            if selected_ids:
                allowed=[did for did in allowed if did in selected_ids]
            if intent and intent.document_names:
                from pathlib import Path
                names={e['document_id']:e.get('file_name') or e.get('name','') for e in snapshot['corpus']['documents']}
                with self.runs.db() as store:
                    names={did:(store.get('documents',did) or {}).get('file_name',name) for did,name in names.items() if did in allowed}
                selected=[]
                for name in intent.document_names:
                    if name.casefold() not in question.casefold():
                        return self.clarify(t,started,'Укажите точные имена документов для сравнения.','DOCUMENT_SELECTION_UNCONFIRMED')
                    matches=[did for did,filename in names.items() if name.casefold() in (filename.casefold(),Path(filename).stem.casefold())]
                    if len(matches)!=1:
                        return self.clarify(t,started,'Указанный документ не найден однозначно в текущей области. Уточните имя и источник.','DOCUMENT_SELECTION_AMBIGUOUS')
                    selected.extend(matches)
                allowed=[did for did in allowed if did in selected]
                t.trace['explicit_document_ids']=allowed
            # Only dialogue turns from this selection version can contribute prior answers.
            permitted_turns=[turn for turn in data['turns'] if turn.get('trace',{}).get('assistant_target')!='ARCHIVE_STATUS' and turn.get('trace',{}).get('source_selection',{}).get('scope_version',0)==selection.get('scope_version',0)]
            message_ids={key for turn in permitted_turns for key in (turn['user_message_id'],turn.get('assistant_message_id'))}
            history=[ChatMessage.model_validate(m) for m in data['messages'] if m['message_id'] in message_ids]
            if not allowed:
                t.trace.update(eligible_document_ids=[], source_selection=selection)
                t.status='COMPLETED';t.grounding_status='NOT_APPLICABLE'
                t.response=ChatResponse(answer_text='В выбранной области пока нет документов, доступных в активном индексе. Проверьте найденные документы и дождитесь успешной индексации.', task_state_version=state.version,resolved_question=t.original_question,status='NO_INDEXED_DOCUMENTS')
                return self.finish(t,started)
            message=next(m for m in history if m.message_id==t.user_message_id)
            if help_command:
                message.content=question
                t.trace['command_question']=question
            patch,extraction=self.memory.propose(state,message)
            t.trace.update(task_state_patch=patch.model_dump(),memory_extraction=extraction)
            updated=self.memory.apply(state,patch,message)
            index=snapshot.get('indexes',{}).get('structure') or {}
            with self.runs.db() as store:
                metadata=enrich_documents(store,index.get('document_ids',[]),index.get('corpus_id'))
            try:
                policy=RetrievalPolicyBuilder().build(updated,'ALL_DOCUMENTS',index.get('corpus_id'),metadata,
                    source_scope=t.scope,source_id=selection.get('source_id'),source_document_ids=allowed)
            except ValueError as exc:
                raise ChatValidationError('Ограничения конфликтуют или ссылаются на документ вне корпуса. Уточните условия.') from exc
            snapshot=ResultsService(self.runs).restrict_chat_snapshot(snapshot, policy.eligible_document_ids)
            data['corpus_snapshot']=snapshot
            data['effective_run_id']=next(iter(snapshot['document_runs'].values()),data['session']['processing_run_id'])
            t.trace['document_runs']=snapshot['document_runs']
            t.trace.update(retrieval_policy_json=policy.model_dump(),eligible_document_ids=policy.eligible_document_ids,
                hard_filters_json=policy.qdrant_filter().model_dump(mode='json',exclude_none=True),
                semantic_constraints_json=policy.semantic_constraints)
            with self.runs.db() as store,store.db:
                store.db.execute('BEGIN IMMEDIATE')
                if store.get('task_states',t.session_id)['version']!=state.version:raise ChatValidationError('Память изменилась параллельно.')
                self.write(store,'task_states',t.session_id,updated)
            t.trace.update(task_state_patch=patch.model_dump(),task_state_after=updated.model_dump(),memory_extraction=extraction)
            t.task_state_version=updated.version
            if patch.intent=='MEMORY_ONLY' and extraction.get('semantic_verified'):
                t.status='COMPLETED';t.grounding_status='NOT_APPLICABLE'
                t.trace['memory_only']=True
                t.response=ChatResponse(answer_text='Учту это условие в следующих вопросах: «'+message.content+'».',
                    task_state_version=updated.version,resolved_question='',status='MEMORY_UPDATED')
                return self.finish(t,started)
            if not policy.eligible_document_ids:
                t.status='COMPLETED';t.grounding_status='NOT_APPLICABLE'
                t.response=ChatResponse(answer_text='В выбранной области нет проиндексированных документов, удовлетворяющих сохранённым условиям поиска.',task_state_version=updated.version,resolved_question=t.original_question,status='NO_ELIGIBLE_DOCUMENTS')
                return self.finish(t,started)
            save('resolving')
            with self.runs.db() as store:
                working=WorkingContextService.validate(store,WorkingContextService.load(store,t.session_id))
            t.trace['dialogue_context_before']=working.model_dump()
            reference=self.references.resolve(message,updated,working,history)
            prior=[]
            if reference.status=='RESOLVED':
                with self.runs.db() as store:
                    prior=ReferenceAwareRetrievalService.bind(store,working,reference,self.runs.config.max_reference_claims)
            t.trace['reference_prior_claims']=prior
            t.trace['goal_injected_into_retrieval']=False
            t.trace.update(reference_resolution_json=reference.model_dump(),resolved_referent_ids=reference.resolved_referents,
                candidate_prior_claim_ids=reference.candidate_prior_claim_ids)
            if reference.status=='UNRESOLVED' or reference.confidence<0.6:
                return self.clarify(t,started,'Уточните, какие ранее перечисленные факты вы имеете в виду. Не удалось надёжно разрешить ссылку в вопросе.','REFERENCE_UNRESOLVED')
            resolved=self.resolver.build(message,updated,history,reference,working)
            from .diagnostic_trace import record
            record('contextual_builder', original_message=message.model_dump(), task_state=updated.model_dump(),
                   references=reference.model_dump(), working_context=working.model_dump(), output=resolved.model_dump())
            t.resolved_question=resolved.resolved_question;t.trace['resolution']=resolved.model_dump()
            save('routing')
            intent=self.router.route(resolved.contextual_question,updated,reference,policy)
            t.trace.update(question_intent_json=intent.model_dump(),answer_mode=intent.answer_mode)
            if intent.question_type=='UNCERTAIN' or intent.confidence<0.6:
                return self.clarify(t,started,'Уточните: нужен конкретный факт или полный анализ всех разрешённых документов?','ANSWER_MODE_UNCERTAIN')
            if intent.answer_mode=='EXHAUSTIVE_NO_RAG':
                return self.exhaustive(t,started,data,updated,policy,save)
            ctx=ChatContextBuilder.prepare(updated,message,history,self.runs.config.llm,resolved)
            t.trace['context_budget']={k:v for k,v in ctx.items() if k not in ('generation_question','base_question')}
            r=self.rag.prepare(data['effective_run_id'],question=resolved.resolved_question,corpus_snapshot=snapshot,
                point_only=True,rag_pipeline_mode='REWRITE_RERANK',rag_scope='ALL_DOCUMENTS',strategy='structure',
                context_token_budget=ctx['context_token_budget'])
            r['chat_generation_question']=ctx['generation_question']
            r['chat_generation_base']=ctx['base_question']
            r['chat_input_budget']=ctx['input_budget']
            r['chat_require_rewrite']=True
            # Goal and hard-filter labels are not relevance terms for a follow-up.
            # Keep semantic conditions and term definitions, enforce hard filters via policy.
            focus=resolved.contextual_question
            if prior and reference.referent_label:focus+='\nПредмет ссылки: '+reference.referent_label
            semantic=dict(constraints=policy.semantic_constraints,
                clarifications=[i.value for i in updated.clarifications_json if i.active],
                terms=[i.value for i in updated.terms_json if i.active])
            if any(semantic.values()):focus+='\nУточнения поиска: '+json.dumps(semantic,ensure_ascii=False)
            r['chat_search_question']=focus;t.trace['search_question']=focus
            if prior:
                r['reference_prior_claims']=prior
                r['reference_predicate']=focus
            r['retrieval_policy_json']=policy.model_dump()
            self.rag.put(r);t.comparison_run_id=r['comparison_run_id'];save('retrieving')
            result=self.rag.execute(r['comparison_run_id'],progress=save)
            t.retrieval_query=result['retrieval_query']
            t.sources_json=result.get('sources',[]);t.citations_json=result.get('citations_json',[])
            t.grounding_status=result.get('grounding_status','ERROR')
            t.trace['claims_json']=result.get('claims_json',[])
            for name in ('reference_searches','reference_verification_results','reference_queries',
                         'policy_validation_results','predicate_evaluation_results'):
                t.trace[name]=result.get(name,[])
            for name in ('reference_answer_status','fresh_retrieval_calls','reused_claims'):
                if name in result:t.trace[name]=result[name]
            t.trace['retrieval_counts']=dict(corpus_documents=len(policy.corpus_document_ids),
                eligible_documents=len(policy.eligible_document_ids),candidates=result.get('retrieved_count',0),contexts=len(t.sources_json))
            t.trace['retrieved_candidates']=[{k:h.get(k) for k in ('document_id','chunk_id','rank','score')} for h in result.get('retrieved_sources',[])]
            t.trace['final_sources']=[{k:s.get(k) for k in ('document_id','chunk_id','file_name')} for s in t.sources_json]
            t.trace.update(grounding=result.get('grounding_result',{}),rewrite=result.get('rewrite_result',{}),
                rag_error=result['rag_result'].get('error',{}),
                history_dropped_for_budget=result.get('chat_history_dropped',False),
                grounding_diagnostics=result.get('grounding_diagnostics',[]),
                durations={k:v for k,v in result.items() if k.endswith('_duration_ms')})
            if result.get('rewrite_fallback') and result.get('rewrite_status')!='GUARDED_FALLBACK':
                t.trace['warning']='Query Rewrite недоступен. Чат остановлен без понижения режима; повторите запрос после восстановления компонента.'
            errors={
                'OUTPUT_LIMIT':'Ответ модели оборвался по лимиту генерации. Неполный ответ не показан. Попробуйте задать более узкий вопрос.',
                'CHAT_CONTEXT_BUDGET_EXCEEDED':'Вопрос, память и данные для проверки ответа не поместились в лимит контекста. Ответ не получен.',
                'QUERY_REWRITE_UNAVAILABLE':'Не удалось переформулировать вопрос для поиска. Попробуйте отправить его ещё раз.',
                'RERANKER_UNAVAILABLE':'Локальный reranker недоступен. Проверьте модель и её настройки.',
                'NETWORK_ERROR':'Не удалось соединиться с MiniMax. Попробуйте отправить сообщение ещё раз.',
                'TIMEOUT':'MiniMax не ответил за отведённое время. Попробуйте отправить сообщение ещё раз.'}
            code=result['rag_result'].get('error',{}).get('code','UNKNOWN_ERROR')
            text=result.get('rag_answer') or errors.get(code,'Ответ не получен. Код ошибки: '+code)
            t.response=ChatResponse(answer_text=text,sources=t.sources_json,citations=t.citations_json,
                grounding_result=result.get('grounding_result',{}),task_state_version=updated.version,
                resolved_question=t.resolved_question,status=result['rag_result']['status'])
            t.status='COMPLETED' if result['status']=='COMPLETED' else 'FAILED'
        except Exception as exc:
            # Never store raw provider/config exceptions.
            if isinstance(exc,ChatValidationError) and hasattr(exc,'diagnostics'):
                t.trace['memory_extraction']=exc.diagnostics
            t.status='FAILED';t.grounding_status='ERROR'
            text=str(exc) if isinstance(exc,ChatValidationError) and not hasattr(exc,'errors') else 'Не удалось завершить ответ. Сообщение сохранено; проверьте настройки и повторите.'
            t.trace['error']=text
            t.response=ChatResponse(answer_text=text,task_state_version=t.task_state_version,resolved_question=t.resolved_question,status='ERROR')
        return self.finish(t,started)

    def clarify(self,t,started,text,reason):
        t.status='COMPLETED';t.grounding_status='NOT_APPLICABLE';t.trace['dialogue_failure']=reason
        t.response=ChatResponse(answer_text=text,task_state_version=t.task_state_version,resolved_question=t.resolved_question,status='NEEDS_CLARIFICATION')
        return self.finish(t,started)

    def exhaustive(self,t,started,data,state,policy,progress):
        progress('full_coverage')
        r=self.rag.prepare(data['effective_run_id'],question=t.resolved_question,point_only=True,corpus_snapshot=data['corpus_snapshot'])
        concepts=t.trace.get('reference_prior_claims',[])
        if concepts:
            r['question_text']+='\nСчитай и сравнивай ТОЛЬКО следующие candidate claims по их ID; не расширяй набор. Они не доказательства; каждый заново проверить по документам. В каждом finding normalized_key ОБЯЗАТЕЛЬНО равен одному из этих claim_id (дословно). Не создавай другие категории и не подменяй claim другой обязанностью: '+json.dumps(concepts,ensure_ascii=False)
            r['reference_prior_claims']=concepts
        r.update(retrieval_policy_json=policy.model_dump(),full_document_context={},status='RUNNING',answer_mode='EXHAUSTIVE_NO_RAG')
        self.rag.put(r);t.comparison_run_id=r['comparison_run_id']
        def save(record):
            self.rag.put(record)
            progress('full_coverage')
        ExhaustiveNoRAG(self.runs,self.rag.llm_factory).run(r,self.runs.config.llm,save)
        coverage=r.get('exhaustive',{})
        success=r.get('full_document_status')=='SUCCESS' and coverage.get('coverage_percent')==100
        r['status']='COMPLETED' if success else 'PARTIAL';self.rag.put(r)
        t.trace['coverage']=coverage;t.trace['exhaustive_error']=r.get('full_document_error_json',{})
        t.status='COMPLETED';t.grounding_status='FULL_COVERAGE' if success else 'PARTIAL'
        text=r.get('full_document_answer') or 'Полный просмотр не завершён. Проверьте покрытие и диагностику.'
        if not success:text='Неполный просмотр разрешённых документов. '+text
        t.response=ChatResponse(answer_text=text,task_state_version=state.version,resolved_question=t.resolved_question,
            status='SUCCESS' if success else 'PARTIAL',grounding_result={'coverage':coverage.get('coverage_percent',0),'validation':'EXHAUSTIVE_EVIDENCE'})
        return self.finish(t,started)

    def finish(self,t,started):
        t.stage='done';t.latency_ms=(time.perf_counter()-started)*1000
        with self.runs.db() as store,store.db:
            store.db.execute('BEGIN IMMEDIATE')
            seq=store.db.execute("SELECT COALESCE(MAX(json_extract(data,'$.sequence')),0) FROM chat_messages WHERE json_extract(data,'$.session_id')=?",(t.session_id,)).fetchone()[0]+1
            m=ChatMessage(session_id=t.session_id,role='ASSISTANT',content=t.response.answer_text,sequence=seq)
            t.assistant_message_id=m.message_id
            self.write(store,'chat_messages',m.message_id,m);self.write(store,'chat_turns',t.turn_id,t)
            working=WorkingContextService.load(store,t.session_id)
            working=WorkingContextService.update(working,t,t.trace.get('claims_json',[]))
            self.write(store,'dialogue_working_contexts',t.session_id,working)
            s=store.get('chat_sessions',t.session_id);s.update(updated_at=now(),last_message_at=m.created_at)
            self.write(store,'chat_sessions',t.session_id,s)
        return t

    def edit(self,sid,version,field,key,value=None):
        if field not in ('constraints_json','clarifications_json','terms_json'):raise ChatValidationError('Недопустимое поле памяти')
        with self.runs.db() as store,store.db:
            store.db.execute('BEGIN IMMEDIATE')
            raw=store.get('task_states',sid)
            if not raw:raise KeyError(sid)
            state=TaskState.model_validate(raw)
            if state.version!=version:raise ChatValidationError('Память изменилась. Обновите страницу.')
            if store.db.execute("SELECT 1 FROM chat_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.status') IN ('RUNNING','QUEUED')",(sid,)).fetchone():raise ChatValidationError('Дождитесь текущего ответа.')
            item=next((i for i in getattr(state,field) if i.key==key),None)
            if not item:raise ChatValidationError('Элемент памяти не найден')
            if value is not None and (field!='terms_json' or not value.strip() or len(value)>1500):raise ChatValidationError('Укажите определение термина длиной 1–1500 символов.')
            seq=store.db.execute("SELECT COALESCE(MAX(json_extract(data,'$.sequence')),0) FROM chat_messages WHERE json_extract(data,'$.session_id')=?",(sid,)).fetchone()[0]+1
            content=f'Изменение памяти: {key} = {value}' if value is not None else f'Удалить из памяти: {key}'
            m=ChatMessage(session_id=sid,role='USER',content=content,sequence=seq)
            patch=TaskStatePatch(expected_version=version)
            remove={'constraints_json':'constraints_remove','clarifications_json':'clarifications_remove','terms_json':'terms_remove'}[field]
            setattr(patch,remove,[key])
            if value is not None:
                patch.terms_set=[item.model_copy(update=dict(value=value,evidence=content,source_message_id=m.message_id))]
            updated=self.memory.apply(state,patch,m,manual=True)
            self.write(store,'chat_messages',m.message_id,m);self.write(store,'task_states',sid,updated)
            session=store.get('chat_sessions',sid);session.update(updated_at=now(),last_message_at=m.created_at)
            self.write(store,'chat_sessions',sid,session)
        return updated
