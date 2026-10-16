"""Bounded Chat presentation reads preserve the full analysis history."""
import re
from fastapi.testclient import TestClient
from rag_arbiter.web.app import create_app
from rag_arbiter.application.chat_models import ChatMessage


def test_chat_ui_cursor_pages_and_incremental_reads(config):
    with TestClient(create_app(config),base_url='http://127.0.0.1',headers={'X-RAG-Request':'1'}) as client:
        chat=client.app.state.chat;sid=chat.create(None).session_id
        with chat.runs.db() as store:
            for n in range(1,122):
                message=ChatMessage(session_id=sid,role='USER' if n%2 else 'ASSISTANT',content=f'Message {n}',sequence=n)
                store.put('chat_messages',message.message_id,message.model_dump())
        page=client.get('/chat',params={'session_id':sid})
        assert page.status_code==200
        assert 'history-drawer' in page.text and 'memory-drawer' in page.text
        assert [int(n) for n in re.findall('data-sequence="(\\d+)"',page.text)]==list(range(82,122))
        earlier=client.get('/ui/chat/'+sid,params={'before':82}).text
        assert [int(n) for n in re.findall('data-sequence="(\\d+)"',earlier)]==list(range(42,82))
        delta=client.get('/ui/chat/'+sid,params={'after':120}).text
        assert re.findall('data-sequence="(\\d+)"',delta)==['121']
        assert 'chat-message ' not in client.get('/ui/chat/'+sid,params={'after':121}).text
        assert len(chat.load(sid)['messages'])==121
        assert client.get('/ui/chat/'+sid,params={'before':0}).status_code==422
