(() => {
const $=s=>document.querySelector(s);let timer;
const state=()=>$('.chat-grid')?.dataset;
function error(e){$('#chat-error').hidden=false;$('#chat-error').textContent=e.message;}
async function api(url,method,body){const r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-RAG-Request':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Не удалось выполнить запрос');return d;}
function sync(){const s=state();if(!s)return;localStorage.setItem('rag-chat-session',s.session);$('#chat-submit').disabled=s.busy==='1';clearTimeout(timer);if(s.busy==='1')timer=setTimeout(refresh,1200);}
async function refresh(){const s=state();if(!s)return;try{const r=await fetch('/ui/chat/'+encodeURIComponent(s.session));if(!r.ok)throw Error('Не удалось обновить чат');$('#chat-content').innerHTML=await r.text();sync();}catch(e){error(e);timer=setTimeout(refresh,3000);}}
$('#new-chat').addEventListener('click',async()=>{try{const id=$('#chat-corpus').value;if(!id)throw Error('Сначала обработайте документы');const s=await api('/api/chat/sessions','POST',{processing_run_id:id});location.href='/chat?session_id='+s.session_id;}catch(e){error(e);}});
$('#chat-send')?.addEventListener('submit',async e=>{e.preventDefault();const s=state();$('#chat-submit').disabled=true;try{await api('/api/chat/sessions/'+s.session+'/messages','POST',{content:$('#chat-input').value,expected_version:Number(s.version)});$('#chat-input').value='';await refresh();}catch(e){error(e);sync();}});
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
if(b.matches('.diagnostic-copy')){
 const status=$('#diagnostic-status');b.disabled=true;status.textContent='Подготовка диагностики…';
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
if(b.matches('.chat-source')){const r=await fetch(b.dataset.url);if(!r.ok)throw Error('Страница недоступна');$('#chat-viewer-content').innerHTML=await r.text();$('#chat-viewer').hidden=false;$('#chat-viewer').scrollIntoView({behavior:'smooth'});}
if(b.id==='close-viewer')$('#chat-viewer').hidden=true;
}catch(e){error(e);}});
if(!state()&&!new URLSearchParams(location.search).has('session_id')){const sid=localStorage.getItem('rag-chat-session');if(sid)fetch('/api/chat/sessions/'+encodeURIComponent(sid)).then(r=>{if(r.ok)location.replace('/chat?session_id='+encodeURIComponent(sid));else localStorage.removeItem('rag-chat-session');}).catch(error);}
sync();
})();
