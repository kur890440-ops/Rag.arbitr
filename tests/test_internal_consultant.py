import json
from unittest.mock import patch
from test_rag import ragweb
from test_public_consultant import setup
from rag_arbiter.application.internal_knowledge import InternalKnowledge
from rag_arbiter.llm import LLMResult


def internal(c,llm):
    s,f=setup(c,llm);s.knowledge=InternalKnowledge(s.runs)
    original=f.generate
    def generate(r):
        if r.question.startswith('DAY33_PUBLIC_ROUTER'):
            d=json.loads(r.question[r.question.index('{"question":'):]);q=d['question']
            if q=='Какие документы у тебя есть?':
                return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='ARCHIVE',question=q,topic='Архив')))
            if q=='Один из договоров':
                return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question=q,topic='Архив',document_titles=d['public_titles'])))
        return original(r)
    f.generate=generate
    return s,f


def test_internal_registry_rag_general_mixed_and_new_ready(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    snap,g=s.knowledge.snapshot();assert len(g)==2
    with s.runs.db() as store: assert not store.all('public_document_grants')
    c.get('/consult')
    r=c.post('/consult/messages',json={'question':'Какие документы у тебя есть?'})
    assert r.status_code==200 and 'Доступно для поиска: 2' in r.text
    assert all(v['title'] in r.text for v in g.values())
    assert not any(x.context for x in f.inputs)
    assert s.diagnostics[-1]['mode']=='ARCHIVE'
    r=c.post('/consult/messages',json={'question':'Что такое премия?'})
    assert r.status_code==200 and not any(x.context for x in f.inputs)
    for q in ('Какая оплата?','А какой срок?','Объясни порядок оплаты'):
        r=c.post('/consult/messages',json={'question':q});assert r.status_code==200,r.text
    assert 'Общие сведения' in r.text and 'По доступным документам' in r.text
    assert any(x.context for x in f.inputs)
    v=s.visitor(c.cookies.get('rag_consult'))[1];assert v.citations and v.context.referents_json
    for cid in v.citations: assert c.get('/consult/sources/'+cid).status_code==200
    r=c.post('/consult/messages',json={'question':'Один из договоров'})
    assert 'Уточните название договора' in r.text
    with s.runs.db() as store:
        keys=[p['run_id'] for p in snap['indexes']['structure']['partitions']]
        originals={k:store.get('index_runs',k) for k in keys}
        for k,row in originals.items():store.put('index_runs',k,{**row,'status':'FAILED'})
    assert not s.knowledge.snapshot()[1]
    assert not s.history(v)
    with s.runs.db() as store:
        for k,row in originals.items():store.put('index_runs',k,row)
    assert len(s.knowledge.snapshot()[1])==2 # automatic admission, no publication or session reset


def test_network_restrictions_actual_peer_and_admin(ragweb):
    c,rid,llm,_=ragweb;internal(c,llm)
    for header in ('X-Forwarded-For','Forwarded','X-Forwarded-Host','X-Forwarded-Proto'):
        assert c.get('/consult',headers={header:'127.0.0.1'}).status_code==403
    from starlette.requests import Request
    from starlette.datastructures import Address
    with patch.object(Request,'client',new=property(lambda self:Address('198.51.100.2',123))):
        assert c.get('/consult').status_code==403
        assert c.get('/chat').status_code==403
    c.app.state.runs.config.consult.allowed_networks=['10.23.0.0/24']
    with patch.object(Request,'client',new=property(lambda self:Address('10.23.0.5',123))):
        assert c.get('/consult').status_code==200
        for path in ('/chat','/sources','/publications','/api/runs'):
            assert c.get(path).status_code==200
        c.app.state.runs.config.consult.public_only=True
        assert c.get('/chat').status_code==403
        c.app.state.runs.config.consult.public_only=False
    with patch.object(Request,'client',new=property(lambda self:Address('10.24.0.5',123))):
        assert c.get('/consult').status_code==403

def test_day31_new_indexed_file_enters_existing_consult_session(ragweb,tmp_path):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    snap,g=s.knowledge.snapshot();did=next(iter(g));sid='consult-test-source'
    root=tmp_path/'source';root.mkdir();s.runs.config.network_sources.allowed_roots.append(str(root))
    with s.runs.db() as store:
        store.put('network_sources',sid,dict(id=sid,name='Test source',root_path=str(root),connection_status='ACCESS_OK'))
        store.put('network_provenance','origin',dict(document_id=did,source_id=sid,run_id=rid))
        row=dict(id='network-file',document_id=did,source_id=sid,run_id=rid,active_run_id=rid,status='NEW',indexing_status='PENDING')
        store.put('network_files',row['id'],row)
    assert did not in s.knowledge.snapshot()[1]
    c.get('/consult');token=c.cookies.get('rag_consult')
    with s.runs.db() as store:store.put('network_files',row['id'],{**row,'indexing_status':'SUCCESS'})
    assert did in s.knowledge.snapshot()[1]
    assert c.cookies.get('rag_consult')==token
    with s.runs.db() as store:
        assert not store.all('public_document_grants')
        store.put('network_sources',sid,dict(id=sid,name='Test source',root_path=str(root),connection_status='ACCESS_DENIED'))
    assert did not in s.knowledge.snapshot()[1]

def test_consult_network_configuration_fails_closed():
    import pytest
    from rag_arbiter.config import ConsultConfig
    for cidr in ('0.0.0.0/0','::/0','8.8.8.0/24','invalid'):
        with pytest.raises(ValueError):ConsultConfig(allowed_networks=[cidr])
    assert ConsultConfig(allowed_networks=['10.23.0.0/24']).allowed_networks==['10.23.0.0/24']


def test_archive_does_not_confuse_agent_and_subagent():
    k=InternalKnowledge(None)
    with patch('rag_arbiter.application.internal_knowledge.CatalogService.snapshot',return_value={'files':[]}):
        answer=k.archive({'a':dict(title='Агентский договор.doc',source_name='C'),'b':dict(title='Субагентский договор.doc',source_name='C')},'агентск')
    assert 'По запросу: 1' in answer and 'Субагентский' not in answer

def test_partial_answer_revalidates_supported_claims_and_excludes_inventions(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);original=f.generate
    def generate(request):
        if request.context_type=='grounded_rag':
            result=original(request);body=json.loads(result.text)
            if body.get('claims'):
                body['claims'].append(dict(claim_id='C2',text='Штраф составляет 987654321 рублей.',supporting_source_ids=body['claims'][0]['supporting_source_ids']))
                body['answer']=' '.join(x['text'] for x in body['claims']);result.text=json.dumps(body)
            return result
        return original(request)
    f.generate=generate
    c.get('/consult');response=c.post('/consult/messages',json={'question':'Какая оплата?'})
    assert response.status_code==200
    assert 'Частичный ответ' in response.text and '987654321' not in response.text
    v=s.visitor(c.cookies.get('rag_consult'))[1]
    assert v.citations and v.context.referents_json
    assert all('987654321' not in x['citation']['quote'] for x in v.citations.values())

def test_general_topic_change_does_not_resolve_old_contract_references(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    c.get('/consult');assert c.post('/consult/messages',json={'question':'Какая оплата?'}).status_code==200
    v=s.visitor(c.cookies.get('rag_consult'))[1];assert v.context.referents_json
    before=len(f.inputs)
    response=c.post('/consult/messages',json={'question':'Что такое премия?'})
    assert response.status_code==200 and s.diagnostics[-1]['mode']=='GENERAL'
    assert all(not r.context and not r.question.startswith('DAY25_REFERENCES') for r in f.inputs[before:])
    routed=next(r for r in f.inputs[before:] if r.question.startswith('DAY33_PUBLIC_ROUTER'))
    payload=json.loads(routed.question[routed.question.index('{"question":'):])
    assert payload['history']==[] and payload['context']['referents_json']==[]
    assert v.context.referents_json # confirmed anchors survive a general-topic detour


def test_conversation_after_archive_never_calls_provider_or_registry(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    c.get('/consult')
    assert c.post('/consult/messages',json={'question':'Какие документы у тебя есть?'}).status_code==200
    v=s.visitor(c.cookies.get('rag_consult'))[1]
    # Reproduce an old wrong answer retained in the current session.
    v.history.extend([dict(role='USER',text='Как дела?',citations=[]),dict(role='ASSISTANT',text='Доступно для поиска: 38 документов.',citations=[])])
    f.inputs.clear();f.fail=True
    with patch.object(s.knowledge,'archive',side_effect=AssertionError('No registry for conversational turns')):
        for q in ('а я не об этом спросил','Как дела?'):
            r=c.post('/consult/messages',json={'question':q})
            assert r.status_code==200,r.text
            assert 'Готов помочь' in r.text and 'Доступно для поиска' not in r.text
            assert s.diagnostics[-1]['mode']=='GENERAL'
            assert s.diagnostics[-1]['calls']==0
    assert not f.inputs
    assert not v.history[-1]['citations']


def test_conversation_full_turn_matches_do_not_swallow_document_questions():
    from rag_arbiter.application.consult import conversational_reply
    assert conversational_reply(' Как   дела?! ',[])
    for q in ('Как дела по договору страхования?', 'Привет, с кем заключены договоры?', 'Я не об этом спросил: кто агент?', 'Какие документы у тебя есть?'):
        assert conversational_reply(q,[]) is None


def test_named_archive_followup_uses_authorized_titles_not_content_retrieval(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    grants=s.knowledge.snapshot()[1];target=next(iter(grants.values()))['title']
    original=f.generate
    def generate(request):
        if request.question.startswith('DAY33_PUBLIC_ROUTER'):
            payload=json.loads(request.question[request.question.index('{"question":'):])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='ARCHIVE',question=payload['question'],topic='Каталог',document_titles=[target],archive_filter='synthetic inflected name')))
        raise AssertionError('Archive follow-up must not invoke RAG generation')
    f.generate=generate;c.get('/consult')
    with patch.object(s.chat.rag,'prepare',side_effect=AssertionError('No content retrieval for inventory')):
        response=c.post('/consult/messages',json={'question':'Перечисли названия файлов по указанному человеку'})
    assert response.status_code==200,response.text
    assert target in response.text and 'По запросу: 1' in response.text
    assert s.diagnostics[-1]['mode']=='ARCHIVE'
    assert s.diagnostics[-1]['grounding']=='REGISTRY'
    # A hallucinated or revoked filename cannot expand the authorized corpus.
    target='Nonexistent synthetic document.docx'
    response=c.post('/consult/messages',json={'question':'Покажи список файлов по другому человеку'})
    assert response.status_code==200 and 'По запросу: 0' in response.text



def test_person_followup_uses_rag_content_with_history_and_citations(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm)
    c.get('/consult')
    assert c.post('/consult/messages',json={'question':'Какая оплата?'}).status_code==200
    original=f.generate;seen=[]
    def generate(request):
        if request.question.startswith('DAY33_PUBLIC_ROUTER'):
            payload=json.loads(request.question[request.question.index('{"question":'):]);seen.append(payload)
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question='Какая оплата?',topic='Оплата',document_titles=[])))
        return original(request)
    f.generate=generate
    with patch.object(s.knowledge,'archive',side_effect=AssertionError('No inventory instead of content')):
        response=c.post('/consult/messages',json={'question':'А что есть у Ханина?'})
    assert response.status_code==200,response.text
    assert any(item['history'] for item in seen)
    assert s.diagnostics[-1]['mode']=='DOCUMENT'
    assert s.diagnostics[-1]['citations']>0
    visitor=s.visitor(c.cookies.get('rag_consult'))[1]
    for citation in visitor.history[-1]['citations']:
        assert c.get(citation['url']).status_code==200
    assert 'Доступно для поиска' not in response.text


