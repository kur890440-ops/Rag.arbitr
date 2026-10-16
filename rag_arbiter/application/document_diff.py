"""Deterministic structural diff. Model interpretations never manufacture changes."""
import re
from collections import Counter
from difflib import SequenceMatcher
from ..documents import digest


def normalized(text):
    text=re.sub(r'^\s*(?:\d+(?:\.\d+)*[.)]|[а-яa-z][.)])\s*(?=[a-zа-яё])', '', text,flags=re.I)
    return ' '.join(text.split()).casefold()


def similarity(a,b):
    x,y=normalized(a),normalized(b)
    wx,wy={w[:5] for w in re.findall(r'\w+',x)},{w[:5] for w in re.findall(r'\w+',y)}
    overlap=2*len(wx&wy)/max(1,len(wx)+len(wy))
    return .65*overlap+.35*SequenceMatcher(None,x,y,autojunk=False).ratio()


def table_grid(block):
    data=block.get('table_data') or {}
    if data.get('table_cells'):
        grid=[['']*data['num_cols'] for _ in range(data['num_rows'])]
        for c in data['table_cells']:
            for r in range(c['start_row_offset_idx'],c['end_row_offset_idx']):
                for col in range(c['start_col_offset_idx'],c['end_col_offset_idx']):grid[r][col]=c['text']
        return grid
    if data.get('rows'):return data['rows']
    lines=[l.strip() for l in block['text'].splitlines() if l.strip().startswith('|')]
    return [[c.strip() for c in l.strip('|').split('|')] for l in lines if not re.fullmatch(r'[|:\s-]+',l)]


def cells_diff(a,b):
    old,new=table_grid(a),table_grid(b)
    if (a.get('table_data') or {}).get('uncertain') or (b.get('table_data') or {}).get('uncertain'):return [],'UNCERTAIN'
    if not old or not new:return [],'UNCERTAIN'
    def keyed(grid):
        headers=grid[0];keys=[row[0] for row in grid[1:] if row]
        if len(set(headers))!=len(headers) or len(set(keys))!=len(keys):return None
        return {(row[0],headers[i]):value for row in grid[1:] for i,value in enumerate(row) if i<len(headers)}
    left,right=keyed(old),keyed(new)
    if left is None or right is None:return [],'UNCERTAIN'
    changes=[]
    for row,col in sorted(left.keys()|right.keys()):
        x,y=left.get((row,col)),right.get((row,col))
        if x!=y:changes.append(dict(row=row,column=col,old_value=x,new_value=y,change_type='ADDED' if x is None else 'REMOVED' if y is None else 'MODIFIED'))
    return changes,'CONFIRMED'


def structured_diff(logical_id,old_version,new_version,a,b):
    left=sorted(a['blocks'],key=lambda x:x['reading_order']);right=sorted(b['blocks'],key=lambda x:x['reading_order'])
    pairs={};uncertain=set();used=set()
    def key(x):return ('table' if x['block_type']=='table' else 'text',normalized(x['text']))
    ca,cb=Counter(map(key,left)),Counter(map(key,right))
    for i,x in enumerate(left):
        matches=[j for j,y in enumerate(right) if key(x)==key(y)]
        if ca[key(x)]==cb[key(x)]==1:pairs[i]=matches[0];used.add(matches[0])
        elif matches:uncertain.add(i)
    # LCS over unique matches identifies true reordering, not shifts after insertion/deletion.
    ordered=sorted(pairs);seq=[pairs[i] for i in ordered];stable=set()
    matches=SequenceMatcher(None,seq,sorted(seq),autojunk=False).get_matching_blocks()
    for m in matches:stable.update(ordered[m.a:m.a+m.size])
    exact=set(pairs)
    for i,x in enumerate(left):
        if i in pairs:continue
        candidates=[]
        for j,y in enumerate(right):
            if j in used or key(x)[0]!=key(y)[0]:continue
            score=similarity(x['text'],y['text'])
            if normalized(x.get('section',''))==normalized(y.get('section','')):score=min(1,score+.03)
            candidates.append((score,j))
        candidates.sort(reverse=True)
        if candidates and candidates[0][0]>=.64 and (len(candidates)==1 or candidates[0][0]-candidates[1][0]>=.12):
            j=candidates[0][1]
            rivals=[similarity(z['text'],right[j]['text']) for k,z in enumerate(left) if k!=i and k not in pairs and key(z)[0]==key(x)[0]]
            if rivals and max(rivals)>=candidates[0][0]-.09:uncertain.add(i);continue
            pairs[i]=j;used.add(j)
        elif candidates and candidates[0][0]>.4:uncertain.add(i)
    changes=[]
    def emit(i,j,kind,status='CONFIRMED'):
        x=left[i] if i is not None else {};y=right[j] if j is not None else {}
        cells=[]
        if kind=='MODIFIED' and x.get('block_type')==y.get('block_type')=='table':cells,status=cells_diff(x,y)
        change=dict(document_id=logical_id,old_version_id=old_version,new_version_id=new_version,
            old_block_id=x.get('block_id'),new_block_id=y.get('block_id'),change_type=kind,
            old_text=x.get('text',''),new_text=y.get('text',''),section=y.get('section') or x.get('section',''),
            status=status,confidence=(similarity(x['text'],y['text']) if kind=='MODIFIED' else 1.0) if status=='CONFIRMED' else 0.0,table_changes=cells)
        change['change_id']=digest(change);changes.append(change)
    for i,x in enumerate(left):
        if i not in pairs:emit(i,None,'REMOVED','UNCERTAIN' if i in uncertain else 'CONFIRMED')
        elif i not in exact:emit(i,pairs[i],'MODIFIED')
        elif i not in stable:emit(i,pairs[i],'MOVED')
    for j,y in enumerate(right):
        if j not in used:
            ambiguous=any(similarity(left[i]['text'],y['text'])>.4 for i in uncertain)
            emit(None,j,'ADDED','UNCERTAIN' if ambiguous else 'CONFIRMED')
    return changes
