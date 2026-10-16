"""Real Edge, synthetic files, real sync/index pipeline with fake OCR/embeddings."""
import sys, time, threading, json
from pathlib import Path
from uuid import uuid4
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / 'tests')]
import uvicorn
from playwright.sync_api import sync_playwright, expect
from conftest import config as config_fixture
from test_web import factory
from test_recognition import FakeRuntime
from test_network_sources import png, scan
from rag_arbiter.web.app import create_app

root=Path('data/day31-browser/ingest-toast') / uuid4().hex
root.mkdir(parents=True)
cfg=config_fixture.__wrapped__(root.resolve())
share=root.resolve()/'share';share.mkdir()
cfg.network_sources.allowed_roots=[str(share)]
cfg.network_sources.stability_seconds=0
cfg.network_sources.enabled=False
app=create_app(cfg,factory(FakeRuntime()))
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18768,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
try:
    deadline=time.monotonic()+30
    while not server.started and time.monotonic()<deadline:time.sleep(.1)
    assert server.started
    source=app.state.network.add(dict(name='Synthetic source',root_path=str(share)))
    sid=app.state.chat.create(None).session_id
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900})
        errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
        page.goto('http://127.0.0.1:18768/chat?session_id='+sid)
        toast=page.locator('#knowledge-toast');expect(toast).to_be_hidden()
        for i in range(5):png(share/f'{i}.png')
        first=scan(app.state.network,source)
        assert first['ingestion_completed']==5
        expect(toast).to_be_visible(timeout=10000)
        assert '5' in toast.inner_text() and '?' not in toast.inner_text()
        box=toast.bounding_box();assert box['x']>950 and box['y']==16
        page.screenshot(path=str(root/'toast.png'))
        page.wait_for_timeout(3500);expect(toast).to_be_visible()
        expect(toast).to_be_hidden(timeout=2000)
        # Lost polling connection, then reconnect: cursor suppresses replay.
        page.context.set_offline(True);page.wait_for_timeout(2200)
        page.context.set_offline(False);page.wait_for_timeout(2500)
        expect(toast).to_be_hidden()
        page.reload();page.wait_for_timeout(2500);expect(toast).to_be_hidden()
        assert scan(app.state.network,source)['unchanged_count']==5
        page.wait_for_timeout(2500);expect(toast).to_be_hidden()
        page.locator('#chat-input').fill('/help');page.locator('#chat-input').press('Enter')
        expect(page.locator('.chat-message.assistant')).to_have_count(1,timeout=15000)
        expect(toast).to_be_hidden()
        assert not errors,errors
        browser.close()
    (root/'evidence.json').write_text(json.dumps({'result':'PASS','checks':['5 indexed -> one toast','top right','hidden after 5 seconds','offline reconnect no repeat','reload no repeat','unchanged no toast','Chat /help works'],'pipeline':'real sync and index with fake OCR and embeddings'},indent=2),encoding='utf-8')
    print(root)
finally:
    server.should_exit=True;thread.join(20)