def test_ambiguous_followup_asks_clarification_without_inventory(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);c.get('/consult')
    def generate(request):
        assert request.question.startswith('DAY33_PUBLIC_ROUTER')
        return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question='А у него?',topic='Уточнение',clarification_question='О каком лице и каких сведениях вы спрашиваете?')))
    f.generate=generate
    with patch.object(s.knowledge,'archive',side_effect=AssertionError('No inventory')):
        response=c.post('/consult/messages',json={'question':'А у него?'})
    assert response.status_code==200 and 'О каком лице' in response.text
    assert s.diagnostics[-1]['grounding']=='CLARIFICATION'
    assert s.diagnostics[-1]['citations']==0



def test_compound_greetings_with_exhausted_llm_budget(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);c.get('/consult')
    s.cfg.daily_token_budget=1
    with patch.object(s,'reserve',side_effect=AssertionError('Greeting must not reserve LLM budget')), \
         patch.object(f,'generate',side_effect=AssertionError('Greeting must not invoke provider')):
        for question in ('Привет, как настроение?', 'Здравствуйте! Как у вас дела?', 'Добрый день, как ваше настроение?'):
            response=c.post('/consult/messages',json={'question':question})
            assert response.status_code==200,response.text
            assert 'Готов помочь' in response.text
            assert s.diagnostics[-1]['calls']==0
            assert s.diagnostics[-1]['reserved_tokens']==0
    # Substantive questions still obey the unchanged provider budget.
    response=c.post('/consult/messages',json={'question':'Привет, какая оплата по договору?'})
    assert response.status_code==429 and 'Дневной лимит' in response.json()['detail']


