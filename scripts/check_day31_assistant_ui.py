"""Browser smoke for /help on an already indexed source. No scans or model calls."""
import argparse,json,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--url',default='http://127.0.0.1:8765');args=parser.parse_args()
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    headers={'X-RAG-Request':'1'}
    response=page.request.post(args.url+'/api/chat/sessions',headers=headers,data={})
    assert response.status==201
    sid=response.json()['session_id']
    response=page.request.patch(args.url+f'/api/chat/sessions/{sid}/source',headers=headers,data=dict(scope='NETWORK',source_id=args.source,expected_version=0))
    assert response.status==200
    page.goto(args.url+'/chat?session_id='+sid)
    expect(page.get_by_text('Вопросы по документам: /help')).to_be_visible()
    page.locator('#chat-input').fill('/help');page.locator('#chat-submit').click()
    expect(page.get_by_text('Я помогаю находить положения документов',exact=False)).to_be_visible(timeout=30000)
    response=page.request.get(args.url+f'/api/chat/sessions/{sid}').json();turn=response['turns'][-1]
    assert turn['response']['status']=='ARCHIVE_STATUS'
    assert turn['trace']['archive_status']['source_id']==args.source
    assert not turn['comparison_run_id'] and not errors
    page.locator('#chat-source-id').select_option('')
    expect(page.locator('#chat-submit')).to_be_enabled(timeout=10000)
    assert page.locator('#chat-discovery-content').count()==0
    expect(page.locator('#choose-documents')).to_be_visible()
    print(json.dumps(dict(result='PASS',session_id=sid,counts=turn['trace']['archive_status']['counts'],js_errors=errors),ensure_ascii=False))
    browser.close()
