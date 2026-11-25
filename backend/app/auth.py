"""Database-backed opaque cookie sessions and role authorization."""
from datetime import datetime, timedelta
import hashlib, hmac, os, secrets, threading
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, delete, func, text
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, UserSession, RateLimitEvent

COOKIE=os.getenv('SESSION_COOKIE_NAME','fieldnote_session')
CSRF_COOKIE=os.getenv('CSRF_COOKIE_NAME','fieldnote_csrf')
SESSION_HOURS=int(os.getenv('SESSION_HOURS','8'))
COOKIE_SECURE=os.getenv('COOKIE_SECURE','false').lower()=='true'
ph=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
MAX_FAILURES=8; WINDOW=900

def password_hash(password:str)->str:
    if len(password)<12 or len(password)>256: raise ValueError('Password must be 12 to 256 characters.')
    return ph.hash(password)
def verify_password(encoded:str,password:str)->bool:
    try:return ph.verify(encoded,password)
    except (VerifyMismatchError,InvalidHashError,ValueError):return False
def _digest(value:str)->str:return hashlib.sha256(value.encode()).hexdigest()
def create_session(response:Response,user:User,request:Request,db:Session):
    raw=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32); now=datetime.utcnow()
    row=UserSession(user_id=user.user_id,token_hash=_digest(raw),csrf_hash=_digest(csrf),created_at=now,expires_at=now+timedelta(hours=SESSION_HOURS),user_agent=(request.headers.get('user-agent') or '')[:512],client_ip=request.client.host if request.client else None)
    db.add(row); db.commit()
    common={'httponly':True,'secure':COOKIE_SECURE,'samesite':'lax','path':'/'}
    response.set_cookie(COOKIE,raw,max_age=SESSION_HOURS*3600,**common)
    response.set_cookie(CSRF_COOKIE,csrf,max_age=SESSION_HOURS*3600,httponly=False,secure=COOKIE_SECURE,samesite='lax',path='/')

def clear_session_cookies(response:Response):
    response.delete_cookie(COOKIE,path='/',secure=COOKIE_SECURE,httponly=True,samesite='lax')
    response.delete_cookie(CSRF_COOKIE,path='/',secure=COOKIE_SECURE,httponly=False,samesite='lax')

def current_user(request:Request,db:Session=Depends(get_db))->User:
    raw=request.cookies.get(COOKIE)
    if not raw:raise HTTPException(401,'Authentication required')
    row=db.scalar(select(UserSession).where(UserSession.token_hash==_digest(raw),UserSession.revoked_at.is_(None),UserSession.expires_at>datetime.utcnow()))
    if not row:raise HTTPException(401,'Authentication required')
    user=db.get(User,row.user_id)
    if not user or not user.is_active:raise HTTPException(401,'Authentication required')
    return user

def require_csrf(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    cookie=request.cookies.get(CSRF_COOKIE,''); submitted=request.headers.get('X-CSRF-Token','')
    if not cookie or not submitted or not hmac.compare_digest(cookie,submitted):raise HTTPException(403,'CSRF validation failed')
    session=db.scalar(select(UserSession).where(UserSession.token_hash==_digest(request.cookies.get(COOKIE,'')),UserSession.user_id==user.user_id,UserSession.revoked_at.is_(None)))
    if not session or not hmac.compare_digest(session.csrf_hash,_digest(submitted)):raise HTTPException(403,'CSRF validation failed')
    return user

def roles(*allowed):
    def check(user:User=Depends(require_csrf)):
        if user.must_change_password:raise HTTPException(403,'Change your temporary password before using this operation')
        if user.role not in allowed:raise HTTPException(403,'Insufficient permissions')
        return user
    return check

_rate_lock=threading.Lock()

def _rate_key(value:str)->str:return _digest(value)

def consume_rate_limit(db:Session,scope:str,key:str,maximum:int,window:int)->None:
    """Shared PostgreSQL throttling; SQLite keeps the same policy for local tests/dev."""
    hashed=_rate_key(key); now=datetime.utcnow(); cutoff=now-timedelta(seconds=window)
    if db.bind and db.bind.dialect.name=='postgresql':
        lock_id=int.from_bytes(hashlib.sha256(f'{scope}:{hashed}'.encode()).digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:lock_id)'),{'lock_id':lock_id})
    else:
        _rate_lock.acquire()
    try:
        db.execute(delete(RateLimitEvent).where(RateLimitEvent.occurred_at<cutoff))
        count=db.scalar(select(func.count()).select_from(RateLimitEvent).where(RateLimitEvent.scope==scope,RateLimitEvent.key_hash==hashed,RateLimitEvent.occurred_at>=cutoff)) or 0
        if count>=maximum:
            db.rollback()
            raise HTTPException(429,'Too many requests. Try again later.')
        db.add(RateLimitEvent(scope=scope,key_hash=hashed,occurred_at=now));db.commit()
    finally:
        if not (db.bind and db.bind.dialect.name=='postgresql'):_rate_lock.release()

def clear_rate_limit(db:Session,scope:str,key:str)->None:
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.scope==scope,RateLimitEvent.key_hash==_rate_key(key)));db.commit()

def login_rate_limit(request:Request,db:Session=Depends(get_db)):
    key=request.client.host if request.client else 'unknown'
    cutoff=datetime.utcnow()-timedelta(seconds=WINDOW)
    count=db.scalar(select(func.count()).select_from(RateLimitEvent).where(RateLimitEvent.scope=='login',RateLimitEvent.key_hash==_rate_key(key),RateLimitEvent.occurred_at>=cutoff)) or 0
    if count>=MAX_FAILURES:raise HTTPException(429,'Too many login attempts. Try again later.')
    return key

def protected_diagnostics(user:User=Depends(current_user)):
    if user.must_change_password:raise HTTPException(403,'Change your temporary password before using this operation')
    return user

def require_diagnostic_role(*allowed):
    def check(user:User=Depends(protected_diagnostics)):
        if user.role not in allowed:raise HTTPException(403,'Insufficient permissions')
        return user
    return check
