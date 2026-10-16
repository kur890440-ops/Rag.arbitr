"""Real browser UX checks; seeded synthetic conversation, no new model calls."""
import json,sys,time,threading,copy
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright,expect
from rag_arbiter.config import Config
from rag_arbiter.web.app import create_app
from rag_arbiter.application.chat_models import ChatMessage,MemoryItem


def main():
    root=Path('data/day31-e2e/analysis-real-7fd22ae2ce').resolve()
    assert (root/'evidence.json').exists(),'Run synthetic analysis E2E fixture first'
    cfg=Config.load().model_copy(deep=True)
    for key,value in dict(sqlite_path=root/'metadata.db',qdrant_path=root/'qdrant',corpus_path=root/'corpus',web_data_path=root/'web',uploads_path=root/'uploads',manifest_path=root/'manifest.json',report_path=root/'report.html').items():setattr(cfg,key,value)
    cfg.network_sources.enabled=False
    app=create_app(cfg);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18767,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    evidence={'classification':'SEEDED SYNTHETIC LONG CHAT / REAL BROWSER / NO GENERATION'};output=Path('data/day31-browser/chat-ux');output.mkdir(parents=True,exist_ok=True)
    try:
        deadline=time.monotonic()+20
        while not server.started and time.monotonic()<deadline:time.sleep(.1)
        chat=app.state.chat;sid=chat.create(None).session_id;other=chat.create(None).session_id
        with chat.runs.db() as store:
            session=store.get('chat_sessions',sid);session['title']='Проверка длинного диалога';store.put('chat_sessions',sid,session)
            messages=[]
            for n in range(1,121):
                content=f'Сообщение {n}. Синтетический пример условий договора.'
                if n==120:content='## Условия договора\n\n'+('\n\n'.join(f'{i}. Стороны уведомляют друг друга об изменении условий. Подтверждение [S1].' for i in range(1,65)))+'\n\n| Условие | Значение |\n| --- | --- |\n| Срок | 7 дней |\n\n- Первый пункт\n- Второй пункт'
                message=ChatMessage(session_id=sid,role='USER' if n%2 else 'ASSISTANT',content=content,sequence=n);messages.append(message);store.put('chat_messages',message.message_id,message.model_dump())
            turn=copy.deepcopy(json.loads((root/'evidence.json').read_text(encoding='utf-8'))['turn']);turn.update(turn_id=uuid4().hex,session_id=sid,user_message_id=messages[-2].message_id,assistant_message_id=messages[-1].message_id)
            store.put('chat_turns',turn['turn_id'],turn)
            state=store.get('task_states',sid);state['terms_json']=[MemoryItem(key='ux-term',value='Тестовый термин',evidence='Тестовый термин',source_message_id=messages[0].message_id).model_dump()];store.put('task_states',sid,state)
        errors=[];base='http://127.0.0.1:18767'
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page(viewport={'width':1440,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/chat?session_id='+sid);expect(page.locator('.chat-message')).to_have_count(40)
            assert not page.locator('#history-drawer').is_visible() and not page.locator('#memory-drawer').is_visible()
            page.locator('#toggle-chat-history').click();expect(page.locator('#history-drawer')).to_be_visible();page.keyboard.press('Escape');expect(page.locator('#history-drawer')).not_to_be_visible()
            page.locator('#toggle-chat-memory').click();expect(page.locator('.task-memory')).to_be_visible();page.locator('.memory-remove').click();expect(page.locator('.task-memory')).not_to_contain_text('Тестовый термин');page.keyboard.press('Escape')
            page.locator('#choose-documents').click();expect(page.locator('[data-document-id]:enabled')).to_have_count(3);page.locator('[data-document-id]:enabled').first.check();page.locator('#selector-apply').click();expect(page.locator('#document-selector')).not_to_be_visible();page.reload();expect(page.locator('#chat-document-scope')).not_to_be_hidden()
            page.locator('#clear-chat-document').click();expect(page.locator('#chat-document-scope')).to_be_hidden()
            page.locator('.chat-message.assistant').last.locator('summary',has_text='Источники ·').click();page.locator('.chat-source').first.click();expect(page.locator('#chat-viewer')).to_be_visible();page.keyboard.press('Escape')
            page.locator('.chat-diagnostics summary').click();expect(page.locator('.chat-diagnostics')).to_contain_text('Режим:')
            assert page.request.get(base+f'/api/chat/sessions/{sid}/diagnostic?download=true').status==200
            long=page.locator('.chat-message.assistant').last
            assert long.locator('table').count()==1
            assert long.evaluate('(e)=>e.scrollHeight<=e.clientHeight+2')
            page.locator('.chat-thread').evaluate('(e)=>e.scrollTop=200')
            anchor=page.locator('.chat-message').first.get_attribute('id');page.evaluate('(id)=>window.uxAnchor=document.getElementById(id)',anchor)
            before_count=len(chat.load(sid)['messages'])
            page.locator('#chat-input').fill('/help');page.evaluate("document.querySelector('#chat-send').requestSubmit();document.querySelector('#chat-send').requestSubmit()")
            expect(page.locator('.chat-message')).to_have_count(42,timeout=20000);expect(page.locator('#chat-new-messages')).to_be_visible()
            assert abs(page.locator('.chat-thread').evaluate('(e)=>e.scrollTop')-200)<5
            assert page.evaluate('(id)=>window.uxAnchor===document.getElementById(id)',anchor)
            assert len(chat.load(sid)['messages'])==before_count+2
            page.locator('#chat-new-messages').click();expect(page.locator('#chat-new-messages')).to_be_hidden()
            page.locator('#chat-input').fill('Строка');page.locator('#chat-input').press('Shift+Enter');expect(page.locator('#chat-input')).to_have_value('Строка\n')
            page.route('**/api/chat/sessions/*/messages',lambda route:route.fulfill(status=400,content_type='application/json',body='{"detail":"Проверка ошибки отправки"}'))
            page.locator('#chat-input').fill('Сохранить черновик');page.locator('#chat-input').press('Enter');expect(page.locator('#chat-error')).to_contain_text('Проверка ошибки');expect(page.locator('#chat-input')).to_have_value('Сохранить черновик');page.unroute('**/api/chat/sessions/*/messages')
            page.locator('#chat-input').fill('/help');page.locator('#chat-input').press('Enter');expect(page.locator('.chat-message')).to_have_count(44,timeout=20000)
            assert page.locator('.chat-thread').evaluate('(e)=>e.scrollHeight-e.scrollTop-e.clientHeight')<80
            page.locator('.chat-thread').evaluate('(e)=>e.scrollTop=0');page.locator('#chat-load-older').click();expect(page.locator('.chat-message')).to_have_count(84)
            assert page.locator('.chat-thread').evaluate('(e)=>e.scrollTop')>0
            page.locator('.chat-thread').evaluate('(e)=>e.scrollTop=e.scrollHeight');page.screenshot(path=str(output/'desktop.png'))
            for w,h in [(1366,768),(768,1024),(390,844)]:
                page.set_viewport_size(dict(width=w,height=h));assert page.evaluate('document.documentElement.scrollWidth')<=w
                box=page.locator('#chat-send').bounding_box();assert box['y']+box['height']<=h
                assert page.locator('.chat-thread').bounding_box()['width']>=w-30
                for button,drawer in [('#toggle-chat-history','#history-drawer'),('#toggle-chat-memory','#memory-drawer')]:
                    page.locator(button).click();expect(page.locator(drawer)).to_be_visible();page.keyboard.press('Escape')
            page.locator('.chat-diagnostics').evaluate_all('(els)=>els.forEach(e=>e.open=false)')
            page.locator('.chat-thread').evaluate('(e)=>e.scrollTop=e.scrollHeight')
            page.screenshot(path=str(output/'mobile.png'))
            page.locator('#toggle-chat-memory').click();page.screenshot(path=str(output/'memory-mobile.png'));page.keyboard.press('Escape')
            page.locator('#toggle-chat-history').click();page.locator(f'.chat-session[href="/chat?session_id={other}"]').click();page.wait_for_url('**session_id='+other);expect(page.locator('#history-drawer')).not_to_be_visible()
            page.locator('#toggle-chat-history').click();page.locator('#new-chat').click();page.wait_for_url(lambda u:'session_id=' in u and other not in u)
            page.locator('#chat-input').fill('/help');page.locator('#chat-input').press('Enter');expect(page.locator('.chat-message')).to_have_count(2,timeout=20000)
            expect(page.locator('#chat-title')).to_have_text('/help');page.locator('#toggle-chat-history').click();expect(page.locator('.chat-session').first).to_contain_text('/help');page.keyboard.press('Escape')
            page.get_by_role('link',name='Источники документов',exact=True).click();expect(page.locator('#sources-content')).to_contain_text('Проиндексировано:',timeout=15000)
            page.locator('[data-tab="retrieval"]').click();expect(page.locator('[data-tab="retrieval"]')).to_have_attribute('aria-selected','true')
            assert not errors,errors;browser.close()
        evidence.update(status='PASS',session_id=sid,checks=['drawers / Escape','memory edit','scope / selection','Markdown / table / long reply','citation viewer / diagnostics','bounded pages','stable DOM and scroll while reading','new messages button','Enter / Shift+Enter / duplicate guard / error draft','desktop / tablet / mobile','new chat / switching','Sources / Search'])
    except BaseException as e:evidence.update(status='FAILED',error=str(e));raise
    finally:
        (output/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8');server.should_exit=True;thread.join(timeout=40)
    print(json.dumps(evidence,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
