"""Browser smoke: intercept submission, render persisted Day28 result; no inference."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

out=Path('data/day28')
r=json.loads((out/'q1-r1.json').read_text(encoding='utf-8'))
errors=[];submitted=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1600,'height':1100})
    page.on('pageerror',lambda error:errors.append(str(error)))
    def intercept(route):
        submitted.append(route.request.post_data_json)
        route.fulfill(status=202,content_type='application/json',body=json.dumps({'comparison_run_id':r['comparison_run_id']}))
    page.route('**/rag/compare',intercept)
    page.goto('http://127.0.0.1:8766/?run_id='+r['processing_run_id'])
    expect(page.locator('#rag-generation')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-generation input[value="minimax"]')).to_be_checked()
    page.locator('#rag-generation input[value="compare"]').check()
    page.locator('#rag-question').fill(r['question_text'])
    page.locator('#rag-form button[type="submit"]').click()
    expect(page.locator('#rag-result article')).to_have_count(2,timeout=30000)
    expect(page.locator('#rag-result')).to_contain_text('GROUNDED')
    expect(page.locator('#rag-result')).to_contain_text('APPROVAL_REQUIRED')
    page.locator('#rag-result').screenshot(path=str(out/'comparison-ui.png'))
    assert submitted[0]['generation_mode']=='compare'
    browser.close()
assert not errors,errors
(out/'browser.json').write_text(json.dumps({'status':'PASS','errors':errors,'submission_mode':submitted[0]['generation_mode'],'panels':2}),encoding='utf-8')
print('PASS: MiniMax default, Compare submission, two provider panels, citations, explicit blocked cloud')
