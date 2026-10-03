"""Read-only exhaustive text inventory for question discovery (no model calls)."""
import json
import re
import sys
from pathlib import Path

data=json.loads(Path('data/evaluation/day24_discovery_snapshot.json').read_text(encoding='utf-8'))
terms=re.compile(r'прошу|просим|определил|постановил|решил|вывод|прекрат|взыскать|удовлетворить|отказать',re.I)
for di,d in enumerate(data['documents']):
    if len(sys.argv)>1 and str(di) not in sys.argv[1:]:continue
    print('\nDOCUMENT',di,d['file_name'])
    for i,b in enumerate(d['blocks']):
        if terms.search(b['text']):print(i,'p'+str(b['page_number']),b['text'][:1800])
