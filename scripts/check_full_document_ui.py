"""Live acceptance: three real requests for small document, two for oversized document."""
import json
import sqlite3
import time
import sys
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8765'
RUN='d6575efd47b349f1a8a733bb2bc50d2c'
SMALL='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'
LARGE='ec5f9ff3b48349567c3fb731250aadddd18b62ed29cea008edccda76b9efdb37'
OUT=Path('data/day22/full-document');OUT.mkdir(exist_ok=True)

def counts():
    with sqlite3.connect('file:data/sqlite/metadata.db?mode=ro',uri=True) as db:
        return {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['recognition_metadata','chunks','index_runs']}

before=counts();evidence={};errors=[]
questions=requests.get(BASE+'/api/rag/questions',timeout=10).json()
q=next(q for q in questions if 'Кто указан заявителем' in q['question'])
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1700,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(f'{BASE}/?run_id={RUN}&document_id={SMALL}')
    expect(page.locator('#rag-form')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator(f'[data-rag-question="{q["question_id"]}"]')).to_be_visible(timeout=15000)
    if '--resume' in sys.argv:
        saved=json.loads((OUT/'small.json').read_text(encoding='utf-8'))
        page.evaluate('(id)=>ragPoll(id)',saved['comparison_run_id'])
    else:
        page.locator(f'[data-rag-question="{q["question_id"]}"]').click()
    page.wait_for_function("() => document.querySelector('.rag-comparison') && !['RUNNING','QUEUED'].includes(document.querySelector('.rag-comparison .section-head small').textContent)",timeout=360000)
    key=page.locator('.rag-comparison').get_attribute('data-comparison-id')
    result=requests.get(BASE+'/api/rag/comparisons/'+key,timeout=15).json()
    (OUT/'small.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['status']=='COMPLETED',result['error_json']
    assert result['full_document_status']=='SUCCESS' and result['minimax_requests_count']==3
    assert result['full_document_context']['included_pages']==[1]
    expect(page.locator('.rag-answers>div')).to_have_count(3)
    positions=page.locator('.rag-answers>div').evaluate_all('(els)=>els.map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,width:e.getBoundingClientRect().width}))')
    assert len(set(round(x['y']) for x in positions))==1 and all(x['width']>=310 for x in positions)
    page.locator('.rag-comparison').scroll_into_view_if_needed();page.screenshot(path=str(OUT/'small.png'))
    page.locator('.full-document-panel .source-page').click();expect(page.locator('#viewer img')).to_be_visible(timeout=15000)
    page.locator('[data-tab="retrieval"]').click()
    page.set_viewport_size({'width':750,'height':1100})
    positions_mobile=page.locator('.rag-answers>div').evaluate_all('(els)=>els.map(e=>e.getBoundingClientRect().y)')
    assert positions_mobile==sorted(set(positions_mobile))
    page.set_viewport_size({'width':1700,'height':1100})
    r=requests.post(f'{BASE}/api/runs/{RUN}/rag/compare',headers={'X-RAG-Request':'1'},json={'question_id':q['question_id'],'document_id':LARGE,'strategy':'structure','top_k':5},timeout=15)
    r.raise_for_status();big_id=r.json()['comparison_run_id']
    page.evaluate('(id)=>ragPoll(id)',big_id)
    page.wait_for_function("(id) => document.querySelector('.rag-comparison')?.dataset.comparisonId===id && !['RUNNING','QUEUED'].includes(document.querySelector('.rag-comparison .section-head small').textContent)",arg=big_id,timeout=360000)
    big=requests.get(BASE+'/api/rag/comparisons/'+big_id,timeout=15).json()
    (OUT/'large.json').write_text(json.dumps(big,ensure_ascii=False,indent=2),encoding='utf-8')
    assert big['status']=='PARTIAL' and big['full_document_status']=='CONTEXT_TOO_LARGE'
    assert big['no_rag_result']['status']==big['rag_result']['status']=='SUCCESS'
    assert big['minimax_requests_count']==2 and big['full_document_context']['text']==''
    assert big['full_document_context']['included_pages']==[]
    page.locator('.full-document-panel').scroll_into_view_if_needed();page.screenshot(path=str(OUT/'large.png'))
    evidence.update(small_id=key,large_id=big_id,desktop=positions,mobile_y=positions_mobile,errors=errors,before=before,after=counts())
    browser.close()
assert evidence['before']==evidence['after'] and not errors
(OUT/'browser-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','small':key,'large':big_id,'unchanged':before}))
