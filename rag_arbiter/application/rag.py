"""Day 22 orchestration reuses Day 21 embeddings, retriever and vector store."""
import copy
import json
import os
import time
from uuid import uuid4
import psutil
from pydantic import BaseModel, Field, model_validator
from ..config import Config
from ..documents import digest, now
from ..llm import LLMConfig, LLMRequest, LLMResult, MiniMaxLLMProvider, BASE_SYSTEM
from ..retrieval import SemanticRetriever
from .retrieval_policy import RetrievalPolicy
from .document_metadata import sync_payload
from .views import ResultsService
from .full_document import FullDocumentContextBuilder, full_document_defaults
from .exhaustive import ExhaustiveNoRAG
from .context_selection import ContextCandidate, CandidateProcessor, ContextSelector
from ..scope import RAGScope, retrieval_document
from ..reranking import RAGPipelineMode, LocalReranker, RerankCandidate, RerankerUnavailable
from .query_rewrite import QueryRewriteService
from .grounding import grounded_generation, CitationBuilder, GroundingResult, REFUSAL


class ExpectedSource(BaseModel):
    document_id: str | None = None
    file_name: str | None = None
    chunk_id: str | None = None
    page_start: int | None = Field(None, ge=1)
    page_end: int | None = Field(None, ge=1)
    section: str | None = None

    @model_validator(mode='after')
    def identified(self):
        if not any((self.document_id,self.file_name,self.chunk_id)):
            raise ValueError('Expected source needs document_id, file_name or chunk_id')
        if self.page_end and (not self.page_start or self.page_end<self.page_start):
            raise ValueError('Invalid expected page range')
        return self


class ControlQuestion(BaseModel):
    expected_unanswerable: bool = False
    question: str = Field(min_length=1,max_length=4000)
    expected_answer: str = Field(max_length=12000)
    expected_sources: list[ExpectedSource] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.question.strip(): raise ValueError('Введите вопрос')
        return self


class QuestionStore:
    def __init__(self,runs): self.runs=runs

    def list(self):
        with self.runs.db() as store:
            return sorted([r for r in store.all('rag_evaluation_questions') if r['active']],key=lambda r:(r['sort_order'],r['question_id']))

    def get(self,key):
        with self.runs.db() as store:r=store.get('rag_evaluation_questions',key)
        if not r or not r['active']:raise KeyError('Question unavailable')
        return r

    def save(self,data,key=None):
        validated=ControlQuestion(**data).model_dump()
        with self.runs.db() as store:
            # Count + insert is one write transaction, including concurrent API clients.
            store.db.execute('BEGIN IMMEDIATE')
            rows=store.all('rag_evaluation_questions')
            old=next((q for q in rows if q['question_id']==key and q['active']),None)
            if key and not old:raise KeyError('Question unavailable')
            if not key and sum(q['active'] for q in rows)>=10:
                raise ValueError('Максимум 10 активных контрольных вопросов. Удалите один, чтобы добавить новый.')
            key=key or uuid4().hex
            result={**(old or {}),**validated,'question_id':key,'active':True,'updated_at':now(),
                    'created_at':old['created_at'] if old else now(),'sort_order':old['sort_order'] if old else max([q['sort_order'] for q in rows],default=0)+1}
            store.put('rag_evaluation_questions',key,result)
            return result

    def delete(self,key):
        q=self.get(key);q.update(active=False,updated_at=now())
        with self.runs.db() as store:store.put('rag_evaluation_questions',key,q)


class RAGContextBuilder:
    """Conservative UTF-8 byte estimate, not claimed to be MiniMax's tokenizer."""
    version='day22-context-1'
    count_method='utf8_bytes_upper_estimate'

    def build(self,hits,budget):
        hits=[h.as_hit() if isinstance(h,ContextCandidate) else h for h in hits]
        text='';sources=[];ids=set();hashes=set()
        for hit in hits:
            content_hash=hit.get('content_hash') or digest(hit['text'])
            if hit['chunk_id'] in ids or content_hash in hashes:continue
            ids.add(hit['chunk_id']);hashes.add(content_hash)
            reference=f'S{len(sources)+1}'
            source={**hit,'reference':reference}
            header=json.dumps({'reference':f'[{reference}]','file':hit.get('file_name'), 'title':hit.get('title'),
                               'section':hit.get('section'), 'pages':[hit['page_start'],hit['page_end']]},ensure_ascii=False)
            block=f'{header}\n{hit["text"]}'
            candidate=text+('\n\n' if text else '')+block
            if len(candidate.encode('utf-8'))>budget:continue
            text=candidate;sources.append(source)
        return dict(text=text,sources=sources,used_chunk_ids=list(dict.fromkeys(cid for s in sources for cid in s.get('anchor_chunk_ids',[s['chunk_id']]))),
                    token_count=len(text.encode('utf-8')),token_count_method=self.count_method,
                    retrieved_count=len(hits),used_count=len(sources),budget=budget,version=self.version)


