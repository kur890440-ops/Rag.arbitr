import json
import pytest
from rag_arbiter.application.contract_extraction import ContractFact,validate_fields,merge_contracts,render_contracts
from rag_arbiter.application.exhaustive import ExhaustiveContextBatcher,ExhaustiveNoRAG
from rag_arbiter.application.dialogue import ConsultIntent,WorkingContextService,DialogueWorkingContext
from rag_arbiter.application.chat_models import ChatTurn
from rag_arbiter.llm import LLMConfig

QUOTE='Субагентский договор № 123 от 01.02.2026. ООО Альфа, именуемое Агент, и ИП Иванов, именуемый Субагент, заключили договор.'
def fact(**changes):
 d=dict(is_relevant_contract=True,contract_type='Субагентский договор',contract_number='123',contract_date='01.02.2026',subagent_name='ИП Иванов',other_party_name='ООО Альфа',party_roles={'ИП Иванов':'Субагент','ООО Альфа':'Агент'},status='CONFIRMED');d.update(changes);return d

def extracted(did='d1',title='нейтральное.docx',text=QUOTE,fields=None):
 doc=dict(document_id=did,file_name=title,page_count=0,pages=[],locator_kind='structure',blocks=[dict(block_id=did+'-b',page_number=0,reading_order=0,text=text)],cache_identity='v1')
 batch=ExhaustiveContextBatcher(LLMConfig(full_document_context_budget=24000),'С кем?',True).build([doc])[0]
 payload=dict(relevant=True,findings=[dict(unit_id='U1',statement='Стороны договора',evidence_text=text,contract=fields or fact())])
 return ExhaustiveNoRAG.parse(json.dumps(payload,ensure_ascii=False),batch)

def test_canonical_party_evidence_neutral_filename():
 r=extracted();f=r.findings[0]
 assert f.contract.subagent_name=='ИП Иванов' and f.block_id=='d1-b' and f.char_start==0
 rows,d=merge_contracts([r],{'d1':'active-v1'})
 assert rows[0]['document_version_id']=='active-v1'
 text,citations,claims=render_contracts(rows,dict(documents_total=1,documents_covered=1,coverage_percent=100))
 assert 'ИП Иванов' in text and 'INDEXED' not in text and 'нейтральное.docx' not in text
 assert citations[0]['quote']==QUOTE and claims[0]['support_status']=='SUPPORTED'

@pytest.mark.parametrize('field,value',[('subagent_name','Петров'),('contract_number','999'),('contract_date','01.01.1999'),('other_party_name','Бета')])
def test_filename_and_hallucinated_fields_not_evidence(field,value):
 with pytest.raises(ValueError):extracted(title='Петров 999.doc',fields=fact(**{field:value}))

def test_role_required_not_just_name():
 with pytest.raises(ValueError):validate_fields(ContractFact(**fact(party_roles={})),QUOTE)

def test_duplicate_copies_exact_identity_and_distinct_contracts():
 first=extracted();copy=extracted('d2');second=extracted('d3',text=QUOTE.replace('123','124'),fields=fact(contract_number='124'))
 rows,count=merge_contracts([first,first,copy,second],{'d1':'v1','d2':'v2','d3':'v3'})
 assert len(rows)==2 and count==2
 text,_,_=render_contracts(rows,dict(documents_total=3,documents_covered=3,coverage_percent=100))
 assert 'Уникальных субагентов: 1' in text
 assert not merge_contracts([first],{})[0] # no unpinned versions

def test_partial_missing_identity_and_ambiguous_counterparty():
 partial=extracted(fields=fact(subagent_name=None,party_roles={},status='PARTIAL'))
 rows,_=merge_contracts([partial],{'d1':'v1'})
 text,_,claims=render_contracts(rows,dict(documents_total=4,documents_covered=1,coverage_percent=25))
 assert not claims and 'не установлен' in text and 'Охват неполный' in text


def test_confirmed_party_referents_use_existing_context_service():
 rows,_=merge_contracts([extracted()],{'d1':'v1'})
 _,citations,claims=render_contracts(rows,{})
 turn=ChatTurn(session_id='s',user_message_id='u',original_question='С кем?',task_state_version=0,owner_pid=0,owner_started=0,assistant_message_id='a',grounding_status='GROUNDED',citations_json=citations)
 context=WorkingContextService.update(DialogueWorkingContext(session_id='s'),turn,claims)
 assert context.referents_json and 'ИП Иванов' in context.referents_json[0].canonical_text


def test_old_router_contract_remains_compatible():
 for mode in ('GENERAL','ARCHIVE','DOCUMENT','MIXED'):
  intent=ConsultIntent(mode=mode,question='q',topic='t')
  assert intent.extraction=='FACTS' and not intent.full_coverage

