"""Explicitly bounded real retest: previous order/truncation pages, then 20 pages twice."""
import json
import sys
import time
import shutil
import os
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag_arbiter.config import Config
from rag_arbiter.storage import MetadataStore
from rag_arbiter.documents import save_json, file_hash
from rag_arbiter.recognition.models import PageOutput, RecognitionRequest, RuntimeResponse
from rag_arbiter.recognition.providers import normalize_page, Qwen3VLRecognitionProvider
from rag_arbiter.recognition.runtime import OllamaRuntime
from rag_arbiter.pipeline import Pipeline

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding='utf-8', errors='backslashreplace')
root = Path('data/reliability-retest')
root.mkdir(parents=True, exist_ok=True)
cfg = Config.load()
store = MetadataStore(cfg.sqlite_path)
run = store.get('processing_runs', '024999b6f575409c8ef62157e4adb37f')
docids = {p.name for p in Path(run['config_json']['corpus_path']).iterdir() if p.is_dir()}
events = store.db.execute("SELECT data FROM processing_run_events WHERE run_id=?", (run['run_id'],)).fetchall()
ids = {json.loads(e[0]).get('metrics',{}).get('recognition_id') for e in events}
records = [m for m in store.all('recognition_metadata') if m.get('recognition_id', Path(m['normalized_output_path']).parent.name) in ids]
store.close()
conflict = next(m for m in records if 'Duplicate reading' in m['diagnostics'].get('error',''))
truncated = next(m for m in records if m['diagnostics'].get('done_reason') == 'length')
raw = json.loads(Path(conflict['raw_output_path']).read_text(encoding='utf-8'))
output = PageOutput.model_validate_json(raw['message']['content'])
blocks, _, warnings = normalize_page(output)
evidence = {'A': {'source':conflict['image_path'], 'status':'UNCERTAIN', 'blocks':len(blocks), 'warnings':warnings}}
assert blocks and any('UNCERTAIN' in w for w in warnings)
save_json(root/'evidence.json', evidence)

class ReplayTruncated(OllamaRuntime):
    def __init__(self, settings):
        super().__init__(settings)
        self.replayed=False
        self.actual_calls=0
    def generate(self, request, prompt, schema):
        if not self.replayed:
            self.replayed=True
            wire=Path(truncated['raw_output_path']).read_text(encoding='utf-8')
            return RuntimeResponse(raw=wire, content=json.loads(wire)['message']['content'], device='gpu', diagnostics=truncated['diagnostics'])
        self.actual_calls+=1
        return super().generate(request,prompt,schema)

runtime=ReplayTruncated(cfg.recognition)
provider=Qwen3VLRecognitionProvider(cfg,runtime)
req=RecognitionRequest(document_id='retest-B',document_hash='original-page',page_number=truncated['page_number'],
    image_path=Path(truncated['image_path']),image_hash=truncated['image_hash'],language_hints=['ru'],render_settings={'dpi':200},attempt_dir=root/'B-attempts')
try:
    result=provider.recognize_page(req)
    evidence['B']={'source':truncated['image_path'],'initial':'replay of original explicit truncation; recovery uses real Ollama',
                   'status':result.status,'provider':result.provider,'actual_qwen_calls':runtime.actual_calls,'diagnostics':result.diagnostics}
    save_json(root/'B-result.json', result.model_dump(exclude={'docling_document'}))
finally:
    provider.close()
save_json(root/'evidence.json', evidence)
assert result.status != 'FAILED', 'B unresolved; medium test stopped'

# Twenty rendered pages from multiple real PDFs, preserving a source mapping.
selected=[]
for m in [conflict,truncated,*records]:
    if m['image_path'] not in [x['image_path'] for x in selected]: selected.append(m)
    if len(selected)==20:break
assert len(selected)==20
corpus=root/'corpus';corpus.mkdir(exist_ok=True)
for n,m in enumerate(selected):shutil.copyfile(m['image_path'],corpus/f'{n:02}.png')
save_json(root/'sources.json',selected)
cfg.corpus_path=corpus
cfg.sqlite_path=root/'metadata.db'
cfg.qdrant_path=root/'qdrant'
cfg.manifest_path=root/'manifest.json'
cfg.snapshot_path=root/'snapshot.json'
cfg.report_path=root/'report.html'
cfg.queries_path=root/'no-evaluation.json'
class CountingRuntime(OllamaRuntime):
    calls=0
    def generate(self,*args):
        self.calls+=1
        return super().generate(*args)
for label in ('medium','repeat'):
    runtime=CountingRuntime(cfg.recognition)
    pipeline=Pipeline(cfg,ocr=Qwen3VLRecognitionProvider(cfg,runtime))
    started=time.perf_counter()
    try:
        snap=pipeline.day21(do_evaluate=False)
        evidence[label]={'status':snap['status'],'pages':20,'source_documents':len({m['document_id'] for m in selected}),
                         'duration':time.perf_counter()-started,'actual_qwen_calls':runtime.calls,
                         'cache_hits':snap['recognition']['cache_hits'],'reliability':snap['recognition']['reliability'],
                         'indexes':{k:{x:v[x] for x in ('status','documents','chunks')} for k,v in snap['indexes'].items()}}
        save_json(root/'evidence.json',evidence)
        print(label,json.dumps(evidence[label]),flush=True)
    finally:pipeline.close()
    if label=='medium' and snap['recognition']['reliability']['failed']:
        raise SystemExit('Medium has unresolved pages; no full run authorized')
