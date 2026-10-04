"""Read-only live UI check; no messages, generation or external LLM calls."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('session_id');args=parser.parse_args()
    out=Path('data/day25');out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1400,'height':800})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/messages',lambda r:r.abort())
        page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedDiagnostic=text;}}});")
        page.goto('http://127.0.0.1:8765/chat?session_id='+args.session_id)
        button=page.locator('.chat-diagnostic-actions .diagnostic-copy')
        expect(button).to_be_visible();button.click()
        expect(page.locator('#diagnostic-status')).to_contain_text('Диагностика скопирована')
        report=page.evaluate('window.copiedDiagnostic')
        assert report.startswith('# rag.арбитр · CHAT DIAGNOSTIC') and args.session_id in report
        (out/'chat-diagnostic-example.md').write_text(report,encoding='utf-8')
        turn_button=page.locator('[data-turn].diagnostic-copy').first
        turn_id=turn_button.get_attribute('data-turn')
        turn_button.locator('..').locator('summary').click()
        page.evaluate('window.copiedDiagnostic=null')
        turn_button.click()
        page.wait_for_function('(id)=>window.copiedDiagnostic?.includes("focused_turn_id:\\n```\\n"+id)',arg=turn_id)
        # Clipboard unavailable on an external plain-HTTP interface: manual fallback.
        page.evaluate("navigator.clipboard.writeText=async()=>{throw Error('denied')};document.execCommand=()=>false;")
        button.click();expect(page.locator('#diagnostic-manual')).to_be_visible()
        expect(page.locator('#diagnostic-status')).to_contain_text('Ctrl+C')
        assert page.locator('#diagnostic-manual').input_value().startswith('# rag.арбитр')
        with page.expect_download() as download:
            page.get_by_role('link',name='Скачать .md').click()
        assert download.value.suggested_filename=='chat-diagnostic.md'
        page.locator('#diagnostic-manual').evaluate('(e)=>e.hidden=true')
        page.set_viewport_size({'width':900,'height':600})
        expect(button).to_be_visible()
        page.screenshot(path=str(out/'chat-diagnostic-ui.png'),full_page=True)
        browser.close()
    assert not errors,errors
    (out/'chat-diagnostic-browser.json').write_text(json.dumps(dict(status='PASS',clipboard_payload=True,
        selected_turn=True,manual_fallback=True,download=True,llm_requests=0),indent=2),encoding='utf-8')
    print('PASS: session/turn Copy, success indicator, manual fallback, download; no LLM calls')


if __name__=='__main__':main()
