"""One real Q4/Q8 pair through the existing Day29 API; no alternate generation client."""
import json,time,subprocess
from pathlib import Path
import httpx
OUT=Path('data/day29/q8')
BASE='http://127.0.0.1:8765'
OLLAMA='http://127.0.0.1:11434'

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def run_pair():
    with httpx.Client(timeout=15) as client:
        tags=client.get(OLLAMA+'/api/tags').json()['models']
        names={m['name'] for m in tags}
        assert 'qwen3:4b-q8_0' in names
        save('installed-models.json',tags)
        shows=[]
        for short,tag,quant in [('q4','qwen3:4b-q4_K_M','Q4_K_M'),('q8','qwen3:4b-q8_0','Q8_0')]:
            resp=client.post(OLLAMA+'/api/show',json={'model':tag});resp.raise_for_status();show=resp.json()
            assert show['details']['family']=='qwen3'
            assert show['details']['quantization_level']==quant
            assert show['model_info']['general.parameter_count']==4022468096
            assert show['model_info']['general.size_label']=='4B'
            save(short+'-show.json',show);shows.append(show)
        assert shows[0]['template']==shows[1]['template'], 'Model templates differ'
        assert sorted(shows[0]['parameters'].splitlines())==sorted(shows[1]['parameters'].splitlines()), 'Model defaults differ'
        for key in shows[0]['model_info']:
            if key.startswith(('qwen3.','tokenizer.')):
                assert shows[0]['model_info'][key]==shows[1]['model_info'][key],key
        original=json.loads((OUT/'original-q4.json').read_text(encoding='utf-8'))
        options={k:original['profile'][k] for k in ('model','temperature','context_window','max_output_tokens','prompt_version','seed')}
        assert options==dict(model='qwen3:4b-q4_K_M',temperature=0,context_window=16384,max_output_tokens=2048,prompt_version='day29-baseline',seed=42)
        assert not any(not r['finished'] for r in client.get(BASE+'/api/local-llm-experiments').json())
        assert client.get(OLLAMA+'/api/ps').json()['models']==[], 'A model is already loaded'
        results=[]
        for short,tag in [('q4','qwen3:4b-q4_K_M'),('q8','qwen3:4b-q8_0')]:
            # Do not duplicate completed or previously submitted real tests on rerun.
            assert not (OUT/(short+'-run-id.json')).exists(), 'Run already submitted; inspect artifacts'
            offset=Path('.runtime/server.stderr.log').stat().st_size
            gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.used','--format=csv,noheader'],text=True)
            response=client.post(BASE+'/api/local-llm-experiments',json={'question':original['question'],'options':options|{'model':tag}},headers={'X-RAG-Request':'1'})
            response.raise_for_status();key=response.json()['id'];save(short+'-run-id.json',{'id':key,'gpu_before':gpu})
            print(short,'started',key,flush=True)
            samples=[];started=time.monotonic();last=started;captured_cli=False
            while True:
                try:
                    loaded=client.get(OLLAMA+'/api/ps').json()
                    samples.append({'elapsed':time.monotonic()-started,'ps':loaded})
                    if not captured_cli and any(m.get('name')==tag for m in loaded.get('models',[])):
                        cli=subprocess.check_output([str(Path('.runtime/ollama/ollama.exe').resolve()),'ps'],text=True,timeout=10)
                        (OUT/(short+'-ollama-ps.txt')).write_text(cli,encoding='utf-8');captured_cli=True
                except Exception as exc:samples.append({'error':type(exc).__name__})
                response=client.get(BASE+'/api/local-llm-experiments/'+key);response.raise_for_status();row=response.json()
                if row['finished']:break
                if time.monotonic()-last>20:print(short,row['status'],round(time.monotonic()-started),flush=True);last=time.monotonic()
                if time.monotonic()-started>600:raise TimeoutError('Experiment still pending; not resubmitting')
                time.sleep(.3)
            save(short+'-run.json',row);save(short+'-placement.json',samples)
            with Path('.runtime/server.stderr.log').open('rb') as log:log.seek(offset);raw=log.read().decode('utf-8',errors='replace')
            lines=[line for line in raw.splitlines() if any(k in line.lower() for k in ('offload','weights','kv cache','compute graph','memory.required','layers.','model buffer')) and 'http' not in line.lower()]
            (OUT/(short+'-placement.log')).write_text('\n'.join(lines),encoding='utf-8')
            print(short,json.dumps({k:row[k] for k in ('status','generation_duration','total_duration','prompt_tokens','output_tokens','tokens_per_second','ram_peak','vram_peak')}),flush=True)
            results.append(row)
            assert row['status']=='SUCCESS',row['error']
        a,b=results
        assert a['question']==b['question'] and a['system_prompt']==b['system_prompt']
        assert a['exact_input']|{'model':b['profile']['model']}==b['exact_input']
        save('pair.json',{'q4':a['id'],'q8':b['id'],'only_model_differs':True})
        page=client.get(BASE+'/ui/llm-optimization');page.raise_for_status()
        assert all(tag in page.text for tag in ('qwen3:4b-q4_K_M','qwen3:4b-q8_0'))
        html=client.get(BASE+'/ui/local-llm-experiments',params={'ids':a['id']+','+b['id'],'baseline':a['id']})
        html.raise_for_status();assert html.text.count('data-direct-run=')==2 and 'FAIR COMPARISON' in html.text
        (OUT/'columns.html').write_text(html.text,encoding='utf-8')
        print('PASS: metadata, same input/options, two real successful runs, selector, FAIR columns',flush=True)
if __name__=='__main__':run_pair()
