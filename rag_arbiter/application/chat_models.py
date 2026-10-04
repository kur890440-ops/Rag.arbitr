"""Persistent Day25 contracts. Memory is user task state, never corpus knowledge."""
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from ..documents import now
from .retrieval_policy import ConstraintPredicate


class ChatValidationError(ValueError):
    """Safe application-authored error suitable for display."""


class Typed(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ChatSession(Typed):
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = 'Новый чат'
    status: Literal['ACTIVE', 'ARCHIVED'] = 'ACTIVE'
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    last_message_at: str | None = None
    processing_run_id: str


class ChatMessage(Typed):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    role: Literal['USER', 'ASSISTANT']
    content: str = Field(min_length=1, max_length=20000)
    sequence: int = Field(ge=1)
    created_at: str = Field(default_factory=now)


class MemoryItem(Typed):
    predicates: list[ConstraintPredicate] = Field(default_factory=list,max_length=8)
    active: bool = True
    key: str = Field(min_length=1, max_length=80, pattern=r'^[\w.-]+$')
    value: str = Field(min_length=1, max_length=1500)
    evidence: str = Field(min_length=1, max_length=2000)
    source_type: Literal['USER_EXPLICIT'] = 'USER_EXPLICIT'
    source_message_id: str


class TaskState(Typed):
    session_id: str
    version: int = Field(default=0, ge=0)
    goal: MemoryItem | None = None
    constraints_json: list[MemoryItem] = Field(default_factory=list, max_length=20)
    clarifications_json: list[MemoryItem] = Field(default_factory=list, max_length=20)
    terms_json: list[MemoryItem] = Field(default_factory=list, max_length=20)
    open_questions_json: list[MemoryItem] = Field(default_factory=list, max_length=20)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class TaskStatePatch(Typed):
    # Approval is process-local and bound to the exact proposal; never accepted from JSON.
    _semantic_approval: str = PrivateAttr(default='')
    intent: Literal['QUESTION', 'MEMORY_ONLY', 'MIXED'] = 'QUESTION'
    expected_version: int = Field(ge=0)
    goal_update: MemoryItem | None = None
    constraints_add: list[MemoryItem] = Field(default_factory=list, max_length=20)
    constraints_remove: list[str] = Field(default_factory=list, max_length=20)
    clarifications_add: list[MemoryItem] = Field(default_factory=list, max_length=20)
    clarifications_remove: list[str] = Field(default_factory=list, max_length=20)
    terms_set: list[MemoryItem] = Field(default_factory=list, max_length=20)
    terms_remove: list[str] = Field(default_factory=list, max_length=20)
    open_questions_update: list[MemoryItem] | None = Field(default=None, max_length=20)


class ResolvedQuestionResult(Typed):
    original_question: str
    resolved_question: str
    contextual_question: str = ''
    prior_concepts: list[str] = Field(default_factory=list)
    resolution_used: bool = False
    referents_resolved: list[str] = Field(default_factory=list)
    memory_constraints_applied: list[str] = Field(default_factory=list)
    warning: str | None = None


class ChatResponse(Typed):
    answer_text: str
    sources: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    grounding_result: dict = Field(default_factory=dict)
    task_state_version: int
    resolved_question: str
    status: str


class ChatTurn(Typed):
    turn_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    user_message_id: str
    assistant_message_id: str | None = None
    task_state_version: int
    original_question: str
    resolved_question: str = ''
    retrieval_query: str = ''
    rag_pipeline_mode: str = 'REWRITE_RERANK'
    scope: str = 'ALL_DOCUMENTS'
    strategy: str = 'structure'
    sources_json: list[dict] = Field(default_factory=list)
    citations_json: list[dict] = Field(default_factory=list)
    grounding_status: str = 'UNCHECKED'
    latency_ms: float = 0
    status: str = 'QUEUED'
    stage: str = 'sending'
    created_at: str = Field(default_factory=now)
    trace: dict = Field(default_factory=dict)
    response: ChatResponse | None = None
    comparison_run_id: str | None = None
    owner_pid: int
    owner_started: float
