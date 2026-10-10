"""Build a transparent Day29 table from saved measurements, never rerun inference."""
import csv
import json
from pathlib import Path
from rag_arbiter.application.local_benchmark import verified_snapshot

ROOT = Path('data/day29')
ORDER = ['baseline','temp01','temp02','max1024','max1536','ctx15360','ctx12288','prompt','prompt-v2','prompt-v3','lease','baseline-confirm','optimized']

def main():
    base=json.loads((ROOT/'baseline.json').read_text(encoding='utf-8'))
    notes=json.loads((ROOT/'quality.json').read_text(encoding='utf-8-sig')) if (ROOT/'quality.json').exists() else {}
    rows=[]
    extra=[]
    for path in ROOT.glob('q*-*.json'):
        name=path.stem.partition('-')[2]
        if name not in ORDER and name not in ('snapshot','baseline-record'):
            if 'profile' in json.loads(path.read_text(encoding='utf-8')):extra.append(name)
    for variant in ORDER+sorted(set(extra)):
        for n in (1,2,3,4,5):
            path=ROOT/f'q{n}-{variant}.json'
            if not path.exists(): continue
            item=json.loads(path.read_text(encoding='utf-8'))
            snapshot=verified_snapshot(json.loads((ROOT/f'q{n}-snapshot.json').read_text(encoding='utf-8')))
            assert item['context_snapshot_id']==snapshot['context_snapshot_id']
            assert item['context_ids']==snapshot['context_ids']
            cfg=item['profile']; g=item.get('grounding_result', {})
            stats=[a.get('diagnostics',{}).get('ollama',a) for a in item.get('attempts',[])]
            def total(key):
                values=[a[key] for a in stats if type(a.get(key)) is int]
                return sum(values) if len(values)==len(stats) and values else None
            evaluation_ns=total('eval_duration');tokens=total('eval_count')
            rows.append(dict(question=f'Q{n}',variant=variant,model=cfg['model'],quantization=base['model']['details']['quantization_level'],
                temperature=cfg.get('temperature',0),keep_alive=cfg.get('keep_alive',0),context_window=cfg['context_window'],max_tokens=cfg['max_output_tokens'],
                prompt_version=cfg.get('prompt_version','day28-baseline'),generation_ms=round(item['generation_ms']),total_ms=round(item['total_ms']),
                grounding='PASS' if item['grounding_status']=='GROUNDED' else 'FAIL',grounding_status=item['grounding_status'],
                claims=item.get('claims_count',len(item.get('claims_json',[]))),citations=item['citations_count'],repairs=item['repair_count'],
                structured_output_valid=item.get('structured_output_valid'),error=item['error'].get('code',''),
                unsupported_final=g.get('claims_unsupported'),
                prompt_eval_count=total('prompt_eval_count'),eval_count=tokens,prompt_eval_duration_ns=total('prompt_eval_duration'),eval_duration_ns=evaluation_ns,
                eval_tokens_per_second=round(tokens/(evaluation_ns/1e9),2) if evaluation_ns and tokens is not None else None,
                rss_peak_mib=round(item['resources']['rss_peak_bytes']/1024**2) if item['resources']['rss_peak_bytes'] else None,
                device_vram_peak_mib=item['resources']['device_vram_peak_mib'],context_snapshot_id=item['context_snapshot_id'],
                quality_note=notes.get(path.stem,'')))
    with (ROOT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (ROOT/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['question','variant','model','quantization','temperature','context_window','max_tokens','keep_alive','generation_ms','grounding','citations','repairs','quality_note']
    table='| '+' | '.join(fields)+' |\n| '+' | '.join(['---']*len(fields))+' |\n'
    table+='\n'.join('| '+' | '.join(str(row[f]).replace('|','/') for f in fields)+' |' for row in rows)
    (ROOT/'results-table.md').write_text(table+'\n',encoding='utf-8')
    print('Saved',len(rows),'measurements; snapshots verified.')

if __name__=='__main__':main()
