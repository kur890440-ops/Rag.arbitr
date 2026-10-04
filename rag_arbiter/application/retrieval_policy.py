"""Day25 user-only executable constraints; independent of generation/grounding."""
import logging
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from qdrant_client import models

DOCUMENT_TYPES = ('COURT_RULING','COURT_DECISION','COMPLAINT','MOTION','APPLICATION',
                  'PROTOCOL','OBJECTION','REPORT','EXPERT_CONCLUSION','ADMINISTRATIVE_ORDER','UNKNOWN')
ConstraintType = Literal['DOCUMENT_TYPE_INCLUDE','DOCUMENT_TYPE_EXCLUDE','DOCUMENT_YEAR_MIN',
    'DOCUMENT_YEAR_MAX','DOCUMENT_ID_INCLUDE','DOCUMENT_ID_EXCLUDE','SEMANTIC_CONSTRAINT']


class ConstraintPredicate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    type: ConstraintType
    values: list[str] = Field(default_factory=list,max_length=100)
    year: int | None = Field(default=None,strict=True,ge=1800,le=2200)

    @model_validator(mode='after')
    def validate_values(self):
        if self.type.startswith('DOCUMENT_YEAR'):
            if self.year is None or self.values:raise ValueError('Year predicate requires only year')
        else:
            if self.year is not None or not self.values or any(not x.strip() for x in self.values):
                raise ValueError('Predicate requires nonempty values')
            if self.type.startswith('DOCUMENT_TYPE') and any(x not in DOCUMENT_TYPES or x=='UNKNOWN' for x in self.values):
                raise ValueError('Unsupported document type')
        return self


class TypedConstraint(ConstraintPredicate):
    constraint_id: str
    display_text: str
    source_type: Literal['USER_EXPLICIT']='USER_EXPLICIT'
    source_message_id: str
    active: bool=True


def legacy_predicates(item):
    """Conservative migration of previously generated, documented keys; no free-text classifier."""
    if item.predicates:return item.predicates
    text=item.value.lower().strip().rstrip('.')
    if item.key=='document_type' and re.fullmatch(r'(?:учитывай|используй|смотри)?\s*только судебные определения',text):
        return [ConstraintPredicate(type='DOCUMENT_TYPE_INCLUDE',values=['COURT_RULING'])]
    if item.key=='exclude_complaints' and re.fullmatch(r'(?:жалобы (?:исключи|не учитывай)|(?:исключи|исключить|не учитывай) жалобы)',text):
        return [ConstraintPredicate(type='DOCUMENT_TYPE_EXCLUDE',values=['COMPLAINT'])]
    if item.key=='years':
        if re.fullmatch(r'после: (?:19|20)\d{2}',text):
            return [ConstraintPredicate(type='DOCUMENT_YEAR_MIN',year=int(text[-4:])+1)]
        if re.fullmatch(r'годы: (?:19|20)\d{2}(?:, (?:19|20)\d{2})*',text):
            years=sorted(set(map(int,re.findall(r'\d{4}',text))))
            if years==list(range(min(years),max(years)+1)):
                return [ConstraintPredicate(type='DOCUMENT_YEAR_MIN',year=min(years)),ConstraintPredicate(type='DOCUMENT_YEAR_MAX',year=max(years))]
    return []


class RetrievalPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope: str
    corpus_id: str | None
    task_state_version: int
    include_filters: list[TypedConstraint]=Field(default_factory=list)
    exclude_filters: list[TypedConstraint]=Field(default_factory=list)
    date_filters: list[TypedConstraint]=Field(default_factory=list)
    semantic_constraints: list[str]=Field(default_factory=list)
    unsupported_constraints: list[str]=Field(default_factory=list)
    document_metadata: dict[str,dict]=Field(default_factory=dict)
    corpus_document_ids: list[str]=Field(default_factory=list)
    eligible_document_ids: list[str]=Field(default_factory=list)

    def matches(self,document_id):
        if document_id not in self.corpus_document_ids:return False
        meta=self.document_metadata.get(document_id,{})
        kind=meta.get('document_type') if meta.get('classification_status')=='CONFIRMED' else 'UNKNOWN'
        for c in self.include_filters:
            if (kind if c.type=='DOCUMENT_TYPE_INCLUDE' else document_id) not in c.values:return False
        for c in self.exclude_filters:
            if (kind if c.type=='DOCUMENT_TYPE_EXCLUDE' else document_id) in c.values:return False
        for c in self.date_filters:
            year=meta.get('document_year')
            if year is None:return False
            if c.type=='DOCUMENT_YEAR_MIN' and year<c.year:return False
            if c.type=='DOCUMENT_YEAR_MAX' and year>c.year:return False
        return True

    def guard(self,items,stage):
        accepted=[]
        for item in items:
            doc=item.get('document_id') if isinstance(item,dict) else item.document_id
            if doc in self.eligible_document_ids and self.matches(doc):accepted.append(item)
            else:logging.getLogger(__name__).error('RETRIEVAL_POLICY_VIOLATION stage=%s document_id=%s',stage,doc)
        return accepted

    def qdrant_filter(self):
        must=[models.FieldCondition(key='document_id',match=models.MatchAny(any=self.eligible_document_ids))]
        excluded=[]
        for c in self.include_filters+self.exclude_filters:
            key='document_type' if c.type.startswith('DOCUMENT_TYPE') else 'document_id'
            condition=models.FieldCondition(key=key,match=models.MatchAny(any=c.values))
            if key=='document_type':
                condition=models.Filter(must=[condition,models.FieldCondition(key='classification_status',match=models.MatchValue(value='CONFIRMED'))])
            (excluded if c.type.endswith('EXCLUDE') else must).append(condition)
        for c in self.date_filters:
            must.append(models.FieldCondition(key='document_year',range=models.Range(**{'gte' if c.type.endswith('MIN') else 'lte':c.year})))
        return models.Filter(must=must,must_not=excluded)


class RetrievalPolicyBuilder:
    def build(self,state,scope,corpus_id,metadata,selected_document_id=None):
        policy=RetrievalPolicy(scope=scope,corpus_id=corpus_id,task_state_version=state.version,
            document_metadata=metadata,corpus_document_ids=sorted(metadata))
        for item in state.constraints_json:
            if not item.active:continue
            predicates=legacy_predicates(item)
            if not predicates:
                policy.unsupported_constraints.append(item.value)
                policy.semantic_constraints.append(item.value)
            for n,p in enumerate(predicates):
                c=TypedConstraint(**p.model_dump(),constraint_id=f'{item.key}:{n}',display_text=item.value,
                    source_message_id=item.source_message_id)
                if c.type.startswith('DOCUMENT_ID') and any(v not in metadata for v in c.values):
                    raise ValueError('Constraint document ID is outside current corpus')
                if c.type=='SEMANTIC_CONSTRAINT':policy.semantic_constraints.append(item.value)
                elif c.type.startswith('DOCUMENT_YEAR'):policy.date_filters.append(c)
                elif c.type.endswith('INCLUDE'):policy.include_filters.append(c)
                else:policy.exclude_filters.append(c)
        low=max([c.year for c in policy.date_filters if c.type.endswith('MIN')],default=1800)
        high=min([c.year for c in policy.date_filters if c.type.endswith('MAX')],default=2200)
        if low>high:raise ValueError('Conflicting document year constraints')
        for family in ('DOCUMENT_TYPE','DOCUMENT_ID'):
            sets=[set(c.values) for c in policy.include_filters if c.type.startswith(family)]
            excludes={v for c in policy.exclude_filters if c.type.startswith(family) for v in c.values}
            if sets and not (set.intersection(*sets)-excludes):raise ValueError('Conflicting document include/exclude constraints')
        policy.eligible_document_ids=[d for d in policy.corpus_document_ids if policy.matches(d)
            and (scope!='SELECTED_DOCUMENT' or d==selected_document_id)]
        return policy
