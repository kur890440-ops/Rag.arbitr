"""Deterministic candidate processing over version-pinned active chunks."""
import re
from pydantic import BaseModel, Field
from ..documents import digest
from .rechunk import partitions


def words(text):return re.findall(r'\w+',text.casefold())


def near_duplicate(a,b):
    if a['document_id']!=b['document_id']:return False
    x,y=words(a['text']),words(b['text'])
    if not x or not y:return a['text']==b['text']
    if min(len(x),len(y))<10:return x==y
    xs={tuple(x[i:i+5]) for i in range(len(x)-4)};ys={tuple(y[i:i+5]) for i in range(len(y)-4)}
    # Near-total containment only: ordinary partial overlap must keep new facts.
    return len(xs&ys)/max(1,min(len(xs),len(ys)))>=0.90 and min(len(x),len(y))/max(len(x),len(y))>=0.8


class ContextCandidate(BaseModel):
    source_id: str
    document_id: str
    file_title: str
    section: str
    page_start: int
    page_end: int
    anchor_chunk_ids: list[str]
    retrieval_score: float
    rerank_score: float | None = None
    rerank_rank: int | None = None
    context_text: str
    context_tokens: int
    expansion_type: str
    content_hashes: list[str]
    original_ranks: list[int]
    expanded_chunk_ids: list[str] = Field(default_factory=list)
    source_pages: list[dict] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    chunking_run_ids: list[str] = Field(default_factory=list)
    anchor: dict = Field(default_factory=dict)

    def as_hit(self):
        return dict(source_id=self.source_id,document_id=self.document_id,file_name=self.file_title,title=self.file_title,
            rerank_score=self.rerank_score,rerank_rank=self.rerank_rank,section=self.section,page_start=self.page_start,page_end=self.page_end,chunk_id=self.anchor_chunk_ids[0],
            anchor_chunk_ids=self.anchor_chunk_ids,expanded_chunk_ids=self.expanded_chunk_ids,expansion_type=self.expansion_type,
            original_ranks=self.original_ranks,rank=min(self.original_ranks),score=self.retrieval_score,text=self.context_text,
            content_hash=digest(self.context_text),content_hashes=self.content_hashes,source_pages=self.source_pages,
            source_block_ids=self.source_block_ids,chunking_run_ids=self.chunking_run_ids,
            chunking_run_id=self.anchor.get('chunking_run_id'),source=self.anchor.get('source','normalized chunks'))

    def fallback(self):
        a=self.anchor
        return self.model_copy(update=dict(source_id=digest([a['document_id'],a['chunk_id'],a['text']]),context_text=a['text'],
            context_tokens=len(a['text'].encode()),expansion_type='anchor_budget_fallback',page_start=a['page_start'],page_end=a['page_end'],
            anchor_chunk_ids=[a['chunk_id']],expanded_chunk_ids=[a['chunk_id']],source_pages=a.get('source_pages',[]),
            source_block_ids=a.get('source_block_ids',[]),content_hashes=[a.get('content_hash') or digest(a['text'])]))


