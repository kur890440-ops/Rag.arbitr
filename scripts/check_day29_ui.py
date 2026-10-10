"""Read-only browser acceptance for the measured Day29 demo in the existing workspace."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from rag_arbiter.config import Config
from rag_arbiter.web.app import create_app

OUT=Path('data/day29/ui')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    snapshot=json.loads(Path('data/day29/q1-snapshot.json').read_text(encoding='utf-8'))
    rid=snapshot['processing_run_id'];errors=[];writes=[]
    with TestClient(create_app(Config.load()),base_url='http://127.0.0.1') as client, sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route_handler(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='127.0.0.1' or req.method!='GET':
                writes.append(req.method+' '+url.path);route.abort();return
            result=client.get(url.path+('?' + url.query if url.query else ''))
            route.fulfill(status=result.status_code,body=result.content,
                headers={k:v for k,v in result.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
        page.route('**/*',route_handler)
        page.goto('http://127.0.0.1/?run_id='+rid+'#llm-optimization')
        expect(page.locator('[data-tab="llm-optimization"]')).to_have_attribute('aria-selected','true')
        expect(page.locator('#llm-optimization')).to_be_visible()
        page.locator('#optimization-content > details > summary').click()
        page.locator('[hx-get="/ui/llm-optimization/archive"]').click()
        root=page.locator('[data-local-optimization]')
        expect(root.locator('.generation-card')).to_have_count(2)
        expect(root.locator('[data-shared-sources]')).to_have_count(1)
        first=root.locator('[data-local-question="Q1"]')
        expect(first).to_contain_text('LOCAL BASELINE');expect(first).to_contain_text('LOCAL OPTIMIZED')
        expected=json.loads(Path('data/day29/q1-baseline-confirm.json').read_text(encoding='utf-8'))
        expect(first.locator('[data-local-variant="1"] [data-parameter="context_window"]')).to_have_text(str(expected['profile']['context_window']))
        expect(first.locator('[data-local-variant="1"] [data-metric="output_tokens"]')).to_have_text(str(expected['result']['usage']['completion_tokens']))
        expect(first.locator('[data-delta="generation_ms"]')).to_contain_text('%')
        expect(first.locator('[data-context-ids]')).to_have_text(', '.join(snapshot['context_ids']))
        assert page.locator('[data-all-experiments] tbody tr').count()>=58
        page.locator('#optimization-question').select_option('Q2')
        expect(root.locator('[data-local-question="Q2"]')).to_be_visible()
        page.locator('#optimization-question').select_option('Q1')
        expect(first).to_be_visible()
        page.locator('[data-tab="retrieval"]').click()
        expect(page.locator('#llm-optimization')).to_be_hidden()
        assert page.locator('#retrieval [data-local-optimization]').count()==0
        page.locator('[data-tab="llm-optimization"]').click()
        expect(first).to_be_visible()
        a,b=first.locator('.generation-card').all()
        assert abs(a.bounding_box()['y']-b.bounding_box()['y'])<2
        page.set_viewport_size({'width':1600,'height':4000})
        first.screenshot(path=str(OUT/'wide.png'),style='.workspace-tabs {visibility:hidden !important;}')
        page.set_viewport_size({'width':560,'height':1000})
        a,b=first.locator('.generation-card').all()
        assert b.bounding_box()['y']>=a.bounding_box()['y']+a.bounding_box()['height']
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width':560,'height':4000})
        first.screenshot(path=str(OUT/'narrow.png'),style='.workspace-tabs {visibility:hidden !important;}')
        page.set_viewport_size({'width':1600,'height':1100})
        expect(first.locator('[data-shared-sources]')).to_have_attribute('open','')
        first.locator('[data-shared-source] > summary').first.click()
        first.locator('[data-shared-sources] .source-page').first.click()
        expect(page.locator('#viewer img')).to_be_visible(timeout=15000)
        browser.close()
    assert not errors and not writes,(errors,writes)
    (OUT/'browser.json').write_text(json.dumps(dict(status='PASS',measured_results=True,questions=5,
        checks=['two cards per question','shared sources once','profiles and times','dedicated tab navigation','question selection','all recorded experiments','wide adjacent','narrow stacked',
                'no horizontal overflow','source page navigation','no write/generation requests'],errors=errors),indent=2),encoding='utf-8')
    print('PASS: Day29 measured demo, responsive cards, sources, read-only requests')

if __name__=='__main__':main()
