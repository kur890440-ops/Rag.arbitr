"""Read canonical, version-pinned normalized blocks; never run recognition/search."""
from ..llm import BASE_SYSTEM, LLMRequest
from .views import ResultsService


def full_document_defaults():
    return dict(full_document_status='NOT_RUN',full_document_id=None,full_document_pages=0,
        full_document_context_tokens=0,full_document_answer='',full_document_duration_ms=0,
        full_document_error_json={},full_document_result=None,full_document_context={},
        generation_call_count=0,minimax_requests_count=0)


class FullDocumentContextBuilder:
    def __init__(self,runs):self.runs=runs

    def build(self,run_id,document_id,budget,question,output_tokens):
        result=dict(text='',document_id=document_id,file_name='',title='',page_count=0,included_pages=[],
            token_count=0,status='NO_DOCUMENT_SELECTED',budget=budget,source_pages=[],
            token_count_method='utf8_bytes_upper_estimate',reserved_output_tokens=output_tokens)
        if not document_id:
            return self.build_corpus(run_id,budget,question,output_tokens)
        views=ResultsService(self.runs)
        entry=next((d for d in views.snapshot(run_id).get('corpus',{}).get('documents',[]) if d.get('document_id')==document_id),None)
        if not entry:return result
        with self.runs.db() as store:
            version=entry.get('recognition_version')
            doc=store.get('normalized_documents',version) if version else None
            doc=doc or store.get('documents',document_id)
        if not doc:
            result['status']='NORMALIZED_DOCUMENT_UNAVAILABLE';return result
        result.update(file_name=doc['file_name'],title=doc.get('title',''),page_count=doc['page_count'],recognition_version=doc.get('cache_identity'))
        pages=sorted(doc['pages'],key=lambda p:p['page_number'])
        if (doc.get('failed_pages') or doc.get('status')!='SUCCESS' or
            [p['page_number'] for p in pages]!=list(range(1,doc['page_count']+1)) or
            any(p['status']=='FAILED' for p in pages)):
            result['status']='NORMALIZED_DOCUMENT_INCOMPLETE';return result
        if version and doc.get('cache_identity')!=version:
            result['status']='NORMALIZED_DOCUMENT_UNAVAILABLE';return result
        known=set(views.recognition_ids(run_id))
        if any(p.get('recognition_id') not in known for p in pages):
            result['status']='NORMALIZED_DOCUMENT_UNAVAILABLE';return result
        blocks=sorted(doc['blocks'],key=lambda b:(b['page_number'],b['reading_order'],b['block_id']))
        if any(b['page_number'] not in range(1,doc['page_count']+1) for b in blocks):
            result['status']='NORMALIZED_DOCUMENT_INCOMPLETE';return result
        text=f"{doc['file_name']}\n"+'\n\n'.join(
            f"[PAGE {p['page_number']}]\n"+'\n'.join(b['text'] for b in blocks if b['page_number']==p['page_number']) for p in pages)
        request=LLMRequest(question=question,context=text,context_type='full_document')
        system_tokens=len(BASE_SYSTEM.encode('utf-8'))
        input_tokens=system_tokens+len(request.user_content().encode('utf-8'))+32
        result.update(token_count=len(text.encode('utf-8')),system_tokens=system_tokens,
            question_tokens=len(question.encode('utf-8')),input_tokens=input_tokens,
            total_reserved_tokens=input_tokens+output_tokens,
            allowed_document_tokens=max(0,budget-output_tokens-(input_tokens-len(text.encode('utf-8')))),
            source_pages=[{'recognition_id':p['recognition_id'],'page_number':p['page_number']} for p in pages])
        if input_tokens+output_tokens>budget:
            result['status']='CONTEXT_TOO_LARGE';return result
        result.update(text=text,included_pages=[p['page_number'] for p in pages],status='READY')
        result.update(scope='DOCUMENT',documents_total=1,documents_included=1,pages_included=len(pages))
        return result

    def build_corpus(self,run_id,budget,question,output_tokens):
        entries=ResultsService(self.runs).snapshot(run_id).get('corpus',{}).get('documents',[])
        entries=sorted(entries,key=lambda d:d['document_id'])
        result=dict(text='',document_id=None,file_name='',title='',scope='CORPUS',
            page_count=0,included_pages=[],source_pages=[],documents=[],documents_total=len(entries),
            documents_included=0,pages_included=0,token_count=0,budget=budget,
            reserved_output_tokens=output_tokens,token_count_method='utf8_bytes_upper_estimate',status='NORMALIZED_DOCUMENT_UNAVAILABLE')
        if not entries:return result
        parts=[]
        for n,entry in enumerate(entries,1):
            # Read and validate every canonical document. No model calls or retrieval.
            doc=self.build(run_id,entry['document_id'],10**15,question,output_tokens)
            if doc['status']!='READY':
                result.update(status=doc['status'],unavailable_document_id=entry['document_id']);return result
            reference=f'D{n}'
            parts.append(f'[DOCUMENT {reference}]\n'+doc['text'])
            result['documents'].append(dict(reference=reference,document_id=entry['document_id'],file_name=doc['file_name'],pages=doc['page_count']))
            result['page_count']+=doc['page_count']
            result['source_pages'].extend({**p,'document_id':entry['document_id'],'reference':reference} for p in doc['source_pages'])
        text='\n\n'.join(parts)
        system_tokens=len(BASE_SYSTEM.encode('utf-8'))
        request=LLMRequest(question=question,context=text,context_type='full_document')
        input_tokens=system_tokens+len(request.user_content().encode('utf-8'))+32
        result.update(token_count=len(text.encode('utf-8')),system_tokens=system_tokens,
            question_tokens=len(question.encode('utf-8')),input_tokens=input_tokens,
            total_reserved_tokens=input_tokens+output_tokens,
            allowed_document_tokens=max(0,budget-output_tokens-input_tokens+len(text.encode('utf-8'))))
        if input_tokens+output_tokens>budget:
            result['status']='CONTEXT_TOO_LARGE';return result
        result.update(status='READY',text=text,documents_included=len(entries),pages_included=result['page_count'],included_pages=result['source_pages'])
        return result
