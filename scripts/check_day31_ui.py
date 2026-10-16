"""Day31 browser acceptance using an isolated local fixture, never a real NAS."""
import gc
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from rag_arbiter.config import Config
from rag_arbiter.web.app import create_app
from test_web import factory
from test_recognition import FakeRuntime
from test_docx import make_docx
from conftest import make_scanned_pdf


def main():
    base = Path('data/day31-browser').resolve()
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as work:
        root=Path(work)
        share=root/'share'; share.mkdir(); (share/'nested').mkdir()
        make_docx(share/'nested'/'example.docx')
        make_scanned_pdf(share/'nested'/'example.pdf')
        cfg=Config(corpus_path=root/'corpus',cache_path=root/'cache',sqlite_path=root/'metadata.db',
            qdrant_path=root/'qdrant', report_path=root/'report.html',manifest_path=root/'manifest.json',
            web_data_path=root/'web',uploads_path=root/'uploads',web_port=18765)
        cfg.network_sources.allowed_roots=[str(share)]
        cfg.network_sources.stability_seconds=0
        cfg.recognition.fallback_enabled=False
        app=create_app(cfg,factory(FakeRuntime()))
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18765,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True); thread.start()
        deadline=time.monotonic()+15
        while not server.started and time.monotonic()<deadline:
            time.sleep(.05)
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport={'width':1400,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:18765/sources')
                expect(page.locator('[data-tab="sources"]')).to_have_attribute('aria-selected','true')
                assert page.url.endswith('/#sources'), page.url
                page.locator('[data-tab="overview"]').click()
                expect(page.locator('#upload')).to_be_visible()
                page.locator('[data-tab="sources"]').click()
                expect(page.locator('#upload')).to_be_hidden()
                page.locator('#open-source-dialog').click()
                page.locator('[name=root_path]').fill(str(share))
                page.locator('[name=authorize_root]').check()
                page.locator('[name=name]').fill('Day31 local fixture')
                page.screenshot(path=str(base/'path-dialog.png'),full_page=True)
                page.locator('#source-dialog form button').last.click()
                page.get_by_role('link',name='Day31 local fixture').click()
                page.get_by_text('Папки источника',exact=True).click()
                expect(page.locator('summary',has_text='nested')).to_be_visible(timeout=15000)
                page.locator('summary',has_text='nested').click()
                expect(page.get_by_text('example.docx',exact=True)).to_be_visible(timeout=15000)
                page.locator('[data-source-action$="/check"]').click()
                expect(page.locator('#source-message')).to_contain_text('ACCESS_OK',timeout=15000)
                page.locator('[data-source-action$="/scan"]').click()
                expect(page.locator('tbody')).to_contain_text('SUCCESS',timeout=180000)
                expect(page.locator('#sources-content section.panel > p').first).to_contain_text('COMPLETED',timeout=180000)
                expect(page.locator('tbody tr')).to_have_count(2)
                page.locator('[data-source-action$="/scan"]').click()
                expect(page.locator('tbody')).to_contain_text('UNCHANGED',timeout=45000)
                page.get_by_text('Папки источника',exact=True).click()
                expect(page.locator('summary',has_text='nested')).to_be_visible(timeout=15000)
                page.locator('summary',has_text='nested').click()
                expect(page.get_by_text('example.docx',exact=True)).to_be_visible(timeout=15000)
                page.screenshot(path=str(base/'sources.png'),full_page=True)
                page.locator('[data-source-link][href="/sources"]').click()
                expect(page.locator('#open-source-dialog')).to_be_visible()
                assert page.locator('header.app-header').count() == 1
                page.reload()
                expect(page.locator('#open-source-dialog')).to_be_visible()
                page.goto('http://127.0.0.1:18765/chat')
                page.locator('#toggle-chat-history').click()
                page.locator('#new-chat').click()
                expect(page.locator('#chat-source-scope')).to_be_visible()
                page.locator('#chat-source-scope').select_option('NETWORK')
                expect(page.locator('#chat-source-scope')).to_be_enabled()
                sid=app.state.network.page('network_sources')[0]['id']
                with page.expect_response(lambda r: '/source' in r.url and r.request.method == 'PATCH'):
                    page.locator('#chat-source-id').select_option(sid)
                expect(page.locator('#chat-source-scope')).to_have_value('SOURCE')
                expect(page.locator('#chat-source-scope')).to_be_enabled()
                chat_url=page.url
                page.reload()
                expect(page.locator('#chat-source-scope')).to_have_value('SOURCE')
                expect(page.locator('#chat-source-id')).to_have_value(sid)
                assert page.locator('#chat-discovery-content').count()==0
                page.locator('#choose-documents').click()
                expect(page.locator('[data-document-id]')).to_have_count(2,timeout=15000)
                page.locator('#selector-all').click()
                expect(page.locator('#selector-count')).to_contain_text('2')
                page.locator('#selector-apply').click()
                expect(page.locator('#document-selector')).not_to_be_visible()
                expect(page.locator('#chat-document-scope')).to_contain_text('2 документов')
                page.screenshot(path=str(base/'analysis-selector.png'),full_page=True)
                page.goto(chat_url)
                page.locator('#chat-source-scope').select_option('MANUAL')
                expect(page.locator('#chat-network-selector')).to_be_hidden()
                assert not errors,errors
                browser.close()
            (base/'result.json').write_text(json.dumps({'status':'PASS','fixture':'local temporary directory',
                'mcp':'real stdio','browser_errors':errors,'checks':['path input dialog','add source','lazy tree','check access','PDF and DOCX SUCCESS','repeat UNCHANGED']},indent=2),encoding='utf-8')
            print('PASS: local fixture browser + real MCP + existing pipeline; not SMB E2E')
        finally:
            server.should_exit=True
            thread.join(timeout=40)
            assert not thread.is_alive()
            gc.collect()


if __name__=='__main__':
    main()
