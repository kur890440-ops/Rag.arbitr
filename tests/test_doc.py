from pathlib import Path
import pytest
from rag_arbiter import doc_converter as converter
from rag_arbiter.documents import file_hash,digest
from rag_arbiter.ingestion import Ingestor
from rag_arbiter.storage import MetadataStore
from rag_arbiter.progress import ConsoleReporter
from test_docx import make_docx,NoOCR,FACT


def test_doc_invalid_container(tmp_path):
    path=tmp_path/'fake.doc';path.write_bytes(b'not word')
    with pytest.raises(converter.DocConversionError,match='DOC_INVALID_CONTAINER'):
        converter.validate_doc(path)


def test_doc_conversion_original_identity_and_cache(config,monkeypatch):
    source=config.corpus_path/'original.doc';source.write_bytes(b'{\\rtf1 test}')
    original_hash=file_hash(source)
    calls=[]
    monkeypatch.setattr(converter,'converter_identity',lambda:{'engine':'MOCK LibreOffice'})
    monkeypatch.setattr(converter,'libreoffice_executable',lambda:Path('mock-soffice'))
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            calls.append(args)
            assert Path(args[-1])!=source
            make_docx(Path(args[-1]).with_suffix('.docx'))
        def poll(self):return 0
        def wait(self,**kwargs):return 0
    from types import SimpleNamespace
    monkeypatch.setattr(converter,'subprocess',SimpleNamespace(Popen=Process,DEVNULL=converter.subprocess.DEVNULL,CREATE_NO_WINDOW=getattr(converter.subprocess,'CREATE_NO_WINDOW',0)))
    store=MetadataStore(config.sqlite_path)
    try:
        docs,stats=Ingestor(config,store,NoOCR()).ingest([source])
        assert not stats['errors']
        doc=docs[0]
        assert doc.document_id==digest([str(source.resolve()),original_hash])
        assert doc.file_name==source.name and doc.source_path==str(source.resolve())
        assert doc.content_type=='application/msword' and doc.locator_kind=='structure'
        assert FACT in [b.text for b in doc.blocks]
        assert file_hash(source)==original_hash
        again,stats=Ingestor(config,store,NoOCR()).ingest([source])
        assert stats['recognition_reused']==1 and len(calls)==1
        assert again[0].document_id==doc.document_id
    finally:store.close()


def test_doc_failed_conversion_does_not_hide_other_documents(config,monkeypatch):
    source=config.corpus_path/'bad.doc';source.write_bytes(b'{\\rtf1 test}')
    good=config.corpus_path/'good.docx';make_docx(good)
    def fail(*args):raise converter.DocConversionError('DOC_CONVERSION_TIMEOUT')
    monkeypatch.setattr(converter,'convert_doc',fail)
    store=MetadataStore(config.sqlite_path)
    try:
        docs,stats=Ingestor(config,store,NoOCR()).ingest([source,good])
        assert len(docs)==1 and docs[0].file_name==good.name
        assert stats['errors'][0]['error']=='DOC_CONVERSION_TIMEOUT'
    finally:store.close()

@pytest.mark.parametrize('cancel',[False,True])
def test_doc_worker_stopped_on_timeout_or_cancel(config,monkeypatch,cancel):
    from types import SimpleNamespace
    from rag_arbiter.progress import RunCancelled
    source=config.corpus_path/'slow.doc';source.write_bytes(b'{\\rtf1 test}')
    monkeypatch.setattr(converter,'converter_identity',lambda:{'engine':'MOCK LibreOffice'})
    monkeypatch.setattr(converter,'libreoffice_executable',lambda:Path('mock-soffice'))
    calls=[]
    class Process:
        def __init__(self,*a,**kw):pass
        def poll(self):return None
        def kill(self):calls.append('kill')
        def wait(self,**kw):calls.append('wait')
    monkeypatch.setattr(converter,'subprocess',SimpleNamespace(Popen=Process,DEVNULL=-3,CREATE_NO_WINDOW=0))
    ticks=iter([0,999])
    monkeypatch.setattr(converter,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    class Reporter:
        def check(self):
            if cancel:raise RunCancelled()
    with pytest.raises(RunCancelled if cancel else converter.DocConversionError):
        converter.convert_doc(source,config,Reporter())
    assert calls==([] if cancel else ['kill','wait'])

from test_web import web,HEADERS


def test_doc_upload_admission(web):
    client=web[0]
    response=client.post('/api/uploads',headers=HEADERS,files={'files':('legacy.doc',b'{\\rtf1 Legacy text}','application/msword')})
    assert response.status_code==201,response.text
    response=client.post('/api/uploads',headers=HEADERS,files={'files':('bad.doc',b'bad signature','application/msword')})
    assert response.status_code==400,response.text




def test_doc_converter_failure_has_no_cached_success(config, monkeypatch):
    from types import SimpleNamespace
    source = config.corpus_path / 'failed.doc'
    source.write_bytes(b'{\\rtf1 test}')
    monkeypatch.setattr(converter, 'converter_identity', lambda: {'engine': 'test'})
    monkeypatch.setattr(converter, 'libreoffice_executable', lambda: Path('mock-soffice'))
    class Process:
        returncode = 0  # LibreOffice may exit successfully without writing a document.
        def __init__(self, *args, **kwargs): pass
        def poll(self): return 0
    monkeypatch.setattr(converter, 'subprocess', SimpleNamespace(Popen=Process, DEVNULL=-3, CREATE_NO_WINDOW=0))
    with pytest.raises(converter.DocConversionError, match='DOC_CONVERSION_FAILED'):
        converter.convert_doc(source, config, ConsoleReporter())
    assert not list(config.cache_path.glob('doc-conversion/*/conversion.json'))


def test_doc_converter_cancellation_during_process(config, monkeypatch):
    from types import SimpleNamespace
    from rag_arbiter.progress import RunCancelled
    source = config.corpus_path / 'cancel.doc'
    source.write_bytes(b'{\\rtf1 test}')
    monkeypatch.setattr(converter, 'converter_identity', lambda: {'engine': 'test'})
    monkeypatch.setattr(converter, 'libreoffice_executable', lambda: Path('mock-soffice'))
    calls = []
    class Process:
        def __init__(self, *args, **kwargs): pass
        def poll(self): return None
        def kill(self): calls.append('kill')
        def wait(self, **kwargs): calls.append('wait')
    monkeypatch.setattr(converter, 'subprocess', SimpleNamespace(Popen=Process, DEVNULL=-3, CREATE_NO_WINDOW=0))
    class Reporter:
        n = 0
        def check(self):
            self.n += 1
            if self.n == 2: raise RunCancelled()
    with pytest.raises(RunCancelled): converter.convert_doc(source, config, Reporter())
    assert calls == ['kill', 'wait']


def test_doc_error_code_visible():
    from rag_arbiter.application.runs import safe_error
    for code in ('DOC_CONVERSION_TIMEOUT', 'DOC_CONVERSION_FAILED', 'DOC_CONVERTER_UNAVAILABLE', 'DOC_PASSWORD_PROTECTED'):
        assert code in safe_error(ValueError(code))
