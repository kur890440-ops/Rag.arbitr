"""Install the tested exact Day23 model revision; weights are gitignored."""
from pathlib import Path
from huggingface_hub import snapshot_download

MODEL='BAAI/bge-reranker-v2-m3'
REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
DEST=Path('data/models/bge-reranker-v2-m3')

if __name__=='__main__':
    snapshot_download(MODEL,revision=REVISION,local_dir=str(DEST),max_workers=2,
        allow_patterns=['config.json','model.safetensors','tokenizer*','special_tokens_map.json','sentencepiece.bpe.model'])
    (DEST/'revision.txt').write_text(REVISION,encoding='utf-8')
    print('Installed',MODEL,REVISION)
