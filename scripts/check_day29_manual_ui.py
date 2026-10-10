"""Browser acceptance for the direct lab, using the real adapter with fake transport."""
import gc
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import sync_playwright,expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from conftest import config as config_fixture
from test_rag import ragweb
from test_direct_experiments import direct_lab

OUT=Path('data/day29/judicial-ui')
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='direct-day29-') as tmp:
        fixture=ragweb.__wrapped__(config_fixture.__wrapped__(Path(tmp)))
        state=next(fixture);patch=pytest.MonkeyPatch()
        client,rid,body,calls,cloud=direct_lab.__wrapped__(state,patch)
        errors=[];posts=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100})
                page.on('pageerror',lambda e:errors.append(str(e)))
                def handler(route):
                    req=route.request;url=urlsplit(req.url)
                    if url.hostname!='127.0.0.1':route.abort();return
                    if req.method=='POST':posts.append(req.post_data_json)
                    response=client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data,
                        headers={'Content-Type':req.headers.get('content-type',''),'X-RAG-Request':'1'})
                    route.fulfill(status=response.status_code,body=response.content,
                        headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                page.route('**/*',handler)
                page.goto('http://127.0.0.1/?run_id='+rid+'#llm-optimization')
                form=page.locator('#manual-experiment-form');expect(form).to_be_visible()
                form.locator('[name=model]').select_option('test-local:Q5')
                form.locator('[name=temperature]').fill('0.13')
                form.locator('[name=max_output_tokens]').fill('950')
                versions=form.locator('[name=document_version] option').evaluate_all('(els)=>els.map(e=>e.value).filter(Boolean)')
                assert len(versions)>=2
                form.locator('[name=document_version]').select_option(versions[0])
                for n in range(3):
                    if n==1:
                        form.locator('[name=max_output_tokens]').fill('800')
                        form.locator('[name=prompt_version]').select_option('day29-judicial-v1')
                    if n==2:form.locator('[name=document_version]').select_option(versions[1])
                    form.locator('[type=submit]').click()
                    expect(page.locator('[data-direct-run]')).to_have_count(n+1,timeout=30000)
                    expect(page.locator('[data-direct-status]').nth(n)).to_have_text('SUCCESS',timeout=30000)
                cards=page.locator('[data-direct-run]')
                boxes=[cards.nth(i).bounding_box() for i in range(3)]
                assert all(abs(b['y']-boxes[0]['y'])<2 and b['width']>=360 for b in boxes)
                assert boxes[2]['x']+boxes[2]['width']<=1600
                expect(page.locator('#file-panel')).to_be_hidden()
                cards.first.locator('[data-direct-baseline]').click()
                expect(cards.first).to_have_class('direct-run direct-baseline')
                expect(cards.nth(1)).to_contain_text('FAIR COMPARISON')
                expect(cards.nth(2)).to_contain_text('DIFFERENT DOCUMENT / TASK')
                assert cards.nth(2).locator('[data-direct-delta]').count()==0
                cards.first.locator('details > summary').filter(has_text='prompt').click()
                expect(cards.first).to_contain_text('System message')
                expect(cards.first).to_contain_text('SHA-256')
                expect(cards.first).to_contain_text('10.00 tok/s')
                quality=cards.first.locator('[data-quality-review]')
                quality.locator('..').locator('summary').first.click()
                quality.locator('[name=decision]').select_option('pass')
                quality.locator('[type=submit]').click()
                expect(quality.locator('[data-review-status]')).not_to_be_empty()
                page.locator('main').evaluate('(el)=>el.scrollTop=0')
                page.screenshot(path=str(OUT/'wide.png'))
                page.set_viewport_size({'width':560,'height':1000})
                a,b=cards.nth(0).bounding_box(),cards.nth(1).bounding_box()
                assert b['y']>=a['y']+a['height']
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.locator('#llm-optimization').screenshot(path=str(OUT/'narrow.png'))
                page.locator('[data-direct-selection=clear]').click()
                expect(cards).to_have_count(0)
                assert len(client.app.state.direct_experiments.history())==3
                page.locator('[data-direct-selection=all]').click()
                expect(cards).to_have_count(3)
                page.reload();expect(cards).to_have_count(3)
                expect(cards.first).to_have_class('direct-run direct-baseline')
                page.locator('[data-tab=retrieval]').click()
                expect(page.locator('#llm-optimization')).to_be_hidden()
                assert page.locator('#retrieval #manual-experiment-form').count()==0
                browser.close()
            assert len(posts)==4 and not errors and not cloud.calls
            assert len([1 for path,_ in calls if path=='/api/chat'])==3
            (OUT/'browser.json').write_text(json.dumps({'status':'PASS','runs':3,'manual_reviews':1,'retrieval_calls':0,'errors':errors}),encoding='utf-8')
            print('PASS: direct calls, columns, baseline, deltas, different document, quality review, exact prompt, persistence, responsive, Search isolation')
        finally:
            patch.undo()
            try:next(fixture)
            except StopIteration:pass
            gc.collect()
if __name__=='__main__':main()
