/* Persisted jobs survive navigation; polling only updates the current workspace. */
let ragView = 0, ragBatchView = 0;
let ragScope='ALL_DOCUMENTS';
const ragJSON = data => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function ragSettings(){return {selected_document_id:selectedDocument||null,rag_scope:ragScope,strategy:$('rag-strategy').value,candidate_top_n:Number($('rag-candidates').value),max_context_sources:Number($('rag-top-k').value),context_token_budget:Number($('rag-context-budget').value),chunking_run_id:$('rag-strategy').selectedOptions[0].dataset.indexId||null};}
function ragIndexLabel(){if($('rag-active-index')){$('rag-scope').value=ragScope;$('rag-active-index').textContent=`RAG: ${ragScope==='ALL_DOCUMENTS'?'Все документы':'Выбранный файл'} · ${$('rag-strategy').selectedOptions[0].textContent} · Candidates ${$('rag-candidates').value} / Contexts ${$('rag-top-k').value} · ChunkingRun: ${$('rag-strategy').selectedOptions[0].dataset.indexId||'Индекс не построен'}`;}}
async function ragFragment(url,target){if($(target))await htmx.ajax('GET',url,{target:'#'+target,swap:'innerHTML'});}
async function ragPoll(id,batch=false){
  const version=batch?++ragBatchView:++ragView, panel=$('retrieval');
  const valid=()=>panel===$('retrieval')&&version===(batch?ragBatchView:ragView);
  const kind=batch?'batches':'comparisons';
  try{while(valid()){
    const record=await api(`/api/rag/${kind}/${id}`);if(!valid())return;
    await ragFragment(`/ui/rag/${kind}/${id}`,batch?'rag-batch-results':'rag-result');
    if(!['QUEUED','RUNNING'].includes(record.status)){await ragFragment(`/ui/runs/${currentRun}/rag/history`,'rag-history');return;}
    await new Promise(resolve=>setTimeout(resolve,1200));
  }}catch(error){if(valid())notice(error.message);}
}
async function ragCompare(question_id=null){
  const data={...ragSettings(),question:$('rag-question').value,question_id};
  const result=await api(`/api/runs/${currentRun}/rag/compare`,ragJSON(data));
  ragPoll(result.comparison_run_id);
}
async function ragEditor(id=null){
  const q=id?(await api('/api/rag/questions')).find(q=>q.question_id===id):{question:$('rag-question').value,expected_answer:'',expected_sources:[]};
  if(!q)return;
  const form=$('rag-edit-form');form.elements.question_id.value=id||'';form.elements.question.value=q.question;
  form.elements.expected_answer.value=q.expected_answer;form.elements.expected_sources.value=JSON.stringify(q.expected_sources,null,2);
  $('rag-editor-error').textContent='';$('rag-question-editor').showModal();
}
document.addEventListener('htmx:afterSwap',event=>{if(event.detail.target.id==='workspace'){ragView++;ragBatchView++;ragIndexLabel();}});
document.addEventListener('change',event=>{if(event.target.id==='rag-scope')ragScope=event.target.value;if(['rag-strategy','rag-scope','rag-top-k','rag-candidates','rag-context-budget'].includes(event.target.id))ragIndexLabel();});
document.addEventListener('htmx:configRequest',event=>{if(event.detail.elt.matches('.search-form')){event.detail.parameters.rag_scope=ragScope;event.detail.parameters.selected_document_id=selectedDocument;}});
document.addEventListener('submit',async event=>{
  if(!['rag-form','rag-edit-form'].includes(event.target.id))return;
  event.preventDefault();const button=event.target.querySelector('[type=submit]');button.disabled=true;
  try{
    if(event.target.id==='rag-form')await ragCompare();
    else{
      const fields=event.target.elements,id=fields.question_id.value;
      const data={question:fields.question.value,expected_answer:fields.expected_answer.value,expected_sources:JSON.parse(fields.expected_sources.value||'[]')};
      await api('/api/rag/questions'+(id?'/'+id:''),{...ragJSON(data),method:id?'PUT':'POST'});
      $('rag-question-editor').close();await ragFragment('/ui/rag/questions','rag-question-list');
    }
  }catch(error){if(event.target.id==='rag-edit-form')$('rag-editor-error').textContent=error.message;else notice(error.message);}
  finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  try{
    if(b.id==='rag-add-question')await ragEditor();
    else if(b.id==='rag-editor-close')$('rag-question-editor').close();
    else if(b.dataset.ragEdit)await ragEditor(b.dataset.ragEdit);
    else if(b.dataset.ragDelete){await api('/api/rag/questions/'+b.dataset.ragDelete,{method:'DELETE'});await ragFragment('/ui/rag/questions','rag-question-list');}
    else if(b.dataset.ragQuestion){const q=(await api('/api/rag/questions')).find(q=>q.question_id===b.dataset.ragQuestion);if(q){$('rag-question').value=q.question;await ragCompare(q.question_id);}}
    else if(b.dataset.ragResult)ragPoll(b.dataset.ragResult);
    else if(b.dataset.ragBatch)ragPoll(b.dataset.ragBatch,true);
    else if(b.id==='rag-run-all'){b.disabled=true;try{const r=await api(`/api/runs/${currentRun}/rag/batch`,ragJSON(ragSettings()));ragPoll(r.batch_id,true);}finally{b.disabled=false;}}
  }catch(error){notice(error.message);}
});
