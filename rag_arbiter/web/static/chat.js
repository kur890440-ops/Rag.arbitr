(() => {
const $=s=>document.querySelector(s);let timer;
// Independent of active generation: updates also arrive while Chat is idle.
const toast=$('#knowledge-toast'), notificationQueue=[];
let notificationCursor=Number(toast?.dataset.cursor||0), toastActive=false, discoveryNotice=null, discoveryBusy=false, knowledgePolling=false;
function showKnowledgeUpdate(){
 if(toastActive||!notificationQueue.length)return;
 toastActive=true;const count=notificationQueue.shift();
 toast.textContent=`База знаний обновлена.\nДобавлено ${count} новых документов.`;toast.hidden=false;
 setTimeout(()=>{toast.hidden=true;toast.textContent='';toastActive=false;showKnowledgeUpdate();},5000);
}
const dismissedReviews=new Map();
function reviewDismissals(){
 const key='rag-chat-dismissed-reviews:'+state()?.session;
 if(!dismissedReviews.has(key)){
  let ids=[];try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))ids=saved;}catch(_){}
  dismissedReviews.set(key,new Set(ids));
 }
 return {key,ids:dismissedReviews.get(key)};
}
function renderDocumentReviews(rows){
 const feed=$('#document-review-feed');if(!feed)return;
 const dismissed=reviewDismissals();rows=rows.filter(row=>!dismissed.ids.has(row.review_id));
 const signature=JSON.stringify(rows);if(feed.dataset.signature===signature)return;
 const opened=new Set([...feed.querySelectorAll('details[open]')].map(e=>e.dataset.review));
 feed.dataset.signature=signature;feed.hidden=!rows.length;feed.replaceChildren();
 const labels={document_type:'Вид документа',summary:'Краткое содержание',key_provisions:'Основные положения',important_dates:'Даты',important_amounts:'Суммы',obligations:'Обязанности',attention_points:'Вопросы для проверки',material_changes:'Оценка существенности',possible_consequences:'Возможные последствия — предположения'};
 const errors={VERSION_NOT_AVAILABLE:'Предыдущая версия недоступна для сравнения.',INDEX_NOT_READY:'Новая версия ещё не готова к анализу.',REVIEW_GENERATION_FAILED:'Не удалось сформировать анализ.',DIFF_FAILED:'Не удалось сравнить версии.',DOCUMENT_TOO_LARGE:'Документ превышает лимит автоматического анализа.',ACCESS_DENIED:'Нет доступа к источнику.'};
 function text(parent,tag,value){const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;}
 if(rows.length){
  const header=document.createElement('div');header.className='document-review-header';feed.append(header);
  const close=text(header,'button','Закрыть');close.type='button';close.className='secondary';close.setAttribute('aria-label','Закрыть автоматический анализ документов');
  close.addEventListener('click',()=>{
   for(const row of rows)dismissed.ids.add(row.review_id);
   try{localStorage.setItem(dismissed.key,JSON.stringify([...dismissed.ids]));}catch(_){}
   feed.hidden=true;feed.replaceChildren();delete feed.dataset.signature;
  });
 }
 for(const row of rows){
  const card=document.createElement('article');card.className='document-review';feed.append(card);
  text(card,'strong',(row.kind==='NEW'?'Добавлен документ: ':'Изменён документ: ')+row.document_name);
  if(row.counts)text(card,'p',`Добавлено: ${row.counts.ADDED}. Удалено: ${row.counts.REMOVED}. Изменено: ${row.counts.MODIFIED}. Перемещено: ${row.counts.MOVED}.`);
  if(row.error)text(card,'p',errors[row.error]||row.error);
  if(['QUEUED','RUNNING'].includes(row.status))text(card,'p','Автоматический анализ: '+(row.status==='QUEUED'?'ожидает обработки':'выполняется'));
  const summary=row.result?.summary;
  if(summary){text(card,'p',row.interpretation?'Оценка LLM:':'Краткое содержание:');for(const item of (Array.isArray(summary)?summary.slice(0,3):[{text:summary}]))text(card,item.text.trim().startsWith('|')?'pre':'p',item.text);if(Array.isArray(summary)&&summary.length>3)text(card,'small','Полное резюме — ниже.');}
  const details=document.createElement('details');details.dataset.review=row.review_id;details.open=opened.has(row.review_id);card.append(details);
  text(details,'summary',row.kind==='MODIFIED'?'Подробные изменения':'Подробное резюме и источники');
  if(row.warnings?.length)text(details,'p','UNCERTAIN: соответствие некоторых блоков неоднозначно.');
  function source(version,block,label){if(!block)return;const button=text(details,'button',label);button.type='button';button.className='secondary chat-source';button.dataset.url='/ui/chat/'+state().session+'/reviews/'+row.review_id+'/versions/'+version+'/blocks/'+block;}
  for(const c of row.changes||[]){
   text(details,'strong',c.change_type+' · '+c.status+' · '+c.section);
   if(c.old_text){text(details,'pre','Было: '+c.old_text);source(c.old_version_id,c.old_block_id,'Источник прежней версии');}
   if(c.new_text){text(details,'pre','Стало: '+c.new_text);source(c.new_version_id,c.new_block_id,'Источник новой версии');}
   for(const cell of c.table_changes||[])text(details,'p',`Строка: ${cell.row} · Столбец: ${cell.column} · ${cell.old_value??'—'} → ${cell.new_value??'—'}`);
  }
  if(row.interpretation)text(details,'p','Ниже — интерпретация модели. Возможные последствия не являются установленными фактами.');
  for(const [field,values] of Object.entries(row.result||{})){
   if(!Array.isArray(values)||!values.length)continue;
   text(details,'strong',labels[field]||field);
   for(const item of values){text(details,item.text.trim().startsWith('|')?'pre':'p',item.text);for(const id of item.source_references||[]){const ref=(row.source_references||[]).find(r=>r.reference_id===id);if(ref?.block_id)source(ref.version_id,ref.block_id,'Подтверждающий фрагмент');if(ref?.change_id){const change=(row.changes||[]).find(c=>c.change_id===ref.change_id);if(change){source(change.old_version_id,change.old_block_id,'До изменения');source(change.new_version_id,change.new_block_id,'После изменения');}}}}
  }
  text(details,'small','Версия: '+row.new_version_id+' · '+row.status);
 }
}
function renderDiscovery(notice){
 const panel=$('#chat-discovery-notice');if(!panel)return;
 discoveryNotice=notice;
 panel.hidden=!notice?.files.length;if(panel.hidden)return;
 const changed=notice.files.some(f=>f.change_type==='MODIFIED'),fresh=notice.files.some(f=>f.change_type!=='MODIFIED');
 $('#discovery-title').textContent=`В источнике ${notice.source_name} найдены ${changed?(fresh?'новые документы и версии':'новые версии документов'):'новые документы'} — ${notice.files.length}.`;
 const list=$('#discovery-files'),signature=JSON.stringify(notice.files);
 if(list.dataset.signature!==signature){
  const position=list.scrollTop;list.replaceChildren();
  for(const file of notice.files){const li=document.createElement('li'),name=document.createElement('span'),status=document.createElement('span');name.textContent=file.filename;status.textContent=(file.change_type==='MODIFIED'?'Новая версия · ':'')+file.status;li.append(name,status);list.append(li);}
  list.dataset.signature=signature;list.scrollTop=position;
 }
 $('#discovery-ready').textContent=`Готовы к анализу: ${notice.ready_count}`;
 $('#discovery-add').textContent=notice.files.some(f=>f.change_type==='MODIFIED')?`Добавить / обновить ${notice.ready_count} документов`:`Добавить ${notice.ready_count} документов`;
 syncDiscoveryButtons();
}
function syncDiscoveryButtons(){
 if(!$('#discovery-add'))return;
 $('#discovery-add').disabled=discoveryBusy||scopeSaving||state()?.busy==='1'||!discoveryNotice?.ready_count;
 $('#discovery-dismiss').disabled=discoveryBusy||scopeSaving;
}
async function pollKnowledgeUpdates(){
 if(knowledgePolling)return;knowledgePolling=true;
 try{
  const sid=state()?.session,version=$('#chat-source-controls')?.dataset.version;
  const r=await fetch('/api/chat/knowledge-updates?after='+notificationCursor+(sid?'&session_id='+encodeURIComponent(sid):''),{cache:'no-store'});
  if(r.ok){const data=await r.json();
   if(discoveryBusy||scopeSaving||sid!==state()?.session||version!==$('#chat-source-controls')?.dataset.version)return;
   if(data.notice&&Number(version)!==data.notice.scope_version){if(!data.session||data.session.scope_version!==data.notice.scope_version)return;applyScopeSession(data.session);}
   renderDiscovery(data.notice);renderDocumentReviews(data.reviews||[]);
   for(const event of data.events){if(event.id>notificationCursor)notificationQueue.push(event.count);}
   notificationCursor=Math.max(notificationCursor,data.cursor);showKnowledgeUpdate();
  }
 }catch(_){}finally{knowledgePolling=false;setTimeout(pollKnowledgeUpdates,2000);}
}
async function decideDiscovery(action){
 if(discoveryBusy||scopeSaving||!discoveryNotice?.files.length)return;
 discoveryBusy=true;syncDiscoveryButtons();$('#discovery-error').textContent='';
 try{
  const result=await api('/api/chat/sessions/'+state().session+'/discovery-decision','POST',{
   action,event_ids:discoveryNotice.files.map(f=>f.event_id),document_ids:discoveryNotice.files.filter(f=>f.status==='INDEXED').map(f=>f.document_id),expected_version:discoveryNotice.scope_version});
  applyScopeSession(result.session);renderDiscovery(result.notice);await refresh();
 }catch(e){$('#discovery-error').textContent=e.message;}
 finally{discoveryBusy=false;syncDiscoveryButtons();}
}
$('#discovery-add')?.addEventListener('click',()=>decideDiscovery('add'));
$('#discovery-dismiss')?.addEventListener('click',()=>decideDiscovery('dismiss'));
const state=()=>$('.chat-grid')?.dataset;
function error(e){$('#chat-error').hidden=false;$('#chat-error').textContent=e.message;}
async function api(url,method,body){const r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-RAG-Request':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Не удалось выполнить запрос');return d;}
let scopeSaving=false;
const dialog=$('#document-selector');
let baseScope=dialog?.dataset.scope||'ALL_DOCUMENTS', savedSource=$('#chat-source-id')?.value||'';
let selected=new Set(dialog?JSON.parse(dialog.dataset.selected):[]);
if(dialog?.dataset.legacy&&!selected.size)selected.add(dialog.dataset.legacy);
let draft=new Set(),selectorOffset=0,selectorTimer,selectorRequest=0;
function scopeLabel(){
 if(!$('#chat-source-scope'))return;
 $('#chat-source-scope').value=selected.size?'SELECTED':baseScope==='NETWORK'&&savedSource?'SOURCE':baseScope;
 $('#chat-network-selector').hidden=baseScope!=='NETWORK';
 $('#chat-source-id').value=savedSource;
 $('#chat-document-scope').hidden=!selected.size;
}
function applyScopeSession(session){
 $('#chat-source-controls').dataset.version=session.scope_version;baseScope=session.source_scope;savedSource=session.source_id||'';selected=new Set(session.selected_document_ids);
 $('#chat-document-scope strong').textContent='Анализ: '+(session.document_name||selected.size+' документов');scopeLabel();
}
async function saveScope(scope,source,ids=[]){
 scopeSaving=true;sync();
 try{
 const session=await api('/api/chat/sessions/'+state().session+'/source','PATCH',{scope,source_id:source||null,selected_document_ids:ids,expected_version:Number($('#chat-source-controls').dataset.version)});
 applyScopeSession(session);renderDiscovery(null);$('#chat-error').hidden=true;await refresh();return true;
 }catch(e){scopeLabel();error(e);return false;}finally{scopeSaving=false;sync();}
}
async function loadDocuments(){
 const request=++selectorRequest;
 const r=await fetch('/api/chat/sessions/'+state().session+'/documents?q='+encodeURIComponent($('#selector-search').value)+'&offset='+selectorOffset);
 if(!r.ok)throw Error('Не удалось загрузить документы');const html=await r.text();if(request!==selectorRequest)return;
 $('#selector-results').innerHTML=html;
 document.querySelectorAll('[data-document-id]').forEach(el=>el.checked=draft.has(el.dataset.documentId));
 $('#selector-count').textContent='Выбрано: '+draft.size;
}
function openSelector(){draft=new Set(selected);selectorOffset=0;$('#selector-error').textContent='';dialog.showModal();loadDocuments().catch(e=>$('#selector-error').textContent=e.message);}
$('#choose-documents')?.addEventListener('click',openSelector);$('#change-documents')?.addEventListener('click',openSelector);
$('#close-document-selector')?.addEventListener('click',()=>{dialog.close();scopeLabel();});dialog?.addEventListener('close',scopeLabel);
$('#selector-search')?.addEventListener('input',()=>{clearTimeout(selectorTimer);selectorOffset=0;selectorTimer=setTimeout(()=>loadDocuments().catch(error),250);});
$('#selector-none')?.addEventListener('click',()=>{draft.clear();loadDocuments().catch(error);});
$('#selector-all')?.addEventListener('click',async()=>{try{const r=await fetch('/api/chat/sessions/'+state().session+'/documents?all_ready=true');if(!r.ok)throw Error('Не удалось проверить готовность');draft=new Set((await r.json()).document_ids);await loadDocuments();}catch(e){$('#selector-error').textContent=e.message;}});
$('#selector-apply')?.addEventListener('click',async()=>{if(!draft.size){$('#selector-error').textContent='Выберите хотя бы один готовый документ. Для полной области используйте «Сбросить» в Chat.';return;}if(await saveScope(baseScope,savedSource,[...draft]))dialog.close();else $('#selector-error').textContent=$('#chat-error').textContent;});
dialog?.addEventListener('change',e=>{if(e.target.matches('[data-document-id]')){const id=e.target.dataset.documentId;e.target.checked?draft.add(id):draft.delete(id);$('#selector-count').textContent='Выбрано: '+draft.size;}});
dialog?.addEventListener('click',e=>{const page=e.target.closest('[data-selector-offset]');if(page){selectorOffset=Number(page.dataset.selectorOffset);loadDocuments().catch(error);}});
$('#chat-source-scope')?.addEventListener('change',()=>{const value=$('#chat-source-scope').value;if(value==='SELECTED'){openSelector();return;}if(value==='SOURCE'){const source=savedSource||$('#chat-source-id').options[1]?.value;if(source)saveScope('NETWORK',source);else{scopeLabel();error(Error('Сначала подключите источник во вкладке «Источники документов».'));}return;}saveScope(value,'');});
$('#chat-source-id')?.addEventListener('change',()=>saveScope('NETWORK',$('#chat-source-id').value));
$('#clear-chat-document')?.addEventListener('click',()=>saveScope(baseScope,savedSource));scopeLabel();
let submitting=false,refreshing=false,olderLoading=false;
const scroller=()=>$('.chat-thread');
const atBottom=()=>{const el=scroller();return !el||el.scrollHeight-el.scrollTop-el.clientHeight<80;};
function bottom(){const el=scroller();if(el)el.scrollTop=el.scrollHeight;$('#chat-new-messages')?.setAttribute('hidden','');}
function savePosition(){const el=scroller();if(!el)return;const top=el.getBoundingClientRect().top;const anchor=[...el.querySelectorAll('.chat-message')].find(m=>m.getBoundingClientRect().bottom>top);sessionStorage.setItem('rag-chat-position:'+state().session,JSON.stringify({bottom:atBottom(),anchor:anchor?.id,offset:anchor?anchor.getBoundingClientRect().top-top:0}));if(atBottom())$('#chat-new-messages').hidden=true;}
scroller()?.addEventListener('scroll',savePosition,{passive:true});window.addEventListener('pagehide',savePosition);
$('#chat-new-messages')?.addEventListener('click',bottom);
function drawer(id,open){const el=document.getElementById(id);if(!el)return;if(open&&!el.open)el.showModal();else if(!open)el.close();}
$('#toggle-chat-memory')?.addEventListener('click',()=>drawer('memory-drawer',true));
$('#toggle-chat-history')?.addEventListener('click',()=>drawer('history-drawer',true));
for(const id of ['history-drawer','memory-drawer']){
 const el=document.getElementById(id),button=$(id==='history-drawer'?'#toggle-chat-history':'#toggle-chat-memory');
 el?.addEventListener('close',()=>button?.setAttribute('aria-expanded','false'));
 el?.addEventListener('toggle',()=>button?.setAttribute('aria-expanded',String(el.open)));
 el?.addEventListener('click',e=>{if(e.target===el){const r=el.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)el.close();}});
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-close-drawer]');if(b)drawer(b.dataset.closeDrawer,false);if(e.target.closest('.chat-session'))drawer('history-drawer',false);});
let memoryHTML='';
function patchMemory(root){const fragment=root.querySelector('#chat-memory-fragment');if(!fragment)return;const html=fragment.innerHTML;if(html!==memoryHTML){const open=[...$('#memory-drawer-body').querySelectorAll('details')].map(d=>d.open);$('#memory-drawer-body').innerHTML=html;$('#memory-drawer-body').querySelectorAll('details').forEach((d,i)=>d.open=!!open[i]);memoryHTML=html;}if(root===document)fragment.remove();}
patchMemory(document);
function sync(){const s=state();if(!s)return;localStorage.setItem('rag-chat-session',s.session);const busy=s.busy==='1'||submitting;$('#chat-submit').disabled=busy||scopeSaving;$('#chat-submit').textContent=busy?'Обработка…':'Отправить';$('#chat-send-status').textContent=busy?'Готовлю ответ':'';
['#chat-source-scope','#chat-source-id','#choose-documents','#change-documents','#clear-chat-document','#selector-apply'].forEach(id=>{if($(id))$(id).disabled=busy||scopeSaving;});syncDiscoveryButtons();clearTimeout(timer);if(s.busy==='1')timer=setTimeout(refresh,1200);}
async function refresh(){const s=state();if(!s||refreshing)return;refreshing=true;
 try{const sequences=[...document.querySelectorAll('.chat-message')].map(el=>Number(el.dataset.sequence));const after=Math.max(0,...sequences);
 const r=await fetch('/ui/chat/'+encodeURIComponent(s.session)+'?after='+after);if(!r.ok)throw Error('Не удалось обновить чат');const parsed=new DOMParser().parseFromString(await r.text(),'text/html');const fresh=parsed.querySelector('.chat-grid');
 const follow=atBottom(),position=scroller().scrollTop;let added=0;
 for(const message of parsed.querySelectorAll('.chat-message')){if(!document.getElementById(message.id)){$('#chat-messages').append(message);added++;}}
 for(const key of ['version','busy','title'])s[key]=fresh.dataset[key];$('#chat-title').textContent=s.title;const historyLink=document.querySelector('.chat-session[href="/chat?session_id='+s.session+'"]');if(historyLink?.firstChild?.nodeType===3)historyLink.firstChild.textContent=s.title;
 const workspace=parsed.querySelector('#workspace-events');if(workspace&&!$('#workspace-events')){scroller().append(workspace);window.htmx?.process(workspace);}
 const progress=parsed.querySelector('#chat-progress-area');if(progress&&progress.innerHTML!==$('#chat-progress-area').innerHTML)$('#chat-progress-area').innerHTML=progress.innerHTML;
 patchMemory(parsed);if(follow)bottom();else{scroller().scrollTop=position;if(added)$('#chat-new-messages').hidden=false;}sync();if(added===40)timer=setTimeout(refresh,10);
 }catch(e){error(e);timer=setTimeout(refresh,3000);}finally{refreshing=false;}}
