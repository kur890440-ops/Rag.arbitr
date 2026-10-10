import copy
import pytest
from test_rag import ragweb
from test_direct_experiments import direct_lab, launch
from rag_arbiter.application.judicial_document import available_documents, read_document, text_hash, TASK, QUALITY_LABELS
from rag_arbiter.application.direct_experiments import comparison


def doc_body(c, body):
    doc=available_documents(c.app.state.runs)[0]
    preview=c.get('/api/local-llm-documents/'+doc['version'])
    assert preview.status_code==200,preview.text
    source=preview.json()
    return {'document_version':source['version'],'document_sha256':source['sha256'],'options':body['options']|{'context_window':16384}},source


def test_canonical_full_text_direct_and_frozen(direct_lab,monkeypatch):
    c,_,body,calls,cloud=direct_lab
    request,source=doc_body(c,body)
    before=c.app.state.runs.config.model_dump(mode='json')
    scheduled=[]
    monkeypatch.setattr(c.app.state.runs.executor,'submit',lambda *a:scheduled.append(a))
    response=c.post('/api/local-llm-experiments',json=request);assert response.status_code==200,response.text
    key=response.json()['id']
    with c.app.state.runs.db() as store:
        canonical=store.get('normalized_documents',source['version'])
        expected='\n\n'.join(f"[PAGE {p['page_number']}]\n"+'\n'.join(b['text'] for b in sorted(canonical['blocks'],key=lambda b:(b['page_number'],b['reading_order'],b['block_id'])) if b['page_number']==p['page_number']) for p in sorted(canonical['pages'],key=lambda p:p['page_number']))
        assert source['text']==expected and source['sha256']==text_hash(expected)
        canonical['blocks'][0]['text']='Changed after submission'
        store.put('normalized_documents',source['version'],canonical)
    fn,*args=scheduled[0];fn(*args)
    row=c.get('/api/local-llm-experiments/'+key).json()
    assert row['kind']=='judicial_act' and row['status']=='SUCCESS'
    assert row['document']==source and source['text'] in row['user_prompt']
    assert TASK in row['user_prompt'] and 'Changed after submission' not in row['user_prompt']
    assert len([1 for path,_ in calls if path=='/api/chat'])==1
    assert not cloud.calls and c.app.state.runs.config.model_dump(mode='json')==before
    # Stale preview cannot silently select changed canonical content.
    assert c.post('/api/local-llm-experiments',json=request).status_code==400


def test_same_document_quality_and_legacy_isolation(direct_lab):
    c,_,body,_,_=direct_lab
    legacy=launch(c,body)
    request,source=doc_body(c,body)
    a=launch(c,request)
    request['options']['prompt_version']='day29-judicial-v1'
    b=launch(c,request)
    assert a['user_prompt']==b['user_prompt'] and a['system_prompt']!=b['system_prompt']
    assert comparison(b,a)['fair'] and not comparison(b,legacy)['fair']
    changed=copy.deepcopy(b);changed['document']['text']+='other'
    assert not comparison(changed,a)['fair']
    assert c.post('/api/local-llm-experiments/'+b['id']+'/review',json={'scores':{'decision':'pass','no_invention':'partial'},'note':'Checked against full act'}).status_code==200
    saved=c.get('/api/local-llm-experiments/'+b['id']).json()
    assert saved['quality_review']['method']=='manual' and saved['quality_review']['scores']['decision']=='pass'
    assert c.post('/api/local-llm-experiments/'+b['id']+'/review',json={'scores':{'fake':'pass'}}).status_code==422
    history=c.get('/api/local-llm-experiments?task=judicial_act').json()
    assert {r['id'] for r in history}=={a['id'],b['id']}
    html=c.get('/ui/local-llm-experiments',params={'ids':a['id']+','+b['id'],'baseline':a['id']}).text
    assert 'FAIR COMPARISON' in html and source['sha256'] in html and html.count('data-quality-review=')==2
    assert all(label in html for label in QUALITY_LABELS.values())
    assert legacy['id'] in c.get('/ui/local-llm-legacy').text
    form=c.get('/ui/llm-optimization').text
    assert 'name="document_version"' in form and 'name="question"' not in form and 'day29-judicial-v1' in form


