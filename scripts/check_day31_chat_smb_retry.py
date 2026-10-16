"""Retry only the Chat stage on an isolated synthetic SMB E2E corpus."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from rag_arbiter.config import Config
from rag_arbiter.llm import MiniMaxLLMProvider
from rag_arbiter.web.app import create_app
from rag_arbiter.application.grounding import Citation,CitationBuilder

parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);args=parser.parse_args()
path=Path(args.evidence).resolve();folder=path.parent
previous=json.loads(path.read_text(encoding='utf-8'))
assert previous['classification']=='REAL SMB' and previous['test_filename'].startswith('day31-chat-e2e-')
cfg=Config.load().model_copy(deep=True);cfg.device='cpu';cfg.reranker.device='cpu'
cfg.sqlite_path=folder/'metadata.db';cfg.qdrant_path=folder/'qdrant';cfg.corpus_path=folder/'corpus'
cfg.web_data_path=folder/'web';cfg.uploads_path=folder/'uploads';cfg.manifest_path=folder/'manifest.json';cfg.report_path=folder/'report.html'
cfg.network_sources.enabled=False
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
calls=[]
def provider(settings):
    implementation=MiniMaxLLMProvider(settings)
    class Recorded:
        def generate(self,request):
            result=implementation.generate(request)
            calls.append({'purpose':request.question[:50],'result':result.model_dump()})
            (folder/'synthetic-provider-calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2),encoding='utf-8')
            return result
    return Recorded()
with TestClient(create_app(cfg,llm_factory=provider),base_url='http://127.0.0.1',headers={'X-RAG-Request':'1'}) as client:
    chat=client.app.state.chat;sync=client.app.state.network
    rows=sync.page('network_files',previous['source_id'])
    assert len(rows)==1 and rows[0]['filename']==previous['test_filename']
    sid=chat.create(None).session_id;chat.select_source(sid,'NETWORK',previous['source_id'],0)
    turn=chat.start(sid,previous['question'],0)
    print('Synthetic-only configured Chat running:',turn.turn_id,flush=True)
    chat.futures[turn.turn_id].result(timeout=900)
    result=chat.load(sid)['turns'][-1]
    with sync.runs.db() as store:
        exact=bool(result['citations_json']) and all(CitationBuilder(store).exact(Citation(**c)) for c in result['citations_json'])
    evidence=dict(classification='REAL SMB / configured Chat / synthetic content only',session_id=sid,question=previous['question'],
        answer=result.get('response',{}).get('answer_text'),turn=result,citations_exact=exact,
        isolated_document_ids=[r['document_id'] for r in rows],model=cfg.llm.model)
    output=folder/'configured-chat-evidence.json';output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Chat result:',result['status'],result['grounding_status'],'exact citations:',exact,flush=True)
    assert result['grounding_status']=='GROUNDED' and exact
    assert {s['document_id'] for s in result['sources_json']}=={rows[0]['document_id']}
    print('PASS',output,flush=True)
