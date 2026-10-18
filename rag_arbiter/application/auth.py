"""Telegram identities and hashed browser credentials in the existing SQLite DB."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException

COOKIE='rag_consult'
NONCE_COOKIE='rag_login_nonce'
CODE_TTL=120

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def setting(name):
    value=os.environ.get(name)
    if value is not None:return value.strip()
    path=Path('.env')
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key,sep,value=line.partition('=')
            if sep and key.strip()==name:return value.strip().strip('\"\'')
    return ''

class AuthService:
    def __init__(self,runs):
        self.runs=runs;self.cfg=runs.config.telegram_auth
        raw=setting('TELEGRAM_ADMIN_IDS')
        try:self.admin_ids=set(int(v.strip()) for v in raw.split(',') if v.strip()) | set(self.cfg.admin_ids)
        except ValueError:raise ValueError('TELEGRAM_ADMIN_IDS must contain numeric IDs') from None
        if any(i<=0 for i in self.admin_ids):raise ValueError('Telegram admin IDs must be positive')

    @contextmanager
    def transaction(self):
        with self.runs.db() as store:
            db=store.db;db.row_factory=sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback();raise

    def rate(self,key,limit,seconds=60):
        now=time.time();key=digest(key)
        with self.transaction() as db:
            db.execute('DELETE FROM auth_limits WHERE window<?',(now-3600,))
            row=db.execute('SELECT * FROM auth_limits WHERE id=?',(key,)).fetchone()
            if not row or row['window']<=now-seconds:
                db.execute('INSERT OR REPLACE INTO auth_limits VALUES (?,?,1)',(key,now));return
            if row['count']>=limit:raise HTTPException(429,'Слишком много попыток. Подождите.')
            db.execute('UPDATE auth_limits SET count=count+1 WHERE id=?',(key,))

    def register(self,telegram_id,username='',display_name=''):
        if not isinstance(telegram_id,int) or isinstance(telegram_id,bool) or telegram_id<=0:raise ValueError('Invalid identity')
        now=time.time()
        with self.transaction() as db:
            row=db.execute('SELECT * FROM auth_users WHERE telegram_user_id=?',(telegram_id,)).fetchone()
            if not row:
                admin=telegram_id in self.admin_ids
                db.execute('INSERT INTO auth_users VALUES (?,?,?,?,?,?,?,?,NULL)',
                    (secrets.token_hex(16),telegram_id,username[:100],display_name[:200],'ADMIN' if admin else 'CLIENT','ACTIVE' if admin else 'PENDING',now,now))
            else:db.execute('UPDATE auth_users SET username=?,display_name=?,updated_at=? WHERE id=?',(username[:100],display_name[:200],now,row['id']))
            if row and row['status']=='PENDING' and telegram_id in self.admin_ids:
                db.execute("UPDATE auth_users SET role='ADMIN',status='ACTIVE' WHERE id=?",(row['id'],))
            return dict(db.execute('SELECT * FROM auth_users WHERE telegram_user_id=?',(telegram_id,)).fetchone())

    def user(self,telegram_id):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM auth_users WHERE telegram_user_id=?',(telegram_id,)).fetchone()
            return dict(row) if row else None

    def create_code(self,peer,old_nonce=None):
        self.rate('codes:'+peer,3);self.rate('codes-global',60)
        nonce=secrets.token_urlsafe(32);code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(10))
        with self.transaction() as db:
            db.execute('DELETE FROM auth_codes WHERE expires<? OR nonce_hash=?',(time.time(),digest(old_nonce or '')))
            db.execute('INSERT INTO auth_codes(nonce_hash,code_hash,expires) VALUES (?,?,?)',(digest(nonce),digest(code),time.time()+CODE_TTL))
        return nonce,code

    def login(self,telegram_id,code):
        self.rate('tg-login:'+str(telegram_id),5,120);self.rate('tg-login-global',100,120)
        valid=False
        with self.transaction() as db:
            user=db.execute("SELECT * FROM auth_users WHERE telegram_user_id=? AND status='ACTIVE'",(telegram_id,)).fetchone()
            row=db.execute('SELECT * FROM auth_codes WHERE code_hash=?',(digest(code.upper()),)).fetchone()
            if row and row['expires']>time.time() and not row['consumed'] and row['user_id'] is None and row['attempts']<5:
                db.execute('UPDATE auth_codes SET attempts=attempts+1 WHERE nonce_hash=?',(row['nonce_hash'],))
                if user:
                    db.execute('UPDATE auth_codes SET user_id=? WHERE nonce_hash=?',(user['id'],row['nonce_hash']));valid=True
        return valid

    def poll(self,nonce,peer):
        self.rate('poll-ip:'+peer,120)
        self.rate('poll:'+peer+':'+digest(nonce or ''),40)
        if not nonce:return None
        with self.transaction() as db:
            now=time.time()
            row=db.execute('SELECT * FROM auth_codes WHERE nonce_hash=?',(digest(nonce),)).fetchone()
            if not row or row['expires']<=now or row['consumed']:raise HTTPException(410,'Код истёк. Получите новый код.')
            if not row['user_id']:return None
            user=db.execute("SELECT * FROM auth_users WHERE id=? AND status='ACTIVE'",(row['user_id'],)).fetchone()
            if not user:raise HTTPException(403,'Доступ не разрешён.')
            db.execute('DELETE FROM auth_sessions WHERE expires<=? OR revoked=1',(now,))
            count=db.execute('SELECT count(*) FROM auth_sessions WHERE user_id=?',(user['id'],)).fetchone()[0]
            total=db.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]
            if count>=self.cfg.max_sessions_per_user or total>=self.cfg.max_sessions:raise HTTPException(429,'Лимит сессий. Попросите администратора отозвать старые сессии.')
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?,0)',(digest(token),user['id'],now+self.cfg.session_ttl))
            db.execute('UPDATE auth_codes SET consumed=1 WHERE nonce_hash=?',(digest(nonce),))
            db.execute('UPDATE auth_users SET last_login_at=? WHERE id=?',(now,user['id']))
            return token

    def authenticate(self,token):
        if not token:raise HTTPException(401,'Требуется вход через Telegram.')
        with self.transaction() as db:
            row=db.execute("SELECT u.* FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked=0 AND s.expires>? AND u.status='ACTIVE'",(digest(token),time.time())).fetchone()
            if not row:raise HTTPException(401,'Сессия завершена или доступ отозван.')
            return dict(row)

    def logout(self,token):
        with self.transaction() as db:db.execute('UPDATE auth_sessions SET revoked=1 WHERE token_hash=?',(digest(token or ''),))

    def list_users(self,actor,status=None):
        self.require_admin(actor)
        with self.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM auth_users WHERE (? IS NULL OR status=?) ORDER BY created_at DESC',(status,status))]

    def require_admin(self,actor):
        with self.transaction() as db:
            row=db.execute("SELECT id FROM auth_users WHERE id=? AND role='ADMIN' AND status='ACTIVE'",(actor['id'],)).fetchone()
            if not row:raise HTTPException(403,'Требуются права администратора.')

    def change(self,actor,target,action,value=None):
        with self.transaction() as db:
            admin=db.execute("SELECT * FROM auth_users WHERE id=? AND status='ACTIVE' AND role='ADMIN'",(actor['id'],)).fetchone()
            if not admin:raise HTTPException(403,'Требуются права администратора.')
            row=db.execute('SELECT * FROM auth_users WHERE id=?',(target,)).fetchone()
            if not row:raise HTTPException(404)
            role,status=row['role'],row['status']
            if action=='approve' and status=='PENDING':status='ACTIVE'
            elif action=='block':status='BLOCKED'
            elif action=='unblock' and status=='BLOCKED':status='ACTIVE'
            elif action=='role' and value in ('ADMIN','CLIENT'):role=value
            elif action!='revoke':raise HTTPException(400,'Недопустимое действие для этого статуса.')
            if row['role']=='ADMIN' and row['status']=='ACTIVE' and (role!='ADMIN' or status!='ACTIVE'):
                if db.execute("SELECT count(*) FROM auth_users WHERE role='ADMIN' AND status='ACTIVE'").fetchone()[0]<=1:raise HTTPException(409,'Нельзя отключить последнего активного администратора.')
            db.execute('UPDATE auth_users SET role=?,status=?,updated_at=? WHERE id=?',(role,status,time.time(),target))
            if action in ('block','revoke','role'):
                db.execute('UPDATE auth_sessions SET revoked=1 WHERE user_id=?',(target,))
                db.execute('DELETE FROM auth_codes WHERE user_id=?',(target,))
            if action in ('approve','unblock'):
                db.execute('INSERT INTO telegram_outbox VALUES (?,?,?,?,0)',(secrets.token_hex(16),row['telegram_user_id'],'Доступ к консультанту одобрен. Откройте /consult и отправьте боту /login с кодом из браузера.',time.time()))
        return {'status':status,'role':role}
