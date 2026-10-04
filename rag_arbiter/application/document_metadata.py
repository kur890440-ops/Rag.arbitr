"""Idempotent metadata enrichment of saved normalized text. No OCR or embeddings."""
import re
from datetime import date
from ..documents import digest

VERSION='day25-metadata-1'
MONTHS='января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()
DATE_PATTERN=r'(\d{1,2})\s+(%s)\s+(20\d{2}|19\d{2})' % '|'.join(MONTHS)


def classify_document(doc,corpus_id):
    blocks=sorted(doc.get('blocks',[]),key=lambda b:(b['page_number'],b.get('reading_order',0)))
    text='\n'.join(b['text'] for b in blocks if b['page_number']<=2)
    fingerprint=digest([VERSION,doc.get('file_name'),text])
    old=doc.get('retrieval_metadata',{})
    if old.get('fingerprint')==fingerprint:return {**old,'corpus_id':corpus_id}
    if old.get('classification_source')=='TRUSTED_MANUAL':return {**old,'corpus_id':corpus_id}
    meta=dict(document_id=doc['document_id'],corpus_id=corpus_id,document_type='UNKNOWN',
        document_date=None,document_year=None,classification_status='UNKNOWN',confidence=0.0,
        classification_source='NONE',evidence='',date_source=None,date_evidence=None,fingerprint=fingerprint)
    # Only headings, never mentions of complaints/decisions within a court ruling.
    rules=[('COURT_RULING',r'^(?:РЕЗОЛЮТИВНАЯ ЧАСТЬ ОПРЕДЕЛЕНИЯ|ОПРЕДЕЛЕНИЕ)(?:\s|$)'),
           ('COURT_DECISION',r'^(?:ПОСТАНОВЛЕНИЕ|РЕШЕНИЕ)(?:\s|$)'),
           ('OBJECTION',r'^Возражения на жалобу'),('COMPLAINT',r'^(?:ЖАЛОБА(?:\s|$)|Заявление\s+о признании незаконным[^\n]{0,100}арбитражного управляющего)'),
           ('MOTION',r'^ХОДАТАЙСТВО(?:\s|$)'),('APPLICATION',r'^ЗАЯВЛЕНИЕ(?:\s|$)'),
           ('PROTOCOL',r'^Протокол(?:\s|$)'),('REPORT',r'^ОТЧЕТ(?:\s|$)'),
           ('EXPERT_CONCLUSION',r'^Заключение\s*$')]
    matches=[]
    for kind,pattern in rules:
        match=re.search(pattern,text[:6500],re.M|re.I)
        if match:
            if kind.startswith('COURT_') and ('СУД' not in text[:match.start()].upper() or match.start()>900):continue
            matches.append((match.start(),kind,match.group(0)))
    if matches:
        _,kind,evidence=min(matches,key=lambda x:x[0])
        meta.update(document_type=kind,classification_status='CONFIRMED',confidence=0.95,
                    classification_source='NORMALIZED_HEADING',evidence=evidence.strip())
    if 'РОСРЕЕСТР' in text[:250] and 'постановления о прекращении' in text[:1000].lower():
        meta.update(document_type='ADMINISTRATIVE_ORDER',classification_status='CONFIRMED',confidence=0.95,
            classification_source='NORMALIZED_HEADER',evidence='РОСРЕЕСТР: копия постановления о прекращении производства')
    elif not matches:
        name=doc.get('file_name','').lower()
        for kind,signal in [('COMPLAINT',r'жалоб|zhalob'),('APPLICATION',r'з[ая]вление'),('MOTION',r'ходатайств'),('COURT_RULING',r'определени|opredelenie')]:
            if re.search(signal,name):
                meta.update(document_type=kind,classification_status='UNCERTAIN',confidence=0.5,
                            classification_source='FILENAME',evidence=doc['file_name']);break
    # Dates must be issuance/header dates. Never extract a referenced case/law date or filename deadline.
    head=text[:1800]
    candidates=[]
    for line in head.splitlines():
        numeric=re.fullmatch(r'\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2}|19\d{2})\s*(?:г\.)?\s*',line)
        if numeric:
            try:candidates.append((date(int(numeric[3]),int(numeric[2]),int(numeric[1])).isoformat(),line))
            except ValueError:pass
        anchored=re.match(r'^\s*(?:Исх\.|Дата изготовления определения|Дата:|от\s+|\d{1,2}\s+(?:'+ '|'.join(MONTHS)+r'))',line,re.I)
        if not anchored:continue
        match=re.search(DATE_PATTERN,line,re.I)
        if match:
            try:candidates.append((date(int(match[3]),MONTHS.index(match[2].lower())+1,int(match[1])).isoformat(),line))
            except ValueError:pass
    # Full-text date outranks announced dispositive date; explicit outgoing/header labels outrank bare dates.
    priority=[x for x in candidates if re.match(r'^(Исх\.|Дата)',x[1],re.I)]
    candidates=priority or candidates
    if candidates and len({x[0] for x in candidates})==1:
        value,evidence=candidates[0]
        meta.update(document_date=value,document_year=int(value[:4]),date_source='NORMALIZED_HEADER',date_evidence=evidence)
    return meta


def enrich_documents(store,document_ids,corpus_id):
    result={}
    for document_id in document_ids:
        doc=store.get('documents',document_id)
        if not doc:raise ValueError('Missing canonical document')
        metadata=classify_document(doc,corpus_id)
        if metadata!=doc.get('retrieval_metadata'):
            store.put('documents',document_id,{**doc,'retrieval_metadata':metadata})
        result[document_id]=metadata
    return result


def sync_payload(vectors,index,metadata):
    from .rechunk import partitions
    from qdrant_client import models
    for part in partitions(index):
        for doc in part['document_ids']:
            if doc not in metadata:continue
            m=metadata[doc]
            vectors.client.set_payload(part['collection'],payload={k:m.get(k) for k in
                ('document_type','document_date','document_year','classification_status','classification_source','corpus_id')},
                points=models.Filter(must=[models.FieldCondition(key='document_id',match=models.MatchValue(value=doc))]),wait=True)
