"""Browser acceptance through the real app with synthetic fixtures, never external LLMs."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from conftest import config as config_fixture
from test_rag import ragweb, FakeLLM
from rag_arbiter.llm import LLMResult

OUT=Path('data/day28/compare-ui')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    errors=[];posts=[];gate=threading.Event();gate.set();fail_local=False
    with tempfile.TemporaryDirectory(prefix='day28-ui-') as tmp:
        cfg=config_fixture.__wrapped__(Path(tmp))
        fixture=ragweb.__wrapped__(cfg)
        client,rid,cloud,_=next(fixture)
        cloud_generate=cloud.generate
        def timed_cloud(request):
            started=time.perf_counter();time.sleep(.35)
            return cloud_generate(request).model_copy(update={'duration_ms':(time.perf_counter()-started)*1000})
        cloud.generate=timed_cloud
        def local_factory(config,**kwargs):
            class Local(FakeLLM):
                def generate(self,request):
                    if not gate.wait(45):raise RuntimeError('UI test gate timeout')
                    if fail_local:return LLMResult(provider='local',model=config.model,status='LOCAL_GENERATION_UNAVAILABLE',error={'code':'LOCAL_GENERATION_UNAVAILABLE'})
                    started=time.perf_counter();time.sleep(.25)
                    return super().generate(request).model_copy(update={'provider':'local','duration_ms':(time.perf_counter()-started)*1000})
            return Local(config)
        try:
            with patch('rag_arbiter.local_llm.LocalLLMProvider',local_factory),sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100})
                page.on('pageerror',lambda err:errors.append(str(err)))
                def request(route):
                    req=route.request;url=urlsplit(req.url)
                    if url.hostname!='127.0.0.1':route.abort();return
                    path=url.path+('?' + url.query if url.query else '')
                    if req.method=='POST' and path.endswith('/rag/compare'):posts.append(req.post_data_json)
                    headers={'X-RAG-Request':'1'}
                    if 'content-type' in req.headers:headers['Content-Type']=req.headers['content-type']
                    response=client.request(req.method,path,content=req.post_data_buffer,headers=headers)
                    route.fulfill(status=response.status_code,body=response.content,
                        headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                page.route('**/*',request)
                page.goto('http://127.0.0.1/?run_id='+rid)
                expect(page.locator('#rag-generation')).to_be_attached()
                page.locator('[data-tab="retrieval"]').click()
                expect(page.locator('input[name="generation_mode"][value="minimax"]')).to_be_checked()
                page.locator('input[name="generation_mode"][value="compare"]').check()
                page.locator('#rag-question').fill('Какой срок оплаты указан в документах?')
                gate.clear()
                page.locator('#rag-form button[type="submit"]').click()
                expect(page.locator('.generation-progress')).to_contain_text('Local generation',timeout=15000)
                expect(page.locator('.generation-card')).to_have_count(2)
                page.locator('.generation-comparison').screenshot(path=str(OUT/'loading.png'))
                gate.set()
                expect(page.locator('.generation-comparison > .section-head')).to_contain_text('COMPLETED',timeout=15000)
                expect(page.locator('.generation-card .generation-status.pass')).to_have_count(2)
                expect(page.locator('[data-shared-sources]')).to_have_count(1)
                assert len(posts)==1 and posts[0]['generation_mode']=='compare'
                a,b=page.locator('.generation-card').all()
                assert abs(a.bounding_box()['y']-b.bounding_box()['y'])<2
                page.locator('.generation-comparison').screenshot(path=str(OUT/'wide.png'))
                page.locator('[data-generation-citation]').first.click()
                expect(page.locator('.generation-source-list')).to_have_attribute('open','')
                quote=page.locator('.generation-shared .grounded-citation[open]').first
                expect(quote.locator('blockquote')).to_be_visible()
                quote.locator('.source-page').first.click()
                expect(page.locator('#viewer img')).to_be_visible(timeout=15000)
                page.locator('[data-tab="retrieval"]').click()
                page.set_viewport_size({'width':560,'height':1000})
                a,b=page.locator('.generation-card').all()
                assert b.bounding_box()['y']>a.bounding_box()['y']+a.bounding_box()['height']
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.locator('.generation-comparison').screenshot(path=str(OUT/'narrow.png'),style='.workspace-tabs {visibility:hidden !important;}')
                page.set_viewport_size({'width':1600,'height':1100})
                fail_local=True
                page.locator('#rag-form button[type="submit"]').click()
                expect(page.locator('.generation-comparison > .section-head')).to_contain_text('PARTIAL',timeout=15000)
                expect(page.locator('[data-provider="local"]')).to_contain_text('LOCAL_GENERATION_UNAVAILABLE')
                expect(page.locator('[data-provider="minimax"] .generation-status.pass')).to_have_count(1)
                page.locator('.generation-comparison').screenshot(path=str(OUT/'partial.png'))
                fail_local=False
                page.locator('input[name="generation_mode"][value="local"]').check()
                page.locator('#rag-form button[type="submit"]').click()
                expect(page.locator('#rag-result .rag-answers > div')).to_have_count(1,timeout=15000)
                expect(page.locator('#rag-result .generation-status.pass')).to_have_count(1)
                page.locator('input[name="generation_mode"][value="minimax"]').check()
                page.locator('#rag-form button[type="submit"]').click()
                expect(page.locator('#rag-result .rag-answers > div')).to_have_count(3,timeout=15000)
                expect(page.locator('#rag-result .generation-status.pass')).to_have_count(1)
                assert len(posts)==4
                browser.close()
        finally:
            gate.set();fixture.close()
    assert not errors,errors
    (OUT/'browser.json').write_text(json.dumps(dict(status='PASS',synthetic=True,errors=errors,
        checks=['one POST per submit','MiniMax default','Local normal result','two Compare cards','one shared source list',
                'wide adjacent','narrow stacked','no horizontal overflow','citation navigation','source page navigation',
                'loading progress','partial failure preserves cloud']),indent=2),encoding='utf-8')
    print('PASS: responsive Compare UI, partial failure, loading, source navigation, single-provider regression')


if __name__=='__main__':main()
