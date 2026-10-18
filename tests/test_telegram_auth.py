"""Auth contracts use synthetic identities, no Telegram or LLM network calls."""
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rag_arbiter.application.auth import AuthService,digest,COOKIE,NONCE_COOKIE
from rag_arbiter.application.runs import RunService
from rag_arbiter.web.app import create_app

@pytest.fixture
def auth(config,monkeypatch):
 monkeypatch.setenv('TELEGRAM_ADMIN_IDS','101');monkeypatch.setenv('RAG_TELEGRAM_BOT_TOKEN','')
 runs=RunService(config);service=AuthService(runs)
 yield service
 runs.close()

def session(auth,tid,peer='fixture'):
 nonce,code=auth.create_code(peer)
 assert auth.login(tid,code)
 return auth.poll(nonce,peer)

def test_registration_bootstrap_approval_and_login(auth):
 u=auth.register(202,'same','Client');assert u['status']=='PENDING' and u['role']=='CLIENT'
 assert auth.register(202,'new','Renamed')['id']==u['id']
 assert auth.register(203,'same','Other')['id']!=u['id']
 n,c=auth.create_code('a');assert not auth.login(202,c)
 a=auth.register(101);assert a['role']=='ADMIN' and a['status']=='ACTIVE'
 auth.change(a,u['id'],'approve')
 assert auth.login(202,c);token=auth.poll(n,'a');assert auth.authenticate(token)['id']==u['id']
 with auth.transaction() as db:
  assert db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]==digest(token)
  assert db.execute('SELECT code_hash FROM auth_codes').fetchone()[0]==digest(c)
  assert db.execute('SELECT count(*) FROM telegram_outbox').fetchone()[0]==1
 assert not auth.login(202,c)
 with pytest.raises(HTTPException):auth.poll(n,'a')

def test_expiry_invalid_code_nonce_and_attempt_limits(auth):
 a=auth.register(101);n,c=auth.create_code('a')
 assert not auth.login(101,'WRONGCODE')
 with pytest.raises(HTTPException):auth.poll('other-browser','b')
 with auth.transaction() as db:db.execute('UPDATE auth_codes SET expires=?',(time.time()-1,))
 assert not auth.login(101,c)
 with pytest.raises(HTTPException):auth.poll(n,'a')
 for _ in range(3):assert not auth.login(101,'bad')
 with pytest.raises(HTTPException) as e:auth.login(101,'bad')
 assert e.value.status_code==429

def test_block_revoke_last_admin_and_roles(auth):
 admin=auth.register(101);client=auth.register(202);auth.change(admin,client['id'],'approve')
 token=session(auth,202)
 with pytest.raises(HTTPException):auth.change(client,admin['id'],'block')
 with pytest.raises(HTTPException):auth.change(admin,admin['id'],'block')
 with pytest.raises(HTTPException):auth.change(admin,admin['id'],'role','CLIENT')
 auth.change(admin,client['id'],'block')
 with pytest.raises(HTTPException):auth.authenticate(token)
 assert not auth.login(202,'anything')
 auth.change(admin,client['id'],'unblock');token=session(auth,202,'other')
 auth.logout(token)
 with pytest.raises(HTTPException):auth.authenticate(token)

def test_code_atomic_single_consumption(auth):
 auth.register(101);n,c=auth.create_code('race');assert auth.login(101,c)
 def poll():
  try:return auth.poll(n,'race')
  except HTTPException:return None
 with ThreadPoolExecutor(max_workers=2) as pool:tokens=list(pool.map(lambda _:poll(),range(2)))
 assert sum(bool(t) for t in tokens)==1

def test_http_auth_isolation_admin_csrf_and_revocation(config,monkeypatch):
 monkeypatch.setenv('TELEGRAM_ADMIN_IDS','101');monkeypatch.setenv('RAG_TELEGRAM_BOT_TOKEN','')
 with TestClient(create_app(config),base_url='https://127.0.0.1',headers={'X-RAG-Request':'1'}) as c:
  auth=c.app.state.auth
  assert c.get('/consult').status_code==200 and 'Вход через Telegram' in c.get('/consult').text
  assert c.post('/consult/messages',json={'question':'secret'}).status_code==401
  assert c.get('/api/runs').status_code==401
  assert c.post('/consult/auth/code',headers={'Origin':'https://evil.invalid'}).status_code==403
  admin=auth.register(101);client=auth.register(202);auth.change(admin,client['id'],'approve')
  response=c.post('/consult/auth/code');code=response.json()['code']
  assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
  assert auth.login(202,code)
  response=c.post('/consult/auth/poll');assert response.json()['authenticated']
  assert 'Secure' in response.headers['set-cookie']
  assert c.get('/consult').status_code==200 and 'consult-form' in c.get('/consult').text
  token=c.cookies.get(COOKIE);_,visitor=c.app.state.consult.visitor(token)
  visitor.history.append(dict(role='USER',text='private fixture',citations=[]))
  assert c.get('/users').status_code==403
  assert c.get('/api/sources').status_code==403
  auth.change(admin,client['id'],'block')
  assert c.get('/consult/auth/session').status_code==401
  assert c.post('/consult/messages',json={'question':'blocked'}).status_code==401
  c.cookies.clear();c.cookies.set(COOKIE,session(auth,101,'admin'))
  assert c.get('/users').status_code==200
  assert 'private fixture' not in c.get('/consult').text
  assert c.get('/consult/sources/forged').status_code==404
  assert c.post('/api/users/'+admin['id'],json={'action':'block'}).status_code==409

