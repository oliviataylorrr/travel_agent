import json,pytest
from app import db,agent
from app.schema import init_db
@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'student.sqlite3')
    init_db()

def test_cents():
    assert agent.to_cents('2.50')==250
    assert agent.usd(375)=='3.75'
    for bad in ['-1','2.555','NaN','oops']:
        with pytest.raises(ValueError):agent.to_cents(bad)

def test_payment_mode_is_simulated():
    from app.payment import SimulatedSettlement
    r=SimulatedSettlement().settle('2.50','0xabc')
    assert r.status=='simulated' and r.reference.startswith('SIM-')

def test_payment_mode_onchain_requires_implementation():
    from app.payment import BaseSepoliaSettlement
    with pytest.raises(NotImplementedError):BaseSepoliaSettlement().settle('1.00','0xabc')

def test_record_business_task():
    agent.record_task('JOB-A','invite',{'offer_usd':'2.00'},{'decision':'accept'})
    row=db.one('SELECT * FROM tasks')
    assert row['job_id']=='JOB-A' and json.loads(row['result_json'])['decision']=='accept'

def test_reject_mismatched_accept_price(monkeypatch):
    async def fake_public(url):return {'transactions':[]} if '/api/history?' in url else {'agents':[{'agent_id':'mine','reputation':50}]}
    monkeypatch.setattr(agent,'public_market',fake_public)
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'mine','name':'test','service_type':'lodging','posted_price_usd':'2.50'})
    monkeypatch.setattr(agent,'decide',lambda *args,**kw:{'decision':'accept','price_usd':'3.00','reason':'test'})
    import asyncio
    with pytest.raises(ValueError):asyncio.run(agent.invite({'job_id':'JOB-1','ensemble_id':'ENS-1','lead_id':'lead',
                                        'round':1,'offer_usd':'2.50','job':{'role':'x'},'history':[]}))

def test_role_guard(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'service_type':'travel','name':'lead'})
    import asyncio
    with pytest.raises(ValueError):asyncio.run(agent.invite({'job_id':'JOB-X'}))

def test_research_schema_validation(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'service_type':'mineral','name':'Mineral','agent_id':'mineral-1'})
    monkeypatch.setattr(agent,'decide',lambda *a,**kw:{'mineral':'Lithium','chemical_facts':[],'commercial_facts':[]})
    import asyncio
    response=asyncio.run(agent.fulfill({'job_id':'JOB','job':{'mineral':'Lithium'},'service_type':'mineral'}))
    assert response['content']['mineral']=='Lithium'
