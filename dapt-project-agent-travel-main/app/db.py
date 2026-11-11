"""Single-writer persistent SQLite store. Write transactions never span network calls."""
import json,sqlite3,threading,time
from contextlib import contextmanager
from datetime import datetime,timezone
from .settings import DB_PATH
_lock=threading.RLock()
def utcnow(): return datetime.now(timezone.utc).isoformat()
def connect():
    cn=sqlite3.connect(str(DB_PATH),timeout=40,check_same_thread=False,isolation_level=None)
    cn.row_factory=sqlite3.Row
    cn.execute('PRAGMA busy_timeout=40000')
    cn.execute('PRAGMA foreign_keys=ON')
    return cn
@contextmanager
def transaction():
    with _lock:
        for n in range(5):
            cn=connect()
            try:
                cn.execute('BEGIN IMMEDIATE')
                break
            except sqlite3.OperationalError as ex:
                cn.close()
                if 'locked' not in str(ex).lower() or n==4: raise
                time.sleep(0.2*(n+1))
        try:
            yield cn
            cn.commit()
        except Exception:
            cn.rollback();raise
        finally:cn.close()
def read(sql, params=()):
    cn=connect()
    try:return [dict(x) for x in cn.execute(sql,params).fetchall()]
    finally:cn.close()
def one(sql,params=()):
    r=read(sql,params)
    return r[0] if r else None
def execute(sql,params=()):
    with transaction() as cn:return cn.execute(sql,params).rowcount
def event(kind,detail,job_id=None,agent_id=None):
    execute('INSERT INTO events(created_at,kind,job_id,agent_id,detail) VALUES(?,?,?,?,?)',(utcnow(),kind,job_id,agent_id,json.dumps(detail,default=str)))
