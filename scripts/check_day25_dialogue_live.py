"""Explicitly authorized real-corpus Q1-Q5 regression; calls configured external LLM."""
import argparse
import time
from check_day25_policy_live import api
from rag_arbiter.documents import save_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
    sid=api('/api/chat/sessions',{'processing_run_id':args.run_id})['session_id']
    questions=['Какие обязанности конкурсного управляющего встречаются в документах?',
        'Учитывай только судебные определения.','Жалобы исключи.',
        'Какие из этих обязанностей связаны с отчетностью?',
        'Какие из них встречаются чаще всего?']
    for n,q in enumerate(questions,1):
        data=api('/api/chat/sessions/'+sid)
        api('/api/chat/sessions/'+sid+'/messages',dict(content=q,expected_version=data['task_state']['version']))
        deadline=time.monotonic()+1800
        while True:
            data=api('/api/chat/sessions/'+sid);turn=data['turns'][-1]
            if turn['status'] not in ('RUNNING','QUEUED'):break
            if time.monotonic()>deadline:raise RuntimeError('Turn timeout')
            time.sleep(2)
        save_json('data/day25/dialogue_live_regression.json',data)
        print(f"Q{n}: {turn['status']} / {turn['grounding_status']} / {turn['trace'].get('answer_mode')}",flush=True)
        assert turn['status']=='COMPLETED', 'Inspect persisted trace'
    q4,q5=data['turns'][3:5]
    assert q4['trace']['candidate_prior_claim_ids'] and q4['trace']['answer_mode']=='POINT_RAG'
    assert q4['grounding_status']=='GROUNDED'
    assert q5['trace']['answer_mode']=='EXHAUSTIVE_NO_RAG' and q5['trace']['coverage']['coverage_percent']==100
    assert q5['grounding_status']=='FULL_COVERAGE'
    assert set(q5['trace']['coverage']['eligible_document_ids'])==set(q5['trace']['eligible_document_ids'])
    reloaded=api('/api/chat/sessions/'+sid)
    assert data['dialogue_context']==reloaded['dialogue_context']
    fresh=api('/api/chat/sessions',{'processing_run_id':args.run_id})['session_id']
    assert not api('/api/chat/sessions/'+fresh)['dialogue_context']['referents_json']
    print('Q1-Q5 PASS; reload and new chat PASS',flush=True)


if __name__=='__main__':main()
