"""Safe exception location traces: no locals, cookies, payloads or exception text."""
import traceback,uuid
from pathlib import Path

def capture(runs,exc,correlation_id=None,route='',context=None):
    context=context or {};name=type(exc).__name__
    category='INTERNAL_ERROR'
    retrieval=context.get('retrieval') or {}
    if retrieval.get('result_status')=='RETRIEVAL_ERROR':category='RETRIEVAL_ERROR'
    elif context.get('limit_error')=='TIMEOUT' or 'Timeout' in name:category='TIMEOUT'
    elif context.get('provider_error'):category='PROVIDER_ERROR'
    elif getattr(exc,'status_code',0) in (401,403):category='AUTH_ERROR'
    elif name=='KeyError':category='DOCUMENT_UNAVAILABLE'
    elif str(exc)=='INDEX_NOT_READY':category='INDEX_NOT_READY'
    trace=[];current=exc
    while current is not None and len(trace)<5:
        item=dict(type=type(current).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(current.__traceback__)])
        if hasattr(current,'errors'):
            try:item['validation']=[dict(type=e['type'],loc=list(e['loc'])) for e in current.errors(include_input=False,include_url=False)]
            except Exception:pass
        trace.append(item);current=current.__cause__ or current.__context__
    result=dict(correlation_id=correlation_id or uuid.uuid4().hex,route=route,category=category,stage=context.get('stage'),traceback=trace)
    with runs.db() as db:db.event('application_errors',result)
    return result
