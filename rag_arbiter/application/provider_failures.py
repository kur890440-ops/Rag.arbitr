"""Stable, payload-free failure categories shared by extraction and review."""
from ..llm import ProviderNotCalled

def category(exc):
    if getattr(exc,'code',None) in ('OUTPUT_TRUNCATED','PROVIDER_NOT_CALLED','PROVIDER_ERROR','TIMEOUT'):return exc.code
    if isinstance(exc,ProviderNotCalled):return 'ADMISSION_REJECTED'
    if isinstance(exc,TimeoutError):return 'TIMEOUT'
    code=str(exc) if type(exc) is ValueError else ''
    if code in ('TASK_CANCELLED','CANCELLED'):return 'CANCELLED'
    if code in ('WORKSPACE_TIMEOUT','PUBLIC_TURN_LIMIT'):return 'TIMEOUT'
    if code in ('CONTEXT_BUDGET_EXCEEDED','BUDGET_EXCEEDED','PUBLIC_INPUT_LIMIT'):return 'ADMISSION_REJECTED'
    if code in ('INVALID_EVIDENCE','INVALID_REVIEW_EVIDENCE_REF','INVALID_EVIDENCE_REVIEW_IDS'):return 'INVALID_EVIDENCE'
    if type(exc).__name__ in ('ValidationError','JSONDecodeError'):return 'INVALID_MODEL_RESPONSE'
    return 'PROVIDER_ERROR'
