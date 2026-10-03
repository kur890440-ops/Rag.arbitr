"""Local cross-encoder inference, independent of HTTP and retrieval orchestration."""
from enum import Enum
from threading import RLock
from typing import Literal, Protocol
import math
from pydantic import BaseModel, Field


class RAGPipelineMode(str,Enum):
    BASELINE='BASELINE'
    RERANK='RERANK'
    REWRITE_RERANK='REWRITE_RERANK'


class RerankerConfig(BaseModel):
    model: str = 'BAAI/bge-reranker-v2-m3'
    local_path: str = 'data/models/bge-reranker-v2-m3'
    device: Literal['auto','cuda','cpu'] = 'auto'
    batch_size: int = Field(4,ge=1,le=64)
    max_length: int = Field(512,ge=64,le=8192)
    threshold: float = Field(0.1,ge=0,le=1)


class RerankCandidate(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    file_title: str
    section: str
    page_start: int
    page_end: int
    retrieval_rank: int
    retrieval_score: float
    metadata: dict = Field(default_factory=dict)


class RerankedCandidate(RerankCandidate):
    rerank_rank: int = Field(ge=1)
    rerank_score: float = Field(ge=0,le=1,allow_inf_nan=False)
    accepted: bool = True
    filter_reason: str | None = None


class RerankerProvider(Protocol):
    def rerank(self,question: str,candidates: list[RerankCandidate]) -> list[RerankedCandidate]: ...


class RerankerUnavailable(RuntimeError):pass


class LocalReranker:
    """Lazy load once; serialize inference; never silently change requested device/model."""
    def __init__(self,config):
        self.config=config.model_copy(deep=True);self.lock=RLock()
        self.model=None;self.tokenizer=None;self.device=None;self.error=None;self.revision=None

    def load(self):
        with self.lock:
            if self.model is not None:return
            try:
                import torch
                from transformers import AutoModelForSequenceClassification,AutoTokenizer
                self.device=('cuda' if torch.cuda.is_available() else 'cpu') if self.config.device=='auto' else self.config.device
                if self.device=='cuda' and not torch.cuda.is_available():raise RerankerUnavailable('CUDA_UNAVAILABLE')
                self.tokenizer=AutoTokenizer.from_pretrained(self.config.local_path,local_files_only=True,trust_remote_code=False)
                self.model=AutoModelForSequenceClassification.from_pretrained(self.config.local_path,local_files_only=True,
                    trust_remote_code=False,use_safetensors=True,dtype=torch.float16 if self.device=='cuda' else torch.float32).to(self.device).eval()
                from pathlib import Path
                revision_file=Path(self.config.local_path)/'revision.txt'
                self.revision=revision_file.read_text().strip() if revision_file.exists() else 'local-unpinned'
                self.error=None
            except Exception:
                self.model=None;self.error='RERANKER_UNAVAILABLE'
                raise RerankerUnavailable(self.error) from None

    def status(self):
        return dict(model=self.config.model,device=self.device or self.config.device,
            status='ERROR' if self.error else 'READY' if self.model is not None else 'NOT_LOADED',loaded=self.model is not None,error=self.error,revision=self.revision)

    def rerank(self,question,candidates):
        if not candidates:return []
        with self.lock:
            self.load()
            try:
                import torch
                scores=[]
                with torch.inference_mode():
                    for start in range(0,len(candidates),self.config.batch_size):
                        part=candidates[start:start+self.config.batch_size]
                        inputs=self.tokenizer([[question,c.text] for c in part],padding=True,truncation=True,
                            max_length=self.config.max_length,return_tensors='pt').to(self.device)
                        scores.extend(torch.sigmoid(self.model(**inputs).logits.view(-1).float()).cpu().tolist())
                if len(scores)!=len(candidates) or not all(math.isfinite(s) for s in scores):raise ValueError('Invalid scores')
                self.error=None
                ordered=sorted(zip(candidates,scores),key=lambda p:(-p[1],p[0].retrieval_rank,p[0].chunk_id))
                return [RerankedCandidate(**c.model_dump(),rerank_score=s,rerank_rank=i+1) for i,(c,s) in enumerate(ordered)]
            except Exception:
                self.error='RERANKER_UNAVAILABLE'
                raise RerankerUnavailable(self.error) from None
