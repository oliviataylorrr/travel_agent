"""Login via password hashes and independent sessions (no one-session lockout)."""
import secrets,hashlib,hmac,base64
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException,Request
from .db import transaction,one,utcnow
from .settings import SESSION_SECRET_KEY
from itsdangerous import URLSafeSerializer,BadSignature

def cookie_signer():
    if len(SESSION_SECRET_KEY)<24:raise RuntimeError('Configure SESSION_SECRET_KEY (at least 24 characters)')
    return URLSafeSerializer(SESSION_SECRET_KEY,salt='dapt-login-cookie')
def sign_sid(sid):return cookie_signer().dumps(sid)
def raw_sid(cookie):
    try:return cookie_signer().loads(cookie) if cookie else None
    except BadSignature:return None

def password_hash(password):
    salt=secrets.token_bytes(16)
    dk=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return 'scrypt$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(dk).decode()
def check_password(password,encoded):
    try:
        algorithm,salt,digest=encoded.split('$')
        if algorithm!='scrypt':return False
        actual=hashlib.scrypt(password.encode(),salt=base64.urlsafe_b64decode(salt),n=2**14,r=8,p=1)
        return hmac.compare_digest(actual,base64.urlsafe_b64decode(digest))
    except Exception:return False

def login(username,password):
    user=one('SELECT * FROM users WHERE username=?',(username.strip().lower(),))
    if not user or not check_password(password,user['password_hash']):return None
    sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    with transaction() as c:c.execute('INSERT INTO sessions(id,username,csrf,last_seen) VALUES(?,?,?,?)',(sid,user['username'],csrf,utcnow()))
    return sid

def session(request:Request):
    sid=raw_sid(request.cookies.get('agent_session'))
    if not sid:return None
    found=one('SELECT * FROM sessions WHERE id=?',(sid,))
    if not found:return None
    try:
        last=datetime.fromisoformat(found['last_seen'])
        if datetime.now(timezone.utc)-last>timedelta(minutes=60):
            execute_logout(sid);return None
    except Exception:return None
    with transaction() as c:c.execute('UPDATE sessions SET last_seen=? WHERE id=?',(utcnow(),sid))
    return found

def execute_logout(sid):
    with transaction() as c:c.execute('DELETE FROM sessions WHERE id=?',(sid,))

def require_session(request:Request):
    s=session(request)
    if not s:raise HTTPException(status_code=401,detail='Sign in required')
    return s

def check_csrf(s,token):
    if not hmac.compare_digest(s['csrf'],token or ''):raise HTTPException(403,'Invalid CSRF token')
