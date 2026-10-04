"""Replay a saved initial answer and make at most one real repair request.

Read-only for application SQLite; result saved separately, never replaces history.
"""
import json
import sqlite3
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.llm import MiniMaxLLMProvider, LLMRequest, LLMResult, BASE_SYSTEM
from rag_arbiter.reranking import LocalReranker
from rag_arbiter.application.grounding import AnswerContract, CitationBuilder, grounded_generation


class Store:
    def __init__(self,db):self.db=db
    def get(self,table,key):
        row=self.db.execute('SELECT data FROM '+table+' WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None


def main():
    cfg=Config.load();provider=MiniMaxLLMProvider(cfg.llm);calls=0
    with sqlite3.connect('file:data/sqlite/metadata.db?mode=ro',uri=True) as db:
        store=Store(db);r=store.get('rag_comparison_runs','394cd9de312b4ec8841732fc31adfdf7')
        claims=r['grounding_diagnostics'][0]['claims']
        original=AnswerContract(answer=' '.join(c['text'] for c in claims),claims=claims)
        record=dict(sources=r['sources'])
        def generate(context,kind):
            nonlocal calls
            calls+=1
            if calls==1:return LLMResult(model=cfg.llm.model,status='SUCCESS',text=original.model_dump_json()).model_dump()
            assert calls==2
            req=LLMRequest(question=r['chat_generation_base'],context=context,context_type=kind)
            assert len((BASE_SYSTEM+req.user_content()).encode())+512<=r['chat_input_budget']
            return provider.generate(req).model_dump()
        result=grounded_generation(record,r['context_text'],generate,CitationBuilder(store),LocalReranker(cfg.reranker),cfg.llm.claim_support_threshold)
    out=Path('data/day25/real_repair_check.json')
    out.write_text(json.dumps(dict(saved_comparison_id=r['comparison_run_id'],reused_initial_answer=True,
        result=result,grounding=record),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],requests=result['request_count'],
        grounding=record['grounding_result'],artifact=str(out))))


if __name__=='__main__':main()
