import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from rag_arbiter.embeddings import ApplicationEmbeddingResource
from rag_arbiter.config import Config


def test_sequential_and_concurrent_load_once(tmp_path):
    calls=[]
    def factory(cfg):
        calls.append(1);time.sleep(.03)
        return SimpleNamespace(identity={'model':'fixture'})
    resource=ApplicationEmbeddingResource(factory);cfg=Config(cache_path=tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:values=list(pool.map(lambda _:resource.get(cfg),range(8)))
    assert len(calls)==1 and all(v is values[0] for v in values)
    assert resource.get(cfg) is values[0] and resource.initializations==1
    resource.close();assert resource._provider is None


def test_failed_load_not_cached_and_next_request_recovers(tmp_path):
    calls=[]
    def factory(cfg):
        calls.append(1)
        if len(calls)==1:raise ValueError('fixture failure')
        return SimpleNamespace(identity={'model':'fixture'})
    r=ApplicationEmbeddingResource(factory);cfg=Config(cache_path=tmp_path)
    with pytest.raises(ValueError):r.get(cfg)
    assert r._provider is None and r.last_event['status']=='FAILED'
    assert r.get(cfg) is r.get(cfg) and r.attempts==2 and r.initializations==1


def test_services_do_not_share_provider(tmp_path):
    factory=lambda cfg:SimpleNamespace(identity={})
    a=ApplicationEmbeddingResource(factory);b=ApplicationEmbeddingResource(factory);cfg=Config(cache_path=tmp_path)
    assert a.get(cfg) is not b.get(cfg)
