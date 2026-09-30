"""Real browser acceptance: existing corpus, one Fixed experiment, no recognition."""
import json
import sqlite3
import time
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8765'
OUT=Path('data/ui-refactor');OUT.mkdir(exist_ok=True)
SOURCE='0715c856bfab405bae0578691de9f578'
LARGE='0f006e8e98084695ae4ede6df976e823'

def recognition_count():
    with sqlite3.connect('file:data/sqlite/metadata.db?mode=ro',uri=True) as db:
        return db.execute('select count(*) from recognition_metadata').fetchone()[0]

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1600,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(f'{BASE}/?run_id={LARGE}')
    expect(page.locator('[data-file-id]')).to_have_count(43,timeout=60000)
    geometry=page.evaluate('''() => ({body:document.documentElement.scrollHeight,viewport:innerHeight,
      panel:document.getElementById('file-panel').getBoundingClientRect().height,
      listHeight:document.getElementById('file-list-content').clientHeight,
      listScroll:document.getElementById('file-list-content').scrollHeight,
      rows:[...document.querySelectorAll('[data-file-id]')].map(r=>({height:r.getBoundingClientRect().height,
      nowrap:getComputedStyle(r.querySelector('.file-name')).whiteSpace,
      ellipsis:getComputedStyle(r.querySelector('.file-name')).textOverflow}))})''')
    assert geometry['body']<=geometry['viewport']+1
    assert geometry['listScroll']>geometry['listHeight']
    assert all(r['height']==34 and r['nowrap']=='nowrap' and r['ellipsis']=='ellipsis' for r in geometry['rows'])
    expect(page.locator('.file-group').first).to_have_attribute('data-group','processed')
    page.locator('.file-group').first.locator('summary').first.click()
    assert not page.locator('.file-group').first.evaluate('el=>el.open')
    page.locator('.file-group').first.locator('summary').first.click()
    page.locator('#file-search').fill('nonexistent-file-filter')
    assert page.locator('[data-file-id]:visible').count()==0
    page.locator('#file-search').fill('')
    page.screenshot(path=str(OUT/'43-files.png'))
    page.goto(f'{BASE}/?run_id={SOURCE}')
    expect(page.locator('[data-file-id]')).to_have_count(16,timeout=60000)
    files=requests.get(f'{BASE}/api/runs/{SOURCE}/files').json()
    chosen=max(files,key=lambda f:f['pages']);doc=chosen['document_id']
    before=requests.get(f'{BASE}/api/runs/{SOURCE}/chunks',params={'document_id':doc,'limit':1}).json()
    count=recognition_count()
    page.locator(f'.file-select[data-select-document="{doc}"]').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document',doc)
    expect(page.locator('#chunk-content .chunk-card').first).to_be_visible(timeout=30000)
    bounds=page.locator('#chunk-content .comparison > div').evaluate_all('(els)=>els.map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}))')
    assert bounds[0]['x']<bounds[1]['x'] and abs(bounds[0]['y']-bounds[1]['y'])<2
    assert not page.locator('#document-select').count()
    page.locator('[name=use_global]').uncheck()
    page.locator('[name=fixed_tokens]').fill('700')
    page.locator('[name=fixed_overlap]').fill('80')
    page.screenshot(path=str(OUT/'chunking-before.png'))
    page.locator('[data-rechunk=fixed]').click()
    page.wait_for_function('(old)=>currentRun!==old',arg=SOURCE,timeout=30000)
    new=page.evaluate('currentRun')
    (OUT/'real-run.json').write_text(json.dumps({'run_id':new,'document_id':doc,'source':SOURCE}),encoding='utf-8')
    print('REAL RUN',new,flush=True)
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        run=requests.get(f'{BASE}/api/runs/{new}').json()
        if run['status'] not in ('RUNNING','QUEUED'):break
        page.wait_for_timeout(1000)
    assert run['status']=='COMPLETED',run
    after=requests.get(f'{BASE}/api/runs/{new}/chunks',params={'document_id':doc,'limit':1}).json()
    assert recognition_count()==count
    assert before['structure']['active_run']==after['structure']['active_run']
    assert before['structure']['stats']['chunks']==after['structure']['stats']['chunks']
    assert before['fixed']['stats']['chunks']!=after['fixed']['stats']['chunks']
    page.goto(f'{BASE}/?run_id={new}&document_id={doc}')
    expect(page.locator('#chunk-content .chunk-card').first).to_be_visible(timeout=60000)
    expect(page.locator(f'[data-file-id="{doc}"] .file-metrics')).to_contain_text(f"F{after['fixed']['stats']['chunks']}")
    page.screenshot(path=str(OUT/'chunking-after.png'))
    page.locator('.all-documents').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document','')
    page.locator('[name=fixed_tokens]').fill('900')
    confirmations=[]
    page.on('dialog',lambda d:(confirmations.append(d.message),d.dismiss()))
    page.locator('[data-rechunk=both]').click()
    page.wait_for_timeout(1500)
    assert confirmations and '16' in confirmations[0] and '90' in confirmations[0] and 'НЕ' in confirmations[0]
    # Restore global defaults; no mass experiment is launched by this check.
    page.locator('[name=fixed_tokens]').fill('1000')
    page.locator('#chunk-settings [type=submit]').click()
    page.wait_for_timeout(500)
    assert not errors,errors
    evidence={'geometry':geometry,'before':before,'after':after,'run_id':new,'document_id':doc,
              'recognition_metadata_before':count,'recognition_metadata_after':recognition_count(),
              'corpus_confirmation':confirmations,'browser_errors':errors}
    (OUT/'browser-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    browser.close()
    print('PASS real UI + Fixed experiment',flush=True)
