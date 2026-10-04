import json
from types import SimpleNamespace
from uuid import uuid4
import numpy as np
import pytest
from qdrant_client import models
from rag_arbiter.application.retrieval_policy import ConstraintPredicate, RetrievalPolicyBuilder
from rag_arbiter.application.chat_models import TaskState,MemoryItem,ChatMessage,TaskStatePatch
from rag_arbiter.application.chat_memory import TaskMemoryUpdater,ContextualQueryBuilder
from rag_arbiter.application.document_metadata import classify_document,sync_payload
from rag_arbiter.vectorstore import LocalVectorStore
from rag_arbiter.retrieval import SemanticRetriever
from rag_arbiter.storage import MetadataStore
from rag_arbiter.llm import LLMConfig,LLMResult


def constraint(key,kind,values=None,year=None):
    return MemoryItem(key=key,value=key,evidence=key,source_message_id='user1',
        predicates=[ConstraintPredicate(type=kind,values=values or [],year=year)])


def metadata(kind='COURT_RULING',year=2025,status='CONFIRMED'):
    return dict(document_type=kind,document_year=year,classification_status=status)


def policy(*items,docs=None):
    return RetrievalPolicyBuilder().build(TaskState(session_id='s',constraints_json=list(items)),
        'ALL_DOCUMENTS','corpus',docs or {'court':metadata(),'complaint':metadata('COMPLAINT'),'unknown':metadata('COMPLAINT',None,'UNCERTAIN')})


def test_include_exclude_year_metadata_predicates():
    p=policy(constraint('only','DOCUMENT_TYPE_INCLUDE',['COURT_RULING']),
        constraint('exclude','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT']),constraint('after','DOCUMENT_YEAR_MIN',year=2025))
    assert p.eligible_document_ids==['court']
    raw=p.qdrant_filter().model_dump(mode='json',exclude_none=True)
    assert raw['must_not'][0]['must'][0]['match']['any']==['COMPLAINT']
    assert {'key':'document_year','range':{'gte':2025.0}} in raw['must']
    assert p.include_filters[0].source_message_id=='user1'


def test_unknown_is_not_automatically_excluded_but_not_included_as_court():
    assert 'unknown' in policy(constraint('exclude','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT'])).eligible_document_ids
    assert 'unknown' not in policy(constraint('include','DOCUMENT_TYPE_INCLUDE',['COURT_RULING'])).eligible_document_ids
    assert 'unknown' not in policy(constraint('date','DOCUMENT_YEAR_MIN',year=2025)).eligible_document_ids


@pytest.mark.parametrize('data',[
    dict(type='DOCUMENT_TYPE_INCLUDE',values=['FAKE']),dict(type='DOCUMENT_YEAR_MIN',year='2025'),
    dict(type='DOCUMENT_YEAR_MIN',year=2025,values=['2025']),dict(type='DOCUMENT_ID_INCLUDE'),
    dict(type='UNSUPPORTED',values=['x'])])
def test_invalid_llm_enum_values_are_rejected(data):
    with pytest.raises(ValueError):ConstraintPredicate(**data)


def test_conflicts_and_foreign_ids():
    for items in [
        [constraint('a','DOCUMENT_TYPE_INCLUDE',['COMPLAINT']),constraint('b','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT'])],
        [constraint('a','DOCUMENT_YEAR_MIN',year=2025),constraint('b','DOCUMENT_YEAR_MAX',year=2024)],
        [constraint('a','DOCUMENT_ID_INCLUDE',['foreign'])]]:
        with pytest.raises(ValueError):policy(*items)
    assert policy(constraint('a','DOCUMENT_ID_INCLUDE',['court'])).eligible_document_ids==['court']
    assert 'court' not in policy(constraint('a','DOCUMENT_ID_EXCLUDE',['court'])).eligible_document_ids


def test_real_qdrant_filters_before_candidate_top_n(tmp_path):
    vectors=LocalVectorStore(tmp_path/'qdrant');store=MetadataStore(tmp_path/'db.sqlite')
    vectors.create('test',2);docs={};points=[];ids=[]
    for n in range(55):
        doc=f'doc{n}';chunk=uuid4().hex;ids.append(chunk)
        kind='COMPLAINT' if n<30 else 'COURT_RULING';docs[doc]=metadata(kind)
        points.append(models.PointStruct(id=str(uuid4()),vector=[1.0,0.0] if n<30 else [0.9,0.1],
            payload=dict(document_id=doc,chunk_id=chunk)))
        store.put('chunks',chunk,dict(chunk_id=chunk,document_id=doc,text=f'canonical {n}'))
    vectors.client.upsert('test',points=points)
    index=dict(collection='test',document_ids=list(docs),chunk_ids=ids,run_id='active',embedding={'test':True},corpus_id='corpus')
    sync_payload(vectors,index,docs)
    p=policy(constraint('exclude','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT']),docs=docs)
    retriever=SemanticRetriever(SimpleNamespace(identity={'test':True}),vectors,store)
    try:
        original=retriever.retrieve_vector(np.array([1.0,0.0]),index,20)
        assert all(docs[h['document_id']]['document_type']=='COMPLAINT' for h in original)
        hits=retriever.retrieve_vector(np.array([1.0,0.0]),index,20,policy=p)
        assert len(hits)==20
        assert all(docs[h['document_id']]['document_type']=='COURT_RULING' for h in hits)
        assert {h['chunk_id'] for h in hits}<=set(ids)
        date_policy=policy(constraint('date','DOCUMENT_YEAR_MIN',year=2026),docs=docs)
        assert not retriever.retrieve_vector(np.array([1.0,0.0]),index,20,policy=date_policy)
    finally:vectors.close();store.close()


