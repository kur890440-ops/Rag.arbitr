"""One leased Telegram long-poll task in the existing web lifecycle."""
import asyncio
import json
import logging
import secrets
import time
import httpx
from .auth import setting,digest
from fastapi import HTTPException

class TelegramBot:
    def __init__(self,auth):
        self.auth=auth;self.token=setting('RAG_TELEGRAM_BOT_TOKEN');self.owner=secrets.token_hex(16)
        self.key='poller:'+digest(self.token);self.status='NOT_CONFIGURED' if not self.token else 'STARTING'
        self.client=None

    def lease(self):
        with self.auth.transaction() as db:
            row=db.execute('SELECT value FROM telegram_state WHERE id=?',(self.key,)).fetchone()
            lease=json.loads(row[0]) if row else {}
            if lease.get('expires',0)>time.time() and lease.get('owner')!=self.owner:return False
            db.execute('INSERT OR REPLACE INTO telegram_state VALUES (?,?)',(self.key,json.dumps(dict(owner=self.owner,expires=time.time()+90))))
            return True

    async def call(self,method,data):
        # Never log exception URLs: the Bot API URL includes the bot secret.
        response=await self.client.post('https://api.telegram.org/bot'+self.token+'/'+method,json=data)
        if response.status_code!=200:raise ValueError('TELEGRAM_HTTP_ERROR')
        body=response.json()
        if not body.get('ok'):raise ValueError('TELEGRAM_API_ERROR')
        return body['result']

    async def handle(self,update):
        msg=update.get('message',{});sender=msg.get('from',{});chat=msg.get('chat',{})
        if chat.get('type')!='private' or sender.get('is_bot') or sender.get('id')!=chat.get('id'):return
        tid=sender.get('id');text=msg.get('text','')
        if not text.strip():return
        command,*args=text.split()
        command=command.split('@')[0] if command else ''
        try:
            self.auth.rate('tg-command:'+str(tid),20)
            if command=='/start':
                user=self.auth.register(tid,sender.get('username',''),' '.join(filter(None,[sender.get('first_name'),sender.get('last_name')])))
                reply={'PENDING':'Заявка отправлена. Ожидайте одобрения администратора.','ACTIVE':'Доступ разрешён. Откройте /consult и отправьте /login CODE с кодом из браузера.','BLOCKED':'Доступ заблокирован.'}[user['status']]
            elif command=='/status':
                user=self.auth.user(tid);reply='Сначала отправьте /start.' if not user else {'PENDING':'Ожидает одобрения.','ACTIVE':'Доступ разрешён.','BLOCKED':'Доступ заблокирован.'}[user['status']]
            elif command=='/login':
                ok=self.auth.login(tid,args[0] if len(args)==1 else '')
                reply='Вход подтверждён. Вернитесь в браузер.' if ok else 'Вход не выполнен. Проверьте статус /status и получите новый код в своём браузере.'
            else:reply='Команды: /start, /status, /login CODE. Отправляйте только код из своего браузера.'
        except HTTPException:reply='Слишком много попыток. Подождите две минуты.'
        await self.call('sendMessage',dict(chat_id=tid,text=reply))

    async def flush(self):
        with self.auth.transaction() as db:rows=[dict(r) for r in db.execute('SELECT * FROM telegram_outbox WHERE attempts<10 ORDER BY created_at LIMIT 10')]
        for row in rows:
            if not self.lease():return
            try:
                await self.call('sendMessage',dict(chat_id=row['telegram_user_id'],text=row['text']))
                with self.auth.transaction() as db:db.execute('DELETE FROM telegram_outbox WHERE id=?',(row['id'],))
            except Exception:
                with self.auth.transaction() as db:db.execute('UPDATE telegram_outbox SET attempts=attempts+1 WHERE id=?',(row['id'],))

    async def run(self):
        if not self.token:return
        # httpx logs include credential-bearing URLs; disable these transport logs.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        async with httpx.AsyncClient(timeout=35,follow_redirects=False) as self.client:
            try:
                while True:
                    if not self.lease():self.status='LEASE_HELD';await asyncio.sleep(5);continue
                    try:
                        with self.auth.transaction() as db:
                            row=db.execute('SELECT value FROM telegram_state WHERE id=?',(self.key+':offset',)).fetchone()
                            offset=int(row[0]) if row else 0
                        await self.flush()
                        updates=await self.call('getUpdates',dict(offset=offset,timeout=25,allowed_updates=['message']))
                        for update in updates:
                            if not self.lease():break
                            await self.handle(update)
                            with self.auth.transaction() as db:db.execute('INSERT OR REPLACE INTO telegram_state VALUES (?,?)',(self.key+':offset',str(update['update_id']+1)))
                        self.status='RUNNING'
                    except asyncio.CancelledError:raise
                    except Exception:
                        self.status='UNAVAILABLE';await asyncio.sleep(5)
            finally:
                with self.auth.transaction() as db:
                    row=db.execute('SELECT value FROM telegram_state WHERE id=?',(self.key,)).fetchone()
                    if row and json.loads(row[0]).get('owner')==self.owner:db.execute('DELETE FROM telegram_state WHERE id=?',(self.key,))
