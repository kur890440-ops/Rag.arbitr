"""Read-only browser checks; intercept new comparisons/evaluations."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

OUT=Path('data/day23');RUN='d6575efd47b349f1a8a733bb2bc50d2c'
records=json.loads((OUT/'comparison.json').read_text(encoding='utf-8'))
record=next(r for r in records if r['rag_pipeline_mode']=='REWRITE_RERANK')
errors=[];payloads=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1700,'height':1100})
    page.on('pageerror',lambda error:errors.append(str(error)))
    def intercept(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(status=400,content_type='application/json',body='{"detail":"Browser validation only"}')
    page.route('**/rag/compare',intercept);page.route('**/rag/evaluate',intercept)
    page.goto('http://127.0.0.1:8765/?run_id='+RUN)
    expect(page.locator('#rag-mode')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-mode')).to_have_value('BASELINE')
    expect(page.locator('#rag-result .rag-answers>div')).to_have_count(3)
    page.locator('#rag-mode').select_option('REWRITE_RERANK')
    page.locator('#rag-threshold').fill('0.5')
    page.locator('#rag-question').fill('Browser check')
    page.locator('#rag-form [type=submit]').click()
    expect(page.locator('#rag-form [type=submit]')).to_be_enabled()
    assert payloads[0]['rag_pipeline_mode']=='REWRITE_RERANK' and payloads[0]['rerank_threshold']==.5
    page.evaluate("url=>htmx.ajax('GET',url,{target:'#rag-result',swap:'innerHTML'})",'/ui/rag/comparisons/'+record['comparison_run_id'])
    expect(page.locator('.rag-comparison')).to_be_attached(timeout=15000)
    page.get_by_text('До / после reranking',exact=True).click()
    expect(page.get_by_text('Rerank score',exact=True)).to_be_visible()
    page.locator('.rag-comparison').screenshot(path=str(OUT/'search-ui.png'))
    page.locator('[data-tab="evaluation"]').click()
    expect(page.locator('#rag-evaluation-table table')).to_be_visible(timeout=15000)
    expect(page.locator('#rag-evaluation-table th')).to_have_count(4)
    page.locator('#rag-evaluate-modes').click()
    expect(page.locator('#rag-evaluate-modes')).to_be_enabled()
    assert len(payloads)==2
    page.locator('#evaluation').screenshot(path=str(OUT/'evaluation-ui.png'))
    browser.close()
assert not errors,errors
(OUT/'browser.json').write_text(json.dumps(dict(status='PASS',errors=errors,payloads=payloads),indent=2),encoding='utf-8')
print('PASS: three panels, mode/threshold, before/after, rewrite, evaluation; zero new LLM calls')
