"""Live Day22 browser acceptance; seeds two questions verified against original page.

Run only on the intended local corpus. Does not require cloud credentials;
without them, verifies NOT_CONFIGURED plus real BGE-M3/Qdrant retrieval.
"""
import json
import sqlite3
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8765'
RUN='d6575efd47b349f1a8a733bb2bc50d2c'
DOC='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'
OUT=Path('data/day22');OUT.mkdir(exist_ok=True)
QUESTIONS=[
 ('На какой срок просит продлить конкурсное производство ООО «РУСМЕТ» заявитель в ходатайстве от 24 февраля 2025 года?', 'На 6 месяцев. Это просьба заявителя в ходатайстве, а не вывод о принятом судом решении.'),
 ('Кто указан заявителем в ходатайстве ООО «РУСМЕТ» от 24 февраля 2025 года?', 'ИО конкурсного управляющего ООО «РУСМЕТ» Погосян Лилия Гамлетовна.'),
]

def counts():
    with sqlite3.connect('file:data/sqlite/metadata.db?mode=ro',uri=True) as db:
        return {t:db.execute('select count(*) from '+t).fetchone()[0] for t in ['recognition_metadata','chunks','index_runs']}

before=counts();evidence={'before':before}
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1600,'height':1000});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(f'{BASE}/?run_id={RUN}')
    expect(page.locator('#rag-form')).to_be_attached(timeout=60000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('#rag-form')).to_be_visible()
    expect(page.locator('#rag-run-all')).to_be_attached(timeout=15000)
    existing=requests.get(BASE+'/api/rag/questions').json()
    for question,answer in QUESTIONS:
        if any(q['question']==question for q in existing):continue
        page.locator('#rag-question').fill(question)
        page.locator('#rag-add-question').click()
        page.locator('[name=expected_answer]').fill(answer)
        page.locator('[name=expected_sources]').fill(json.dumps([{'document_id':DOC,'page_start':1}],ensure_ascii=False))
        page.locator('#rag-edit-form [type=submit]').click()
        expect(page.locator('#rag-question-editor')).not_to_be_visible()
        expect(page.locator('.question-text',has_text=question)).to_be_visible()
    assert page.locator('.control-questions details').count()==0
    page.locator('[data-rag-edit]').first.click()
    expect(page.locator('[name=expected_answer]')).to_have_value(QUESTIONS[0][1])
    page.locator('#rag-edit-form [type=submit]').click()
    expect(page.locator('#rag-question-editor')).not_to_be_visible()
    page.locator('.question-text',has_text=QUESTIONS[0][0]).click()
    page.wait_for_function("() => document.querySelector('#rag-result .rag-comparison') && !['QUEUED','RUNNING'].includes(document.querySelector('#rag-result .section-head small').textContent)",timeout=180000)
    key=page.locator('.rag-comparison').get_attribute('data-comparison-id')
    result=requests.get(BASE+'/api/rag/comparisons/'+key).json()
    assert result['retrieved_count']==5 and result['used_count']>0,result['error_json']
    assert result['question_id'] and result['source_metrics']['source_hit@5']==1
    columns=page.locator('.rag-answers>div').evaluate_all('(els)=>els.map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}))')
    assert columns[0]['x']<columns[1]['x'] and abs(columns[0]['y']-columns[1]['y'])<2
    assert not page.locator('.rag-source').first.evaluate('e=>e.open')
    page.locator('.rag-source summary').first.click()
    expect(page.locator('.rag-source pre').first).to_be_visible()
    page.screenshot(path=str(OUT/'rag-comparison.png'))
    page.locator('.rag-source .source-page').first.click()
    expect(page.locator('#viewer img')).to_be_visible(timeout=20000)
    page.locator('[data-tab="retrieval"]').click()
    page.locator('#rag-run-all').click()
    expect(page.locator('#rag-batch-results [data-batch-id]')).to_be_attached(timeout=15000)
    page.wait_for_function("() => !document.querySelector('#rag-batch-results').textContent.includes('RUNNING') && !document.querySelector('#rag-batch-results').textContent.includes('QUEUED')",timeout=180000)
    page.locator('#rag-batch-results [data-rag-result]').first.click()
    expect(page.locator('.rag-comparison')).to_have_attribute('data-comparison-id',page.locator('#rag-batch-results [data-rag-result]').first.get_attribute('data-rag-result'))
    page.locator(f'.file-select[data-select-document="{DOC}"]').click()
    expect(page.locator('#chunk-settings')).to_have_attribute('data-document',DOC)
    page.locator('[data-tab="retrieval"]').click()
    page.locator('#rag-strategy').select_option('fixed');page.locator('#rag-top-k').fill('2')
    page.locator('#rag-question').fill(QUESTIONS[0][0]);page.locator('#rag-form [type=submit]').click()
    page.wait_for_function("() => document.querySelector('.rag-comparison') && !['QUEUED','RUNNING'].includes(document.querySelector('.rag-comparison .section-head small').textContent)",timeout=180000)
    scoped=requests.get(BASE+'/api/rag/comparisons/'+page.locator('.rag-comparison').get_attribute('data-comparison-id')).json()
    assert scoped['retrieval_strategy']=='fixed' and scoped['document_id']==DOC
    assert all(s['document_id']==DOC for s in scoped['sources'])
    page.screenshot(path=str(OUT/'rag-document-scope.png'))
    # Delete deactivates a disposable UI-only question; the two verified ones remain.
    page.locator('#rag-question').fill('Временная проверка удаления Day 22')
    page.locator('#rag-add-question').click();page.locator('#rag-edit-form [type=submit]').click()
    expect(page.locator('#rag-question-editor')).not_to_be_visible()
    row=page.locator('.control-questions li',has_text='Временная проверка удаления Day 22')
    expect(row).to_be_visible();row.locator('[data-rag-delete]').click();expect(row).to_have_count(0)
    page.reload();expect(page.locator('#rag-form')).to_be_attached(timeout=30000)
    page.locator('[data-tab="retrieval"]').click()
    expect(page.locator('.question-text')).to_have_count(2,timeout=15000)
    page.locator('#rag-history').locator('..').locator('summary').click()
    expect(page.locator('[data-rag-batch]').first).to_be_visible()
    page.locator('[data-rag-batch]').first.click()
    expect(page.locator('#rag-batch-results [data-batch-id]')).to_be_attached(timeout=15000)
    evidence.update(comparison=result,scoped_comparison=scoped,browser_errors=errors,columns=columns)
    browser.close()
evidence['after']=counts();assert evidence['before']==evidence['after']
assert not errors,errors
(OUT/'browser-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','comparison':result['comparison_run_id'],'retrieved':result['retrieved_count'],'used':result['used_count'],'generation':result['rag_result']['status'],'unchanged_counts':before}))
