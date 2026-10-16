(() => {
htmx.config.allowEval=false;htmx.config.allowScriptTags=false;htmx.config.includeIndicatorStyles=false;
const form=document.getElementById('catalog-filters'),error=document.getElementById('catalog-error');let debounce;
const params=()=>new URLSearchParams(new FormData(form));
const treeKey=()=> 'rag-catalog-folders:'+['scope','source_id','run_id'].map(k=>form.elements[k].value).join(':');
let expanded=new Set();
function readExpanded(){try{expanded=new Set(JSON.parse(sessionStorage.getItem(treeKey())||'[]'));}catch{expanded=new Set();}}
function fail(message){error.hidden=false;error.textContent=message;}
function save(){const q=params().toString();sessionStorage.setItem('rag-catalog-filters',q);history.replaceState(null,'','/catalog?'+q);}
function tree(){readExpanded();const q=params();q.delete('folder');q.delete('offset');htmx.ajax('GET','/ui/catalog/tree?'+q,{target:'#catalog-tree-root',swap:'innerHTML'});}
const saved=location.search.slice(1)||sessionStorage.getItem('rag-catalog-filters')||'';
for(const [key,value] of new URLSearchParams(saved)){if(form.elements[key])form.elements[key].value=value;}
if(!form.elements.scope.value)form.elements.scope.value='ALL_DOCUMENTS';
if(!form.elements.page_size.value)form.elements.page_size.value='25';
form.addEventListener('submit',()=>{clearTimeout(debounce);error.hidden=true;save();});
form.addEventListener('input',e=>{if(e.target.name==='q'){form.elements.offset.value=0;clearTimeout(debounce);debounce=setTimeout(()=>form.requestSubmit(),300);}});
form.addEventListener('change',e=>{
 form.elements.offset.value=0;
 if(['scope','source_id','run_id'].includes(e.target.name)){
  form.elements.folder.value='';
  if(e.target.name==='scope')form.elements.source_id.value='';
  if(e.target.name==='source_id'&&e.target.value)form.elements.scope.value='NETWORK';
  tree();
 }
 form.requestSubmit();
});
document.addEventListener('toggle',e=>{
 const node=e.target;if(!node.matches?.('.catalog-folder'))return;
 if(node.open)expanded.add(node.dataset.folder);else expanded.delete(node.dataset.folder);
 sessionStorage.setItem(treeKey(),JSON.stringify([...expanded]));
 if(node.open&&!node.dataset.loaded){node.dataset.loaded='true';htmx.ajax('GET',node.dataset.childrenUrl,{target:node.querySelector('.catalog-branch'),swap:'innerHTML'}).catch(()=>{delete node.dataset.loaded;fail('Не удалось загрузить папку. Раскройте её повторно.');});}
},true);
document.body.addEventListener('htmx:afterSwap',e=>{for(const node of e.detail.target.querySelectorAll('.catalog-folder'))if(expanded.has(node.dataset.folder))node.open=true;});
document.body.addEventListener('htmx:responseError',()=>fail('Не удалось загрузить данные каталога. Проверьте выбранные фильтры и повторите.'));
document.addEventListener('click',async e=>{
 const page=e.target.closest('[data-catalog-offset]'),folder=e.target.closest('[data-catalog-folder]'),viewer=e.target.closest('[data-catalog-viewer]'),discuss=e.target.closest('[data-catalog-discuss]');
 if(page){form.elements.offset.value=page.dataset.catalogOffset;form.requestSubmit();document.getElementById('catalog-results').scrollTop=0;}
 if(folder){form.elements.folder.value=folder.dataset.catalogFolder;form.elements.offset.value=0;form.requestSubmit();}
 if(viewer){document.getElementById('catalog-viewer').showModal();htmx.ajax('GET',viewer.dataset.catalogViewer,{target:'#catalog-viewer-body',swap:'innerHTML'});}
 if(e.target.id==='catalog-close-viewer')document.getElementById('catalog-viewer').close();
 if(discuss){discuss.disabled=true;try{
  const p=params(),r=await fetch('/api/catalog/discuss',{method:'POST',headers:{'Content-Type':'application/json','X-RAG-Request':'1'},body:JSON.stringify({file_id:discuss.dataset.catalogDiscuss,scope:p.get('scope'),source_id:p.get('source_id')||null,run_id:p.get('run_id')||null})});
  const data=await r.json();if(!r.ok)throw Error(data.detail||'Документ недоступен');localStorage.setItem('rag-chat-session',data.session_id);location.href=data.url;
 }catch(err){fail(err.message);discuss.disabled=false;}}
});
document.addEventListener('change',e=>{if(e.target.id==='catalog-viewer-selection')htmx.ajax('GET',e.target.value,{target:'#catalog-viewer-fragment',swap:'innerHTML'});});
const sid=localStorage.getItem('rag-chat-session');if(sid)document.querySelector('[data-resume-chat]').href='/chat?session_id='+encodeURIComponent(sid);
document.addEventListener('DOMContentLoaded',()=>{form.requestSubmit();tree();},{once:true});
})();
