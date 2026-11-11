import asyncio,json,hashlib
from pathlib import Path
import pytest

def configured(monkeypatch,tmp_path):
    from app import db,schema,tracing
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'student.sqlite3')
    schema.init_db()
    monkeypatch.setattr(tracing,'MARKETPLACE_URL','https://market.example')
    monkeypatch.setattr(tracing,'A2A_SHARED_SECRET','class-test-secret-here')
    monkeypatch.setattr(tracing,'instructions',lambda:'Premium negotiation: only top 20%')
    monkeypatch.setattr('app.agent.profile',lambda:{'agent_id':'lodging-7'})
    return db,tracing

def test_snapshot_is_immutable(monkeypatch,tmp_path):
    db,t=configured(monkeypatch,tmp_path)
    a=t.snapshot('JOB-1','ENS-1','NEG-1')
    assert a['hash']==hashlib.sha256(a['text'].encode()).hexdigest()
    monkeypatch.setattr(t,'instructions',lambda:'Take the lowest price')
    assert t.snapshot('JOB-1','ENS-1','NEG-1')==a
    b=t.snapshot('JOB-2','ENS-2','NEG-2')
    assert b['hash']!=a['hash']
    assert db.one('SELECT COUNT(*) n FROM strategy_snapshots')['n']==2

def test_a2a_outbox_and_redaction(monkeypatch,tmp_path):
    db,t=configured(monkeypatch,tmp_path)
    eid=t.record('a2a_outbound_message',{'job_id':'JOB-1','authorization':'Bearer some-secret-value-123456','wallet_password':'x', 'action':'invite'},job_id='JOB-1',ensemble_id='ENS-1',recipient='lodging-2',task_id='TASK-1',context_id='CTX-1')
    row=db.one('SELECT * FROM trace_outbox WHERE event_id=?',(eid,))
    assert row['delivered']==0 and row['task_id']=='TASK-1'
    payload=json.loads(row['payload_json'])
    assert payload['authorization']=='[REDACTED]' and payload['wallet_password']=='[REDACTED]'
    assert t.record('chat',{'question':'hello'}) is None

def test_deferred_trace_delivered_once(monkeypatch,tmp_path):
    import httpx
    db,t=configured(monkeypatch,tmp_path)
    t.record('a2a_outbound_message',{'action':'invite'},job_id='JOB-2',recipient='agent2')
    captured=[]
    def responder(request):
        captured.append(json.loads(request.content))
        if len(captured)==1:return httpx.Response(503,json={'error':'temporary'})
        return httpx.Response(200,json={'accepted':True})
    orig=httpx.AsyncClient
    monkeypatch.setattr(t.httpx,'AsyncClient',lambda **kwargs:orig(transport=httpx.MockTransport(responder),**kwargs))
    assert asyncio.run(t.flush_once())==0
    assert db.one('SELECT attempts n FROM trace_outbox')['n']==1
    assert asyncio.run(t.flush_once())==1
    assert db.one('SELECT delivered n FROM trace_outbox')['n']==1
    assert asyncio.run(t.flush_once())==0
    assert captured[0]['event_id']==captured[1]['event_id']

def test_invitation_uses_locked_strategy_and_reports_hash(monkeypatch,tmp_path):
    db,tr=configured(monkeypatch,tmp_path)
    from app import agent
    async def pub(path):
        if path.startswith('/api/history'):return {'transactions':[]}
        return {'agents':[{'agent_id':'lodging-7','reputation':60}]}
    monkeypatch.setattr(agent,'public_market',pub)
    monkeypatch.setattr(agent,'decide',lambda system,prompt:{'decision':'accept','price_usd':prompt['offer_usd'],'reason':'test'})
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'lodging-7','name':'Lodging 7','service_type':'lodging','posted_price_usd':'2.00'})
    msg={'job_id':'JOB-1','ensemble_id':'ENS-1','lead_id':'TRAVEL-1','job':{'mineral':'Lithium'},'offer_usd':'2.00','round':1,'history':[]}
    first=asyncio.run(agent.invite(msg))
    monkeypatch.setattr(tr,'instructions',lambda:"Another week's completely different strategy")
    msg['round']=2
    second=asyncio.run(agent.invite(msg))
    assert first['strategy_hash']==second['strategy_hash']
    assert db.one('SELECT COUNT(*) n FROM strategy_snapshots')['n']==1
    assert db.one('SELECT COUNT(*) n FROM trace_outbox WHERE event_type=?',('negotiation_decision',))['n']==2
