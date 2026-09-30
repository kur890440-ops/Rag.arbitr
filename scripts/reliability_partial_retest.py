"""C: replay real three-page source/results with one unresolved page; no GPU calls."""
import json,sys,os
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from pathlib import Path
from collections import defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pypdfium2 as pdfium
from rag_arbiter.config import Config
from rag_arbiter.storage import MetadataStore
from rag_arbiter.documents import digest,file_hash,save_json
from rag_arbiter.recognition.models import RecognitionResult
from rag_arbiter.pipeline import Pipeline

cfg=Config.load();store=MetadataStore(cfg.sqlite_path)
run=store.get('processing_runs','024999b6f575409c8ef62157e4adb37f')
groups=defaultdict(dict)
for m in store.all('recognition_metadata'):
    groups[m['document_id']][m['page_number']]=m
source=None
for path in Path(run['config_json']['corpus_path']).rglob('*.pdf'):
    key=digest([str(path.resolve()),file_hash(path)])
    rows=groups[key]
    if all(i in rows for i in (1,2,3)) and all(rows[i]['status']!='FAILED' for i in (1,2)) and rows[3]['status']=='FAILED':
        source=path;break
assert source is not None
store.close()
root=Path('data/reliability-C');(root/'corpus').mkdir(parents=True,exist_ok=True)
with pdfium.PdfDocument(source) as original:
    with pdfium.PdfDocument.new() as target:
        target.import_pages(original,[0,1,2])
        target.save(root/'corpus'/'three-pages.pdf')
class Replay:
    def identity(self):return rows[1]['settings']
    def preflight(self):return {'effective_device':'replay'}
    def close(self):pass
    def recognize_page(self,req):
        result=RecognitionResult.model_validate_json(Path(rows[req.page_number]['normalized_output_path']).read_text(encoding='utf-8'))
        result.raw_output=Path(rows[req.page_number]['raw_output_path']).read_text(encoding='utf-8')
        return result
cfg.corpus_path=root/'corpus';cfg.sqlite_path=root/'metadata.db';cfg.qdrant_path=root/'qdrant'
cfg.report_path=root/'report.html';cfg.snapshot_path=root/'snapshot.json';cfg.manifest_path=root/'manifest.json'
pipeline=Pipeline(cfg,ocr=Replay())
try:
    snapshot=pipeline.day21(do_evaluate=False)
    doc=pipeline.store.all('documents')[0]
    assert doc['status']=='PARTIAL' and doc['failed_pages']==[3]
    assert all(b['page_number'] in (1,2) for b in doc['blocks'])
    assert all(i['chunks']>0 for i in snapshot['indexes'].values())
    save_json(root/'evidence.json',{'source':str(source),'method':'real PDF pages 1-3 and saved Qwen outputs; unresolved page 3 deliberately replayed to test partial indexing',
        'status':snapshot['status'],'document_status':doc['status'],'failed_pages':doc['failed_pages'],
        'indexes':{k:{x:v[x] for x in ('status','pages','chunks')} for k,v in snapshot['indexes'].items()},'actual_qwen_calls':0})
finally:pipeline.close()