class CandidateProcessor:
    def __init__(self,store,index,strategy,expansion_budget):
        self.store,self.index,self.strategy,self.expansion_budget=store,index,strategy,expansion_budget
        self.active={};self.versions={}
        for part in partitions(index):
            for key in part.get('chunk_ids',[]):
                chunk=store.get('chunks',key)
                if chunk and chunk['document_id'] in part['document_ids']:
                    self.active[key]=chunk;self.versions[key]=part['run_id']

    def dedup(self,hits,minimum_score=None):
        kept=[];ids=set();hashes=set();diagnostics=[]
        for hit in sorted(hits,key=lambda h:(-h['score'],h['rank'],h['chunk_id'])):
            key=hit['chunk_id'];chash=hit.get('content_hash') or digest(hit['text'])
            reason=None
            if key not in self.active:reason='inactive'
            elif hit['document_id']!=self.active[key]['document_id']:reason='wrong_document'
            elif self.index.get('corpus_id') and hit.get('corpus_id',self.index['corpus_id'])!=self.index['corpus_id']:reason='wrong_corpus'
            elif minimum_score is not None and hit['score']<minimum_score:reason='below_threshold'
            elif key in ids:reason='duplicate_chunk_id'
            elif chash in hashes:reason='duplicate_content_hash'
            elif any(near_duplicate(hit,k) for k in kept):reason='near_identical_overlap'
            if reason:diagnostics.append(dict(chunk_id=key,rank=hit['rank'],reason=reason));continue
            ids.add(key);hashes.add(chash);kept.append(hit)
        return kept,diagnostics

    def make(self,anchor,chunks,text,kind,blocks=None):
        pages=[];block_ids=[]
        for chunk in chunks:
            for bid in chunk.get('source_block_ids',[]):
                if bid not in block_ids:block_ids.append(bid)
            if chunk.get('recognition_ids'):
                trace=self.store.trace_chunk(chunk['chunk_id'])
                pages.extend(dict(recognition_id=s['page']['recognition_id'],page_number=s['page']['page_number']) for s in trace['sources'])
        if blocks:
            block_ids=[b['block_id'] for b in blocks]
            pages=[dict(recognition_id=b['recognition_id'],page_number=b['page_number']) for b in blocks if b.get('recognition_id')]
        unique={ (p['page_number'],p['recognition_id']):p for p in pages}
        starts=[b['page_number'] for b in blocks] if blocks else [c['page_start'] for c in chunks]
        ends=[b['page_number'] for b in blocks] if blocks else [c['page_end'] for c in chunks]
        return ContextCandidate(source_id=digest([anchor['document_id'],text]),document_id=anchor['document_id'],file_title=anchor.get('file_name') or anchor.get('title',''),
            section=anchor.get('section',''),page_start=min(starts),page_end=max(ends),anchor_chunk_ids=[anchor['chunk_id']],
            retrieval_score=anchor['score'],rerank_score=anchor.get('rerank_score'),rerank_rank=anchor.get('rerank_rank'),context_text=text,context_tokens=len(text.encode()),expansion_type=kind,
            content_hashes=sorted({c.get('content_hash') or digest(c['text']) for c in chunks}),original_ranks=[anchor['rank']],
            expanded_chunk_ids=[c['chunk_id'] for c in chunks],source_pages=[unique[k] for k in sorted(unique)],source_block_ids=block_ids,
            chunking_run_ids=sorted({self.versions[c['chunk_id']] for c in chunks}),anchor=anchor)

    @staticmethod
    def join(chunks):
        chunks=sorted(chunks,key=lambda c:(c.get('chunk_index',0),c['chunk_id']))
        text=chunks[0]['text']
        for chunk in chunks[1:]:
            other=chunk['text'];limit=min(len(text),len(other));overlap=0
            for n in range(limit,0,-1):
                if text.endswith(other[:n]):overlap=n;break
            text+=other[overlap:] if overlap else '\n'+other
        return text

    def build(self,hits,minimum_score=None,precleaned=False):
        deduped,diagnostics=(hits,[]) if precleaned else self.dedup(hits,minimum_score)
        groups={}
        for hit in deduped:groups.setdefault((hit['document_id'],hit.get('section','')),[]).append(hit)
        candidates=[];retrieved={h['chunk_id'] for h in deduped};expansion_exclusions=[]
        for group in groups.values():
            for hit in group:
                anchor={**self.active[hit['chunk_id']],**hit};chunk=self.active[hit['chunk_id']]
                same=[c for c in self.active.values() if c['document_id']==hit['document_id'] and self.versions[c['chunk_id']]==self.versions[hit['chunk_id']]]
                result=self.make(anchor,[chunk],chunk['text'],'anchor')
                if self.strategy=='structure' and hit.get('section'):
                    # Resolve only source blocks of this active partition; do not
                    # consult latest mutable document/chunk versions.
                    all_ids={bid for c in same for bid in c.get('source_block_ids',[])}
                    all_blocks=[self.store.get('document_blocks',bid) for bid in all_ids]
                    all_blocks=sorted([b for b in all_blocks if b],key=lambda b:(b['page_number'],b['reading_order'],b['block_id']))
                    segments=[]
                    for b in all_blocks:
                        if not segments or segments[-1][0].get('section')!=b.get('section'):segments.append([])
                        segments[-1].append(b)
                    anchored=set(chunk.get('source_block_ids',[]))
                    parent=next((s for s in segments if s[0].get('section')==hit['section'] and anchored&{b['block_id'] for b in s}),None)
                    if parent and len(parent)<len(all_blocks):
                        text='\n'.join(b['text'] for b in parent)
                        if len(text.encode())>self.expansion_budget and anchored<={b['block_id'] for b in parent}:
                            expansion_exclusions.append(dict(anchor_chunk_id=hit['chunk_id'],reason='PARENT_EXPANSION_BUDGET',block_ids=[b['block_id'] for b in parent],text_bytes=len(text.encode()),budget=self.expansion_budget))
                        if len(text.encode())<=self.expansion_budget and anchored<={b['block_id'] for b in parent}:
                            members=[c for c in same if c.get('section')==hit['section'] and set(c.get('source_block_ids',[]))&{b['block_id'] for b in parent}]
                            result=self.make(anchor,members,text,'parent_section',parent)
                elif self.strategy=='fixed':
                    members=[chunk]
                    for neighbor in sorted(same,key=lambda c:c.get('chunk_index',0)):
                        if abs(neighbor.get('chunk_index',0)-chunk.get('chunk_index',0))!=1:continue
                        section_ok=bool(chunk.get('section')) and neighbor.get('section')==chunk.get('section')
                        useful=neighbor['chunk_id'] in retrieved or (section_ok and bool(set(chunk.get('source_block_ids',[]))&set(neighbor.get('source_block_ids',[]))))
                        gap=max(chunk.get('char_start',0),neighbor.get('char_start',0))-min(chunk.get('char_end',0),neighbor.get('char_end',0))
                        if not useful or gap>2:continue
                        trial=members+[neighbor];text=self.join(trial)
                        if len(trial)<len(same) and len(text.encode())>self.expansion_budget:
                            expansion_exclusions.append(dict(anchor_chunk_id=hit['chunk_id'],excluded_chunk_id=neighbor['chunk_id'],reason='NEIGHBOR_EXPANSION_BUDGET',text_bytes=len(text.encode()),budget=self.expansion_budget))
                        if len(trial)<len(same) and len(text.encode())<=self.expansion_budget:members=trial
                    if len(members)>1:result=self.make(anchor,sorted(members,key=lambda c:c['chunk_index']),self.join(members),'fixed_neighbors')
                # Multiple anchors may resolve to one logical context.
                old=next((c for c in candidates if c.source_id==result.source_id),None)
                if old:
                    old.anchor_chunk_ids=sorted(set(old.anchor_chunk_ids+result.anchor_chunk_ids))
                    old.original_ranks=sorted(set(old.original_ranks+result.original_ranks))
                else:candidates.append(result)
        return candidates,dict(expansion_budget_exclusions=expansion_exclusions,candidates_after_dedup=len(deduped),dedup_diagnostics=diagnostics,groups=len(groups),contexts_built=len(candidates))


