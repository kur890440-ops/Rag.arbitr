"""Real Day28 benchmark using the existing indexed corpus and comparison service."""
import argparse
import json
import statistics
from pathlib import Path
from rag_arbiter.config import Config
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.rag import RAGComparisonService
from rag_arbiter.local_llm import LocalLLMProvider
from rag_arbiter.llm import LLMRequest, LLMResult, MiniMaxLLMProvider

RUN = 'd6575efd47b349f1a8a733bb2bc50d2c'
OUT = Path('data/day28')


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-only', action='store_true', help='Prepare comparison but do not transmit corpus to cloud')
    args = parser.parse_args()
    cfg = Config.load()
    local = LocalLLMProvider(cfg.llm.local)
    info = local.inspect()
    save('model.json', info)
    simple = local.generate(LLMRequest(question='Reply with the single word OK.'))
    save('simple.json', simple.model_dump())
    print('simple', simple.status, round(simple.duration_ms), flush=True)
    if simple.status != 'SUCCESS':
        raise RuntimeError('Real simple generation failed; benchmark not started')
    runs = RunService(cfg)
    class ApprovalBlocked:
        def __init__(self, config): self.config = config
        def generate(self, request):
            return LLMResult(model=self.config.model, status='APPROVAL_REQUIRED', error={'code':'APPROVAL_REQUIRED'})
    service = RAGComparisonService(runs, llm_factory=ApprovalBlocked if args.local_only else MiniMaxLLMProvider)
    controls = service.questions.list()
    questions = [controls[i] for i in (0, 6, 7)]
    questions[1] = dict(question='\u041a\u0430\u043a\u0438\u0435 \u043e\u0431\u044f\u0437\u0430\u043d\u043d\u043e\u0441\u0442\u0438 \u043f\u043e \u043f\u0440\u0435\u0434\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u044e \u043e\u0442\u0447\u0435\u0442\u0430 \u0438 \u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044e \u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0440\u043e\u0432 \u0432\u043e\u0437\u043b\u043e\u0436\u0435\u043d\u044b \u043d\u0430 \u043a\u043e\u043d\u043a\u0443\u0440\u0441\u043d\u043e\u0433\u043e \u0443\u043f\u0440\u0430\u0432\u043b\u044f\u044e\u0449\u0435\u0433\u043e?')
    results = []
    try:
        for number, question in enumerate(questions, 1):
            for repeat in range(1, 3):
                record = service.prepare(RUN, question=question['question'],question_id=question.get('question_id'),
                    generation_mode='compare', rag_pipeline_mode='RERANK', max_context_sources=5)
                service.put(record)
                result = service.execute(record['comparison_run_id'])
                save(f'q{number}-r{repeat}.json', result)
                results.append(result)
                print(number, repeat, result['status'], [(g['provider'], g['result']['status'],
                    round(g['generation_ms']), g['grounding_status']) for g in result['generation_runs']], flush=True)
        summary = []
        for number in range(1, 4):
            pair = results[(number-1)*2:number*2]
            for provider in ('local','minimax'):
                gs = [g for r in pair for g in r['generation_runs'] if g['provider']==provider]
                if not gs:
                    summary.append(dict(question=number,provider=provider,status='NO_GENERATION',reason=pair[0]['rag_result']['status']))
                    continue
                if all(g['result']['status']=='APPROVAL_REQUIRED' for g in gs):
                    summary.append(dict(question=number,text=pair[0]['question_text'],provider=provider,
                        status='APPROVAL_REQUIRED',measured=False,generation_ms_mean=None,total_ms_mean=None))
                    continue
                times = [g['generation_ms'] for g in gs]
                summary.append(dict(question=number, text=pair[0]['question_text'], provider=provider,
                    same_snapshot_across_repeats=len({g['context_snapshot_id'] for g in gs})==1,
                    successful_runs=sum(g['result']['status']=='SUCCESS' for g in gs),
                    structured_valid=sum(g.get('structured_output_valid') is True for g in gs),
                    grounding_pass=sum(g['grounding_status']=='GROUNDED' for g in gs),
                    repairs=sum(g['repair_count'] for g in gs), generation_ms_mean=statistics.mean(times),
                    generation_ms_min=min(times), generation_ms_max=max(times),
                    total_ms_mean=statistics.mean(g['total_ms'] for g in gs)))
        save('summary.json', summary)
    finally:
        runs.close()


if __name__ == '__main__':
    main()
