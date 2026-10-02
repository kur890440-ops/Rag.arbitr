"""Browser smoke: intercept comparison writes, never sends documents to an LLM."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8765'
RUN='d6575efd47b349f1a8a733bb2bc50d2c'
out=Path('data/day22/candidates');out.mkdir(parents=True,exist_ok=True)
errors=[];requests=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1700,'height':1100})
    page.on('pageerror',lambda error:errors.append(str(error)))
    def intercept(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=400,content_type='application/json',body='{"detail":"Local UI validation only"}')
    page.route('**/api/runs/*/rag/compare',intercept)
    page.goto(BASE+'/?run_id='+RUN)
    expect(page.locator('#rag-candidates')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-candidates')).to_have_value('20')
    expect(page.locator('#rag-top-k')).to_have_value('5')
    expect(page.locator('#rag-scope')).to_have_value('ALL_DOCUMENTS')
    expect(page.locator('#rag-result .rag-answers>div')).to_have_count(3)
    page.locator('#rag-question').fill('Browser smoke question')
    page.locator('#rag-candidates').fill('17')
    page.locator('#rag-top-k').fill('3')
    page.locator('#rag-form [type=submit]').click()
    expect(page.locator('#rag-form [type=submit]')).to_be_enabled()
    assert requests and requests[0]['candidate_top_n']==17 and requests[0]['max_context_sources']==3
    assert 'top_k' not in requests[0] and requests[0]['rag_scope']=='ALL_DOCUMENTS'
    page.screenshot(path=str(out/'ui.png'),full_page=True)
    browser.close()
assert not errors,errors
(out/'ui.json').write_text(json.dumps(dict(status='PASS',errors=errors,payload=requests[0]),indent=2),encoding='utf-8')
print('Browser PASS: three panels, separate controls and serialized parameters; no LLM calls')