class ContextSelector:
    def select(self,candidates,max_sources,budget,builder,diversity_penalty=0.04):
        selected=[];remaining=list(candidates);docs={};sections={}
        self.diagnostics=[]
        def record(candidate,reason,**extra):
            self.diagnostics.append(dict(source_id=candidate.source_id,context_ids=list(candidate.anchor_chunk_ids),
                expanded_chunk_ids=list(candidate.expanded_chunk_ids),reason=reason,
                text_bytes=len(candidate.context_text.encode('utf-8')),**extra))
        def excluded_reason(candidate):
            excluded=getattr(builder,'diagnostics',{}).get('budget_excluded',[])
            return 'BUDGET' if any(e['chunk_id'] in candidate.anchor_chunk_ids for e in excluded) else 'DUPLICATE'

        while remaining and len(selected)<max_sources:
            remaining.sort(key=lambda c:(-((c.rerank_score if c.rerank_score is not None else c.retrieval_score)-diversity_penalty*docs.get(c.document_id,0)-diversity_penalty/2*sections.get((c.document_id,c.section),0)),min(c.original_ranks),c.source_id))
            candidate=remaining.pop(0)
            if any(near_duplicate(candidate.as_hit(),s.as_hit()) for s in selected):
                record(candidate,'NEAR_DUPLICATE');continue
            trial=builder.build(selected+[candidate],budget)
            if trial['used_count']!=len(selected)+1:
                original=candidate
                reason=excluded_reason(candidate)
                if candidate.expansion_type=='anchor':
                    record(candidate,reason);continue
                candidate=candidate.fallback();trial=builder.build(selected+[candidate],budget)
                if trial['used_count']!=len(selected)+1:
                    record(original,excluded_reason(candidate));continue
                if any(near_duplicate(candidate.as_hit(),s.as_hit()) for s in selected):
                    record(original,'NEAR_DUPLICATE');continue
                record(original,reason+'_FALLBACK',retained_context_ids=list(candidate.anchor_chunk_ids),
                       retained_bytes=len(candidate.context_text.encode('utf-8')))

            selected.append(candidate);docs[candidate.document_id]=docs.get(candidate.document_id,0)+1
            group=(candidate.document_id,candidate.section);sections[group]=sections.get(group,0)+1
        for candidate in remaining:record(candidate,'MAX_CONTEXTS')
        return selected
