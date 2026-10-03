"""Local retrieval-only verification. In-memory comparisons; no API/production writes."""
import copy
import json
import re
from pathlib import Path
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.application.runs import RunService
from rag_arbiter.config import Config
from rag_arbiter.llm import LLMResult
from scripts.discover_day24_questions import OUT,sha,verify


class NoGeneration:
    def __init__(self,cfg):self.model=cfg.model
    def generate(self,request):
        # Same production retrieval/selection, but generation stops here locally.
        assert request.context_type=='grounded_rag'
        return LLMResult(model=self.model,status='NOT_RUN',request_count=0,error={'code':'LOCAL_RETRIEVAL_ONLY'})


class DryRun(RAGComparisonService):
    def __init__(self,runs):
        self.records={}
        super().__init__(runs,llm_factory=NoGeneration)
    def recover(self):pass
    def put(self,r,table='rag_comparison_runs'):
        assert table=='rag_comparison_runs'
        self.records[r['comparison_run_id']]=copy.deepcopy(r)
    def get(self,key,table='rag_comparison_runs',public=True):
        assert table=='rag_comparison_runs'
        return copy.deepcopy(self.records[key])


def audit_absence(data):
    patterns={
        'surname_cyrillic':r'дорош[её]н|доро[шщ]|дорош',
        'surname_latin':r'doroshen|doros',
        'full_name_variant':r'владимир.{0,30}евгень|в[.]\s*е[.]\s*доро',
        'email_labels':r'e[ -]?mail|электронн.{0,30}(почт|адрес)|эл[.]?\s*почт',
        'email_addresses':r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-ZА-Я]{2,}',
        'reply_contact':r'обратн.{0,20}связ|для\s+связи|контакт',
    }
    matches={k:[] for k in patterns};docs=[];pages=0;blocks=0
    for d in data['documents']:
        text='\n'.join(b['text'] for b in d['blocks'])
        docs.append(dict(document_id=d['document_id'],file_title=d['file_name'],pages=len(d['pages']),
            complete=d['status']=='SUCCESS' and not d.get('failed_pages'),normalized_text_sha256=sha(text)))
        pages+=len(d['pages'])
        for b in d['blocks']:
            blocks+=1
            for label,pattern in patterns.items():
                for m in re.finditer(pattern,b['text'],re.I):
                    matches[label].append(dict(document_id=d['document_id'],file_title=d['file_name'],page=b['page_number'],
                        source_block_id=b['block_id'],match=m.group(),context=b['text'][max(0,m.start()-120):m.end()+180]))
    relevant_ids={m['document_id'] for k in ('surname_cyrillic','surname_latin','full_name_variant') for m in matches[k]}
    emails=[m for m in matches['email_addresses']]
    related_emails=[m for m in emails if m['document_id'] in relevant_ids]
    assert not related_emails,'A related document contains email; inspect before labeling unanswerable'
    return dict(search_terms_checked=patterns,matching_blocks=matches,documents=docs,
        corpus_coverage=dict(documents=len(docs),normalized_pages=pages,blocks=blocks,complete=all(d['complete'] for d in docs)),
        person_document_ids=sorted(relevant_ids),email_addresses_in_person_documents=related_emails,
        finding='Во всех документах с упоминаниями заявителя нет адресов электронной почты. Все email в других документах проверяются отдельно; почтовый адрес не является email.',
        scope='Только существующий нормализованный корпус; отсутствие email в исходном PDF этим аудитом не доказывается.',
        confidence=.92,manual_review_required=False)


