"""Day29 tab read model and asynchronous adapter to the existing snapshot replay."""
import copy
import json
import threading
import uuid
from pathlib import Path
from .local_benchmark import verified_snapshot, same_evidence, demo_variant, load_demo, replay_snapshot
from .benchmark_resources import Resources
from ..llm import LocalLLMConfig
from ..local_llm import LocalLLMProvider
from ..reranking import LocalReranker


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def snapshots(directory):
    result=[]
    for path in sorted(directory.glob('q*-snapshot.json')):
        try: result.append((path,verified_snapshot(read_json(path))))
        except (OSError,ValueError,KeyError,TypeError): continue
    return result


def laboratory(directory):
    """All recorded experiments, including failed attempts and incomplete pairs."""
    frozen=snapshots(directory)
    try: manifest=read_json(directory/'baseline.json')
    except (OSError,ValueError): manifest={}
    questions=[]; experiments=[]
    for run_id in dict.fromkeys(s['processing_run_id'] for _,s in frozen):
        questions.extend(load_demo(directory,run_id))
    for path,snapshot in frozen:
        prefix=path.name.removesuffix('-snapshot.json')
        for artifact in sorted(directory.glob(prefix+'-*.json')):
            name=artifact.stem[len(prefix)+1:]
            if name in ('snapshot','baseline-record'):continue
            try:
                item=read_json(artifact)
                if 'profile' not in item:continue
                same_evidence(item,snapshot)
                variant=demo_variant(item,name,'',manifest)
                variant['question_id']=snapshot['question_id']
                experiments.append(variant)
            except (OSError,ValueError,KeyError,TypeError):continue
    return dict(questions=questions,experiments=experiments,can_run=bool(frozen and manifest.get('config')))


class LocalBenchmarkService:
    """Queue a paired replay; preserve immutable snapshots and original artifacts."""
    def __init__(self,runs):
        self.runs=runs
        self.directory=runs.config.web_data_path.parent/'day29'
        self.lock=threading.Lock()
        self.job=dict(status='IDLE',completed=0,total=0)

    def status(self):
        with self.lock:return copy.deepcopy(self.job)

    def start(self):
        with self.lock:
            if self.job['status'] in ('QUEUED','RUNNING'):return copy.deepcopy(self.job)
            frozen=snapshots(self.directory)
            if not frozen:raise ValueError('No frozen Day29 test set. Run the existing freeze mechanism first.')
            manifest=read_json(self.directory/'baseline.json')
            baseline=LocalLLMConfig.model_validate(manifest['config'] | manifest.get('explicit_options',{}))
            profile=self.runs.config.llm.local.model_copy(deep=True)
            if not profile.enabled:raise ValueError('Local generation is disabled')
            name='ui-'+uuid.uuid4().hex
            self.job=dict(id=name,status='QUEUED',completed=0,total=len(frozen)*2)
            self.runs.executor.submit(self.execute,name,frozen,manifest,baseline,profile)
            return copy.deepcopy(self.job)

    def execute(self,name,frozen,manifest,baseline,profile):
        def update(**values):
            with self.lock:self.job.update(values)
        update(status='RUNNING')
        try:
            # Compare the same frozen model, not an accidentally replaced installation.
            for selected in (baseline,profile):
                info=LocalLLMProvider(selected).inspect()
                if info.get('digest') != manifest['model']['digest']:
                    raise ValueError('Frozen model digest mismatch')
            reranker=LocalReranker(self.runs.config.reranker)
            reranker.load()
            failures=0;completed=0
            for path,snapshot in frozen:
                prefix=path.name.removesuffix('-snapshot.json')
                for variant,selected in (('baseline',baseline),('optimized',profile)):
                    with Resources() as resources:
                        result=replay_snapshot(self.runs,snapshot,selected,reranker)
                    result.update(variant=name+'-'+variant,resources=resources.summary())
                    target=self.directory/f'{prefix}-{name}-{variant}.json'
                    temporary=target.with_suffix('.tmp')
                    with temporary.open('x',encoding='utf-8') as f:
                        json.dump(result,f,ensure_ascii=False,indent=2)
                    temporary.replace(target)
                    completed+=1
                    failures+=bool(result.get('error'))
                    update(completed=completed)
            update(status='PARTIAL' if failures else 'COMPLETED')
        except Exception:
            # Avoid exposing source text, server response bodies or credentials.
            update(status='FAILED',error='LOCAL_BENCHMARK_FAILED')
