"""Scripted semantic classifications for orchestration tests, never model-quality claims."""
import json
import re
from rag_arbiter.llm import LLMResult
from rag_arbiter.application.dialogue import ReferenceResolver, AnswerModeRouter
from test_exhaustive import MapLLM


class DialogueProvider:
    def generate(self,request):
        d=json.loads('{'+request.question.split('\n{',1)[1])
        if request.question.startswith('DAY25_REFERENCES'):
            refs=d['context']['referents_json']
            chosen=refs if any(s in d['current'].lower() for s in ('из них','из этих','этих обязанностей','этих причин')) else []
            body=dict(original_message=d['current'],resolved_message=d['current'],
                resolved_referents=[r['referent_id'] for r in chosen],
                candidate_prior_claim_ids=[c for r in chosen for c in r['claim_ids']],
                source_turn_ids=[r['source_turn_id'] for r in chosen],unresolved_referents=[],
                status='RESOLVED' if chosen else 'NOT_NEEDED',confidence=1)
        else:
            full=any(s in d['question'].lower() for s in ('чаще','обычно','повторяются','сравни'))
            body=dict(question_type='FREQUENCY' if full else 'LOCAL_SEMANTIC',
                answer_mode='EXHAUSTIVE_NO_RAG' if full else 'POINT_RAG',requires_full_coverage=full,
                requires_counting=False,requires_frequency_analysis=full,requires_cross_document_comparison=False,
                requires_prior_referents=bool(d['references']['resolved_referents']),reason='Scripted fixture',confidence=1)
        return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(body,ensure_ascii=False))


def install(chat,llm,cfg):
    provider=DialogueProvider()
    chat.references=ReferenceResolver(lambda _:provider,cfg)
    chat.router=AnswerModeRouter(lambda _:provider,cfg)
    original=llm.generate;maps=MapLLM()
    def generate(request):
        if request.question.startswith('DAY25_PREDICATE_EVALUATION'):
            d=json.loads('{'+request.question.split('\n{',1)[1])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'results':[
                dict(claim_id=c['claim_id'],status='MATCH',supporting_source_ids=c['supporting_source_ids'],
                     reason='Scripted predicate',confidence=1) for c in d['claims']]}))
        if request.question.startswith('DAY25_REFERENCE_OCCURRENCES'):
            d=json.loads('{'+request.question.split('\n{',1)[1])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'findings':[
                dict(finding_index=f['finding_index'],supported=True,reason='Scripted occurrence') for f in d['findings']]}))
        if request.question.startswith('DAY25_CLAIM_PREDICATE'):
            d=json.loads('{'+request.question.split('\n{',1)[1])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps({'claims':[
                dict(claim_id=c['claim_id'],matches_current_predicate=True,supporting_sources=[s['reference'] for s in d['sources'] if c['claim_text'] in s['text']],
                     relation_text=c['claim_text'],reason='Scripted relation') for c in d['candidate_prior_claims']]},ensure_ascii=False))
        if '\nUNITS:\n' in request.question or 'MERGED DATA' in request.question:
            result=maps.generate(request)
            ids=re.findall(r'"claim_id"\s*:\s*"([^"]+)"',request.question)
            if ids and '\nUNITS:\n' in request.question:
                body=json.loads(result.text)
                for f in body.get('findings',[]):f['normalized_key']=ids[0]
                result.text=json.dumps(body)
            return result
        return original(request)
    llm.generate=generate
