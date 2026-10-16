let sourceView = '/sources';
let sourceLoading=null;
const sourceParams = new URLSearchParams(location.search);
if (sourceParams.has('source')) sourceView += '/' + encodeURIComponent(sourceParams.get('source')) + '?offset=' + encodeURIComponent(sourceParams.get('offset') || '0');
window.loadSources = function(force=false) {
  const content = document.getElementById('sources-content');
  if(sourceLoading)return force?sourceLoading.then(()=>window.loadSources(true)):sourceLoading;
  if (content && (force || !content.children.length)){
    sourceLoading=htmx.ajax('GET', sourceView, {target:'#sources-content',swap:'innerHTML'}).finally(()=>{sourceLoading=null;});
    return sourceLoading;
  }
};
document.addEventListener('click', event => {
  const link = event.target.closest('[data-source-link]');
  if (!link) return;
  event.preventDefault();
  sourceView = link.getAttribute('href');
  const view = new URL(sourceView, location.origin);
  const url = new URL(location.href);
  url.searchParams.delete('source');
  url.searchParams.delete('offset');
  url.searchParams.delete('connect');
  if (view.pathname !== '/sources') {
    url.searchParams.set('source', decodeURIComponent(view.pathname.split('/').pop()));
    if (view.searchParams.has('offset')) url.searchParams.set('offset', view.searchParams.get('offset'));
  }
  url.hash = 'sources';
  history.replaceState(null, '', url);
  window.loadSources(true);
});
document.addEventListener('htmx:afterSwap', event => {
  if (event.detail.target.id === 'sources-content') initSourceDialog();
});
﻿htmx.config.includeIndicatorStyles = false;
htmx.config.allowEval = false;
htmx.config.allowScriptTags = false;
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-source-action]');
  if (!button) return;
  button.disabled = true;
  const target = document.getElementById('source-message');
  try {
    const response = await fetch(button.dataset.sourceAction, {method:'POST', headers:{'X-RAG-Request':'1'}});
    const data = await response.json();
    if (response.ok && (data.scan_id || button.dataset.sourceAction.endsWith("/toggle"))) { window.loadSources(true); return; }
    target.textContent = response.ok ? (data.scan_id ? 'Сканирование поставлено в очередь: '+data.scan_id+'. Обновите результаты для просмотра прогресса.' : data.status || 'Состояние изменено. Обновите страницу.') : data.detail || 'Ошибка';
  } catch (error) { target.textContent = 'Ошибка соединения'; }
  finally { button.disabled = false; }
});
document.addEventListener('htmx:responseError', event => {
  const target = document.getElementById('path-check-result') || document.getElementById('source-message');
  if (target && event.detail.elt.closest('#sources-panel')) {
    let message = event.detail.xhr.responseText;
    try { message = JSON.parse(message).detail || message; } catch (_) {}
    target.textContent = message;
  }
});

function initSourceDialog() {
const sourceDialog = document.getElementById('source-dialog');
document.getElementById('open-source-dialog')?.addEventListener('click', () => sourceDialog.showModal());
document.getElementById('close-source-dialog')?.addEventListener('click', () => sourceDialog.close());
document.getElementById('check-source-path')?.addEventListener('click', async event => {
  const status = document.getElementById('path-check-result');
  event.target.disabled = true;
  status.textContent = 'Проверка доступа…';
  try {
    const response = await fetch('/api/sources/check-path', {method:'POST', headers:{'X-RAG-Request':'1','Content-Type':'application/json'},
      body:JSON.stringify({root_path:sourceDialog.querySelector('[name=root_path]').value})});
    const result = await response.json();
    status.textContent = response.ok ? 'Доступ на чтение подтверждён: ' + result.root_path + '. Приложение не записывает файлы в источник.' : result.detail || 'Источник недоступен';
  } catch { status.textContent = 'Ошибка соединения'; }
  finally { event.target.disabled = false; }
});

if (sourceDialog && sourceParams.has('connect')) {
  sourceDialog.showModal();
  sourceParams.delete('connect');
}
}
if (location.hash === '#sources') window.loadSources();

document.addEventListener('click',async e=>{const b=e.target.closest('[data-discuss-file]');if(!b)return;b.disabled=true;try{const r=await fetch('/api/catalog/discuss',{method:'POST',headers:{'Content-Type':'application/json','X-RAG-Request':'1'},body:JSON.stringify({file_id:b.dataset.discussFile,scope:'NETWORK',source_id:b.dataset.discussSource})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Документ недоступен');location.href=d.url;}catch(err){document.getElementById('source-message').textContent=err.message;b.disabled=false;}});

document.addEventListener('htmx:beforeRequest',event=>{if(event.detail.elt.matches?.('.source-summaries')&&document.getElementById('source-dialog')?.open)event.preventDefault();});