def test_selected_blocks_number_date_and_party_share_canonical_provenance():
 from rag_arbiter.application.contract_extraction import parse_contract
 doc=dict(document_id='d',file_name='Петров.doc',page_count=0,pages=[],locator_kind='structure',blocks=[dict(block_id='heading',page_number=0,reading_order=0,text='Договор № 123 от 01.02.2026'),dict(block_id='preamble',page_number=0,reading_order=1,text='ООО Альфа — Агент, ИП Иванов — Субагент.')])
 batch=ExhaustiveContextBatcher(LLMConfig(full_document_context_budget=24000),'С кем?',True).build([doc])[0]
 result=parse_contract(json.dumps(dict(contracts=[dict(unit_ids=['U1','U2'],contract=fact())])),batch)
 assert {f.block_id for f in result.findings}=={'heading','preamble'}
 partial=parse_contract(json.dumps(dict(contracts=[dict(unit_ids=['U2'],contract=fact())])),batch)
 assert partial.findings and partial.findings[0].contract.status=='PARTIAL'
 assert partial.findings[0].contract.contract_number is None
 assert partial.findings[0].contract.contract_date is None
 with pytest.raises(ValueError):parse_contract(json.dumps(dict(contracts=[dict(unit_ids=['U9'],contract=fact())])),batch)
 assert not parse_contract('{"contracts":[]}',batch).findings


@pytest.mark.parametrize('supported',[True,False])
def test_exhaustive_contract_pipeline_role_veto_and_all_documents(ragweb,monkeypatch,supported):
 from rag_arbiter.llm import LLMResult
 c,rid,_,_=ragweb;runs=c.app.state.runs
 source=[]
 for i in range(3):
  source.append(dict(document_id='d'+str(i),file_name='report.doc' if i==2 else 'neutral.docx',page_count=0,pages=[],locator_kind='structure',blocks=[dict(block_id='b'+str(i),page_number=0,reading_order=0,text=QUOTE)],cache_identity='v'+str(i)))
 class Provider:
  def generate(self,request):
   if request.question.startswith('SEMANTIC_CONTRACT_EXTRACTION_V2'):
    data=json.loads(request.question[request.question.index('{"question":'):]);unit=data['units'][0]
    body=dict(contracts=[] if unit['file_title']=='report.doc' else [dict(unit_ids=['U1'],contract=fact())])
   else:
    data=json.loads(request.question[request.question.index('{"question":'):])
    body=dict(checks=[dict(index=f['index'],supported=supported,reason='Independent role check') for f in data['findings']])
   return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(body),request_count=1)
 engine=ExhaustiveNoRAG(runs,lambda cfg:Provider());monkeypatch.setattr(engine,'load',lambda *a,**kw:source)
 record=dict(comparison_run_id='semantic-fixture',processing_run_id=rid,document_id=None,question_text='С кем заключены договоры?',full_document_context={},generation_call_count=0,minimax_requests_count=0,contract_extraction=True,corpus_snapshot={'corpus':{'documents':[dict(document_id=d['document_id'],recognition_version=d['cache_identity']) for d in source]}})
 engine.run(record,LLMConfig(full_document_context_budget=24000),lambda r:None)
 ex=record['exhaustive'];assert ex['documents_total']==3 and ex['documents_covered']==3
 assert len(ex['contracts'])==int(supported) and ex['confirmed_contracts']==int(supported)
 if supported:assert len(ex['contracts'][0]['evidence_blocks'])==2
 assert ex['status']=='SUCCESS'

from test_rag import ragweb

def test_exhaustive_summary_unverified_keeps_verified_fact_without_quote_dump():
 from rag_arbiter.application.contract_extraction import render_exhaustive_answer
 from rag_arbiter.llm import LLMResult
 r=dict(question_text='When is the report due?',full_document_status='SUCCESS',full_document_answer='Invented deadline.',exhaustive=dict(documents_total=1,documents_covered=1,merged_findings=[dict(reference='E1',statements=['The report is due on day 5.'],evidence=[dict(document_id='d',file_title='doc',evidence_text='Report on day 5.',statement='The report is due on day 5.',validation_status='CONFIRMED',block_id='b',page=0)])]))
 class Verify:
  def generate(self,req):return LLMResult(model='fixture',status='SUCCESS',text='{"checks":[{"index":0,"supported":false,"reason":"unsupported"}]}')
 text,citations=render_exhaustive_answer(r,lambda cfg:Verify(),LLMConfig(full_document_context_budget=12000))
 assert 'Invented deadline' not in text and 'The report is due on day 5.' in text
 assert citations[0]['locator']['block_id']=='b'


def test_confirmed_party_with_unknown_date_keeps_referent():
 from rag_arbiter.application.contract_extraction import party_confirmed
 rows,_=merge_contracts([extracted(fields=fact(contract_date=None,status='PARTIAL'))],{'d1':'v1'})
 assert party_confirmed(rows[0])
 text,citations,claims=render_contracts(rows,dict(documents_total=1,documents_covered=1,coverage_percent=100))
 assert claims and 'дата: не установлена' in text and 'Подтверждено договоров: 1' in text

