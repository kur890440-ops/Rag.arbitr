"""Real resolver/router with invented text only. No corpus or chat history read."""
from rag_arbiter.config import Config
from rag_arbiter.documents import save_json
from rag_arbiter.llm import MiniMaxLLMProvider
from rag_arbiter.application.chat_models import ChatMessage,ChatTurn,TaskState
from rag_arbiter.application.dialogue import DialogueWorkingContext,WorkingContextService,ReferenceResolver,AnswerModeRouter
from rag_arbiter.application.retrieval_policy import RetrievalPolicyBuilder


def main():
    cfg=Config.load().llm;sid='synthetic-dialogue'
    turn=ChatTurn(session_id=sid,user_message_id='user1',assistant_message_id='assistant1',task_state_version=0,
        original_question='Какие обязанности есть у управляющего?',grounding_status='GROUNDED',owner_pid=1,owner_started=1,
        citations_json=[dict(claim_id='C1',exact_match=True),dict(claim_id='C2',exact_match=True)])
    claims=[dict(claim_id='C1',text='Управляющий представляет ежемесячный отчет.',support_status='SUPPORTED'),
        dict(claim_id='C2',text='Управляющий проводит инвентаризацию имущества.',support_status='SUPPORTED')]
    context=WorkingContextService.update(DialogueWorkingContext(session_id=sid),turn,claims)
    state=TaskState(session_id=sid)
    policy=RetrievalPolicyBuilder().build(state,'ALL_DOCUMENTS','synthetic',{})
    refs=ReferenceResolver(MiniMaxLLMProvider,cfg);router=AnswerModeRouter(MiniMaxLLMProvider,cfg)
    results=[]
    for n,(text,mode) in enumerate([
        ('Какие из этих обязанностей связаны с отчетностью?','POINT_RAG'),
        ('Какие из них встречаются чаще всего?','EXHAUSTIVE_NO_RAG'),
        ('Сопоставь перечисленные обязанности по всем документам.','EXHAUSTIVE_NO_RAG'),
        ('Что сказано о сроке отчета?','POINT_RAG')],1):
        message=ChatMessage(session_id=sid,role='USER',sequence=n,content=text)
        reference=refs.resolve(message,state,context,[message])
        intent=router.route(reference.resolved_message,state,reference,policy)
        results.append(dict(question=text,reference=reference.model_dump(),intent=intent.model_dump()))
        save_json('data/day25/dialogue_synthetic_probe.json',results)
        print(f'{n}: {reference.status} / {intent.answer_mode} / confidence={intent.confidence}',flush=True)
        # The report deadline can legitimately refer to the preceding report claim.
        assert reference.status in (('RESOLVED',) if n<4 else ('RESOLVED','NOT_NEEDED')) and reference.confidence>=.6
        assert intent.answer_mode==mode and intent.confidence>=.6
    print('Synthetic semantic resolver/router PASS',flush=True)


if __name__=='__main__':main()
