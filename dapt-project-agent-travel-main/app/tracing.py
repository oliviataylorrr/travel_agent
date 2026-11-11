"""Persistent local A2A event outbox and immutable negotiation strategy snapshots."""
import asyncio,hashlib,json,re,uuid,logging
import httpx
from .db import transaction,read,one,utcnow
from .settings import MARKETPLACE_URL,A2A_SHARED_SECRET
from .llm import instructions
log=logging.getLogger(__name__)
BLOCKED={'authorization','cookie','set_cookie','password','wallet_password','private_key','privatekey','secret','api_key','access_token','refresh_token','seed','mnemonic','keystore','client_name','name_of_client','email','phone','contact'}

def scrub(obj,depth=0):
    if depth>18:return '[depth limit]'
    if isinstance(obj,dict):
        return {str(k)[:100]:('[REDACTED]' if str(k).lower().replace('-','_') in BLOCKED else scrub(v,depth+1)) for k,v in list(obj.items())[:120]}
    if isinstance(obj,list):return [scrub(x,depth+1) for x in obj[:250]]
    if isinstance(obj,str):
        value=obj[:24000]
        return re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}',r'\1[REDACTED]',value)
    return obj if obj is None or isinstance(obj,(int,float,bool)) else str(obj)[:300]

def snapshot(job_id,ensemble_id,negotiation_id):
    if not all((job_id,ensemble_id,negotiation_id)):raise ValueError('Strategy snapshot needs job, ensemble, negotiation IDs')
    params=(job_id,ensemble_id,negotiation_id)
    row=one('SELECT * FROM strategy_snapshots WHERE job_id=? AND ensemble_id=? AND negotiation_id=?',params)
    if row:return {'hash':row['strategy_hash'],'text':row['strategy_text']}
    text=instructions();digest=hashlib.sha256(text.encode()).hexdigest()
    with transaction() as c:
        c.execute('INSERT OR IGNORE INTO strategy_snapshots(job_id,ensemble_id,negotiation_id,strategy_hash,strategy_text,created_at) VALUES(?,?,?,?,?,?)',(*params,digest,text,utcnow()))
    row=one('SELECT strategy_hash,strategy_text FROM strategy_snapshots WHERE job_id=? AND ensemble_id=? AND negotiation_id=?',params)
    return {'hash':row['strategy_hash'],'text':row['strategy_text']}

def record(event_type,payload,*,job_id=None,ensemble_id=None,negotiation_id=None,trace_id=None,sender=None,recipient=None,task_id=None,context_id=None,strategy_hash=None):
    if not job_id and isinstance(payload,dict):job_id=payload.get('job_id')
    if not job_id:return None
    from .agent import profile
    event_id='EVT-'+uuid.uuid4().hex
    encoded=json.dumps(scrub(payload),ensure_ascii=False,default=str)
    if len(encoded)>100000:encoded=json.dumps({'warning':'message omitted: exceeds 100KB'})
    with transaction() as c:
        c.execute('INSERT INTO trace_outbox(event_id,created_at,job_id,ensemble_id,negotiation_id,trace_id,event_type,sender,recipient,task_id,context_id,strategy_hash,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (event_id,utcnow(),job_id,ensemble_id,negotiation_id,trace_id or job_id,event_type,sender or profile()['agent_id'],recipient or '',task_id,context_id,strategy_hash,encoded))
    return event_id

async def flush_once(limit=40):
    if not MARKETPLACE_URL:return 0
    rows=await asyncio.to_thread(read,'SELECT * FROM trace_outbox WHERE delivered=0 ORDER BY created_at,event_id LIMIT ?',(limit,))
    sent=0
    if not rows:return sent
    async with httpx.AsyncClient(timeout=18,follow_redirects=False,headers={'Authorization':'Bearer '+A2A_SHARED_SECRET}) as client:
        for r in rows:
            item={k:r[k] for k in ('event_id','created_at','job_id','ensemble_id','negotiation_id','trace_id','event_type','sender','recipient','task_id','context_id','strategy_hash')}
            item['payload']=json.loads(r['payload_json'])
            try:
                resp=await client.post(MARKETPLACE_URL+'/internal/trace-events',json=item)
                resp.raise_for_status()
                def ack():
                    with transaction() as c:c.execute("UPDATE trace_outbox SET delivered=1,last_error='' WHERE event_id=?",(r['event_id'],))
                await asyncio.to_thread(ack);sent+=1
            except Exception as exc:
                def fail():
                    with transaction() as c:c.execute('UPDATE trace_outbox SET attempts=attempts+1,last_error=? WHERE event_id=?',(str(exc)[:300],r['event_id']))
                await asyncio.to_thread(fail)
                log.warning('Trace delivery deferred: %s',str(exc)[:150]);break
    return sent

async def flush_loop(stop):
    while not stop.is_set():
        try:await flush_once()
        except Exception:log.exception('Trace delivery loop failed')
        try:await asyncio.wait_for(stop.wait(),timeout=6)
        except asyncio.TimeoutError:pass
