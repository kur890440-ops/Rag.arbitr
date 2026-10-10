"""Manual Local experiments on existing RAG records; no mutable global options."""
import copy
import json
import time
import uuid
import os
import psutil
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from ..llm import LocalLLMConfig, LLMResult, LLMRequest
from ..local_diagnostics import input_budget
from ..generation_prompts import PromptVersion
from ..local_llm import LocalLLMProvider
from ..reranking import RerankerConfig
from ..recognition.runtime import RuntimeUnavailable
from .local_benchmark import replay_snapshot, verified_snapshot, snapshot_digest, demo_variant, metric_delta
from .benchmark_resources import Resources
from .runs import now


class ExperimentOptions(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    model:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=200,pattern=r'^[^\s\x00-\x1f]+$')]
    temperature:float=Field(ge=0,le=2,allow_inf_nan=False)
    context_window:int=Field(ge=4096,le=32768,strict=True)
    max_output_tokens:int=Field(ge=128,le=8192,strict=True)
    prompt_version:PromptVersion
    seed:int=Field(ge=-2147483648,le=2147483647,strict=True)

    def profile(self,base):
        return LocalLLMConfig.model_validate(base.model_dump() | self.model_dump() | {'enabled':True})


class ExperimentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    processing_run_id:str
    question:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=4000)]
    options:ExperimentOptions
    reuse_run_id:str|None=None


