"""Routes for the isolated Day29 workspace tab."""
from fastapi import Request
from ..application.direct_experiments import DirectExperimentRequest, QualityReview, comparison
from ..application.judicial_document import available_documents, read_document, QUALITY_LABELS
from fastapi.responses import HTMLResponse
from ..application.manual_optimization import ExperimentRequest
from ..application.optimization import laboratory
from ..local_llm import LocalLLMProvider
from ..recognition.runtime import gpu_diagnostics


def register_optimization(app,render):
    @app.get('/ui/llm-optimization/archive',response_class=HTMLResponse)
    def optimization(request:Request,question_id:str=''):
        service=request.app.state.local_benchmark
        data=laboratory(service.directory)
        available=data.pop('questions')
        selected=next((q for q in available if q['snapshot']['question_id']==question_id),available[0] if available else None)
        local=service.runs.config.llm.local
        summary=dict(model=local.model,profile=local.model_dump(),status='DISABLED' if not local.enabled else 'UNAVAILABLE',quantization=None,device=None)
        try:
            info=LocalLLMProvider(local).inspect()
            summary.update(status='READY',quantization=info.get('details',{}).get('quantization_level'))
        except Exception:pass
        gpu=gpu_diagnostics()
        if gpu.get('nvidia_detected'):summary['device']=gpu['nvidia_smi'].split(',')[0]
        return render(request,'optimization_archive.html',available=available,questions=[selected] if selected else [],
                      summary=summary,job=service.status(),tab_view=True,**data)

    @app.post('/ui/llm-optimization/benchmark',response_class=HTMLResponse)
    def start(request:Request):
        job=request.app.state.local_benchmark.start()
        return render(request,'optimization_job.html',job=job)

    @app.get('/ui/llm-optimization/benchmark',response_class=HTMLResponse)
    def status(request:Request):
        job=request.app.state.local_benchmark.status()
        response=render(request,'optimization_job.html',job=job)
        if job['status'] not in ('IDLE','QUEUED','RUNNING'):
            response.headers['HX-Trigger']='optimizationFinished'
        return response

    @app.get('/ui/llm-optimization',response_class=HTMLResponse)
    def manual(request:Request):
        from typing import get_args
        from ..direct_generation import DirectPromptVersion
        service=request.app.state.direct_experiments
        models=[]
        try:
            tags,_=LocalLLMProvider(service.runs.config.llm.local).transport('/api/tags',timeout=3)
            models=[m['name'] for m in tags.get('models',[]) if m.get('name') and not m.get('remote_host') and 'cloud' not in m['name'].lower() and 'vision' not in m.get('capabilities',[])]
        except Exception:pass
        return render(request,'optimization_tab.html',profile=service.runs.config.llm.local.model_dump() | {"prompt_version": "day29-baseline"},models=models,
                      prompts=('day29-baseline','day29-judicial-v1'), documents=available_documents(service.runs))

    @app.post('/api/llm-experiments')
    def experiment_start(request:Request,body:ExperimentRequest):
        return {'id':request.app.state.experiments.start(body)}

    @app.get('/api/llm-experiments/{key}')
    def experiment(request:Request,key:str):
        service=request.app.state.experiments
        return service.view(service.get(key))

    @app.get('/ui/llm-experiments/history',response_class=HTMLResponse)
    def history(request:Request):
        service=request.app.state.experiments
        return render(request,'experiment_history.html',rows=[service.view(r) for r in service.history()])

    @app.get('/ui/llm-experiments/compare',response_class=HTMLResponse)
    def compare(request:Request,run_a:str,run_b:str):
        views,fair,deltas=request.app.state.experiments.compare(run_a,run_b)
        return render(request,'experiment_results.html',views=views,fair=fair,deltas=deltas)

    @app.get('/ui/llm-experiments/{key}',response_class=HTMLResponse)
    def result(request:Request,key:str):
        service=request.app.state.experiments
        return render(request,'experiment_results.html',views=[service.view(service.get(key))], legacy_readonly=True)

    @app.get('/ui/llm-optimization/legacy', response_class=HTMLResponse)
    def legacy(request: Request):
        service = request.app.state.experiments
        return render(request, 'direct_legacy.html', rows=[service.view(r) for r in service.history()])

    @app.post('/api/local-llm-experiments')
    def direct_start(request: Request, body: DirectExperimentRequest):
        return {'id': request.app.state.direct_experiments.start(body)}

    @app.get('/api/local-llm-experiments')
    def direct_history(request: Request, task: str = ''):
        rows=request.app.state.direct_experiments.history()
        return [r for r in rows if not task or r.get('kind')==task]

    @app.get('/api/local-llm-experiments/{key}')
    def direct_get(request: Request, key: str):
        return request.app.state.direct_experiments.get(key)

    @app.get('/ui/local-llm-experiments', response_class=HTMLResponse)
    def direct_columns(request: Request, ids: str = '', baseline: str = ''):
        service = request.app.state.direct_experiments
        keys = list(dict.fromkeys(filter(None, ids.split(','))))
        rows = [service.get(key) for key in keys]
        base = service.get(baseline) if baseline else None
        return render(request, 'direct_columns.html', rows=rows, baseline=baseline,
                      comparisons={r['id']: comparison(r, base) for r in rows}, quality_labels=QUALITY_LABELS)


    @app.get('/api/local-llm-documents/{version}')
    def judicial_document(request: Request, version: str):
        return read_document(request.app.state.runs, version).model_dump()

    @app.post('/api/local-llm-experiments/{key}/review')
    def review(request: Request, key: str, body: QualityReview):
        return request.app.state.direct_experiments.review(key, body)

    @app.get('/ui/local-llm-legacy', response_class=HTMLResponse)
    def old_direct(request: Request):
        rows=[r for r in request.app.state.direct_experiments.history() if r.get('kind')!='judicial_act']
        return render(request, 'direct_columns.html', rows=rows, baseline='', comparisons={r['id']:None for r in rows}, quality_labels=QUALITY_LABELS)