def main():
    data=json.loads((OUT/'day24_discovery_snapshot.json').read_text(encoding='utf-8'))
    selected_path=OUT/'day24_control_questions.json';selected=json.loads(selected_path.read_text(encoding='utf-8'))
    pool=json.loads((OUT/'day24_candidates.json').read_text(encoding='utf-8'))
    summary=json.loads((OUT/'day24_discovery_summary.json').read_text(encoding='utf-8'))
    gt=lambda:sha([{k:c[k] for k in ('candidate_id','question','expected_answer','expected_claims','expected_sources')} for c in selected])
    before=gt();assert before==summary['selected_ground_truth_hash']
    runs=RunService(Config.load());service=DryRun(runs);results=[]
    with runs.db() as store:
        controls_before=sha(store.all('rag_evaluation_questions'))
        # Independent live SQLite verification, not just the discovery snapshot.
        for d in data['documents']:
            for b in d['blocks']:
                live=store.get('document_blocks',b['block_id'])
                assert live and live['text']==b['text'] and live['document_id']==b['document_id']
        live_data=copy.deepcopy(data)
        live_data['chunks']=[store.get('chunks',c['chunk_id']) for c in data['chunks']]
        verify(live_data,selected)
    absence=audit_absence(data)
    (OUT/'day24_unanswerable_verification.json').write_text(json.dumps(absence,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        for i,c in enumerate(selected,1):
            r=service.prepare(data['run_id'],question=c['question'],rag_pipeline_mode='RERANK',point_only=True)
            service.put(r);service.execute(r['comparison_run_id']);r=service.get(r['comparison_run_id'])
            assert r['minimax_requests_count']==0
            assert r['rag_result']['error'].get('code') in (None,'LOCAL_RETRIEVAL_ONLY'),r['rag_result']['error']
            hits=r['retrieved_sources'];trace=r['candidate_trace'];contexts=r['sources'];per_source=[]
            for s in c['expected_sources']:
                wanted=set(s['chunk_ids'])
                raw=[h for h in hits if h['chunk_id'] in wanted]
                reranked=[t for t in trace if t.get('chunk_id') in wanted and t.get('rerank_rank')]
                final=[h for h in contexts if s['source_block_id'] in h.get('source_block_ids',[]) and s['exact_quote'] in h['text']]
                per_source.append(dict(source_block_id=s['source_block_id'],document_id=s['document_id'],page=s['page'],
                    expected_chunk_ids=s['chunk_ids'],retrieved_source_rank=min((h['rank'] for h in raw),default=None),
                    expected_source_rank=min((t['rerank_rank'] for t in reranked),default=None),
                    rerank_score=max((t['rerank_score'] for t in reranked),default=None),
                    survived_threshold=any(t['accepted'] for t in reranked),in_final_context=bool(final)))
            closest=[{k:h.get(k) for k in ('chunk_id','document_id','file_name','page_start','page_end','section','score','rank','text')} for h in hits[:10]]
            result=dict(question_id=c['question_id'],question=c['question'],mode='RERANK',candidate_top_n=r['candidate_top_n'],
                candidate_count=r['candidates_retrieved'],after_cleanup=r['candidates_after_cleanup'],after_rerank=r['candidates_after_rerank'],
                contexts_used=r['contexts_used'],rerank_threshold=r['rerank_threshold'],per_expected_source=per_source,
                closest_candidates=closest,final_context_source_ids=[s['reference'] for s in contexts],
                minimax_requests_count=0,generation_executed=False,duration_ms=r['retrieval_duration_ms'],
                retrieval_challenge=any(not x['in_final_context'] for x in per_source))
            results.append(result)
            c['retrieval_challenge']=result['retrieval_challenge']
            c['precheck']['rag_currently_retrieves_all_sources']=all(x['in_final_context'] for x in per_source) if per_source else None
            c['precheck']['ground_truth_independent_of_retrieval']=True
            c['retrieval_dry_run']=dict(mode='RERANK',candidate_count=result['candidate_count'],per_expected_source=per_source)
            if c['expected_unanswerable']:
                absence.update(semantic_query_used=c['question'],semantic_pipeline='BGE-M3 → 20 candidates → Day23 RERANK → threshold 0.1',
                    closest_candidate_sources=closest,semantic_contexts_used=r['contexts_used'])
            (OUT/'day24_retrieval_dry_run.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(number=i,question_id=c['question_id'],sources=per_source,challenge=c['retrieval_challenge']),ensure_ascii=False),flush=True)
        assert before==gt(),'Ground truth changed during retrieval'
        with runs.db() as store:assert controls_before==sha(store.all('rag_evaluation_questions'))
        for c in pool:
            match=next((x for x in selected if x['candidate_id']==c['candidate_id']),None)
            if match:
                for k in ('retrieval_challenge','precheck','retrieval_dry_run'):c[k]=match[k]
        for name,value in [('day24_candidates.json',pool),('day24_control_questions.json',selected),('day24_unanswerable_verification.json',absence)]:
            (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        summary.update(dry_run_complete=True,ground_truth_hash_after=gt(),existing_controls_unchanged=True,
            existing_controls_sha256=controls_before,retrieval_challenges=[c['question_id'] for c in selected if c['retrieval_challenge']])
        (OUT/'day24_discovery_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