def test_social_clause_composition_never_discards_substantive_suffix():
    from rag_arbiter.application.consult import is_social_turn
    for q in ('Привет, как настроение?', 'ПРИВЕТ! Как дела?', 'Добрый вечер. А как вы поживаете?', 'Как твоё настроение?'):
        assert is_social_turn(q),q
    for q in ('Привет, какие выплаты?', 'Как дела по договору?', 'Как настроение влияет на страховку?', 'Здравствуйте! Что есть у Ханина?', 'Приветствие в договоре', 'Привет, как дела? Покажи документы.'):
        assert not is_social_turn(q),q


def test_low_relevance_content_entity_reaches_existing_grounded_rag(ragweb):
    from rag_arbiter.reranking import RerankedCandidate
    c,rid,llm,_=ragweb;s,f=internal(c,llm);original=f.generate
    c.get('/consult')
    query='Что известно о Хлеб?'
    def generate(request):
        if request.question.startswith('DAY33_PUBLIC_ROUTER'):
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(mode='DOCUMENT',question=query,topic='Товар')))
        if request.context_type=='grounded_rag':
            import re
            parts=re.split(r'(?m)^(\{"reference":[^\n]+\})\n',request.context)
            i=next(i for i in range(1,len(parts),2) if 'Хлеб' in parts[i+1])
            ref=json.loads(parts[i])['reference'].strip('[]')
            fact=next(line.strip() for line in parts[i+1].splitlines() if 'Хлеб' in line)
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(answer=fact,claims=[dict(claim_id='C1',text=fact,supporting_source_ids=[ref])]),ensure_ascii=False))
        return original(request)
    f.generate=generate
    class LowRetrievalReranker:
        def rerank(self,question,candidates):
            return [RerankedCandidate(**h.model_dump(),rerank_rank=i+1,rerank_score=.01 if question==query and 'Хлеб' in h.text else .99) for i,h in enumerate(candidates)]
    c.app.state.rag.rerankers.clear();c.app.state.rag.reranker_factory=lambda cfg:LowRetrievalReranker()
    records=[]
    with patch.object(s,'purge_record',side_effect=lambda key:records.append(s.chat.rag.get(key))):
        response=c.post('/consult/messages',json={'question':query})
    assert response.status_code==200,response.text
    record=records[-1]
    assert any(h['filter_reason']=='SEMANTIC_EVIDENCE_REJECTED' and not h['accepted'] and h['rerank_score']==.99 for h in record['candidate_trace'])
    assert any(h['filter_reason']=='SEMANTIC_EVIDENCE_ACCEPTED' for h in record['candidate_trace'])
    assert record['contexts_used']>0 and s.diagnostics[-1]['citations']>0
    assert record['grounding_status']=='GROUNDED'


