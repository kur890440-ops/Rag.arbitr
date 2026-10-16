"""LOCAL FIXTURE TEST: real Docling, MCP, BGE-M3 and Qdrant. No cloud calls."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_docx import make_docx, QUESTION, FACT
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.views import ResultsService
from rag_arbiter.network.sync import SyncService


def main():
    folder=Path('data/day31-e2e/local-model').resolve();folder.mkdir(parents=True,exist_ok=True)
    source_path=folder/'source';source_path.mkdir(exist_ok=True)
    make_docx(source_path/'insurance.docx')
    cfg=Config.load().model_copy(deep=True)
    cfg.device='cpu'
    cfg.sqlite_path=folder/'metadata.db';cfg.qdrant_path=folder/'qdrant'
    cfg.corpus_path=folder/'corpus';cfg.manifest_path=folder/'manifest.json'
    cfg.web_data_path=folder/'web';cfg.uploads_path=folder/'uploads';cfg.report_path=folder/'report.html'
    cfg.network_sources.allowed_roots=[str(source_path)]
    cfg.network_sources.stability_seconds=0
    cfg.network_sources.enabled=False
    runs=RunService(cfg);sync=SyncService(runs)
    try:
        source=sync.add(dict(name='LOCAL FIXTURE TEST — real BGE',root_path=str(source_path)))
        def scan():
            sid=sync.start(source['id'])
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                with runs.db() as store:record=store.get('network_scans',sid)
                if record['status'] not in {'QUEUED','RUNNING'}:return record
                time.sleep(.2)
            sync.cancel(sid);raise TimeoutError('Local fixture scan timeout')
        first=scan();file=sync.page('network_files',source['id'])[0]
        evidence=dict(classification='LOCAL FIXTURE TEST',components='real Docling + MCP stdio + BGE-M3 + Qdrant',
            source_id=source['id'],scan=first,file=file,question=QUESTION,chat='NOT RUN; this test does not call generation')
        if first['ingestion_completed']==1:
            result=ResultsService(runs).search(file['run_id'],QUESTION)
            evidence['retrieval']={strategy:[{k:h.get(k) for k in ('document_id','chunk_id','file_name','score','text','page_start','section')} for h in group['hits']] for strategy,group in result.items()}
            evidence['correct_fragment_found']=any(FACT in h['text'] for g in result.values() for h in g['hits'])
            evidence['repeat_scan']=scan()
        (folder.parent/'local-model-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        assert first['ingestion_completed']==1,evidence
        assert evidence['correct_fragment_found']
        assert evidence['repeat_scan']['ingestion_queued']==0
        print('PASS: LOCAL FIXTURE, real BGE-M3 retrieval; Chat NOT RUN')
    finally:
        sync.close();runs.close()


if __name__=='__main__':main()
