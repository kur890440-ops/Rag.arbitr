/* Direct lab state is independent from the corpus and Search controls. */
let optimizationLoading=false, directSelected=[], directBaseline='', directTimer=null;
let directRevision=0, directRenderQueue=Promise.resolve();
try {directBaseline=localStorage.getItem('day29-direct-baseline')||'';} catch (_) {}
window.loadOptimization=async function(force=false){
  const target=document.getElementById('optimization-content');
  if(!target||optimizationLoading||(!force&&target.childElementCount))return;
  optimizationLoading=true;
  try{await htmx.ajax('GET','/ui/llm-optimization',{target:'#optimization-content'});}
  finally{optimizationLoading=false;}
};
function optimizationFromHash(){if(['#local-optimization','#llm-optimization'].includes(location.hash))setTab('llm-optimization');}
window.addEventListener('hashchange',optimizationFromHash);
optimizationFromHash();
async function experimentJSON(url,body){
  const response=await fetch(url,{method:body?'POST':'GET',headers:{'X-RAG-Request':'1','Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  const result=await response.json();
  if(!response.ok){const detail=result.detail;throw new Error(Array.isArray(detail)?detail.map(x=>x.loc.slice(1).join('.')+': '+x.msg).join('; '):detail||'Ошибка запроса');}
  return result;
}
function experimentError(error){const el=document.getElementById('experiment-error');if(el)el.textContent=error.message;}
function renderDirect(){
  clearTimeout(directTimer);
  const revision=++directRevision;
  directRenderQueue=directRenderQueue.catch(()=>{}).then(async()=>{
    if(revision!==directRevision)return;
    const query=new URLSearchParams({ids:directSelected.join(','),baseline:directBaseline});
    await htmx.ajax('GET','/ui/local-llm-experiments?'+query,{target:'#direct-columns'});
    if(revision!==directRevision)return;
    if(document.querySelector('#direct-columns [data-finished="false"]')){
      directTimer=setTimeout(()=>renderDirect().catch(experimentError),1000);
    }
  });
  return directRenderQueue;
}
async function selectDirect(mode){
  if(mode==='clear'){directSelected=[];return renderDirect();}
  const rows=await experimentJSON('/api/local-llm-experiments?task=judicial_act');
  if(directBaseline&&!rows.some(r=>r.id===directBaseline))directBaseline='';
  directSelected=(mode==='all'?rows:rows.slice(0,3)).reverse().map(r=>r.id);
  return renderDirect();
}
document.addEventListener('submit',async event=>{
  if(event.target.matches('[data-quality-review]')){
    event.preventDefault();const form=event.target, scores={};
    for(const select of form.querySelectorAll('select'))scores[select.name]=select.value;
    const status=form.querySelector('[data-review-status]');
    try{await experimentJSON('/api/local-llm-experiments/'+form.dataset.qualityReview+'/review',{scores,note:form.elements.note.value});status.textContent='Сохранено';}
    catch(error){status.textContent=error.message;}return;
  }
  if(event.target.id!=='manual-experiment-form')return;
  event.preventDefault();const form=event.target, fields=form.elements;
  const options={model:fields.model.value,prompt_version:fields.prompt_version.value};
  for(const key of ['temperature','context_window','max_output_tokens','seed'])options[key]=Number(fields[key].value);
  const button=form.querySelector('[type=submit]');button.disabled=true;
  document.getElementById('experiment-error').textContent='';
  try{
    const result=await experimentJSON('/api/local-llm-experiments',{document_version:fields.document_version.value,document_sha256:fields.document_sha256.value,options});
    directSelected.push(result.id);
    await renderDirect();
    document.querySelector('#direct-columns [data-direct-run="'+result.id+'"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
  }catch(error){experimentError(error);}finally{button.disabled=!fields.document_sha256.value;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
    if(button.dataset.directSelection)await selectDirect(button.dataset.directSelection);
    if(button.dataset.directBaseline){
      directBaseline=button.dataset.directBaseline;
      try{localStorage.setItem('day29-direct-baseline',directBaseline);}catch(_){}
      await renderDirect();
    }
  }catch(error){experimentError(error);}
});
document.addEventListener('htmx:afterSwap',event=>{
  if(event.detail.target.id==='optimization-content')selectDirect('recent').catch(experimentError);
});

let documentRevision=0;
document.addEventListener('change',async event=>{
  if(event.target.name!=='document_version')return;
  const revision=++documentRevision,form=event.target.form;
  form.elements.document_sha256.value='';form.querySelector('[type=submit]').disabled=true;
  document.getElementById('judicial-document-text').textContent='';
  document.getElementById('judicial-document-info').textContent='';
  if(!event.target.value)return;
  try{
    const doc=await experimentJSON('/api/local-llm-documents/'+encodeURIComponent(event.target.value));
    if(revision!==documentRevision)return;
    form.elements.document_sha256.value=doc.sha256;
    document.getElementById('judicial-document-text').textContent=doc.text;
    document.getElementById('judicial-document-info').textContent=doc.page_count+' стр. · '+doc.size_bytes+' UTF-8 bytes · SHA-256: '+doc.sha256;
    form.querySelector('[type=submit]').disabled=false;
  }catch(error){if(revision===documentRevision)experimentError(error);}
});
