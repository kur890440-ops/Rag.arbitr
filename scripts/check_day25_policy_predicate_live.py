"""Requested real Q1-Q4 and controlled Q4 replay; uses configured external LLM."""
import argparse
import time
from check_day25_policy_live import api
from rag_arbiter.documents import save_json


def send(sid,question,path):
    data=api('/api/chat/sessions/'+sid)
    api('/api/chat/sessions/'+sid+'/messages',dict(content=question,expected_version=data['task_state']['version']))
    deadline=time.monotonic()+1800
    while True:
        data=api('/api/chat/sessions/'+sid);t=data['turns'][-1]
        if t['status'] not in ('RUNNING','QUEUED'):break
        assert time.monotonic()<deadline,'Turn timeout'
        time.sleep(3)
    save_json(path,data)
    print(t['turn_id'],t['status'],t['grounding_status'],round(t['latency_ms']/1000,1),flush=True)
    assert t['status']=='COMPLETED'
    return data


def check(data):
    t=data['turns'][-1];trace=t['trace']
    assert trace['answer_mode']=='POINT_RAG'
    assert trace['policy_validation_results']
    validated={v['claim_id'] for v in trace['policy_validation_results'] if v['status'].startswith('REVALIDATED_')}
    assert {e['claim_id'] for e in trace['predicate_evaluation_results']}==validated
    allowed=set(trace['eligible_document_ids'])
    assert all(s['document_id'] in allowed for s in t['sources_json'])
    for v in trace['policy_validation_results']:
        if v['fresh_retrieval']:assert v['fresh_query']==v['claim_text']
    if trace['predicate_evaluation_results'] and all(e['status']=='NO_MATCH' for e in trace['predicate_evaluation_results']) and len(validated)==len(trace['policy_validation_results']):
        assert t['grounding_status']=='VALID_EMPTY_RESULT'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('baseline_session');args=parser.parse_args()
    old=api('/api/chat/sessions/'+args.baseline_session)
    sid=api('/api/chat/sessions',{'processing_run_id':old['session']['processing_run_id']})['session_id']
    print('NEW SESSION',sid,flush=True)
    questions=['Какие обязанности конкурсного управляющего встречаются в документах?',
        'Учитывай только судебные определения.','Жалобы исключи.',
        'Какие из этих обязанностей связаны с отчетностью?']
    for i,q in enumerate(questions):
        data=send(sid,q,'data/day25/policy_predicate_live.json')
        if i==0:assert data['turns'][-1]['grounding_status']=='GROUNDED','Q1 must establish grounded referents'
    check(data)
    replay=send(args.baseline_session,questions[-1],'data/day25/policy_predicate_same_claims_live.json')
    check(replay)
    print('PASS: Q1-Q4 and original-claim Q4 replay',flush=True)


if __name__=='__main__':main()
