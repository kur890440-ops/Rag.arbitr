"""Small sequential real benchmark against archived results; never modifies run/corpus."""
import os
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
import sys,json,time,re
from collections import defaultdict,Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag_arbiter.config import Config
from rag_arbiter.storage import MetadataStore
from rag_arbiter.documents import save_json
from rag_arbiter.recognition import Qwen3VLRecognitionProvider,RecognitionRequest

sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace',line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8',errors='backslashreplace')
root=Path('data/budget-comparison');root.mkdir(parents=True,exist_ok=True)
store=MetadataStore(Config.load().sqlite_path)
run=store.get('processing_runs','0122abdf69ef4b1c8dab13d233c5d9b5')
assert run['status']=='CANCELLED', 'Wait for cooperative cancellation before GPU benchmark'
ids={r[0] for r in store.db.execute("SELECT json_extract(data,'$.metrics.recognition_id') FROM processing_run_events WHERE run_id=? AND json_extract(data,'$.event_type')='recognition_completed'",(run['run_id'],))}
groups=defaultdict(list)
for key in ids:
    meta=store.get('recognition_metadata',key)
    if not meta or meta['status']=='FAILED':continue
    d=meta['diagnostics']
    category='fallback' if d.get('fallback_used') else 'split' if d.get('fragmented') else 'retry' if d.get('retried') else 'direct'
    groups[category].append(meta)
for values in groups.values():values.sort(key=lambda m:(m['document_id'],m['page_number']))
selected=[('direct',groups['direct'][len(groups['direct'])//2]),('retry',groups['retry'][len(groups['retry'])//2]),
          ('split',groups['split'][len(groups['split'])//3]),('split',groups['split'][2*len(groups['split'])//3]),
          ('fallback',groups['fallback'][len(groups['fallback'])//2])]
store.close()
cfg=Config(**run['config_json']);cfg.recognition.max_generation_tokens=12288
cfg.recognition.split_after_first_truncation=True
provider=Qwen3VLRecognitionProvider(cfg)
report={'baseline_run':run['run_id'],'baseline_pages':run['processed_pages'],'sample_method':'stratified 1 direct, 1 retry, 2 split, 1 fallback; archived baseline, fresh candidate; not randomized A/B',
        'candidate':cfg.recognition.model_dump(),'results':[]}
save_json(root/'comparison.json',report)
try:
    provider.preflight()
    for n,(category,meta) in enumerate(selected):
        old=json.loads(Path(meta['normalized_output_path']).read_text(encoding='utf-8'))
        request=RecognitionRequest(document_id=meta['document_id'],document_hash=meta.get('document_hash','archived'),
            page_number=meta['page_number'],image_path=Path(meta['image_path']),image_hash=meta['image_hash'],
            language_hints=cfg.recognition.language_hints,render_settings=meta.get('render_settings',{'dpi':cfg.render_dpi}),attempt_dir=root/f'page-{n}')
        print(f'BEGIN {n} {category} page={request.page_number}',flush=True)
        result=provider.recognize_page(request)
        save_json(root/f'result-{n}.json',result.model_dump(exclude={'docling_document'}))
        def words(s):return Counter(re.findall(r'\w+',s.lower()))
        before,after=words(old['normalized_text']),words(result.normalized_text)
        numbers_before=Counter(re.findall(r'\d+(?:[.,]\d+)*',old['normalized_text']))
        numbers_after=Counter(re.findall(r'\d+(?:[.,]\d+)*',result.normalized_text))
        row=dict(sample=n,category=category,document_id=meta['document_id'],page_number=meta['page_number'],image_path=meta['image_path'],
            baseline_result=meta['normalized_output_path'],baseline_sec=meta['duration_ms']/1000,candidate_sec=result.duration_ms/1000,
            baseline_calls=meta['diagnostics'].get('original_qwen_attempts',1),candidate_calls=result.diagnostics['original_qwen_attempts'],
            status=result.status,provider=result.provider,baseline_provider=meta['provider'],
            exact_text=old['normalized_text']==result.normalized_text,
            word_retention=sum((before&after).values())/max(1,sum(before.values())),
            number_retention=sum((numbers_before&numbers_after).values())/max(1,sum(numbers_before.values())),
            baseline_chars=len(old['normalized_text']),candidate_chars=len(result.normalized_text),
            fragmented=result.diagnostics.get('fragmented'),fallback=result.diagnostics.get('fallback_used'),
            finishes=[a.get('done_reason') for a in result.diagnostics['attempts']])
        report['results'].append(row);save_json(root/'comparison.json',report)
        print(json.dumps(row,ensure_ascii=True),flush=True)
finally:provider.close()
report['baseline_total_sec']=sum(r['baseline_sec'] for r in report['results'])
report['candidate_total_sec']=sum(r['candidate_sec'] for r in report['results'])
report['saving_percent']=100*(1-report['candidate_total_sec']/report['baseline_total_sec'])
save_json(root/'comparison.json',report)
print('DONE',report['saving_percent'],flush=True)