def source_metrics(hits,expected,top_k):
    if not expected:return {}
    def matches(hit,source):
        for key in ('document_id','file_name','chunk_id'):
            if key=='file_name' and source.get('document_id'):continue
            if source.get(key) and hit.get(key)!=source[key]:return False
        if source.get('page_start') and not (hit['page_start']<= (source.get('page_end') or source['page_start']) and hit['page_end']>=source['page_start']):return False
        if source.get('section') and source['section']!=(hit.get('section') or ''):return False
        return True
    result={f'source_hit@{k}':int(any(matches(h,s) for h in hits[:k] for s in expected)) if top_k>=k else None for k in (1,3,5)}
    docs=[s for s in expected if s.get('document_id') or s.get('file_name')]
    if docs:
        result.update({f'document_hit@{k}':int(any(h.get('document_id')==s['document_id'] if s.get('document_id') else h.get('file_name')==s['file_name'] for h in hits[:k] for s in docs)) if top_k>=k else None for k in (1,3,5)})
    rank=next((i+1 for i,h in enumerate(hits) if any(matches(h,s) for s in expected)),None)
    result.update(expected_source_rank=rank,reciprocal_rank=1/rank if rank else 0)
    return result


class RAGComparisonService:
    def __init__(self,runs,llm_factory=MiniMaxLLMProvider,reranker_factory=LocalReranker):
        self.runs,self.llm_factory=runs,llm_factory
        self.questions=QuestionStore(runs)
        self.reranker_factory=reranker_factory;self.rerankers={}
        self.futures={}
        self.recover()

    def recover(self):
        with self.runs.db() as store:
            for table in ('rag_comparison_runs','rag_batches'):
                for r in store.all(table):
                    if r['status'] not in ('RUNNING','QUEUED'):continue
                    try:alive=psutil.Process(r['owner_pid']).create_time()==r['owner_started']
                    except psutil.Error:alive=False
                    if not alive:
                        r.update(status='INTERRUPTED',finished_at=now())
                        store.put(table,r['comparison_run_id'] if table=='rag_comparison_runs' else r['batch_id'],r)

    def get(self,key,table='rag_comparison_runs',public=True):
        with self.runs.db() as store:r=store.get(table,key)
        if not r:raise KeyError('Comparison unavailable')
        if table=='rag_comparison_runs':
            r.setdefault('rag_pipeline_mode','BASELINE')
            r={**full_document_defaults(),**r}
            if 'candidate_policy_version' not in r:
                r.setdefault('candidate_top_n',r.get('top_k',5))
                r.setdefault('max_context_sources',r.get('top_k',5))
                r.setdefault('candidate_policy_version','legacy-raw-top-k')
                r.setdefault('context_document_ids',sorted({s['document_id'] for s in r.get('sources',[])}))
        return {k:v for k,v in r.items() if k not in ('index_snapshot','config_snapshot','owner_pid','owner_started')} if public else r

    def put(self,r,table='rag_comparison_runs'):
        with self.runs.db() as store:store.put(table,r['comparison_run_id'] if table=='rag_comparison_runs' else r['batch_id'],r)

    def prepare(self,run_id,question='',question_id=None,document_id=None,strategy='structure',top_k=None,chunking_run_id=None,rag_scope=RAGScope.ALL_DOCUMENTS,selected_document_id=None,candidate_top_n=None,max_context_sources=None,context_token_budget=None,minimum_candidate_score=None,rag_pipeline_mode=RAGPipelineMode.BASELINE,rerank_threshold=None,point_only=False,claim_support_threshold=None):
        rag_scope=RAGScope(rag_scope)
        rag_pipeline_mode=RAGPipelineMode(rag_pipeline_mode)
        rerank_threshold=self.runs.config.reranker.threshold if rerank_threshold is None else rerank_threshold
        if not 0<=rerank_threshold<=1:raise ValueError("Rerank threshold: 0..1")
        if selected_document_id and document_id and selected_document_id!=document_id:raise ValueError('Conflicting selected documents')
        document_id=selected_document_id or document_id
        if top_k is not None and (type(top_k) is not int or not 1<=top_k<=10):raise ValueError('Legacy Top-K: 1–10')
        if top_k is not None and max_context_sources is not None and top_k!=max_context_sources:raise ValueError('Top-K alias conflicts with max_context_sources')
        candidate_top_n=self.runs.config.candidate_top_n if candidate_top_n is None else candidate_top_n
        max_context_sources=max_context_sources if max_context_sources is not None else top_k if top_k is not None else self.runs.config.max_context_sources or self.runs.config.top_k
        context_token_budget=self.runs.config.llm.context_budget if context_token_budget is None else context_token_budget
        minimum_candidate_score=self.runs.config.minimum_candidate_score if minimum_candidate_score is None else minimum_candidate_score
        if strategy not in ('fixed','structure') or type(candidate_top_n) is not int or not 1<=candidate_top_n<=100:raise ValueError('Candidates: 1–100')
        if type(max_context_sources) is not int or not 1<=max_context_sources<=20:raise ValueError('Context sources: 1–20')
        if type(context_token_budget) is not int or not 256<=context_token_budget<=64000:raise ValueError('Context budget: 256–64000')
        if minimum_candidate_score is not None and not -1<=minimum_candidate_score<=1:raise ValueError('Invalid score threshold')
        q=self.questions.get(question_id) if question_id else None
        if q:question=q['question']
        if not isinstance(question,str) or not question.strip() or len(question)>4000:raise ValueError('Введите вопрос длиной 1–4000 символов')
        run=self.runs.get(run_id)
        if run['status'] in ('QUEUED','RUNNING'):raise ValueError('Дождитесь завершения обработки документов')
        views=ResultsService(self.runs);snapshot=views.snapshot(run_id)
        if document_id and not any(f['document_id']==document_id for f in views.files(run_id)):raise ValueError('Документ не принадлежит запуску')
        index=copy.deepcopy(snapshot.get('indexes',{}).get(strategy))
        if chunking_run_id and (not index or index['run_id']!=chunking_run_id):raise ValueError('Активная версия индекса изменилась. Обновите выбор.')
        cfg=Config(**run['config_json']);cfg.llm=self.runs.config.llm.model_copy(deep=True)
        cfg.reranker=self.runs.config.reranker.model_copy(deep=True)
        if claim_support_threshold is not None:
            cfg.llm=cfg.llm.model_copy(update={"claim_support_threshold":claim_support_threshold})
            cfg.llm=LLMConfig.model_validate(cfg.llm.model_dump() | {"api_key":cfg.llm.api_key})
        cfg.context_expansion_budget=self.runs.config.context_expansion_budget
        cfg.context_diversity_penalty=self.runs.config.context_diversity_penalty
        full_defaults=full_document_defaults()
        full_defaults.update(full_document_id=document_id,full_document_status='NOT_RUN')
        from .rechunk import partitions
        return dict(**full_defaults,rag_pipeline_mode=rag_pipeline_mode.value,rerank_threshold=rerank_threshold,
            original_question=question,retrieval_query=question,reranker_model=cfg.reranker.model,rerank_duration_ms=0,vector_retrieval_duration_ms=0,reranker_settings=cfg.reranker.model_dump(),
            candidates_after_cleanup=0,candidates_after_rerank=0,candidate_trace=[],rewrite_used=False,rewrite_status='DISABLED',
            rewrite_duration_ms=0,rewrite_fallback=False,point_only=point_only,comparison_run_id=uuid4().hex,processing_run_id=run_id,question_id=question_id,question_text=question,
            rag_scope=rag_scope.value,selected_document_id=document_id,corpus_id=snapshot.get('corpus',{}).get('corpus_id'),
            index_versions_used=[{'collection':p['collection'],'run_id':p['run_id'],'document_ids':p['document_ids'],'chunks':len(p['chunk_ids'])} for p in partitions(index) if rag_scope==RAGScope.ALL_DOCUMENTS or document_id in p['document_ids']] if index else [],
            corpus_chunks_eligible=len(index.get('chunk_ids',[])) if index else 0,documents_represented=[],
            scope_type='DOCUMENT' if rag_scope==RAGScope.SELECTED_DOCUMENT else 'CORPUS',scope_id=document_id if rag_scope==RAGScope.SELECTED_DOCUMENT else snapshot.get('corpus',{}).get('corpus_id'),document_id=document_id,
            llm_provider=cfg.llm.provider,llm_model=cfg.llm.model,generation_settings_json=cfg.llm.model_dump(exclude={'api_key_env'}),
            prompt_version='day22-exhaustive-1',base_system=BASE_SYSTEM,retrieval_strategy=strategy,chunking_run_id=index.get('run_id') if index else None,
            top_k=max_context_sources,candidate_top_n=candidate_top_n,max_context_sources=max_context_sources,context_token_budget=context_token_budget,
            minimum_candidate_score=minimum_candidate_score,candidates_retrieved=0,candidates_after_dedup=0,candidate_document_ids=[],
            contexts_used=0,context_document_ids=[],context_candidate_metadata_json=[],final_source_ids=[],candidate_policy_version='day22-candidates-1',
            embedding_model=index.get('embedding',{}) if index else {},retrieved_chunk_ids_json=[],used_chunk_ids_json=[],
            retrieved_count=0,used_count=0,context_tokens=0,context_token_count_method=RAGContextBuilder.count_method,
            no_rag_answer='',rag_answer='',no_rag_duration_ms=0,rag_duration_ms=0,retrieval_duration_ms=0,
            no_rag_result=None,rag_result=None,sources=[],retrieved_sources=[],context_text='',source_metrics={},
            expected_answer=q['expected_answer'] if q else '',expected_sources=q['expected_sources'] if q else [],
            expected_unanswerable=q.get('expected_unanswerable',False) if q else False,
            status='QUEUED',error_json=[],created_at=now(),started_at=None,finished_at=None,
            index_snapshot=index,config_snapshot=cfg.model_dump(mode='json'),owner_pid=os.getpid(),owner_started=psutil.Process().create_time())

    def start(self,run_id,background=True,**kwargs):
        record=self.prepare(run_id,**kwargs);self.put(record);key=record['comparison_run_id']
        if background:self.futures[key]=self.runs.executor.submit(self.execute,key)
        else:self.execute(key)
        return key

    def execute(self,key,progress=None):
        try:
            return self._execute(key,progress)
        except Exception:
            r=self.get(key,public=False)
            r['rag_result']=LLMResult(model=r['llm_model'],error={'code':'COMPARISON_ERROR'}).model_dump()
            if not r.get('no_rag_result') and not r.get('point_only'):
                r['no_rag_result']=LLMResult(model=r['llm_model'],error={'code':'COMPARISON_ERROR'}).model_dump()
            r.update(status='PARTIAL' if r.get('no_rag_answer') or r.get('full_document_answer') else 'FAILED',finished_at=now(),error_json=[{'code':'COMPARISON_ERROR'}])
            self.put(r)
            return self.get(key)

    def _execute(self,key,progress=None):
        progress=progress or (lambda stage: None)
        r=self.get(key,public=False);r.update(status='RUNNING',started_at=now());self.put(r)
        cfg=Config(**r['config_snapshot'])
        cfg.llm.api_key=self.runs.config.llm.api_key
        if r.get('reference_prior_claims'):
            from .policy_predicate import execute_reference
            return execute_reference(self,r,cfg,progress)
        provider=self.llm_factory(cfg.llm)
        def generate(context,context_type='rag'):
            progress('generating')
            r['generation_call_count']+=1
            try:
                request=LLMRequest(question=r.get('chat_generation_question',r['question_text']),context=context,context_type=context_type)
                if r.get('chat_input_budget'):
                    def fits():return len((BASE_SYSTEM+request.user_content()).encode('utf-8'))+512<=r['chat_input_budget']
                    if not fits():
                        request.question=r['chat_generation_base'];r['chat_history_dropped']=True
                    if not fits():return LLMResult(model=cfg.llm.model,error={'code':'CHAT_CONTEXT_BUDGET_EXCEEDED'}).model_dump()
                from .diagnostic_trace import record
                record('generation_request', system=BASE_SYSTEM, user=request.user_content(),
                       question=request.question, context_type=request.context_type)
                result=provider.generate(request).model_dump()
                record('generation_result', result=result)
                r['minimax_requests_count']+=result.get('request_count',0)
                return result
            except Exception:return LLMResult(model=cfg.llm.model,error={'code':'GENERATION_ERROR'}).model_dump()
        if not r.get('point_only'):
            r['no_rag_result']=generate(None)
            r['no_rag_answer']=r['no_rag_result']['text'];r['no_rag_duration_ms']=r['no_rag_result']['duration_ms'];self.put(r)
            try:
                full=FullDocumentContextBuilder(self.runs).build(r['processing_run_id'],r['document_id'],
                    cfg.llm.full_document_context_budget,r['question_text'],cfg.llm.max_output_tokens)
                r.update(full_document_context=full,full_document_status=full['status'],
                    full_document_id=full['document_id'],full_document_pages=full['page_count'],
                    full_document_context_tokens=full['token_count'])
                self.put(r)
                if full['status']=='READY':
                    answer=generate(full['text'],'full_document')
                    r.update(full_document_result=answer,full_document_answer=answer['text'],
                        full_document_duration_ms=answer['duration_ms'],full_document_error_json=answer['error'],
                        full_document_status=answer['status'] if answer['status'] in ('SUCCESS','TRUNCATED','NOT_CONFIGURED') else 'API_ERROR')
                    success=answer['status']=='SUCCESS'
                    r['exhaustive']=dict(mode='EXHAUSTIVE_NO_RAG',path='FAST',scope='DOCUMENT' if r['document_id'] else 'ALL_DOCUMENTS',
                        documents_total=full.get('documents_total',1),documents_covered=full.get('documents_total',1) if success else 0,
                        pages_total=full['page_count'],pages_covered=full['page_count'] if success else 0,coverage_percent=100 if success else 0,
                        batches_total=1,batches_completed=1 if success else 0,input_tokens_processed=answer['usage'].get('prompt_tokens',0),
                        intermediate_output_tokens=0,final_synthesis_tokens=answer['usage'].get('completion_tokens',0),
                        minimax_requests=answer.get('request_count',0),map_requests=0,cache_hits=0,duration_ms=answer['duration_ms'],status=r['full_document_status'])
                elif full['status']=='CONTEXT_TOO_LARGE':
                    ExhaustiveNoRAG(self.runs,self.llm_factory).run(r,cfg.llm,self.put)
            except Exception:
                r.update(full_document_status='BUILD_ERROR',full_document_error_json={'code':'FULL_DOCUMENT_BUILD_ERROR'})
            self.put(r)
        if r['rag_pipeline_mode']=='REWRITE_RERANK':
            progress('rewriting')
            rewrite=QueryRewriteService(self.runs,cfg.llm,self.llm_factory).rewrite(r.get('chat_search_question') or r['question_text'])
            r.update(retrieval_query=rewrite.retrieval_query,rewrite_status=rewrite.status,rewrite_used=rewrite.status=='SUCCESS',
                rewrite_duration_ms=rewrite.duration_ms,rewrite_fallback=rewrite.fallback,rewrite_result=rewrite.model_dump())
            r['minimax_requests_count']+=rewrite.request_count
            r['generation_call_count']+=int(rewrite.request_count>0)
            if r.get('chat_require_rewrite') and rewrite.status not in ('SUCCESS','GUARDED_FALLBACK'):
                r['rag_result']=LLMResult(model=cfg.llm.model,error={'code':'QUERY_REWRITE_UNAVAILABLE'}).model_dump()
                r.update(status='FAILED',finished_at=now(),error_json=[r['rag_result']['error']])
                self.put(r)
                return self.get(key)
        started=time.perf_counter();pipeline=None;hits=[];error=None;candidates=[];ranked_hits=[]
        progress('retrieving')
        try:
            index=r['index_snapshot']
            if r['rag_scope']=='SELECTED_DOCUMENT' and not r['selected_document_id']:
                error='DOCUMENT_REQUIRED'
            elif not index or index.get('status') not in ('SUCCESS','PARTIAL') or not index.get('chunk_ids'):
                error='NO_ACTIVE_INDEX'
            else:
                with self.runs.operation_lock():
                    try:
                        pipeline=self.runs.pipeline_factory(cfg)
                        pipeline.validate_current_corpus(ResultsService(self.runs).snapshot(r['processing_run_id']))
                        pipeline.resources()
                        policy=RetrievalPolicy.model_validate(r['retrieval_policy_json']) if r.get('retrieval_policy_json') else None
                        if policy:sync_payload(pipeline.vectors,index,policy.document_metadata)
                        vector_started=time.perf_counter()
                        vector=pipeline.provider.encode([r['retrieval_query']])[0]
                        hits=SemanticRetriever(pipeline.provider,pipeline.vectors,pipeline.store).retrieve_vector(vector,index,r.get('candidate_top_n',r['top_k']),retrieval_document(r['rag_scope'],r['selected_document_id']),policy=policy)
                        r['vector_retrieval_duration_ms']=(time.perf_counter()-vector_started)*1000
                        for hit in hits:
                            hit['source_pages']=[{'recognition_id':s['page']['recognition_id'],'page_number':s['page']['page_number']} for s in hit.get('provenance',{}).get('sources',[])]
                            hit.pop('provenance',None)
                        processor=CandidateProcessor(pipeline.store,index,r['retrieval_strategy'],min(cfg.context_expansion_budget,r.get('context_token_budget',cfg.llm.context_budget)))
                        cleaned,diagnostics=processor.dedup(hits,r.get('minimum_candidate_score'))
                        r.update(candidates_after_cleanup=len(cleaned),candidates_after_dedup=len(cleaned),dedup_diagnostics=diagnostics)
                        ranked_hits=cleaned
                        raw_by_id={h['chunk_id']:h for h in hits}
                        trace=[dict(chunk_id=d['chunk_id'],retrieval_rank=d['rank'],retrieval_score=raw_by_id[d['chunk_id']]['score'],
                            file_title=raw_by_id[d['chunk_id']].get('file_name',''),section=raw_by_id[d['chunk_id']].get('section',''),
                            page_start=raw_by_id[d['chunk_id']]['page_start'],accepted=False,filter_reason=d['reason']) for d in diagnostics]
                        if r['rag_pipeline_mode']!='BASELINE':
                            progress('reranking')
                            provider_key=json.dumps(cfg.reranker.model_dump(),sort_keys=True)
                            reranker=self.rerankers.setdefault(provider_key,self.reranker_factory(cfg.reranker)) if provider_key not in self.rerankers else self.rerankers[provider_key]
                            inputs=[RerankCandidate(chunk_id=h['chunk_id'],document_id=h['document_id'],text=h['text'],
                                file_title=h.get('file_name',''),section=h.get('section',''),page_start=h['page_start'],page_end=h['page_end'],
                                retrieval_rank=h['rank'],retrieval_score=h['score']) for h in cleaned]
                            tick=time.perf_counter()
                            try:reranked=reranker.rerank(r.get('chat_search_question') or r['question_text'],inputs)
                            finally:r['rerank_duration_ms']=(time.perf_counter()-tick)*1000
                            originals={h['chunk_id']:h for h in cleaned};ranked_hits=[]
                            if len(reranked)!=len(cleaned) or {c.chunk_id for c in reranked}!=set(originals):raise RerankerUnavailable('INVALID_RERANK_OUTPUT')
                            for c in reranked:
                                c.accepted=c.rerank_score>=r['rerank_threshold']
                                c.filter_reason=None if c.accepted else 'BELOW_RERANK_THRESHOLD'
                                trace.append(c.model_dump(exclude={'text','metadata'}))
                                if c.accepted:ranked_hits.append({**originals[c.chunk_id],'rerank_score':c.rerank_score,'rerank_rank':c.rerank_rank})
                            r['reranker_status']=reranker.status() if hasattr(reranker,'status') else {'status':'READY'}
                            if not ranked_hits:error='NO_RELEVANT_CONTEXT'
                        else:
                            trace.extend(dict(chunk_id=h['chunk_id'],document_id=h['document_id'],file_title=h.get('file_name',''),section=h.get('section',''),
                                page_start=h['page_start'],page_end=h['page_end'],retrieval_rank=h['rank'],retrieval_score=h['score'],
                                rerank_rank=None,rerank_score=None,accepted=True,filter_reason=None) for h in cleaned)
                        r.update(candidate_trace=trace,candidates_after_rerank=len(ranked_hits))
                        candidates,processing=processor.build(ranked_hits,precleaned=True)
                        if policy:candidates=policy.guard(candidates,'final_context')
                        r.update({k:v for k,v in processing.items() if k not in ('candidates_after_dedup','dedup_diagnostics')})
                    finally:
                        if pipeline:pipeline.close()
        except RerankerUnavailable:
            error='RERANKER_UNAVAILABLE'
        except Exception:
            error='RETRIEVAL_ERROR'
        finally:
            r['retrieval_duration_ms']=(time.perf_counter()-started)*1000
        r['retrieved_sources']=hits;r['retrieved_count']=len(hits);r['retrieved_chunk_ids_json']=[h['chunk_id'] for h in hits]
        r['documents_represented']=sorted({h['document_id'] for h in hits})
        r.update(candidates_retrieved=len(hits),candidate_document_ids=r['documents_represented'])
        r['retrieval_source_metrics']=source_metrics(hits,r['expected_sources'],r.get('candidate_top_n',r['top_k']))
        r['source_metrics']=source_metrics(hits if r['rag_pipeline_mode']=='BASELINE' else ranked_hits,r['expected_sources'],r.get('candidate_top_n',r['top_k']))
        budget=r.get('context_token_budget',cfg.llm.context_budget)
        selected=ContextSelector().select(candidates,r.get('max_context_sources',r['top_k']),budget,RAGContextBuilder(),cfg.context_diversity_penalty)
        context=RAGContextBuilder().build(selected,budget)
        r.update(contexts_used=context['used_count'],context_document_ids=sorted({s['document_id'] for s in context['sources']}),
            final_source_ids=[s['source_id'] for s in context['sources']],
            context_candidate_metadata_json=[c.model_dump(exclude={'anchor','context_text'}) for c in candidates])
        r.update(context_text=context['text'],context_tokens=context['token_count'],used_count=context['used_count'],sources=context['sources'],used_chunk_ids_json=context['used_chunk_ids'])
        if r.get('revalidation_claim') and not error:
            from .grounding import GroundingValidator, GroundedClaim, AnswerContract
            claim=GroundedClaim(claim_id='C1',text=r['revalidation_claim'],
                supporting_source_ids=[s['reference'] for s in context['sources']])
            provider_key=json.dumps(cfg.reranker.model_dump(),sort_keys=True)
            if provider_key not in self.rerankers:self.rerankers[provider_key]=self.reranker_factory(cfg.reranker)
            with self.runs.db() as store:
                ground,citations=GroundingValidator(CitationBuilder(store),self.rerankers[provider_key],cfg.llm.claim_support_threshold).validate(
                    AnswerContract(answer=claim.text,claims=[claim]),context['sources'])
            r.update(claims_json=[claim.model_dump()],citations_json=[c.model_dump() for c in citations] if ground.grounded else [],
                grounding_status='GROUNDED' if ground.grounded else 'INSUFFICIENT_CONTEXT',grounding_result=ground.model_dump(),
                refusal_reason=None if ground.grounded else 'CLAIM_NOT_RECONFIRMED',status='COMPLETED',finished_at=now(),
                rag_answer=claim.text if ground.grounded else '',
                rag_result=LLMResult(model=cfg.llm.model,status='SUCCESS' if ground.grounded else 'INSUFFICIENT_CONTEXT',
                    text=claim.text if ground.grounded else '').model_dump())
            self.put(r);return self.get(key)
        self.put(r)
        if error in (None,'NO_RELEVANT_CONTEXT') and not context['sources']:
            ground=GroundingResult(refusal_reason=error or 'ZERO_CONTEXTS')
            r.update(grounding_status='INSUFFICIENT_CONTEXT',grounding_result=ground.model_dump(),
                grounding_diagnostics=[{'relevance_gate':'REJECTED','cause':ground.refusal_reason}],
                claims_json=[],citations_json=[],repair_used=False,refusal_reason=ground.refusal_reason,
                claim_support_threshold=cfg.llm.claim_support_threshold)
            r['rag_result']=LLMResult(model=cfg.llm.model,status='INSUFFICIENT_CONTEXT',text=REFUSAL).model_dump()
        elif error:
            r['rag_result']=LLMResult(model=cfg.llm.model,status=error or 'EMPTY_RETRIEVAL',error={'code':error or 'EMPTY_RETRIEVAL'}).model_dump()
        else:
            provider_key=json.dumps(cfg.reranker.model_dump(),sort_keys=True)
            if provider_key not in self.rerankers:self.rerankers[provider_key]=self.reranker_factory(cfg.reranker)
            try:
                with self.runs.db() as store:
                    final_generate=generate
                    r['rag_result']=grounded_generation(r,context['text'],final_generate,CitationBuilder(store),
                        self.rerankers[provider_key],cfg.llm.claim_support_threshold,progress=progress)
            except RerankerUnavailable:
                r.update(grounding_status='ERROR',claims_json=[],citations_json=[])
                r['rag_result']=LLMResult(model=cfg.llm.model,status='RERANKER_UNAVAILABLE',error={'code':'RERANKER_UNAVAILABLE'}).model_dump()
        r['rag_answer']=r['rag_result']['text'];r['rag_duration_ms']=r['rag_result']['duration_ms']
        if r.get('point_only'):
            r.update(status='COMPLETED' if r['rag_result']['status'] in ('SUCCESS','INSUFFICIENT_CONTEXT') else 'FAILED',finished_at=now(),error_json=[r['rag_result']['error']] if r['rag_result']['error'] else [])
            self.put(r);return self.get(key)
        branches=[r['no_rag_result'],r['rag_result']]
        r['error_json']=[dict(branch=name,**result['error']) for name,result in zip(('NO_RAG','RAG'),branches) if result['error']]
        full_result=r['full_document_result'] or {'status':r['full_document_status'],'text':''}
        branches.append(full_result)
        if r['full_document_status']!='SUCCESS':
            r['error_json'].append(dict(branch='FULL_DOCUMENT',code=r['full_document_error_json'].get('code',r['full_document_status'])))
        r['status']='COMPLETED' if all(x['status'] in ('SUCCESS','INSUFFICIENT_CONTEXT') for x in branches) else 'PARTIAL' if any(x['status']=='SUCCESS' or x['text'] for x in branches) else 'FAILED'
        r['finished_at']=now();self.put(r)
        return self.get(key)

    def start_all(self,run_id,compare_modes=False,**kwargs):
        questions=self.questions.list()
        if not questions:raise ValueError('Добавьте контрольные вопросы')
        if compare_modes:
            kwargs.pop('rag_pipeline_mode',None)
            records=[self.prepare(run_id,question_id=q['question_id'],rag_pipeline_mode=mode,point_only=True,**kwargs) for q in questions for mode in RAGPipelineMode]
        else:records=[self.prepare(run_id,question_id=q['question_id'],**kwargs) for q in questions]
        for r in records:self.put(r)
        batch=dict(batch_id=uuid4().hex,day23_evaluation=compare_modes,processing_run_id=run_id,status='QUEUED',comparison_ids=[r['comparison_run_id'] for r in records],
            total=len(records),current=0,completed=0,failed=0,partial=0,created_at=now(),finished_at=None,
            owner_pid=os.getpid(),owner_started=psutil.Process().create_time())
        self.put(batch,'rag_batches')
        def work():
            batch['status']='RUNNING'
            for n,r in enumerate(records,1):
                batch['current']=n;self.put(batch,'rag_batches')
                result=self.execute(r['comparison_run_id'])
                field='completed' if result['status']=='COMPLETED' else 'partial' if result['status']=='PARTIAL' else 'failed'
                batch[field]+=1;self.put(batch,'rag_batches')
            batch.update(status='COMPLETED' if batch['completed']==batch['total'] else 'PARTIAL' if batch['completed'] or batch['partial'] else 'FAILED',finished_at=now())
            self.put(batch,'rag_batches')
        self.futures[batch['batch_id']]=self.runs.executor.submit(work)
        return batch['batch_id']
