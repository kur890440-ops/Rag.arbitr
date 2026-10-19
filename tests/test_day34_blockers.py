import json
import pytest
from rag_arbiter.application.dialogue import ReferenceResolver,ReferenceResolutionResult,DialogueWorkingContext,ConsultIntent,AnswerModeRouter,bound_document_scope
from rag_arbiter.application.chat_models import ChatMessage,TaskState
from rag_arbiter.llm import LLMConfig,LLMResult
from test_dialogue import context,provider

@pytest.mark.parametrize('name',['Новый Контрагент','Лицо №127','Договор Альфа-2026'])
@pytest.mark.parametrize('history',[False,True])
def test_explicit_new_subject_not_blocked(name,history):
    q='Что известно о '+name+'?';c=context() if history else DialogueWorkingContext(session_id='s')
    value=dict(original_message=q,resolved_message=q,reference_kind='NEW_SUBJECT',subject_span=name,status='UNRESOLVED',confidence=.2,unresolved_referents=[name])
    r=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),c,[])
    assert r.status=='NOT_NEEDED' and not r.candidate_prior_claim_ids

@pytest.mark.parametrize('span',['Другой человек','у кого','они','этот договор'])
def test_unproven_new_subject_cannot_clear_reference(span):
    q='У кого отличаются условия отчетности?';c=context()
    value=dict(original_message=q,resolved_message=q,reference_kind='NEW_SUBJECT',subject_span=span,status='NOT_NEEDED',confidence=1)
    r=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),c,[])
    assert r.status=='UNRESOLVED'


def test_comparison_router_rejects_false_new_subject():
    q='У кого отличаются условия отчетности?'
    value=dict(mode='DOCUMENT',question=q,topic='Отчетность',reference_kind='NEW_SUBJECT',subject_span='условия отчетности')
    r=AnswerModeRouter(provider(value),LLMConfig()).consult(q,[],context())
    assert r.reference_kind=='FOLLOWUP_COMPARISON' and r.full_coverage


def test_scope_checks_ids_acl_and_current_versions():
    c=context();c.document_scope=[dict(document_id='allowed',document_version_id='v2'),dict(document_id='forbidden',document_version_id='v1'),dict(document_id='stale',document_version_id='v1')];c.scope_partial=True
    grants={'allowed':dict(recognition_version='v2'),'stale':dict(recognition_version='v2'),'unrelated':dict(recognition_version='v1')}
    assert bound_document_scope(c,grants)=={'allowed'}
    assert c.scope_partial


@pytest.mark.parametrize('q',['Они обязаны отчитываться?','Что предусматривает этот договор?'])
def test_missing_history_is_not_invented(q):
    value=dict(original_message=q,resolved_message=q,reference_kind='ANAPHORA',status='UNRESOLVED',confidence=1,unresolved_referents=['previous'])
    result=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),DialogueWorkingContext(session_id='s'),[])
    assert result.status=='UNRESOLVED' and not result.resolved_referents


def test_independent_empty_history_retrievable():
    q='Какие документы содержат сроки оплаты?'
    value=dict(original_message=q,resolved_message=q,reference_kind='INDEPENDENT',status='NOT_NEEDED',confidence=1)
    result=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),DialogueWorkingContext(session_id='s'),[])
    assert result.status=='NOT_NEEDED'


@pytest.mark.parametrize('version',['v1','missing','v2'])
def test_no_stale_or_missing_scope_fallback(version):
    c=context();c.document_scope=[dict(document_id='chosen',document_version_id=version)]
    assert bound_document_scope(c,{'chosen':dict(recognition_version='v2'),'other':dict(recognition_version='v9')})==({'chosen'} if version=='v2' else set())


@pytest.mark.parametrize('q',['Какие у них обязанности?','Они должны отчитываться?','Что содержит этот договор?'])
def test_independent_cannot_bypass_anaphora(q):
    value=dict(original_message=q,resolved_message=q,reference_kind='INDEPENDENT',status='NOT_NEEDED',confidence=1)
    r=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),context(),[])
    assert r.status=='UNRESOLVED'



def test_partial_exhaustive_scope_is_canonical_not_claim_evidence(tmp_path):
    from rag_arbiter.application.dialogue import WorkingContextService
    from rag_arbiter.application.chat_models import ChatTurn
    from rag_arbiter.storage import MetadataStore
    from contextlib import closing
    with closing(MetadataStore(tmp_path/'scope.db')) as db:
        db.put('normalized_documents','v',dict(document_id='d',blocks=[dict(block_id='b',text='Canonical evidence')]))
        record=dict(retrieval_policy_json=dict(eligible_document_ids=['d']),exhaustive=dict(merged_findings=[dict(evidence=[dict(document_id='d',document_version_id='v',block_id='b',char_start=0,evidence_text='Canonical evidence')])]))
        scope=WorkingContextService.canonical_scope(db,record)
        assert scope==[dict(document_id='d',document_version_id='v')]
        t=ChatTurn(session_id='s',user_message_id='u',assistant_message_id='a',original_question='Compare',task_state_version=0,owner_pid=0,owner_started=0,grounding_status='PARTIAL',comparison_run_id='r',trace=dict(canonical_document_scope=scope))
        db.put('chat_turns',t.turn_id,t.model_dump());db.put('chat_messages','a',dict(session_id='s',role='ASSISTANT'));db.put('rag_comparison_runs','r',record)
        c=WorkingContextService.update(DialogueWorkingContext(session_id='s'),t,[])
        assert c.scope_partial and not c.last_grounded_claim_refs_json
        assert WorkingContextService.validate(db,c)==c
        ref=c.referents_json[0];q='У кого отличаются условия отчетности?'
        value=dict(original_message=q,resolved_message=q,reference_kind='FOLLOWUP_COMPARISON',status='RESOLVED',confidence=1,resolved_referents=[ref.referent_id],source_turn_ids=[ref.source_turn_id])
        r=ReferenceResolver(provider(value),LLMConfig()).resolve(ChatMessage(session_id='s',role='USER',content=q,sequence=1),TaskState(session_id='s'),c,[])
        assert r.status=='RESOLVED' and not r.candidate_prior_claim_ids
        c.document_scope=[dict(document_id='other',document_version_id='v')]
        with pytest.raises(ValueError):WorkingContextService.validate(db,c)


from test_rag import ragweb

def test_consult_explicit_document_changes_effective_ids(ragweb):
    from test_internal_consultant import internal
    c,_,llm,_=ragweb;service,fake=internal(c,llm)
    grants=service.knowledge.snapshot()[1];pairs=list(grants.items());assert len(pairs)>=2
    original=fake.generate
    def generate(request):
        if request.question.startswith('DAY33_PUBLIC_ROUTER'):
            payload=json.loads(request.question[request.question.index('{"question":'):]);q=payload['question']
            title=next(g['title'] for _,g in pairs if g['title'] in q)
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question='Какая оплата?',topic='Оплата',reference_kind='EXPLICIT_DOCUMENT',subject_span=title,document_titles=[title])))
        return original(request)
    fake.generate=generate;c.get('/consult')
    for did,g in pairs[:2]:
        response=c.post('/consult/messages',json=dict(question='Какая оплата в документе '+g['title']+'?'))
        assert response.status_code==200,response.text
        assert service.diagnostics[-1]['effective_document_scope']==[did]
        assert service.diagnostics[-1]['reference_kind']=='EXPLICIT_DOCUMENT'