def test_question_relative_review_rejects_exact_but_unrelated_answer(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);c.get('/consult')
    original=f.generate
    def generate(request):
        if request.question.startswith('DAY33_EVIDENCE_CLAIMS'):
            payload=json.loads(request.question[request.question.index('{"question":'):])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(decisions=[dict(id=x['id'],status='UNRELATED',evidence=[],reason='Fact belongs to another party') for x in payload['items']])))
        return original(request)
    f.generate=generate
    response=c.post('/consult/messages',json={'question':'Какая оплата?'})
    assert response.status_code==200,response.text
    visitor=s.visitor(c.cookies.get('rag_consult'))[1]
    assert not visitor.history[-1]['citations']
    assert 'Не удалось подтвердить связь' in visitor.history[-1]['text']
    assert s.diagnostics[-1]['grounding']=='INSUFFICIENT_CONTEXT'
    assert not visitor.context.referents_json


def test_failed_evidence_review_is_not_reported_as_missing_documents(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);c.get('/consult')
    original=f.generate
    def generate(request):
        if request.question.startswith('DAY33_EVIDENCE_SELECT'):
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(decisions=[])))
        return original(request)
    f.generate=generate
    response=c.post('/consult/messages',json={'question':'Какая оплата?'})
    assert response.status_code==503,response.text
    assert 'по доступным документам' not in response.text
    assert s.diagnostics[-1]['retrieval']['stage']=='SEMANTIC_REVIEW'
    assert s.diagnostics[-1]['retrieval']['error_kind']=='ValueError'
    assert s.diagnostics[-1]['retrieval']['result_status']=='RETRIEVAL_ERROR'


def test_consult_recovers_one_truncated_generation_without_showing_it(ragweb):
    c,rid,llm,_=ragweb;s,f=internal(c,llm);c.get('/consult');original=f.generate;calls=[]
    def generate(request):
        if request.context_type=='grounded_rag':
            calls.append(request.context)
            if len(calls)==1:
                return LLMResult(model='fixture',status='TRUNCATED',finish_reason='length',text='UNFINISHED_TEST_PAYLOAD',error={'code':'OUTPUT_LIMIT'},usage=dict(total_tokens=10))
        return original(request)
    f.generate=generate
    response=c.post('/consult/messages',json={'question':'Какая оплата?'})
    assert response.status_code==200,response.text
    assert len(calls)==1 and 'UNFINISHED_TEST_PAYLOAD' not in response.text
    assert s.diagnostics[-1]['grounding']=='GROUNDED'
    assert 'provider_error' not in s.diagnostics[-1]