def test_defensive_candidate_and_context_validation(caplog):
    p=policy(constraint('exclude','DOCUMENT_TYPE_EXCLUDE',['COMPLAINT']))
    assert p.guard([{'document_id':'court'},{'document_id':'complaint'}],'candidate')==[{'document_id':'court'}]
    assert not p.guard([SimpleNamespace(document_id='complaint')],'final_context')
    assert 'RETRIEVAL_POLICY_VIOLATION' in caplog.text


def test_semantic_constraint_reaches_query_and_has_no_fake_filter():
    item=constraint('Отчетность','SEMANTIC_CONSTRAINT',['Обязанности по отчетности'])
    p=policy(item)
    assert p.semantic_constraints==['Отчетность'] and not p.include_filters and not p.exclude_filters
    m=ChatMessage(session_id='s',role='USER',content='Перечисли обязанности',sequence=1)
    resolved=ContextualQueryBuilder(None,LLMConfig()).build(m,TaskState(session_id='s',constraints_json=[item]),[])
    assert 'Отчетность' in resolved.resolved_question


def test_removal_and_year_expansion_preserve_policy():
    m=ChatMessage(session_id='s',role='USER',content='Теперь включи также 2024.',sequence=1)
    old=MemoryItem(key='years',value='Годы: 2025',evidence='2025',source_message_id='old')
    state=TaskState(session_id='s',constraints_json=[old])
    updated=TaskMemoryUpdater.date_item(state,m)
    patch=TaskStatePatch(expected_version=0,constraints_remove=['years'],constraints_add=[updated])
    state=TaskMemoryUpdater.apply(state,patch,m)
    p=RetrievalPolicyBuilder().build(state,'ALL_DOCUMENTS','c',{'a':metadata(year=2024),'b':metadata(year=2025),'c':metadata(year=2023)})
    assert p.eligible_document_ids==['a','b']
    m.content='Удали ограничение';p=TaskStatePatch(expected_version=state.version,constraints_remove=['years'])
    state=TaskMemoryUpdater.apply(state,p,m)
    assert not state.constraints_json


def doc(text,name='example.pdf'):
    return dict(document_id='d',file_name=name,blocks=[dict(page_number=1,reading_order=0,text=text)])


def test_classification_uses_heading_not_mentions_or_filename_deadline():
    m=classify_document(doc('АРБИТРАЖНЫЙ СУД\nРЕЗОЛЮТИВНАЯ ЧАСТЬ ОПРЕДЕЛЕНИЯ\n23 декабря 2024 года\nРассмотрена жалоба.', 'Продление по 26.06.2025.pdf'),'c')
    assert m['document_type']=='COURT_RULING' and m['document_year']==2024
    assert classify_document(doc('В суд\nЖАЛОБА\nРешением от 01.02.2025','жалоба.pdf'),'c')['document_year'] is None
    assert classify_document(doc('Нет заголовка','жалоба.pdf'),'c')['classification_status']=='UNCERTAIN'
    assert classify_document(doc('Нет заголовка'),'c')['classification_status']=='UNKNOWN'
    admin=classify_document(doc('РОСРЕЕСТР\nНаправляет копию постановления о прекращении производства в суд\nПОСТАНОВЛЕНИЕ'),'c')
    assert admin['document_type']=='ADMINISTRATIVE_ORDER'


def test_new_session_has_no_inherited_filters():
    assert len(policy().eligible_document_ids)==3


def test_predicate_only_change_updates_memory_version():
    m=ChatMessage(session_id='s',role='USER',content='Только определения',sequence=1)
    old=MemoryItem(key='kind',value=m.content,evidence=m.content,source_message_id=m.message_id)
    state=TaskState(session_id='s',constraints_json=[old])
    updated=old.model_copy(update={'predicates':[ConstraintPredicate(type='DOCUMENT_TYPE_INCLUDE',values=['COURT_RULING'])]})
    result=TaskMemoryUpdater.apply(state,TaskStatePatch(expected_version=0,constraints_add=[updated]),m)
    assert result.version==1 and state.version==0