document.addEventListener('click',async e=>{const b=e.target.closest('#chat-load-older');if(!b||olderLoading)return;olderLoading=true;b.disabled=true;
 try{const r=await fetch('/ui/chat/'+state().session+'?before='+b.dataset.before);if(!r.ok)throw Error('Не удалось загрузить историю');const parsed=new DOMParser().parseFromString(await r.text(),'text/html');const el=scroller(),height=el.scrollHeight,top=el.scrollTop;
 const messages=[...parsed.querySelectorAll('.chat-message')].filter(m=>!document.getElementById(m.id));$('#chat-messages').prepend(...messages);const previous=parsed.querySelector('#chat-load-older');if(previous)b.replaceWith(previous);else b.remove();el.scrollTop=top+el.scrollHeight-height;
 }catch(err){error(err);b.disabled=false;}finally{olderLoading=false;}});
$('#new-chat').addEventListener('click',async()=>{try{const id=$('#chat-corpus').value;const s=await api('/api/chat/sessions','POST',{processing_run_id:id||null});location.href='/chat?session_id='+s.session_id;}catch(e){error(e);}});
$('#chat-input')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!submitting&&state()?.busy!=='1'&&!scopeSaving)$('#chat-send').requestSubmit();}});
$('#chat-send')?.addEventListener('submit',async e=>{e.preventDefault();const s=state();if(submitting||s.busy==='1'||scopeSaving)return;const field=$('#chat-input'),content=field.value;if(!content.trim())return;submitting=true;sync();
 try{await api('/api/chat/sessions/'+s.session+'/messages','POST',{content,expected_version:Number(s.version),expected_scope_version:Number($('#chat-source-controls').dataset.version)});if(field.value===content)field.value='';s.busy='1';$('#chat-error').hidden=true;await refresh();}catch(e){error(e);}finally{submitting=false;sync();}});