def test_two_browsers_no_cross_login_and_user_session_limit(auth):
 auth.register(101);n1,c1=auth.create_code('one');n2,c2=auth.create_code('two')
 assert auth.login(101,c1)
 assert auth.poll(n2,'two') is None
 token=auth.poll(n1,'one');assert auth.authenticate(token)
 assert not auth.login(101,c1)
 auth.cfg.max_sessions_per_user=1
 assert auth.login(101,c2)
 with pytest.raises(HTTPException) as e:auth.poll(n2,'two')
 assert e.value.status_code==429


def test_last_admin_race_and_migration_idempotence(auth):
 a=auth.register(101);b=auth.register(202);auth.change(a,b['id'],'approve');auth.change(a,b['id'],'role','ADMIN')
 b=auth.user(202)
 def block(pair):
  actor,target=pair
  try:auth.change(actor,target['id'],'block');return True
  except HTTPException:return False
 with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(block,[(a,b),(b,a)]))
 assert sum(result)==1
 with auth.transaction() as db:
  assert db.execute("SELECT count(*) FROM auth_users WHERE role='ADMIN' AND status='ACTIVE'").fetchone()[0]==1
  assert db.execute('PRAGMA user_version').fetchone()[0]==11


def test_telegram_private_commands_and_single_lease(auth):
 import asyncio
 from rag_arbiter.application.telegram_auth import TelegramBot
 bot=TelegramBot(auth);other=TelegramBot(auth);sent=[]
 async def call(method,data):sent.append((method,data));return {}
 bot.call=call
 def message(text,tid=202,kind='private'):
  return dict(message=dict(chat=dict(type=kind,id=tid),**{'from':dict(id=tid,first_name='Synthetic')},text=text))
 asyncio.run(bot.handle(message('/start')));asyncio.run(bot.handle(message('/start')))
 assert auth.user(202)['status']=='PENDING'
 asyncio.run(bot.handle(message('/start',203,'group')));assert auth.user(203) is None
 admin=auth.register(101);auth.change(admin,auth.user(202)['id'],'approve');asyncio.run(bot.flush())
 assert any('одобрен' in data['text'] for _,data in sent)
 n,c=auth.create_code('browser');asyncio.run(bot.handle(message('/login '+c)))
 assert auth.poll(n,'browser')
 asyncio.run(bot.handle(message('/status')))
 assert sent[-1][1]['text']=='Доступ разрешён.'
 assert bot.lease() and not other.lease()
 assert all(c not in data['text'] for _,data in sent)


def test_lan_full_interface_keeps_auth_roles_and_network_boundary(config,monkeypatch):
 monkeypatch.setenv('TELEGRAM_ADMIN_IDS','101');monkeypatch.setenv('RAG_TELEGRAM_BOT_TOKEN','')
 config.consult.allowed_networks=['127.0.0.0/8','192.168.11.0/24']
 config.web_allowed_hosts=['192.168.11.41']
 app=create_app(config)
 with TestClient(app,base_url='https://192.168.11.41',client=('192.168.11.52',12345),headers={'X-RAG-Request':'1'}) as c:
  auth=app.state.auth
  assert c.get('/consult').status_code==200
  assert c.get('/api/users').status_code==401
  admin=auth.register(101);client=auth.register(202);auth.change(admin,client['id'],'approve')
  c.cookies.set(COOKIE,session(auth,101,'admin'))
  for path in ('/','/chat','/sources','/users','/api/users'):
   assert c.get(path).status_code==200,path
  assert c.get('/api/users',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403
  assert c.post('/api/users/'+client['id'],json={'action':'block'},headers={'Origin':'https://evil.invalid'}).status_code==403
  c.cookies.clear();c.cookies.set(COOKIE,session(auth,202,'client'))
  assert c.get('/consult').status_code==200
  for path in ('/','/chat','/sources','/users','/api/users'):
   assert c.get(path).status_code==403,path
  with TestClient(app,base_url='https://192.168.11.41',client=('8.8.8.8',12345)) as outside:
   for path in ('/consult','/users','/','/static/users.js'):
    assert outside.get(path).status_code==403,path
