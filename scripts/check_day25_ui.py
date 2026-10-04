"""Browser smoke: two empty persistent sessions, reload, navigation; no LLM calls."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

out=Path('data/day25');out.mkdir(parents=True,exist_ok=True)
errors=[];sessions=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1000})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/messages',lambda route:route.abort())
    page.route('**/rag/compare',lambda route:route.abort())
    page.goto('http://127.0.0.1:8765/chat')
    expect(page.locator('#new-chat')).to_be_visible()
    page.locator('#new-chat').click()
    expect(page.locator('.chat-grid')).to_be_visible()
    sid=page.locator('.chat-grid').get_attribute('data-session');sessions.append(sid)
    expect(page.locator('.task-memory')).to_contain_text('Версия 0')
    expect(page.locator('.chat-message')).to_have_count(0)
    page.reload();expect(page.locator('.chat-grid')).to_have_attribute('data-session',sid)
    page.goto('http://127.0.0.1:8765/chat')
    expect(page.locator('.chat-grid')).to_have_attribute('data-session',sid)
    page.locator('#new-chat').click();expect(page.locator('.chat-grid')).not_to_have_attribute('data-session',sid)
    sessions.append(page.locator('.chat-grid').get_attribute('data-session'))
    expect(page.locator('.task-memory')).to_contain_text('Версия 0')
    expect(page.locator('.chat-message')).to_have_count(0)
    page.screenshot(path=str(out/'chat-ui.png'),full_page=True)
    page.goto('http://127.0.0.1:8765/')
    expect(page.locator('#rag-mode')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('[data-tab="retrieval"]')).to_have_attribute('aria-selected','true',timeout=60000)
    expect(page.locator('a[href="/chat"]')).to_be_visible()
    browser.close()
assert not errors,errors
(out/'browser.json').write_text(json.dumps(dict(status='PASS',errors=errors,session_ids=sessions,
    empty_sessions_only=True,llm_requests=0,reload=True,new_chat_isolation=True,search_navigation=True),indent=2),encoding='utf-8')
print('PASS: Chat tab, new session, empty memory, reload, isolation, Search; no LLM requests')
