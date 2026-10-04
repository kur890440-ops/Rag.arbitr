"""Live memory-only probe: synthetic phrases, no corpus reads or database writes."""
import json
from rag_arbiter.config import Config
from rag_arbiter.llm import MiniMaxLLMProvider
from rag_arbiter.application.chat_memory import TaskMemoryUpdater
from rag_arbiter.application.chat_models import ChatMessage, TaskState


def main():
    class ProbeProvider(MiniMaxLLMProvider):
        def generate(self,request):
            result=super().generate(request)
            # This script uses only the synthetic state below, never corpus/user history.
            print(json.dumps(dict(stage=request.question.split('.')[0],status=result.status,
                text=result.text,usage=result.usage),ensure_ascii=False),flush=True)
            return result
    updater=TaskMemoryUpdater(ProbeProvider,Config.load().llm)
    state=TaskState(session_id='synthetic-free-memory')
    cases=[
        ('Можно ограничиться определениями суда?', 'MEMORY_ONLY'),
        ('Жалобы давай оставим за рамками.', 'MEMORY_ONLY'),
        ('Отдельное ограничение на жалобы больше не нужно, остальные условия оставь как есть.', 'MEMORY_ONLY'),
        ('Давай смотреть только за 2025 год и перечисли сроки.', 'MIXED'),
    ]
    for n,(text,intent) in enumerate(cases,1):
        message=ChatMessage(session_id=state.session_id,role='USER',content=text,sequence=n)
        patch,diagnostics=updater.propose(state,message)
        state=updater.apply(state,patch,message)
        assert patch.intent==intent, 'Wrong intent'
        keys={i.key for i in state.constraints_json}
        assert 'document_type' in keys, 'Court-only condition lost'
        if n==2:assert 'exclude_complaints' in keys
        if n==3:assert 'exclude_complaints' not in keys
        print(json.dumps(dict(case=n,intent=patch.intent,requests=diagnostics['requests'],
            constraints={i.key:i.value for i in state.constraints_json}),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
