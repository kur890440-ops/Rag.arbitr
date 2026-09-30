/* Browser state is disposable: ProcessingRun and event history live in SQLite. */
htmx.config.includeIndicatorStyles = false;
htmx.config.allowEval = false;
htmx.config.allowScriptTags = false;
let currentRun = document.body.dataset.run || '', stream = null, latestRun = null, refreshTimer = null;
const terminal = new Set(['COMPLETED','PARTIAL','FAILED','CANCELLED','INTERRUPTED']);
const $ = id => document.getElementById(id);
let uploading = false;
let selectedDocument = new URLSearchParams(location.search).get('document_id') || '', activeTab = 'overview';
const collapsedGroups = {};
function setTab(name) {
  activeTab=name;
  document.querySelectorAll('[data-tab-panel]').forEach(el=>el.hidden=el.dataset.tabPanel!==name);
  document.querySelectorAll('[data-tab]').forEach(el=>el.setAttribute('aria-selected',String(el.dataset.tab===name)));
}
function filterFiles() {
  const query=$('file-search').value.toLowerCase();
  document.querySelectorAll('[data-file-name]').forEach(el=>{el.hidden=!el.dataset.fileName.includes(query);el.classList.toggle('selected',el.dataset.fileId===selectedDocument);});
  document.querySelector('.all-documents')?.classList.toggle('selected',!selectedDocument);
}
function refreshFiles(){if(currentRun)htmx.ajax('GET',`/ui/runs/${currentRun}/files`,{target:'#file-list-content',swap:'innerHTML'});}
function updateStartState() {
  let ids=[];
  try { ids=JSON.parse($('uploaded').querySelector('[name=upload_ids]')?.value || '[]'); } catch (_) {}
  const ready=Array.isArray(ids)&&ids.length>0;
  $('start-form').querySelector('button').disabled=uploading||!ready;
  $('start-notice').textContent=uploading?'Дождитесь завершения загрузки файлов.':ready?'':'Сначала выберите файлы и нажмите «Загрузить выбранные файлы».';
}
function notice(message) { $('notification').textContent = message; $('notification').hidden = false; }
async function api(url, options={}) {
  const response = await fetch(url, {...options, headers:{'X-RAG-Request':'1', ...(options.headers||{})}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте выбранные файлы и параметры запроса.');
  return data;
}
document.body.addEventListener('htmx:configRequest', event => { event.detail.headers['X-RAG-Request']='1'; });
document.body.addEventListener('htmx:responseError', event => {
  let message='Операция не выполнена. Проверьте состояние run и параметры.';
  try { const data=JSON.parse(event.detail.xhr.responseText); if(typeof data.detail==='string') message=data.detail; } catch (_) {}
  notice(message);
});
document.body.addEventListener('htmx:afterSwap', event => {
  if(event.detail.target.id==='uploaded') {$('notification').hidden=true;updateStartState();}
  if(event.detail.target.id==='workspace') setTab(activeTab);
  if(event.detail.target.id==='file-list-content'){
    document.querySelectorAll('.file-group').forEach(el=>el.open=collapsedGroups[el.dataset.group]!==true);
    filterFiles();
  }
});
function fileList() {
  $('pending-files').replaceChildren();
  [...$('files').files].forEach((file,index)=>{
    const li=document.createElement('li'), label=document.createElement('span'), button=document.createElement('button');
    label.textContent=`${file.name} · ${(file.size/1048576).toFixed(2)} МБ`; button.textContent='×'; button.type='button'; button.setAttribute('aria-label',`Убрать ${file.name}`);
    button.disabled=uploading;
    button.addEventListener('click',()=>{if(uploading)return;const transfer=new DataTransfer(); [...$('files').files].filter((_,i)=>i!==index).forEach(f=>transfer.items.add(f)); $('files').files=transfer.files; fileList();});
    li.append(label,button); $('pending-files').append(li);
  });
}
$('files').addEventListener('change',fileList);
['dragenter','dragover'].forEach(name=>$('drop-zone').addEventListener(name,event=>{event.preventDefault();$('drop-zone').classList.add('dragover');}));
['dragleave','drop'].forEach(name=>$('drop-zone').addEventListener(name,event=>{event.preventDefault();$('drop-zone').classList.remove('dragover');}));
$('drop-zone').addEventListener('drop',event=>{ if(uploading)return; $('files').files=event.dataTransfer.files; fileList(); });
$('upload-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(uploading||!$('files').files.length)return;
  uploading=true;
  $('files').disabled=true;
  const submit=$('upload-form').querySelector('[type=submit]');
  submit.disabled=true;
  $('notification').hidden=true;
  updateStartState();fileList();
  try {
    while($('files').files.length){
      const file=$('files').files[0], body=new FormData();
      submit.textContent=`Загрузка: ${file.name}`;
      body.append('files',file);
      const response=await fetch('/ui/uploads',{method:'POST',headers:{'X-RAG-Request':'1'},body});
      if(!response.ok){
        let message=`Не удалось загрузить ${file.name}`;
        try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(_){}
        throw new Error(message);
      }
      const fragment=document.createElement('template');
      fragment.innerHTML=await response.text();
      const incoming=fragment.content.querySelector('[name=upload_ids]');
      const existing=$('uploaded').querySelector('[name=upload_ids]');
      incoming.value=JSON.stringify([...JSON.parse(existing?.value||'[]'),...JSON.parse(incoming.value)]);
      fragment.content.querySelector('ul').prepend(...$('uploaded').querySelectorAll('li'));
      $('uploaded').replaceChildren(fragment.content);
      const remaining=new DataTransfer();
      [...$('files').files].slice(1).forEach(item=>remaining.items.add(item));
      $('files').files=remaining.files;
      fileList();
    }
  }catch(error){notice(error.message);}
  finally{
    uploading=false;$('files').disabled=false;submit.disabled=false;
    submit.textContent='Загрузить выбранные файлы';fileList();updateStartState();
  }
});
function elapsed() {
  if(!latestRun?.started_at) return;
  const seconds=Math.max(0,Math.floor(((latestRun.finished_at?Date.parse(latestRun.finished_at):Date.now())-Date.parse(latestRun.started_at))/1000));
  $('elapsed').textContent=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
}
function workspace() { if(currentRun) htmx.ajax('GET',`/ui/runs/${currentRun}/workspace${selectedDocument?'?document_id='+encodeURIComponent(selectedDocument):''}`,{target:'#workspace',swap:'innerHTML'}); }
async function refresh() {
  if(!currentRun) return;
  const requestedRun=currentRun;
  try {
    const run=await api(`/api/runs/${requestedRun}`);
    if(currentRun!==requestedRun) return;
    latestRun=run;
    const option=$('run-select').querySelector(`option[value="${run.run_id}"]`);
    if(option) option.textContent=`${run.created_at.slice(0,19)} · ${run.status} · ${run.run_id.slice(0,8)}`;
    $('run-status').textContent=run.status; $('run-stage').textContent=run.stage; $('run-percent').textContent=`${Math.round(run.progress_percent)}%`;
    $('run-progress').value=run.progress_percent; $('run-message').textContent=run.last_message;
    $('processed-pages').textContent=`${run.processed_pages} / ${run.total_pages}`;
    $('current-page').textContent=run.metrics_json.current_file ? `${run.metrics_json.current_file} · Страница ${run.current_page||'—'} / ${run.metrics_json.document_pages||'—'}` : '—';
    $('recognition-average').textContent=run.metrics_json.average_sec_per_page == null ? 'Среднее время recognition: —' : `Recognition: ${run.metrics_json.average_sec_per_page.toFixed(2)} с/страница (без cache HIT)`;
    $('cache-count').textContent=`${run.cache_hits} / ${run.cache_misses}`;
    $('embedding-count').textContent=`${run.embeddings_created} / ${run.embeddings_reused}`;
    $('cancel-run').disabled=terminal.has(run.status)||run.cancel_requested;
    $('run-diagnostics').textContent=JSON.stringify(run,null,2); elapsed();
    const summary=$('recognition-summary');summary.replaceChildren();
    const stats=run.metrics_json.reliability||{};
    for(const [key,label] of Object.entries({documents_total:'Документов всего',documents_success:'Документы SUCCESS',documents_partial:'Документы PARTIAL',documents_failed:'Документы FAILED',pages_total:'Страниц всего',qwen_success:'Qwen',qwen_retry_success:'Qwen после retry',qwen_fragment_success:'Qwen после split',classic_fallback:'Classic fallback',failed:'Ошибочные страницы',cache_reused:'Страницы из кеша'})){
      if(stats[key]!==undefined){const line=document.createElement('p');line.textContent=`${label}: ${stats[key]}`;summary.append(line);}
    }
    refreshFiles();
    if(terminal.has(run.status) && stream) {stream.close();stream=null;setTab('chunking');workspace();}
  } catch(error){notice(error.message);}
}
function scheduleRefresh(){if(!refreshTimer) refreshTimer=setTimeout(()=>{refreshTimer=null;refresh();},180);}
async function loadRun(id){
  if(stream) stream.close(); stream=null; currentRun=id; latestRun=null;
  $('run-status').textContent=id?'LOADING':'NO RUN YET';
  $('run-stage').textContent='—'; $('run-percent').textContent='—'; $('run-progress').value=0;
  ['processed-pages','elapsed','cache-count','embedding-count','current-page','recognition-average'].forEach(key=>$(key).textContent='—');
  $('run-message').textContent=id?'Получаем сохранённое состояние…':'Выберите запуск.';
  $('cancel-run').disabled=true; $('run-diagnostics').textContent='—';
  $('workspace').replaceChildren();
  $('recognition-summary').replaceChildren();
  if(!id) return;
  history.replaceState(null,'',`/?run_id=${encodeURIComponent(id)}`);
  if(!$('run-select').querySelector(`option[value="${id}"]`)) {const option=new Option(`Run ${id.slice(0,8)}`,id);$('run-select').add(option);}
  $('run-select').value=id; await refresh();
  if(currentRun!==id) return;
  setTab(terminal.has(latestRun?.status)?'chunking':'overview');
  workspace();
  if(!terminal.has(latestRun?.status)){
    stream=new EventSource(`/runs/${id}/events`);
    ['run_started','stage_started','stage_completed','document_started','document_completed','document_error','page_started','page_completed','cache_hit','recognition_completed','embedding_progress','index_completed','evaluation_completed','run_completed','run_failed'].forEach(name=>stream.addEventListener(name,scheduleRefresh));
    stream.onerror=()=>scheduleRefresh();
  }
}
document.body.addEventListener('runCreated',event=>{ $('notification').hidden=true; loadRun(event.detail.run_id); });
$('run-select').addEventListener('change',event=>{selectedDocument='';loadRun(event.target.value);});
$('cancel-run').addEventListener('click',async()=>{try{await api(`/api/runs/${currentRun}/cancel`,{method:'POST'});await refresh();}catch(error){notice(error.message);}});
function showPage(run,id){setTab('recognition');htmx.ajax('GET',`/ui/runs/${run}/pages/${id}`,{target:'#viewer',swap:'innerHTML'});if($('page-select'))$('page-select').value=id;}
document.body.addEventListener('change',event=>{if(event.target.id==='page-select'&&event.target.value)showPage(event.target.dataset.run,event.target.value);});
document.body.addEventListener('click',async event=>{
  const tab=event.target.closest('[data-tab]');if(tab){setTab(tab.dataset.tab);return;}
  const select=event.target.closest('[data-select-document]');
  if(select){selectedDocument=select.dataset.selectDocument;setTab(select.dataset.openTab||'chunking');filterFiles();workspace();history.replaceState(null,'',`/?run_id=${currentRun}${selectedDocument?'&document_id='+selectedDocument:''}`);return;}
  const recalc=event.target.closest('[data-rechunk]');
  if(recalc){try{
    recalc.disabled=true;await saveChunkSettings();
    const payload={document_id:selectedDocument||null,strategies:recalc.dataset.rechunk==='both'?['fixed','structure']:[recalc.dataset.rechunk]};
    const preview=await api(`/api/runs/${currentRun}/rechunk-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!selectedDocument&&!confirm(`Пересчитать corpus: ${preview.documents} документов, ${preview.pages} страниц?\nИзменятся: ${preview.affected_documents} документов, ${preview.affected_pages} страниц.\nРаспознавание Qwen/OCR и PDF rendering НЕ запускаются.\nИндивидуальные настройки файлов сохраняются.`))return;
    payload.confirmed=true;
    const result=await api(`/api/runs/${currentRun}/rechunk`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    await loadRun(result.run_id);
  }catch(error){notice(error.message);}finally{recalc.disabled=false;}return;}
  const evaluation=event.target.closest('[data-evaluate]');if(evaluation){try{const result=await api(`/api/runs/${currentRun}/evaluate`,{method:'POST'});await loadRun(result.run_id);}catch(error){notice(error.message);}return;}
  const retry=event.target.closest('[data-retry-mode]');
  if(retry){try{retry.disabled=true;const result=await api(`/api/runs/${retry.dataset.run}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:retry.dataset.retryMode,document_id:retry.dataset.document||null,page_number:retry.dataset.page?Number(retry.dataset.page):null})});await loadRun(result.run_id);}catch(error){notice(error.message);}finally{retry.disabled=false;}return;}
  const remove=event.target.closest('[data-remove-upload]');if(remove){const input=$('uploaded').querySelector('[name=upload_ids]');input.value=JSON.stringify(JSON.parse(input.value).filter(id=>id!==remove.dataset.removeUpload));remove.closest('li').remove();updateStartState();}
  const source=event.target.closest('.source-page');if(source)showPage(source.dataset.run,source.dataset.page);
  const generate=event.target.closest('[data-generate-report]');if(generate){try{await api(`/api/runs/${generate.dataset.generateReport}/report`,{method:'POST'});generate.textContent='Отчёт обновлён ✓';}catch(error){notice(error.message);}}
});
document.body.addEventListener('toggle',async event=>{
  const embedding=event.target;
  if(embedding.matches?.('.embedding-details')&&embedding.open&&!embedding.dataset.loaded&&!embedding.dataset.loading){
    embedding.dataset.loading='true';
    const output=embedding.querySelector('pre');output.textContent='Загрузка эмбеддинга…';
    try{
      const data=await api(embedding.dataset.embeddingUrl);
      embedding.querySelector('.embedding-info').textContent=`${data.model} · ${data.dimension} значений · ${data.normalization||'без нормализации'}`;
      output.textContent=JSON.stringify(data.vector,null,2);
      embedding.dataset.loaded='true';
    }catch(error){output.textContent=error.message;}
    finally{delete embedding.dataset.loading;}
    return;
  }
  const element=event.target;if(!element.matches?.('.raw-details')||!element.open||element.dataset.loaded)return;
  try{const response=await fetch(element.dataset.rawUrl);if(!response.ok)throw new Error('Raw output недоступен');element.querySelector('pre').textContent=await response.text();element.dataset.loaded='true';}catch(error){element.querySelector('pre').textContent=error.message;}
},true);
async function saveChunkSettings(){
  const form=$('chunk-settings');if(!form.reportValidity())throw new Error('Проверьте параметры');
  const settings={};form.querySelectorAll('input[type=number]').forEach(el=>settings[el.name]=Number(el.value));
  const result=await api(`/api/runs/${currentRun}/chunk-settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_id:selectedDocument||null,use_global:form.elements.use_global?.checked||false,settings})});
  $('chunk-settings-notice').textContent='Параметры сохранены. Активные индексы изменятся после пересчёта.';
  return result;
}
document.body.addEventListener('submit',async event=>{
  if(event.target.id==='chunk-settings'){event.preventDefault();try{await saveChunkSettings();}catch(error){notice(error.message);}}
  if(event.target.id==='source-filter'){event.preventDefault();const params=new URLSearchParams();if(selectedDocument)params.set('document_id',selectedDocument);for(const [k,v] of new FormData(event.target))if(v)params.set(k,v);htmx.ajax('GET',`/ui/runs/${currentRun}/chunks?${params}`,{target:'#chunk-content'});}
});
document.body.addEventListener('change',async event=>{
  if(event.target.name==='use_global'){
    const form=$('chunk-settings');form.querySelectorAll('input[type=number]').forEach(el=>el.disabled=event.target.checked);
    if(event.target.checked){try{const result=await api(`/api/runs/${currentRun}/chunk-settings`);for(const [k,v] of Object.entries(result.global_settings))form.elements[k].value=v;}catch(error){notice(error.message);}}
  }
});
document.body.addEventListener('toggle',event=>{if(event.target.matches?.('.file-group'))collapsedGroups[event.target.dataset.group]=!event.target.open;},true);
$('file-search').addEventListener('input',filterFiles);
$('toggle-files').addEventListener('click',()=>$('file-panel').classList.toggle('open'));
setTab('overview');
setInterval(elapsed,1000);
if(currentRun)loadRun(currentRun);
