"""Day24: existing ten controls plus two separate acceptance probes. Point RAG only."""
import json
from pathlib import Path
from uuid import uuid4
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.application.views import ResultsService
from rag_arbiter.application.grounding import evaluate_grounding
from rag_arbiter.documents import now

RUN='d6575efd47b349f1a8a733bb2bc50d2c'
DOC='f8bfb0a57289b204905382cb19513922de8b911ed435fdbaa94a195639023c29'


def main():
    cfg=Config.load();runs=RunService(cfg);service=RAGComparisonService(runs)
    out=Path('data/day24');out.mkdir(parents=True,exist_ok=True)
    controls=service.questions.list()
    assert len(controls)==10
    # Audit all pinned active source blocks, not just retrieval results.
    snap=ResultsService(runs).snapshot(RUN)
    with runs.db() as store:
        chunks=[store.get('chunks',cid) for cid in snap['indexes']['structure']['chunk_ids']]
        blocks=[store.get('document_blocks',bid) for bid in sorted({b for c in chunks for b in c['source_block_ids']})]
        markers=['юпитер','ганимед','каллисто','европа','ио','спутник','астроном']
        # Whole-word Io/Europa can occur in unrelated legal text; inspect matches, use unambiguous subject markers.
        matches={m:[b['block_id'] for b in blocks if m in b['text'].casefold()] for m in markers}
        assert not any(matches[m] for m in ['юпитер','ганимед','каллисто','астроном'])
        audit=dict(documents=len({c['document_id'] for c in chunks}),blocks=len(blocks),matches=matches,
            question='Какой период обращения Ганимеда вокруг Юпитера указан в документах корпуса?',
            basis='В полном наборе активных нормализованных блоков нет Юпитера, Ганимеда, Каллисто или астрономических данных по этим именам.')
        (out/'absence-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    probes=[dict(question='Кто заявитель ходатайства ООО «РУСМЕТ» от 24 февраля 2025 года и на сколько месяцев он просит продлить конкурсное производство?',
        expected_answer='Погосян Лилия Гамлетовна; на 6 месяцев.',expected_sources=[dict(document_id=DOC,page_start=1)]),
        dict(question=audit['question'],expected_answer='INSUFFICIENT_CONTEXT',expected_sources=[],expected_unanswerable=True)]
    records=[]
    for i,q in enumerate(controls+probes):
        r=service.prepare(RUN,question=q['question'],question_id=q.get('question_id'),rag_pipeline_mode='RERANK',point_only=True)
        r.update(expected_answer=q['expected_answer'],expected_sources=q['expected_sources'],
            expected_unanswerable=q.get('expected_unanswerable',False),day24_probe=i>=10)
        records.append(r)
    batch=dict(batch_id=uuid4().hex,processing_run_id=RUN,status='RUNNING',day23_evaluation=True,day24_evaluation=True,
        comparison_ids=[r['comparison_run_id'] for r in records],current=0,total=len(records),created_at=now(),finished_at=None)
    for r in records:service.put(r)
    service.put(batch,'rag_batches')
    try:
        for i,r in enumerate(records):
            path=out/f'{i+1:02d}.json'
            service.execute(r['comparison_run_id']);result=service.get(r['comparison_run_id']);records[i]=result
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            batch['current']=i+1;service.put(batch,'rag_batches')
            print(json.dumps(dict(number=i+1,status=result['rag_result']['status'],grounding=result.get('grounding_result'),
                id=result['comparison_run_id']),ensure_ascii=True),flush=True)
        metrics=dict(controls=evaluate_grounding(records[:10]),probes=evaluate_grounding(records[10:]),all=evaluate_grounding(records))
        (out/'evaluation.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
        batch.update(status='COMPLETED',finished_at=now(),grounding_metrics=metrics)
        service.put(batch,'rag_batches');print(json.dumps(metrics),flush=True)
    finally:runs.executor.shutdown(wait=True)


if __name__=='__main__':main()
