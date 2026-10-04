import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from rag_arbiter.application.reference_retrieval import ReferenceAwareRetrievalService as Service
from rag_arbiter.application.query_rewrite import RewriteResult,guard_rewrite
from rag_arbiter.application.dialogue import ReferenceResolver
from rag_arbiter.application.chat_models import TaskState
from rag_arbiter.llm import LLMConfig,LLMResult
from test_dialogue import context,user,provider
from test_grounding import setup,Reranker
from test_retrieval_policy import policy,constraint
from test_exhaustive import docs,cfg
from rag_arbiter.application.exhaustive import ExhaustiveNoRAG,ExhaustiveContextBatcher
from test_rag import ragweb


@pytest.mark.parametrize('qualifier',['статья 129','2027','ООО Ромашка','судебное определение'])
def test_rewrite_drift_returns_original(qualifier):
    r=RewriteResult(original_question='отчетность',retrieval_query='отчетность '+qualifier,provider='test',model='test',status='SUCCESS')
    guarded=guard_rewrite(r,['отчетность'])
    assert guarded.status=='GUARDED_FALLBACK' and guarded.retrieval_query=='отчетность'
    assert guard_rewrite(r,['отчетность '+qualifier]).status=='SUCCESS'


def test_resolver_binds_ids_without_accepting_generated_facts_or_goal():
    c=context();ref=c.referents_json[0];m=user('Какие из этих обязанностей связаны с отчетностью?')
    body=dict(original_message=m.content,resolved_message='Обязанности по статье 129, broad goal',
        resolved_referents=[ref.referent_id],candidate_prior_claim_ids=ref.claim_ids,
        source_turn_ids=[ref.source_turn_id],unresolved_referents=[],status='RESOLVED',confidence=1)
    result=ReferenceResolver(provider(body),LLMConfig()).resolve(m,TaskState(session_id='s'),c,[m])
    assert result.resolved_message==m.content and result.candidate_prior_claim_ids==ref.claim_ids


@pytest.mark.parametrize('label,accepted',[('обязанности конкурсного управляющего',True),('статья 129',False),('выдуманная обязанность',False)])
def test_reference_label_is_only_a_user_topic_span(label,accepted):
    c=context();c.last_topic='Какие обязанности конкурсного управляющего встречаются в документах?'
    ref=c.referents_json[0];m=user('Какие из них?')
    body=dict(original_message=m.content,resolved_message=m.content,referent_label=label,
        resolved_referents=[ref.referent_id],candidate_prior_claim_ids=ref.claim_ids,
        source_turn_ids=[ref.source_turn_id],unresolved_referents=[],status='RESOLVED',confidence=1)
    result=ReferenceResolver(provider(body),LLMConfig()).resolve(m,TaskState(session_id='s'),c,[m])
    assert result.referent_label==(label if accepted else '')


def test_bound_rejected_without_silent_truncation():
    ref=SimpleNamespace(candidate_prior_claim_ids=[str(i) for i in range(11)])
    with pytest.raises(ValueError,match='Слишком много'):Service.bind(None,None,ref,10)


def test_exhaustive_rejects_category_outside_referent_set():
    batch=ExhaustiveContextBatcher(cfg(),'q').build(docs(size=1))[0];u=batch.units[0]
    body=dict(relevant=True,findings=[dict(unit_id=u.unit_id,statement='fact',evidence_text='evidence.',normalized_key='unrelated')])
    with pytest.raises(ValueError,match='INVALID_REFERENCE_CLAIM'):ExhaustiveNoRAG.parse(json.dumps(body),batch,{'t:C1'})
    body['findings'][0]['normalized_key']='t:C1'
    assert ExhaustiveNoRAG.parse(json.dumps(body),batch,{'t:C1'}).findings[0].normalized_key=='t:C1'


def test_exhaustive_absence_is_not_counted_even_from_cache(ragweb,monkeypatch):
    client,rid,_,_=ragweb
    from test_exhaustive import MapLLM
    llm=MapLLM();original=llm.generate;checks=[]
    def generate(request):
        if request.question.startswith('DAY25_REFERENCE_OCCURRENCES'):
            d=json.loads(request.question.split('\n',1)[1]);checks.append(d)
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'findings':[
                dict(finding_index=f['finding_index'],supported=False,reason='No positive support') for f in d['findings']]}))
        response=original(request)
        if '\nUNITS:\n' in request.question:
            data=json.loads(response.text)
            for f in data['findings']:f.update(normalized_key='t:C1',statement='Not found')
            response.text=json.dumps(data)
        return response
    llm.generate=generate
    service=ExhaustiveNoRAG(client.app.state.runs,llm.factory)
    monkeypatch.setattr(service,'load',lambda *a:docs(count=1,size=1))
    for key in ['reference-first','reference-cache']:
        record=dict(comparison_run_id=key,processing_run_id=rid,document_id=None,question_text='frequency',
            full_document_context={},generation_call_count=0,minimax_requests_count=0,
            reference_prior_claims=[dict(claim_id='t:C1',claim_text='Positive claim')])
        service.run(record,cfg(),lambda r:None)
        assert record['exhaustive']['coverage_percent']==100
        assert record['exhaustive']['reference_claim_counts'][0]['document_count']==0
        assert not record['exhaustive']['merged_findings']
    assert record['exhaustive']['cache_hits'] and len(checks)==2


