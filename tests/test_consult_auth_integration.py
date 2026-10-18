"""Authenticated CLIENT uses the original indexed-corpus RAG and citations."""
from test_rag import ragweb
from test_internal_consultant import internal
from rag_arbiter.application.auth import COOKIE


def login(client,auth,tid):
 client.cookies.clear();client.base_url='https://127.0.0.1'
 response=client.post('/consult/auth/code');assert response.status_code==200
 assert auth.login(tid,response.json()['code'])
 assert client.post('/consult/auth/poll').json()['authenticated']
 assert client.get('/consult').status_code==200


def test_client_rag_citations_foreign_history_and_block(ragweb):
 c,_,llm,_=ragweb;s,f=internal(c,llm);auth=c.app.state.auth
 admin=auth.user(101);a=auth.register(202,'client_a','Synthetic A');b=auth.register(203,'client_b','Synthetic B')
 for u in (a,b):auth.change(admin,u['id'],'approve')
 login(c,auth,202);token_a=c.cookies.get(COOKIE)
 response=c.post('/consult/messages',json={'question':'Какая оплата?'})
 assert response.status_code==200,response.text
 visitor=s.visitor(token_a)[1];assert visitor.citations
 cid=next(iter(visitor.citations));assert c.get('/consult/sources/'+cid).status_code==200
 assert any(r.context_type=='grounded_rag' for r in f.inputs)
 assert all('telegram_user_id' not in r.question and token_a not in r.question for r in f.inputs)
 login(c,auth,203)
 assert not s.visitor(c.cookies.get(COOKIE))[1].history
 assert c.get('/consult/sources/'+cid).status_code==404
 assert c.get('/chat').status_code==403 and c.get('/api/users').status_code==403
 assert c.post('/api/users/'+a['id'],json={'action':'block'}).status_code==403
 c.cookies.clear();c.cookies.set(COOKIE,token_a)
 auth.change(admin,a['id'],'block')
 assert c.get('/consult/sources/'+cid).status_code==401
 assert c.post('/consult/messages',json={'question':'Какая оплата?'}).status_code==401