def test_consult_three_turn_scope_and_compact_contract_referents(ragweb,monkeypatch):
 from test_internal_consultant import internal
 from rag_arbiter.application.dialogue import AnswerModeRouter,ReferenceResolver,ReferenceResolutionResult
 from rag_arbiter.application.exhaustive import ExhaustiveNoRAG
 c,_,llm,_=ragweb;s,_=internal(c,llm);_,grants=s.knowledge.snapshot();did=next(iter(grants));g=grants[did]
 def route(self,q,history,context,titles):
  return ConsultIntent(reference_kind='NEW_SUBJECT' if q=='fresh' else 'ANAPHORA',mode='DOCUMENT',question=q,topic='стороны договоров',full_coverage=True,extraction='CONTRACT_PARTIES' if q=='parties' else 'FACTS')
 monkeypatch.setattr(AnswerModeRouter,'consult',route)
 references=[]
 def resolve(self,message,state,context,history):
  if message.content=='fresh':raise AssertionError('New explicit subject must not invoke old-reference resolver')
  ref=context.referents_json[0];references.append(ref)
  return ReferenceResolutionResult(original_message=message.content,resolved_message=message.content,status='RESOLVED',confidence=1,resolved_referents=[ref.referent_id],candidate_prior_claim_ids=ref.claim_ids,source_turn_ids=[ref.source_turn_id])
 monkeypatch.setattr(ReferenceResolver,'resolve',resolve)
 scopes=[]
 def run(self,record,config,save):
  scopes.append(record['retrieval_policy_json']['eligible_document_ids'])
  record['full_document_status']='SUCCESS'
  evidence=[dict(document_id=did,document_version_id=g['recognition_version'],file_title=g['title'],quote=QUOTE,exact_match=True,locator={'block_id':str(i)}) for i in range(8)]
  row=dict(**fact(),document_id=did,document_version_id=g['recognition_version'],evidence_blocks=evidence)
  record['exhaustive']=dict(contracts=[row],documents_total=len(scopes[-1]),documents_covered=len(scopes[-1]),coverage_percent=100,merged_findings=[])
 monkeypatch.setattr(ExhaustiveNoRAG,'run',run)
 token,v=s.visitor(create=True)
 for q in ('parties','duties','reporting'):s.answer(token,'127.0.0.1',q)
 assert len(references)==2 and set(scopes[0])==set(grants)
 assert scopes[1:]==[[did],[did]]
 assert len(v.context.entities_json)==1 and v.context.referents_json
 assert len(v.context.model_dump_json().encode())<s.cfg.history_budget
 assert len(v.diagnostics)==3 and all(x['answer_mode']=='EXHAUSTIVE_NO_RAG' for x in v.diagnostics)
 s.answer(token,'127.0.0.1','fresh')
 assert not v.context.entities_json and not v.context.referents_json
 assert set(scopes[-1])==set(grants)

def test_typed_party_intent_cannot_render_inventory_or_filter_by_filename():
 intent=ConsultIntent(mode='ARCHIVE',question='С кем?',topic='стороны',extraction='CONTRACT_PARTIES',document_titles=['suggested.doc'])
 assert intent.mode=='DOCUMENT' and intent.full_coverage and not intent.document_titles



def test_contract_validation_metadata_is_owned_by_application():
 from rag_arbiter.application.contract_extraction import parse_contract,contract_request
 doc=dict(document_id='d',file_name='neutral.doc',page_count=0,pages=[],locator_kind='structure',blocks=[dict(block_id='b',page_number=0,reading_order=0,text=QUOTE)])
 batch=ExhaustiveContextBatcher(LLMConfig(full_document_context_budget=24000),'parties',True).build([doc])[0]
 fields=fact();fields['field_validation']={'party_roles':{'untrusted':{'nested':'model data'}}}
 result=parse_contract(json.dumps(dict(contracts=[dict(unit_ids=['U1'],contract=fields)])),batch)
 assert result.findings[0].contract.field_validation['subagent_name']['status']=='CONFIRMED'
 request=contract_request('question',[])
 schema=json.loads(request.question[request.question.index('{"question":'):])['schema']
 assert 'field_validation' not in schema['$defs']['ContractFact']['properties']


def test_long_conversation_text_keeps_last_question_and_verified_anchors(ragweb,monkeypatch):
 from test_internal_consultant import internal
 from rag_arbiter.application.dialogue import Referent
 c,_,llm,_=ragweb;s,_=internal(c,llm)
 token,v=s.visitor(create=True)
 v.context.referents_json=[Referent(referent_id='verified',label='set',canonical_text='Verified fact',claim_ids=['C1'],source_turn_id='t',source_message_id='m')]
 v.context.entities_json=[dict(kind='CONTRACT',document_id='d',document_version_id='v')]
 monkeypatch.setattr('rag_arbiter.application.consult.conversational_reply',lambda *args:'Long answer. '*2000)
 s.answer(token,'127.0.0.1','new question')
 assert len(v.history)==2 and v.history[0]['text']=='new question'
 assert v.context.referents_json[0].referent_id=='verified'
 assert v.context.entities_json[0]['document_version_id']=='v'
 assert sum(len(m['text'].encode()) for m in v.history)<=s.cfg.history_budget
