"""Local DOC versus historical Word DOCX comparison; never uploads documents."""
import json,subprocess,time,zipfile,xml.etree.ElementTree as ET,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.storage import MetadataStore
c=Config.load();s=MetadataStore(c.sqlite_path);rows=[r for r in s.all('network_files') if r.get('format')=='DOC' and (len(sys.argv)<2 or r.get('source_id')==sys.argv[1])];s.close()
from rag_arbiter.doc_converter import libreoffice_executable, isolated_profile
exe=libreoffice_executable()
out=Path('data/day31-doc-libreoffice-check').resolve();out.mkdir(exist_ok=True)
import tempfile
profile_work=tempfile.TemporaryDirectory(prefix='comparison-',dir=out)
profile_uri=isolated_profile(Path(profile_work.name))
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def stats(p):
 with zipfile.ZipFile(p) as z:x=ET.fromstring(z.read('word/document.xml'))
 clean=lambda t:re.sub(r'\s+','',''.join(t.itertext()))
 return clean(x),[[clean(cell) for cell in row.findall('w:tc',ns)] for table in x.findall('.//w:tbl',ns) for row in table.findall('w:tr',ns)],len(x.findall('.//w:tbl',ns))
results=[]
for r in rows:
 src=c.web_data_path/'network'/'versions'/r['id']/r['content_hash']/r['filename'];dest=out/r['id'];dest.mkdir(exist_ok=True)
 t=time.monotonic();result=subprocess.run([str(exe),'-env:UserInstallation='+profile_uri,'--headless','--convert-to','docx:Office Open XML Text','--outdir',str(dest),str(src.resolve())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
 target=dest/(src.stem+'.docx');assert result.returncode==0 and target.is_file(),result.stderr
 txt,tab,nt=stats(target);record=dict(file_id=r['id'],previously_indexed=bool(r.get('last_indexed')),seconds=round(time.monotonic()-t,2),characters=len(txt),tables=nt,rows=len(tab),cells=sum(map(len,tab)))
 for manifest in (c.cache_path/'doc-conversion').glob('*/conversion.json'):
  meta=json.loads(manifest.read_text(encoding='utf-8'))
  if meta['source_hash']==r['content_hash'] and meta['identity']['engine']=='Microsoft Word':
   old,ot,on=stats(manifest.parent/'document.docx');record.update(word_tables=on,text_equal=old==txt,table_rows_cells_equal=ot==tab);break
 results.append(record);print(json.dumps(record),flush=True)
(out/'evidence.json').write_text(json.dumps(results,indent=2),encoding='utf-8')

profile_work.cleanup()
