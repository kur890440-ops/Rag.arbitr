import json
import pytest
from rag_arbiter.application.chat_memory import TaskMemoryUpdater
from rag_arbiter.application.chat_models import TaskState,ChatValidationError
from rag_arbiter.llm import LLMConfig,LLMResult
from test_chat import MemoryProvider,user


@pytest.mark.parametrize('stage',['extraction','verification'])
@pytest.mark.parametrize('persistent',[False,True])
def test_temporary_failure_one_retry_then_verified_or_atomic_failure(stage,persistent):
    base=MemoryProvider();calls=[];failures=[]
    class Provider:
        def generate(self,r):
            current='verification' if r.question.startswith('DAY25_VERIFY_MEMORY') else 'extraction'
            calls.append(current)
            if current==stage and (persistent or not failures):
                failures.append(current)
                return LLMResult(model='test',status='ERROR',error={'code':'NETWORK_ERROR','private':'secret'},request_count=1)
            return base.generate(r)
    updater=TaskMemoryUpdater(lambda _:Provider(),LLMConfig());state=TaskState(session_id='s');before=state.model_dump()
    m=user('Жалобы исключи.')
    if persistent:
        with pytest.raises(ChatValidationError) as e:updater.propose(state,m)
        diag=e.value.diagnostics
        assert state.model_dump()==before and diag['provider_error']=={'code':'NETWORK_ERROR'}
    else:
        patch,diag=updater.propose(state,m)
        assert updater.apply(state,patch,m).constraints_json and diag['semantic_verified']
    assert len(diag['transport_retries'])==1 and len(failures)==(2 if persistent else 1)
    assert diag['requests']==len(calls) and 'secret' not in json.dumps(diag)


@pytest.mark.parametrize('code',['AUTH_ERROR','PAYMENT_REQUIRED','NOT_CONFIGURED','HTTP_ERROR','private-token'])
def test_permanent_error_no_retry_no_secret(code):
    calls=[]
    class P:
        def generate(self,r):
            calls.append(r)
            return LLMResult(model='test',status='ERROR',error={'code':code,'message':'private-token','http_status':401},request_count=1)
    with pytest.raises(ChatValidationError) as e:TaskMemoryUpdater(lambda _:P(),LLMConfig()).propose(TaskState(session_id='s'),user('q'))
    assert len(calls)==1 and not e.value.diagnostics['transport_retries']
    assert 'private-token' not in str(e.value)+json.dumps(e.value.diagnostics)
