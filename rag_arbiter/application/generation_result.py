"""Typed UI projection of existing RAG records; no additional storage or pipeline."""
from typing import Literal
from pydantic import BaseModel, Field
from .grounding import Citation, GroundedClaim


ERROR_MESSAGES = {
    'LOCAL_GENERATION_UNAVAILABLE': 'Локальная модель недоступна. Проверьте Ollama и установленную модель.',
    'LOCAL_CONTEXT_LIMIT': 'Контекст превышает лимит локальной модели.',
    'TIMEOUT': 'Провайдер не ответил вовремя.',
    'NOT_CONFIGURED': 'Облачный провайдер не настроен.',
    'AUTH_ERROR': 'Провайдер отклонил авторизацию.',
    'RATE_LIMIT': 'Достигнут лимит запросов провайдера.',
    'PAYMENT_REQUIRED': 'Для облачного провайдера требуется доступный баланс.',
    'INSUFFICIENT_BALANCE': 'Для облачного провайдера требуется доступный баланс.',
    'APPROVAL_REQUIRED': 'Облачный запрос не выполнялся: ожидается разрешение на передачу данных.',
    'NO_ACTIVE_INDEX': 'Для выбранной стратегии нет готового индекса.',
    'RETRIEVAL_ERROR': 'Не удалось получить RAG-контекст.',
    'RERANKER_UNAVAILABLE': 'Модель проверки источников недоступна.',
    'OUTPUT_LIMIT': 'Ответ превысил лимит генерации.',
    'DOCUMENT_REQUIRED': 'Выберите документ или поиск по всему корпусу.',
}


class GenerationResult(BaseModel):
    provider: str
    model: str
    answer: str = ''
    status: str = 'PENDING'
    generation_ms: float | None = None
    total_ms: float | None = None
    grounding_status: str = 'UNCHECKED'
    grounding_label: Literal['PASS', 'PARTIAL', 'FAIL'] | None = None
    citations_count: int = 0
    repair_count: int = 0
    citations: list[Citation] = Field(default_factory=list)
    claims: list[GroundedClaim] = Field(default_factory=list)
    error: dict = Field(default_factory=dict)
    error_message: str = ''

    @classmethod
    def from_record(cls, record, provider):
        run = next((g for g in record.get('generation_runs', []) if g['provider'] == provider), None)
        busy = record['status'] in ('QUEUED', 'RUNNING')
        settings = record.get('generation_settings_json', {})
        model = settings.get('local', {}).get('model', '') if provider == 'local' else settings.get('model', record.get('llm_model', ''))
        if run is not None:
            raw = run['result']
            details = run
        elif record.get('generation_mode', 'minimax') != 'compare' or not record.get('generation_runs') and not busy:
            raw = record.get('rag_result') or {}
            details = record
        else:
            raw, details = {}, {}
        status = raw.get('status', 'PENDING' if busy else record['status'])
        ground = details.get('grounding_status', 'UNCHECKED')
        label = ('PASS' if ground == 'GROUNDED' else 'PARTIAL' if ground in ('PARTIAL', 'PARTIAL_REFERENCE_RESULT')
                 else None if status == 'PENDING' else 'FAIL')
        error = raw.get('error') or {}
        # A not-attempted provider has no measured generation time.
        measured = bool(raw) and status not in ('APPROVAL_REQUIRED', 'NOT_CONFIGURED') and (run is not None or raw.get('request_count', 0) > 0)
        citations = details.get('citations_json', [])
        return cls(provider=provider, model=raw.get('model', model) if run is not None else model,
            answer=raw.get('text', ''), status=status,
            generation_ms=run.get('generation_ms', raw.get('duration_ms')) if measured and run else raw.get('duration_ms') if measured else None,
            total_ms=run.get('total_ms') if measured and run else None,
            grounding_status=ground, grounding_label=label,
            citations_count=len(citations), repair_count=int(details.get('repair_used', False)),
            citations=citations, claims=details.get('claims_json', []), error=error,
            error_message=ERROR_MESSAGES.get(error.get('code'), 'Не удалось получить ответ провайдера.') if error else '')


class SharedRetrievalResult(BaseModel):
    retrieval_run_id: str
    context_snapshot_id: str | None = None
    context_ids: list[str] = Field(default_factory=list)
    context_size: int = 0
    context_size_unit: str = 'utf8_bytes'
    final_contexts: int = 0
    sources: list[dict] = Field(default_factory=list)


class CompareResult(BaseModel):
    comparison_run_id: str
    status: str
    stage: str
    active_provider: str | None = None
    shared: SharedRetrievalResult
    local: GenerationResult
    cloud: GenerationResult

    @classmethod
    def from_record(cls, record):
        return cls(comparison_run_id=record['comparison_run_id'], status=record['status'],
            stage=record.get('generation_stage', 'queued'), active_provider=record.get('active_generation_provider'),
            shared=SharedRetrievalResult(retrieval_run_id=record['comparison_run_id'],
                context_snapshot_id=record.get('context_snapshot_id'), context_ids=record.get('used_chunk_ids_json', []),
                context_size=record.get('context_tokens', 0), final_contexts=record.get('used_count', 0),
                sources=record.get('sources', [])),
            local=GenerationResult.from_record(record, 'local'), cloud=GenerationResult.from_record(record, 'minimax'))
