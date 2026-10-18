"""Catalog contracts: real registry/canonical/Qdrant, explicitly mocked inference."""
from pathlib import Path
from test_rag import ragweb
from test_chat import MemoryProvider,enable_rewrite
from test_document_assistant import ask
from test_chat_sources import document,FACTS
from test_network_sources import scan
from dialogue_fixtures import install
from rag_arbiter.application.chat_memory import TaskMemoryUpdater,ContextualQueryBuilder
from rag_arbiter.application.catalog import CatalogService


def test_catalog_filters_lazy_tree_viewer_and_document_chat(ragweb,tmp_path):
    client,rid,llm,_=ragweb;runs=client.app.state.runs;svc=client.app.state.network;chat=client.app.state.chat
    root=tmp_path/'catalog';(root/'first'/'nested').mkdir(parents=True)
    document(root/'first'/'nested'/'network.docx',FACTS[0])
    svc.config.network_sources.allowed_roots.append(str(root));svc.config.network_sources.stability_seconds=0
    source=svc.add(dict(name='Catalog fixture',root_path=str(root)));assert scan(svc,source)['ingestion_completed']==1
    row=svc.page('network_files',source['id'])[0]
    filters=dict(scope='NETWORK',source_id=source['id'])
    assert client.get('/catalog').status_code==200
    html=client.get('/ui/catalog',params=filters).text
    assert 'network.docx' in html and 'INDEXED' in html and 'Catalog fixture' in html
    assert 'network.docx' not in client.get('/ui/catalog',params={**filters,'q':'absent'}).text
    assert 'network.docx' in client.get('/ui/catalog',params={**filters,'q':'NETWORK','format':'DOCX','status':'INDEXED','folder':'first/nested'}).text
    assert 'network.docx' not in client.get('/ui/catalog',params={**filters,'format':'PDF'}).text
    tree=client.get('/ui/catalog/tree',params=filters).text
    assert 'data-folder="first"' in tree and 'data-folder="first/nested"' not in tree
    tree=client.get('/ui/catalog/tree',params={**filters,'folder':'first'}).text
    assert 'data-folder="first/nested"' in tree
    viewer=client.get('/ui/catalog/viewer/'+row['id'],params=filters)
    assert viewer.status_code==200 and f'/documents/{row["document_id"]}/blocks/' in viewer.text
    session=client.post('/api/catalog/discuss',json={**filters,'file_id':row['id']}).json()
    selected=chat.load(session['session_id'])['session']
    assert selected['document_id']==row['document_id'] and selected['source_id']==source['id']
    page=client.get(session['url']).text
    assert 'id="chat-discovery"' not in page and 'id="chat-discovery-notice"' in page and 'discovery-search' not in page and 'Выбрать документы' in page
    assert 'Анализ:' in page and 'network.docx' in page
    with runs.db() as store:
        pending={**row,'id':'pending','filename':'pending.docx','document_id':None,'active_run_id':None,'indexing_status':'PENDING','ingestion_status':'PENDING','status':'NEW'}
        store.put('network_files','pending',pending)
    denied=client.post('/api/catalog/discuss',json={**filters,'file_id':'pending'})
    assert denied.status_code==400
    assert client.post('/api/catalog/discuss',json={'scope':'MANUAL','run_id':rid,'file_id':row['id']}).status_code==404
    enable_rewrite(llm);memory=MemoryProvider()
    chat.memory=TaskMemoryUpdater(lambda _:memory,runs.config.llm);chat.resolver=ContextualQueryBuilder(lambda _:memory,runs.config.llm)
    install(chat,llm,runs.config.llm)
    manual=CatalogService(runs).snapshot('MANUAL',run_id=rid)['files'][0]
    a=client.post('/api/catalog/discuss',json=dict(file_id=manual['id'],scope='MANUAL',run_id=rid)).json()['session_id']
    b=chat.create(rid).session_id
    point=ask(client,a,'/help Когда оплата?')
    assert point['grounding_status']=='GROUNDED' and point['trace']['eligible_document_ids']==[manual['document_id']]
    follow=ask(client,a,'А когда оплата?')
    assert follow['trace']['eligible_document_ids']==[manual['document_id']]
    full=ask(client,a,'/help Сравни все условия.')
    assert full['trace']['answer_mode']=='EXHAUSTIVE_NO_RAG'
    assert full['trace']['coverage']['eligible_document_ids']==[manual['document_id']]
    overview=ask(client,a,'/help');assert overview['trace']['archive_status']['counts']['discovered']==1
    assert chat.load(b)['session']['document_id'] is None and chat.load(b)['session']['scope_version']==0
    chat.select_source(a,'MANUAL',None,0)
    assert chat.load(a)['session']['document_id'] is None and chat.load(a)['session']['scope_version']==1
    assert chat.load(b)['session']['scope_version']==0
    pdf=client.get('/ui/catalog/viewer/'+manual['id'],params={'scope':'MANUAL','run_id':rid})
    assert pdf.status_code==200 and '/pages/' in pdf.text
    assert len(CatalogService(runs).snapshot('MANUAL')['files'])==2
    with runs.db() as store:
        row['active_run_id']=None;store.put('network_files',row['id'],row)
        # Day32 keeps explicitly pinned versions even when the registry head disappears.
        # This scenario requires that the pinned version's index is unavailable too.
        for version in store.all('document_versions'):
            if version['canonical_document_id']==row['document_id']:
                index=store.get('document_version_indexes',version['version_id'])
                if index:
                    index['status']='FAILED';store.put('document_version_indexes',version['version_id'],index)
    stale=ask(client,selected['session_id'],'/help Какие условия?')
    assert stale['response']['status']=='NO_INDEXED_DOCUMENTS' and stale['comparison_run_id'] is None
    # Historical manifests can outlive document metadata or have null filenames.
    with runs.db() as store:
        doc=store.get('documents',manual['document_id']);doc['file_name']=None
        store.put('documents',manual['document_id'],doc)
    result=client.get('/ui/catalog')
    assert result.status_code==200 and 'network.docx' in result.text
    assert client.get('/ui/catalog/tree').status_code==200




def test_catalog_pagination_status_and_bounded_tree():
    info=dict(files=[dict(id=str(i),filename=f'{i:03}.docx',relative_path=f'folder{i:03}/deep/a.docx',format='DOCX',availability='DISCOVERED',status='NEW',source_name='source') for i in range(125)])
    page=CatalogService.page(info,q='124',page_size=10,offset=100)
    assert page['total']==1 and page['files'][0]['id']=='124' and page['page']==1
    assert not CatalogService.page(info,status='INDEXED')['files']
    branch=CatalogService.branches(info)
    assert len(branch['nodes'])==100 and branch['more']
    assert len(CatalogService.branches(info,offset=100)['nodes'])==25


def test_catalog_legacy_manifest_without_document_id(ragweb,monkeypatch):
    from rag_arbiter.application.views import ResultsService
    client,rid,_,_=ragweb
    original=ResultsService.snapshot
    def legacy(self,run_id):
        result=original(self,run_id)
        if run_id==rid:
            entry=result['corpus']['documents'][0]
            result['corpus']['documents'].append({**entry,'document_id':None})
        return result
    monkeypatch.setattr(ResultsService,'snapshot',legacy)
    response=client.get('/ui/catalog')
    assert response.status_code==200
    assert all(f['id'] for f in CatalogService(client.app.state.runs).snapshot()['files'])