class ManualExperimentService:
    def __init__(self,runs,rag):self.runs,self.rag=runs,rag

    def history(self):
        with self.runs.db() as store:
            rows=[r for r in store.all('rag_comparison_runs') if r.get('manual_experiment')]
        return sorted(rows,key=lambda r:r['created_at'],reverse=True)

    def get(self,key):
        r=self.rag.get(key,public=False)
        if not r.get('manual_experiment'):raise KeyError('Experiment not found')
        return r

    def start(self,request:ExperimentRequest):
        profile=request.options.profile(self.runs.config.llm.local)
        if request.reuse_run_id:
            source=self.get(request.reuse_run_id)
            if not source.get('experiment_finished'):
                raise ValueError('Wait for the selected experiment to finish')
            snapshot=verified_snapshot(source['experiment_snapshot'])
            if request.question!=snapshot['question'] or request.processing_run_id!=snapshot['processing_run_id']:
                raise ValueError('Reuse requires the original question and corpus')
            budget=input_budget(LLMRequest(question=request.question,context=snapshot['text'],context_type='grounded_rag'),profile)
            if budget['estimated_remaining']<0:
                raise ValueError('Выбранный context snapshot не помещается в num_ctx. Увеличьте num_ctx или уменьшите num_predict. Проверка: консервативная UTF-8 estimate, не exact tokenizer.')
            r=copy.deepcopy(source)
            for field in ('generation_runs','rag_result','error_json','experiment_model_info','experiment_resources'):
                r.pop(field,None)
            r.update(comparison_run_id=uuid.uuid4().hex,status='QUEUED',created_at=now(),started_at=None,
                     finished_at=None,rag_answer='',reuse_run_id=request.reuse_run_id,
                     owner_pid=os.getpid(),owner_started=psutil.Process().create_time())
        else:
            r=self.rag.prepare(request.processing_run_id,question=request.question,generation_mode='local',
                               point_only=True,rag_pipeline_mode='RERANK')
        # prepare already produces an isolated per-run config snapshot.
        r['config_snapshot']['llm']['local']=profile.model_dump()
        r['generation_settings_json']['local']=profile.model_dump()
        r.update(manual_experiment=True,experiment_options=request.options.model_dump(),llm_model=profile.model,
                 experiment_finished=False)
        self.rag.put(r)
        self.runs.executor.submit(self.execute,r['comparison_run_id'])
        return r['comparison_run_id']

    def execute(self,key):
        r=self.get(key);started=time.perf_counter()
        profile=LocalLLMConfig.model_validate(r['generation_settings_json']['local'])
        try:
            r.update(status='RUNNING',started_at=now());self.rag.put(r)
            info=LocalLLMProvider(profile).inspect()
            with Resources() as resources:
                if r.get('reuse_run_id'):
                    snapshot=verified_snapshot(r['experiment_snapshot'])
                    settings=RerankerConfig.model_validate(snapshot['reranker_settings'])
                    provider_key=json.dumps(settings.model_dump(),sort_keys=True)
                    if provider_key not in self.rag.rerankers:
                        self.rag.rerankers[provider_key]=self.rag.reranker_factory(settings)
                    g=replay_snapshot(self.runs,snapshot,profile,self.rag.rerankers[provider_key],LocalLLMProvider,capture_diagnostics=True)
                    # Total here measures this execution, not historical retrieval.
                    g['total_ms']=(time.perf_counter()-started)*1000
                    r.update(generation_runs=[g],rag_result=g['result'],rag_answer=g['answer'],
                             status='COMPLETED' if g['result']['status'] in ('SUCCESS','INSUFFICIENT_CONTEXT') else 'FAILED',
                             grounding_status=g['grounding_status'])
                else:
                    self.rag.execute(key)
                    r=self.get(key)
                    if r.get('context_snapshot_id') and r.get('sources'):
                        snapshot=dict(question_id=key,question=r['question_text'],processing_run_id=r['processing_run_id'],
                            retrieval_run_id=key,context_snapshot_id=r['context_snapshot_id'],context_ids=r['used_chunk_ids_json'],
                            context_size=len(r['context_text'].encode('utf-8')),text=r['context_text'],sources=r['sources'],
                            shared_pipeline_ms=0,claim_support_threshold=r['claim_support_threshold'],
                            reranker_settings=r['reranker_settings'],retrieval_policy_json=r.get('retrieval_policy_json'),
                            retrieval_diagnostics={k:copy.deepcopy(r.get(k)) for k in ('candidate_top_n','candidates_retrieved',
                                'candidates_after_cleanup','rerank_output_count','candidates_after_threshold',
                                'candidate_trace','context_selection_diagnostics','expansion_budget_exclusions','retrieval_strategy','chunking_run_id')})
                        snapshot['integrity_sha256']=snapshot_digest(snapshot)
                        r['experiment_snapshot']=verified_snapshot(snapshot)
            r['experiment_model_info']=info
            r['experiment_resources']=resources.summary()
            for g in r.get('generation_runs',[]):
                g.update(profile=profile.model_dump(),resources=resources.summary(),total_ms=(time.perf_counter()-started)*1000)
                g['result'].setdefault('diagnostics',{})['details']=info.get('details',{})
        except Exception as exc:
            code=str(exc) if isinstance(exc,RuntimeUnavailable) and str(exc) in ('MODEL_NOT_INSTALLED','TEXT_LOCAL_MODEL_REQUIRED','MODEL_CONTEXT_LIMIT','DISABLED') else 'LOCAL_EXPERIMENT_FAILED'
            r.update(status='FAILED',rag_result=LLMResult(provider='local',model=profile.model,status='ERROR',
                error={'code':code}).model_dump(),error_json=[{'code':code}])
        r.update(experiment_finished=True,finished_at=now(),total_duration_ms=(time.perf_counter()-started)*1000)
        self.rag.put(r)

    def view(self,r):
        g=copy.deepcopy((r.get('generation_runs') or [{k:r[k] for k in ('grounding_status','grounding_result','grounding_diagnostics','claims_json','citations_json','repair_used') if k in r}])[0])
        g.setdefault('provider','local');g.setdefault('profile',r['generation_settings_json']['local'])
        g.setdefault('result',r.get('rag_result') or LLMResult(provider='local',model=r['llm_model'],status=r['status']).model_dump())
        variant=demo_variant(g,r['comparison_run_id'][:8],'',{})
        snapshot=r.get('experiment_snapshot')
        if snapshot:
            try: snapshot=verified_snapshot(snapshot)
            except (ValueError,KeyError,TypeError):snapshot=None
        from .experiment_diagnostics import diagnostics_view
        diagnostics=diagnostics_view(r,g,snapshot)
        return dict(diagnostics=diagnostics,id=r['comparison_run_id'],question=r['question_text'],processing_run_id=r['processing_run_id'],
                    status=r['status'],finished=r.get('experiment_finished',False) or r['status']=='INTERRUPTED',
                    snapshot=snapshot,variant=variant)

    def compare(self,a,b):
        views=[self.view(self.get(k)) for k in (a,b)]
        x,y=[v['snapshot'] for v in views]
        fair=bool(x and y and views[0]['finished'] and views[1]['finished'] and
            all(x[k]==y[k] for k in ('question','processing_run_id','context_snapshot_id','context_ids','text','sources')))
        deltas={k:metric_delta(views[0]['variant']['metrics'][k],views[1]['variant']['metrics'][k]) for k in views[0]['variant']['metrics']}
        return views,fair,deltas
