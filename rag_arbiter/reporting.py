from html import escape
import json
from .documents import save_json


def generate_report(config, snapshot):
    def pretty(value):
        return '<pre>' + escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'
    html = ['<!doctype html><html lang="ru"><meta charset="utf-8"><title>rag.арбитр · Day 21</title>',
            '<style>body{font:16px system-ui;margin:2rem;background:#f6f7fb;color:#17243b}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{width:100%;table-layout:fixed}td{vertical-align:top;background:white;padding:1rem}article{border-bottom:1px solid #ccc}summary{cursor:pointer}th{text-align:left}</style>',
            '<h1>rag.арбитр · DAY 21 · DOCUMENT INDEXING</h1>',
            '<p>' + escape(snapshot.get("status", "")) + '</p>']
    if snapshot.get("status") == "CORPUS REQUIRED":
        html.append('<p>No corpus documents found. Положите scanned PDFs в data/corpus/. Реальный эксперимент не выполнен.</p>')
    for key in ("corpus", "embedding"):
        html.append(f'<h2>{key.upper()}</h2>' + pretty(snapshot.get(key, {})))
    recognition = snapshot.get("recognition", snapshot.get("ocr", {
        "status": "NOT_RUN", "provider": config.recognition.provider, "model": config.recognition.model,
        "runtime": config.recognition.runtime, "requested_device": config.recognition.device,
        "effective_device": "NOT_RUN", "quantization": config.recognition.quantization,
        "processed_pages": 0, "cache_hits": 0, "errors": [], "average_sec_per_page": None}))
    html.append('<h2>DOCUMENT RECOGNITION</h2>' + pretty(recognition))
    html.append('<h2>Recognition Reliability</h2>' + pretty(recognition.get('reliability', {})) + pretty(recognition.get('document_stats', [])))
    html.append('<p>Вход recognition — изображения страниц; один результат для обеих стратегий. Dense BGE-M3, L2 normalization документов и запросов, Cosine. SQLite — canonical text; Qdrant — vectors + provenance.</p>')
    html.append('<table><tr><th>FIXED</th><th>STRUCTURE</th></tr><tr>')
    for strategy in ("fixed", "structure"):
        index = snapshot.get('indexes', {}).get(strategy, {'status':'NOT_RUN'})
        examples = index.get('examples', [])
        if not examples and index.get('chunk_ids'):
            from .storage import MetadataStore
            store = MetadataStore(config.sqlite_path)
            try:
                examples = [store.get('chunks', k) for k in index['chunk_ids'][:2]]
            finally:
                store.close()
        html.append('<td><h3>Active run</h3>' + pretty(index.get('run_id')) +
                    '<h3>Active parameters</h3>' + pretty(index.get('parameters_json', index.get('settings'))) +
                    '<h3>Chunk metadata examples</h3>' + pretty([{k:c.get(k) for k in ('chunk_id','source','title','file_name','section','strategy','page_start','page_end','token_count')} for c in examples if c]) +
                    '<details><summary>Index statistics and provenance</summary>' + pretty(index) + '</details></td>')
    html.append('</tr></table><h2>EVALUATION</h2>')
    evaluation = snapshot.get("evaluation", {"status": "NOT_RUN"})
    html.append(pretty({k: v for k, v in evaluation.items() if k != "queries"}))
    for row in evaluation.get("queries", []):
        html.append('<h3>' + escape(row["query"]["question"]) + '</h3><table><tr><th>Fixed Top-K</th><th>Structure Top-K</th></tr><tr>')
        for strategy in ("fixed", "structure"):
            result = row["results"][strategy]
            html.append('<td>' + pretty({k: v for k, v in result.items() if k != "hits"}))
            for hit in result["hits"]:
                html.append('<article><h4>' + escape(f"#{hit['rank']} · {hit['score']:.5f} · {hit['file_name']} · pages {hit['page_start']}–{hit['page_end']} · {hit['section']} · recognition={hit.get('recognition_provider', 'legacy')}") + '</h4>')
                html.append('<pre>' + escape(hit["text"][:config.preview_chars]) + '</pre><details><summary>Полный chunk</summary><pre>' + escape(hit["text"]) + '</pre></details></article>')
            html.append('</td>')
        html.append('</tr></table>')
    html.append('</html>')
    config.report_path.parent.mkdir(parents=True, exist_ok=True)
    config.report_path.write_text('\n'.join(html), encoding="utf-8")
    save_json(config.report_path.with_suffix(".json"), snapshot)
    return config.report_path