@pytest.mark.parametrize('fault',['missing_page','failed','empty_page','bad_block','wrong_version'])
def test_incomplete_canonical_document_rejected(direct_lab,fault):
    c,_,body,calls,_=direct_lab
    request,source=doc_body(c,body)
    with c.app.state.runs.db() as store:
        doc=store.get('normalized_documents',source['version'])
        if fault=='missing_page':doc['pages']=[]
        elif fault=='failed':doc['status']='PARTIAL'
        elif fault=='empty_page':doc['blocks']=[]
        elif fault=='bad_block':doc['blocks'][0]['document_id']='other'
        elif fault=='wrong_version':doc['cache_identity']='other'
        store.put('normalized_documents',source['version'],doc)
    assert c.post('/api/local-llm-experiments',json=request).status_code==400
    assert not calls


def test_no_silent_truncation_or_user_text_override(direct_lab):
    c,_,body,calls,_=direct_lab
    request,source=doc_body(c,body)
    with c.app.state.runs.db() as store:
        doc=store.get('normalized_documents',source['version']);doc['blocks'][0]['text']='X'*40000
        store.put('normalized_documents',source['version'],doc)
    source=read_document(c.app.state.runs,source['version']);request['document_sha256']=source.sha256
    response=c.post('/api/local-llm-experiments',json=request)
    assert response.status_code==400 and 'utf8_bytes_upper_estimate' in response.text
    request['question']='substitute text'
    assert c.post('/api/local-llm-experiments',json=request).status_code==422
    assert not any(path == '/api/chat' for path, _ in calls)

def test_legacy_canonical_documents_without_version_table(direct_lab):
    c,_,body,calls,_=direct_lab
    request,source=doc_body(c,body)
    with c.app.state.runs.db() as store:
        doc=store.get('normalized_documents',source['version'])
        store.db.execute('DELETE FROM normalized_documents WHERE id=?',(source['version'],));store.db.commit()
        assert store.get('documents',doc['document_id'])['cache_identity']==source['version']
    assert source['version'] in {r['version'] for r in available_documents(c.app.state.runs)}
    assert read_document(c.app.state.runs,source['version']).text==source['text']
    row=launch(c,request)
    assert row['status']=='SUCCESS' and row['document']['sha256']==source['sha256']


from test_local_token_budget import qwen_metadata, make_provider


def test_judicial_admission_uses_same_token_budget_as_provider(direct_lab, qwen_metadata, monkeypatch):
    c,_,body,_,_=direct_lab
    request,source=doc_body(c,body)
    with c.app.state.runs.db() as store:
        doc=store.get('normalized_documents',source['version'])
        doc['blocks'][0]['text']='Суд постановил взыскать 1000 рублей. ' * 500
        store.put('normalized_documents',source['version'],doc)
    source=read_document(c.app.state.runs,source['version'])
    request['document_sha256']=source.sha256
    request['options']['model']='same-tag'
    show,_=qwen_metadata
    def factory(cfg, **kwargs):
        provider,_=make_provider(show)
        provider.config=cfg
        return provider
    monkeypatch.setattr('rag_arbiter.application.direct_experiments.LocalLLMProvider',factory)
    row=launch(c,request)
    assert row['status']=='SUCCESS' and row['input_budget']['method']=='ollama_gguf_qwen2_bpe'
    assert source.size_bytes>request['options']['context_window']
    assert source.text in row['exact_input']['messages'][1]['content']
    assert row['input_budget']['estimated_required']<=request['options']['context_window']
    assert 'data-input-budget' in c.get('/ui/local-llm-experiments',params={'ids':row['id']}).text
