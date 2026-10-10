"""Independent direct Ollama experiments in the existing SQLite store/executor."""
import time
import uuid
import os
import psutil
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from ..direct_generation import DirectLocalConfig, DirectGenerationRequest, DirectPromptVersion
from ..local_llm import LocalLLMProvider
from .benchmark_resources import Resources
from .runs import now

from .judicial_document import JudicialDocument, read_document, task_input, TASK_VERSION, QUALITY_LABELS

TABLE = 'local_llm_experiment_runs'

class DirectExperimentOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r'^[^\s\x00-\x1f]+$')]
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    context_window: int = Field(ge=4096, le=32768, strict=True)
    max_output_tokens: int = Field(ge=128, le=8192, strict=True)
    prompt_version: DirectPromptVersion
    seed: int = Field(ge=-2147483648, le=2147483647, strict=True)

    def profile(self, base):
        return DirectLocalConfig.model_validate(base.model_dump() | self.model_dump() | {'enabled': True, 'repair_reserve': 0})

class DirectExperimentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] | None = None
    document_version: str | None = None
    document_sha256: str | None = None
    options: DirectExperimentOptions

    @model_validator(mode='after')
    def input_source(self):
        if self.document_version:
            if self.question is not None or not self.document_sha256:
                raise ValueError('Document input requires its SHA256 and no free question')
        elif self.question is None or self.document_sha256:
            raise ValueError('Select a document')
        return self

class QualityReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scores: dict[str, Literal['pass','partial','fail','not_evaluated']] = Field(default_factory=dict)
    note: str = Field('', max_length=4000)

    @model_validator(mode='after')
    def known_criteria(self):
        if set(self.scores)-set(QUALITY_LABELS):
            raise ValueError('Unknown quality criterion')
        return self

class LocalLLMExperimentRun(BaseModel):
    id: str
    created_at: str
    question: str
    profile: DirectLocalConfig
    kind: str = 'direct_local'
    task_version: str | None = None
    document: JudicialDocument | None = None
    quality_review: dict = Field(default_factory=dict)
    status: str = 'QUEUED'
    finished: bool = False
    owner_pid: int
    owner_started: float
    quantization: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    exact_input: dict | None = None
    transport_attempted: bool = False
    input_budget: dict = Field(default_factory=dict)
    answer: str = ''
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    load_duration: int | None = None
    prompt_eval_duration: int | None = None
    eval_duration: int | None = None
    ollama_total_duration: int | None = None
    generation_duration: float | None = None
    total_duration: float | None = None
    tokens_per_second: float | None = None
    ram_peak: int | None = None
    vram_peak: int | None = None
    error: dict = Field(default_factory=dict)

