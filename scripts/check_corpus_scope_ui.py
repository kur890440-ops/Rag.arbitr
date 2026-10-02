"""Live corpus-wide comparison plus selected-document retrieval debug check."""
import json
import sqlite3
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright,expect

BASE='http://127.0.0.1:8765';RUN='d6575efd47b349f1a8a733bb2bc50d2c'
DOC='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'
OUT=Path('data/day22/corpus-scope');OUT.mkdir(exist_ok=True)
q=next(q for q in requests.get(BASE+'/api/rag/questions').json() if 'Кто указан заявителем' in q['question'])
def counts():
    with sqlite3.connect('file:data/sqlite/metadata.db?mode=ro',uri=True) as db:
        return {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['recognition_metadata','chunks','index_runs']}
before=counts();errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1700,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(f'{BASE}/?run_id={RUN}')
    expect(page.locator('#rag-scope')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-scope')).to_have_value('ALL_DOCUMENTS')
    other=page.locator('.file-select[data-select-document]').first.get_attribute('data-select-document')
    page.locator(f'.file-select[data-select-document="{DOC}"]').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document',DOC)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-scope')).to_have_value('ALL_DOCUMENTS')
    page.locator('#rag-scope').select_option('SELECTED_DOCUMENT')
    page.locator(f'.file-select[data-select-document="{other}"]').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document',other)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-scope')).to_have_value('SELECTED_DOCUMENT')
    page.locator(f'.file-select[data-select-document="{DOC}"]').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document',DOC)
    page.locator('[data-tab="retrieval"]').click()
    page.locator('#rag-scope').select_option('ALL_DOCUMENTS')
    expect(page.locator(f'[data-rag-question="{q["question_id"]}"]')).to_be_visible(timeout=15000)
    page.locator(f'[data-rag-question="{q["question_id"]}"]').click()
    page.wait_for_function("() => document.querySelector('.rag-comparison') && !['QUEUED','RUNNING'].includes(document.querySelector('.rag-comparison .section-head small').textContent)",timeout=360000)
    key=page.locator('.rag-comparison').get_attribute('data-comparison-id')
    a=requests.get(BASE+'/api/rag/comparisons/'+key).json()
    (OUT/'all-documents.json').write_text(json.dumps(a,ensure_ascii=False,indent=2),encoding='utf-8')
    assert a['status']=='COMPLETED',a['error_json']
    assert a['rag_scope']=='ALL_DOCUMENTS' and a['selected_document_id']==a['full_document_id']==DOC
    assert a['retrieved_count']==5 and len(a['documents_represented'])>1
    assert a['source_metrics']['document_hit@1']==a['source_metrics']['source_hit@1']==1
    expect(page.locator('.rag-answers>div')).to_have_count(3)
    page.locator('.rag-comparison').scroll_into_view_if_needed();page.screenshot(path=str(OUT/'all-documents.png'))
    # Raw search uses the exact same explicit scope control, without another model call.
    page.locator('#rag-scope').select_option('SELECTED_DOCUMENT')
    page.locator('details',has=page.locator('.search-form')).locator('summary').click()
    page.locator('.search-form [name=question]').fill(q['question'])
    with page.expect_response(lambda r:r.url.endswith(f'/ui/runs/{RUN}/search') and r.request.method=='POST',timeout=120000) as response:
        page.locator('.search-form button').click()
    assert response.value.status==200
    body=response.value.request.post_data
    assert 'rag_scope=SELECTED_DOCUMENT' in body and DOC in body
    b=requests.post(f'{BASE}/api/runs/{RUN}/search',headers={'X-RAG-Request':'1'},json={'question':q['question'],'rag_scope':'SELECTED_DOCUMENT','selected_document_id':DOC},timeout=120).json()
    assert all(h['document_id']==DOC for group in b.values() for h in group['hits'])
    (OUT/'selected-document.json').write_text(json.dumps(b,ensure_ascii=False,indent=2),encoding='utf-8')
    page.screenshot(path=str(OUT/'selected-document.png'))
    browser.close()
evidence={'comparison_id':key,'before':before,'after':counts(),'js_errors':errors,'raw_scope_forwarded':True}
assert before==evidence['after'] and not errors
(OUT/'browser-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','comparison':key,'documents_in_topk':len(a['documents_represented']),'expected_rank':next(h['rank'] for h in a['retrieved_sources'] if h['document_id']==DOC)}))
