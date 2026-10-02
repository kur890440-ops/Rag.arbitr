from rag_arbiter.application.context_selection import CandidateProcessor, ContextSelector, near_duplicate
from rag_arbiter.application.rag import RAGContextBuilder
from rag_arbiter.documents import digest


class Store:
    def __init__(self,chunks,blocks=()):
        self.rows={'chunks':{c['chunk_id']:c for c in chunks},'document_blocks':{b['block_id']:b for b in blocks}}
    def get(self,table,key):return self.rows[table].get(key)


def chunk(key,text,doc='a',section='s',i=0,blocks=None,start=0):
    return dict(chunk_id=key,text=text,document_id=doc,section=section,chunk_index=i,
        source_block_ids=blocks or [],page_start=1,page_end=1,char_start=start,char_end=start+len(text),
        content_hash=digest(text),file_name=doc+'.pdf')


def processor(chunks,strategy='fixed',blocks=(),budget=6000):
    return CandidateProcessor(Store(chunks,blocks),dict(collection='test',run_id='active',
        document_ids=sorted({c['document_id'] for c in chunks}),chunk_ids=[c['chunk_id'] for c in chunks]),strategy,budget)


def hits(chunks):return [dict(**c,score=1-n*.01,rank=n+1) for n,c in enumerate(chunks)]


def test_exact_near_duplicates_and_threshold():
    text=' '.join('word'+str(i) for i in range(50))
    a=chunk('a',text);b=chunk('b',text);c=chunk('c',text+' extra')
    d=chunk('d','Different useful facts')
    p=processor([a,b,c,d]);raw=hits([a,a,b,c,d])+hits([chunk('inactive','old')])
    kept,diag=p.dedup(raw)
    assert [h['chunk_id'] for h in kept]==['a','d']
    assert {d['reason'] for d in diag}=={'duplicate_chunk_id','duplicate_content_hash','near_identical_overlap','inactive'}
    assert len(p.dedup(raw,minimum_score=.99)[0])==1
    assert not near_duplicate(a,chunk('partial',' '.join(text.split()[20:])+' new facts'))


def test_structure_parent_expansion_not_whole_document_and_budget():
    blocks=[dict(block_id=str(i),text=text,section=section,page_number=1,reading_order=i)
            for i,(text,section) in enumerate([('Heading','s'),('Useful answer','s'),('Unrelated','other')])]
    chunks=[chunk(str(i),b['text'],section=b['section'],i=i,blocks=[str(i)]) for i,b in enumerate(blocks)]
    built,stats=processor(chunks,'structure',blocks).build(hits(chunks[:2]))
    assert len(built)==1 and stats['candidates_after_dedup']==2
    assert built[0].context_text=='Heading\nUseful answer'
    assert built[0].anchor_chunk_ids==['0','1'] and built[0].expansion_type=='parent_section'
    assert built[0].original_ranks==[1,2] and built[0].chunking_run_ids==['active']
    assert processor(chunks,'structure',blocks,budget=3).build(hits(chunks[:1]))[0][0].expansion_type=='anchor'
    assert processor(chunks[:2],'structure',blocks[:2]).build(hits(chunks[:1]))[0][0].expansion_type=='anchor'


def test_fixed_neighbors_require_contiguity_and_relevance():
    chunks=[chunk(str(i),text,i=i,blocks=['shared'],start=i*10) for i,text in enumerate(['0123456789','abcdefghij','klmnopqrst','uvwxyz0123'])]
    built,_=processor(chunks).build(hits(chunks[1:2]))
    assert built[0].expanded_chunk_ids==['0','1','2']
    assert built[0].context_text=='0123456789\nabcdefghij\nklmnopqrst'
    chunks[0]['char_start']=100;chunks[0]['char_end']=110
    chunks[2]['section']='unrelated'
    assert processor(chunks).build(hits(chunks[1:2]))[0][0].expansion_type=='anchor'


def test_soft_diversity_multiple_contexts_per_doc_and_budget_fallback():
    chunks=[chunk('a','First fact',section='one'),chunk('b','Second fact',section='two'),chunk('c','Third fact',doc='b')]
    candidates,_=processor(chunks,'structure').build(hits(chunks))
    builder=RAGContextBuilder();selector=ContextSelector()
    selected=selector.select(candidates,2,5000,builder)
    assert [c.document_id for c in selected]==['a','b']
    assert len(selector.select(candidates,3,5000,builder))==3
    large=candidates[0].model_copy(update={'context_text':'x'*10000,'expansion_type':'parent_section'})
    fallback=selector.select([large],5,500,builder)
    assert len(fallback)==1 and fallback[0].expansion_type=='anchor_budget_fallback'
    assert builder.build(fallback,500)['token_count']<=500
    assert not selector.select(candidates,5,1,builder)
    text=builder.build(selected,5000)['text']
    assert 'anchor_chunk_ids' not in text and 'retrieval_score' not in text
