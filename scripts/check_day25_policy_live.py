"""User-requested Q1-Q4 production regression through the local chat API."""
import argparse
import json
import time
from urllib.request import Request,urlopen
from rag_arbiter.documents import save_json


def api(path,data=None):
    req=Request('http://127.0.0.1:8765'+path,data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-RAG-Request':'1','Origin':'http://127.0.0.1:8765'})
    with urlopen(req,timeout=30) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
    created=api('/api/chat/sessions',{'processing_run_id':args.run_id})
    sid=created.get('session_id') or created['session']['session_id']
    questions=['Какие обязанности конкурсного управляющего встречаются в документах?',
        'Учитывай только судебные определения.','Жалобы исключи.',
        'Какие из этих обязанностей связаны с отчетностью?']
    records=[]
    for n,question in enumerate(questions,1):
        data=api('/api/chat/sessions/'+sid)
        api('/api/chat/sessions/'+sid+'/messages',dict(content=question,expected_version=data['task_state']['version']))
        deadline=time.monotonic()+900
        while True:
            data=api('/api/chat/sessions/'+sid);turn=data['turns'][-1]
            if turn['status'] not in ('RUNNING','QUEUED'):break
            if time.monotonic()>deadline:raise RuntimeError('Chat turn timeout')
            time.sleep(2)
        records.append(turn)
        save_json('data/day25/policy_live_regression.json',dict(session_id=sid,turns=records))
        print(json.dumps(dict(question=n,session_id=sid,status=turn['status'],grounding=turn['grounding_status'],
            counts=turn['trace'].get('retrieval_counts'),sources=[s['file_name'] for s in turn['sources_json']]),ensure_ascii=False),flush=True)
        assert turn['status']=='COMPLETED', 'Turn did not complete; inspect saved trace'
    policy=records[-1]['trace']['retrieval_policy_json']
    assert any(c['values']==['COURT_RULING'] for c in policy['include_filters'])
    assert any(c['values']==['COMPLAINT'] for c in policy['exclude_filters'])
    candidates=records[-1]['trace']['retrieved_candidates'];sources=records[-1]['sources_json']
    assert candidates, 'No candidates retrieved'
    assert all(policy['document_metadata'][h['document_id']]['document_type']=='COURT_RULING' for h in candidates+sources)
    print('Q1-Q4 POLICY PASS',flush=True)


if __name__=='__main__':main()