class DirectExperimentService:
    def __init__(self, runs):
        self.runs = runs
        # Never recover another live application's jobs.
        for row in self.history():
            if row['finished']:
                continue
            try:
                alive = abs(psutil.Process(row['owner_pid']).create_time() - row['owner_started']) < .1
            except psutil.Error:
                alive = False
            if not alive:
                row.update(status='INTERRUPTED', finished=True, error={'code': 'PROCESS_INTERRUPTED'})
                self.put(LocalLLMExperimentRun.model_validate(row))

    def put(self, row):
        with self.runs.db() as store:
            store.put(TABLE, row.id, row.model_dump(mode='json'))

    def get(self, key):
        with self.runs.db() as store:
            row = store.get(TABLE, key)
        if row is None:
            raise KeyError('Experiment not found')
        return row

    def history(self):
        with self.runs.db() as store:
            rows = store.all(TABLE)
        return sorted(rows, key=lambda r: r['created_at'], reverse=True)

    def start(self, request):
        profile = request.options.profile(self.runs.config.llm.local)
        document = read_document(self.runs, request.document_version) if request.document_version else None
        if document and document.sha256 != request.document_sha256:
            raise ValueError('Document changed: reload the selected normalized text')
        question = task_input(document) if document else request.question
        budget = {}
        if document:
            budget = LocalLLMProvider(profile).input_budget(DirectGenerationRequest(question=question))
            if budget['estimated_remaining'] < 0:
                raise ValueError(
                    f"Недостаточно контекста: вход {budget['total_estimated_input']} + "
                    f"резерв шаблона {budget['template_safety_reserve']} + "
                    f"ответ {profile.max_output_tokens} = {budget['estimated_required']} "
                    f"> num_ctx {profile.context_window}. Метод: {budget['method']}. "
                    "Увеличьте num_ctx или уменьшите num_predict. Текст не обрезается.")
        row = LocalLLMExperimentRun(id=uuid.uuid4().hex, created_at=now(), question=question,
            document=document, kind='judicial_act' if document else 'direct_local', task_version=TASK_VERSION if document else None,
            profile=profile, input_budget=budget, owner_pid=os.getpid(), owner_started=psutil.Process().create_time())
        self.put(row)
        try:
            self.runs.executor.submit(self.execute, row.id)
        except Exception:
            row.status='ERROR'; row.finished=True; row.error={'code': 'QUEUE_UNAVAILABLE'}
            self.put(row)
            raise
        return row.id

    def execute(self, key):
        row = LocalLLMExperimentRun.model_validate(self.get(key))
        started = time.perf_counter()
        row.status = 'RUNNING'; self.put(row)
        try:
            if row.document and task_input(row.document) != row.question:
                raise ValueError('Frozen document input mismatch')
            provider = LocalLLMProvider(row.profile, operation_lock=self.runs.operation_lock,
                                        ocr_model=self.runs.config.recognition.model)
            with Resources() as resources:
                with provider.generation_session():
                    result = provider.generate(DirectGenerationRequest(question=row.question, prompt_version=row.profile.prompt_version))
            metrics = result.diagnostics.get('ollama', {})
            row.answer=result.text; row.status=result.status; row.error=result.error
            row.quantization=result.diagnostics.get('details', {}).get('quantization_level')
            row.input_budget=result.diagnostics.get('input_budget', row.input_budget)
            row.exact_input=result.diagnostics.get('exact_input')
            row.transport_attempted=result.diagnostics.get('transport_attempted', False)
            if row.exact_input:
                row.system_prompt=row.exact_input['messages'][0]['content']
                row.user_prompt=row.exact_input['messages'][1]['content']
            row.prompt_tokens=metrics.get('prompt_eval_count'); row.output_tokens=metrics.get('eval_count')
            for field in ('load_duration', 'prompt_eval_duration', 'eval_duration'):
                setattr(row, field, metrics.get(field))
            row.ollama_total_duration=metrics.get('total_duration')
            row.generation_duration=result.duration_ms / 1000
            if row.eval_duration and row.eval_duration > 0 and row.output_tokens is not None:
                row.tokens_per_second=row.output_tokens * 1_000_000_000 / row.eval_duration
            measured=resources.summary()
            row.ram_peak=measured.get('rss_peak_bytes'); row.vram_peak=measured.get('device_vram_peak_mib')
        except Exception:
            row.status='ERROR'; row.error={'code': 'LOCAL_EXPERIMENT_FAILED'}
        finally:
            row.total_duration=time.perf_counter()-started; row.finished=True; self.put(row)

    def review(self, key, review):
        # Finished results are immutable except for this explicit human assessment.
        with self.runs.mutation_lock:
            row = LocalLLMExperimentRun.model_validate(self.get(key))
            if not row.finished or row.kind != 'judicial_act':
                raise ValueError('Review requires a finished judicial analysis')
            row.quality_review = review.model_dump() | {'updated_at': now(), 'method': 'manual'}
            self.put(row)
        return row.quality_review

def comparison(row, baseline):
    if not baseline or row['id'] == baseline['id']:
        return None
    judicial = row.get('kind') == 'judicial_act' or baseline.get('kind') == 'judicial_act'
    fair = row['question'] == baseline['question']
    if judicial:
        x,y=row.get('document') or {},baseline.get('document') or {}
        fair = bool(fair and row.get('kind') == baseline.get('kind') == 'judicial_act'
                    and row.get('task_version') == baseline.get('task_version')
                    and x.get('sha256') and x.get('sha256') == y.get('sha256') and x.get('text') == y.get('text'))
    deltas = {}
    if fair and row['finished'] and baseline['finished']:
        for field in ('generation_duration', 'tokens_per_second', 'output_tokens', 'ram_peak', 'vram_peak'):
            a,b=baseline.get(field),row.get(field)
            if a is not None and b is not None:
                deltas[field]={'before': a, 'after': b, 'percent': (b-a)/a*100 if a else None}
    return {'fair': fair, 'deltas': deltas, **({'judicial': True} if judicial else {})}
