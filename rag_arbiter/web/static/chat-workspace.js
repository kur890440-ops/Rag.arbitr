document.addEventListener('DOMContentLoaded',()=>{htmx.config.allowEval=false;htmx.config.allowScriptTags=false;});
document.addEventListener('htmx:configRequest',e=>{e.detail.headers['X-RAG-Request']='1';});
document.addEventListener('htmx:beforeRequest',e=>{if(e.target.id==='workspace-events'&&e.target.contains(document.activeElement))e.preventDefault();});
