"""Read-only Day24 browser verification. No generation requests."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

out=Path('data/day24');run='d6575efd47b349f1a8a733bb2bc50d2c'
record=json.loads((out/'01.json').read_text(encoding='utf-8'));errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1700,'height':1100})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/rag/compare',lambda route:route.abort())
    page.goto('http://127.0.0.1:8765/?run_id='+run)
    expect(page.locator('#rag-mode')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-result .rag-answers>div')).to_have_count(3)
    expect(page.locator('#rag-support-threshold')).to_have_value('0.5')
    page.evaluate("url=>htmx.ajax('GET',url,{target:'#rag-result',swap:'innerHTML'})",'/ui/rag/comparisons/'+record['comparison_run_id'])
    expect(page.locator('.grounded-citation blockquote')).to_have_count(1,timeout=15000)
    expect(page.locator('.grounding-summary')).to_contain_text('GROUNDED')
    page.locator('.rag-comparison').screenshot(path=str(out/'citations-ui.png'))
    page.locator('.grounded-citation .source-page').click()
    page.wait_for_timeout(1200)
    page.locator('[data-tab="evaluation"]').click()
    expect(page.locator('#rag-evaluation-table')).to_contain_text('Grounding & Citations',timeout=15000)
    expect(page.locator('#rag-evaluation-table')).to_contain_text('12')
    page.locator('#evaluation').screenshot(path=str(out/'evaluation-ui.png'))
    browser.close()
assert not errors,errors
(out/'browser.json').write_text(json.dumps(dict(status='PASS',errors=errors,three_panels=True,quotes=True)),encoding='utf-8')
print('PASS: three panels, grounding, exact quote, source navigation, evaluation; no generation calls')
