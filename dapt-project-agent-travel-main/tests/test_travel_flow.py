import asyncio,pytest
from app import agent,db
from app.schema import init_db
@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'student.sqlite3')
    init_db()

def test_travel_submits_complete_bid(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'travel1','name':'Travel 1','service_type':'travel','posted_price_usd':'2.50'})
    roster=[{'agent_id':s,'url':'http://localhost:8000','service_type':s,'price_cents':250,'reputation':50,'active':1} for s in ('lodging','interview','mineral')]
    async def fake_public(path):return {'agents':roster}
    async def fake_market(method,path,payload=None):
        if path.endswith('/claim'):return {'reserved':True}
        if path.endswith('/bids'):
            assert len(payload['participants'])==4
            assert sum(x['price_cents'] for x in payload['participants'])==1000
            return {'bid_id':'BID1','total_usd':'10.00'}
        raise AssertionError(path)
    async def fake_peer(url,payload):return {'decision':'accept','price_usd':'2.50','reason':'Accepted the consulting fee'}
    monkeypatch.setattr(agent,'public_market',fake_public)
    monkeypatch.setattr(agent,'market_api',fake_market)
    monkeypatch.setattr(agent,'send_peer',fake_peer)
    r=asyncio.run(agent.start_bid({'job_id':'JOB-1','job':{'mineral':'Lithium'}}))
    assert r['status']=='bid_submitted' and r['total_usd']=='10.00'

def test_interview_assembles_and_distributes(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'interview1','name':'Interview','service_type':'interview','posted_price_usd':'2.50'})
    seen=[]
    async def fake_market(method,path,payload=None):
        seen.append((path,payload))
        if path=='/internal/package':return {'accepted':True}
        if path=='/internal/payments':return {'status':'simulated','payment_id':'PAY-'+payload['recipient'],'reference':'SIM-X'}
        raise AssertionError(path)
    monkeypatch.setattr(agent,'market_api',fake_market)
    content={k:{'research':'ok'} for k in agent.SERVICES}
    result=asyncio.run(agent.assemble({'job_id':'JOB-1','ensemble_id':'ENS-1','content':content}))
    assert result['posted']
    participants=[{'agent_id':'interview1' if s=='interview' else s,'service_type':s,'price_cents':250} for s in agent.SERVICES]
    result=asyncio.run(agent.settle({'job_id':'JOB-1','ensemble_id':'ENS-1','participants':participants}))
    assert result['distributed'] and len(result['receipts'])==4
    assert sum(x['amount_cents'] for x in db.read('SELECT * FROM payments'))==1000