document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
if(b.matches('.diagnostic-copy')){
 const status=$('#diagnostic-status');$('.chat-export').open=true;b.disabled=true;status.textContent='Подготовка диагностики…';
 try {
  const url='/api/chat/sessions/'+encodeURIComponent(state().session)+'/diagnostic'+(b.dataset.turn?'?turn_id='+encodeURIComponent(b.dataset.turn):'');
  const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error('Не удалось получить диагностику');const text=await r.text();
  let copied=false;
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);copied=true;}}catch(_){}
  if(!copied){const field=$('#diagnostic-manual');field.hidden=false;field.value=text;field.focus();field.select();try{copied=document.execCommand('copy');}catch(_){}if(copied)field.hidden=true;}
  status.textContent=copied?'Диагностика скопирована. Её можно вставить в ChatGPT.':'Автоматическое копирование недоступно. Нажмите Ctrl+C в выделенном поле или скачайте .md.';
 }catch(err){status.textContent=err.message;}finally{b.disabled=false;}
}
if(b.matches('.memory-remove,.memory-edit')){const s=state();const body={expected_version:Number(s.version),field:b.dataset.field,key:b.dataset.key};if(b.matches('.memory-edit')){body.value=prompt('Новое определение термина',b.dataset.value);if(body.value===null)return;}await api('/api/chat/sessions/'+s.session+'/memory','PATCH',body);await refresh();}
if(b.matches('.chat-source')){const r=await fetch(b.dataset.url);if(!r.ok)throw Error('Страница недоступна');$('#chat-viewer-content').innerHTML=await r.text();if(!$('#chat-viewer').open)$('#chat-viewer').showModal();}
if(b.id==='close-viewer')$('#chat-viewer').close();
}catch(e){error(e);}});
if(!state()&&!new URLSearchParams(location.search).has('session_id')){const sid=localStorage.getItem('rag-chat-session');if(sid)fetch('/ui/chat/'+encodeURIComponent(sid)+'?before=1').then(r=>{if(r.ok)location.replace('/chat?session_id='+encodeURIComponent(sid));else localStorage.removeItem('rag-chat-session');}).catch(error);}
sync();
if(toast)pollKnowledgeUpdates();
if(state()){requestAnimationFrame(()=>{let saved;try{saved=JSON.parse(sessionStorage.getItem('rag-chat-position:'+state().session)||'null');}catch{}const anchor=saved?.anchor?document.getElementById(saved.anchor):null;if(saved&&!saved.bottom&&anchor)scroller().scrollTop+=anchor.getBoundingClientRect().top-scroller().getBoundingClientRect().top-saved.offset;else bottom();});}
})();
