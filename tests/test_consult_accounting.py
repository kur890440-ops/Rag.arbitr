"""Daily accounting retains unknown costs and settles known provider usage."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading,time
import pytest
from fastapi import HTTPException
from rag_arbiter.application.runs import RunService
from rag_arbiter.application.consult import ConsultService
from rag_arbiter.llm import LLMRequest,LLMResult

@pytest.fixture
def accounting(config):
    runs=RunService(config);s=ConsultService.__new__(ConsultService)
    s.runs=runs;s.cfg=config.consult;s.lock=threading.RLock()
    s.knowledge=SimpleNamespace(still_allowed=lambda grants:True)
    yield s
    runs.close()

def used(s,day):
    with s.runs.db() as db:return db.get('consult_usage',day)['reserved_tokens']

def test_settlement_releases_unused_reservation(accounting):
    s=accounting;s.cfg.daily_token_budget=1000
    reservation=s.reserve(900)
    assert s.settle(reservation,dict(prompt_tokens=100,completion_tokens=50,total_tokens=150))==150
    assert used(s,reservation[0])==150
    s.reserve(800)
    with pytest.raises(HTTPException):s.reserve(51)

@pytest.mark.parametrize('usage',[{},dict(total_tokens=-1),dict(total_tokens=True),dict(total_tokens='100'),dict(prompt_tokens=10)])
def test_unknown_usage_keeps_reservation(accounting,usage):
    s=accounting;r=s.reserve(100)
    assert s.settle(r,usage) is None
    assert used(s,r[0])==100

def test_parallel_settlement_keeps_other_reservations(accounting):
    s=accounting;items=[s.reserve(1000) for _ in range(8)];pending=s.reserve(250)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda r:s.settle(r,dict(total_tokens=100)),items))
    assert used(s,pending[0])==1050

def test_usage_over_reservation_is_fully_counted(accounting):
    s=accounting;r=s.reserve(100)
    assert s.settle(r,dict(total_tokens=150,prompt_tokens=120,completion_tokens=80))==200
    assert used(s,r[0])==200

def test_previous_day_settles_on_original_day(accounting):
    s=accounting
    with s.runs.db() as db:db.put('consult_usage','2000-01-01',dict(reserved_tokens=500))
    today=s.reserve(100)
    s.settle(('2000-01-01',500),dict(total_tokens=75))
    assert used(s,'2000-01-01')==75 and used(s,today[0])==100

def test_provider_wrapper_settles_actual_usage(accounting):
    s=accounting
    provider=SimpleNamespace(generate=lambda request:LLMResult(model='fixture',status='SUCCESS',text='test',usage=dict(total_tokens=30)))
    s.chat=SimpleNamespace(rag=SimpleNamespace(llm_factory=lambda config:provider))
    metrics=dict(calls=0,reserved_tokens=0)
    s.factory({},metrics,time.monotonic()+30)(s.runs.config.llm).generate(LLMRequest(question='synthetic'))
    assert metrics['calls']==1 and metrics['actual_tokens']==30
    with s.runs.db() as db:assert sum(row['reserved_tokens'] for row in db.all('consult_usage'))==30


def test_generation_budget_and_recovered_limit_accounting(accounting):
    s=accounting;limits=[];responses=iter([
        LLMResult(model='fixture',status='SUCCESS',text='{}',usage=dict(total_tokens=10)),
        LLMResult(model='fixture',status='TRUNCATED',finish_reason='length',text='unfinished',error={'code':'OUTPUT_LIMIT'},usage=dict(total_tokens=20)),
        LLMResult(model='fixture',status='SUCCESS',finish_reason='stop',text='{}',usage=dict(total_tokens=30))])
    def factory(config):
        limits.append(config.max_output_tokens)
        return SimpleNamespace(generate=lambda request:next(responses))
    s.chat=SimpleNamespace(rag=SimpleNamespace(llm_factory=factory))
    metrics=dict(calls=0,reserved_tokens=0)
    provider=s.factory({},metrics,time.monotonic()+30)(s.runs.config.llm)
    provider.generate(LLMRequest(question='route'))
    provider.generate(LLMRequest(question='answer',context='synthetic',context_type='grounded_rag'))
    assert metrics['provider_error']=='OUTPUT_LIMIT'
    provider.generate(LLMRequest(question='answer',context='synthetic',context_type='grounded_rag'))
    assert limits==[s.cfg.max_output_tokens,s.cfg.generation_output_tokens,s.cfg.generation_output_tokens]
    assert metrics['actual_tokens']==60 and 'provider_error' not in metrics
    assert metrics['provider_calls'][1]['finish_reason']=='length'
    assert metrics['provider_calls'][2]['status']=='SUCCESS'
