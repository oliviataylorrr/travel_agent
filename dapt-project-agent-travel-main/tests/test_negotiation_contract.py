"""Regression checks for consulting-service fee semantics and detailed failures."""
import asyncio
import pytest
from app import agent,db
from app.schema import init_db

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'student.sqlite3')
    init_db()


def test_contract_reject_wrong_accept_or_reject_and_counter_at_same_price():
    for decision,price in [('accept','10.00'),('reject','10.00'),('counter','3.50'),('counter','0')]:
        with pytest.raises(ValueError):
            agent.validate_negotiation_response({'decision':decision,'price_usd':price,'reason':'Reason'},350)
    assert agent.validate_negotiation_response({'decision':'reject','price_usd':'3.50','reason':'Below minimum'},350)['decision']=='reject'


def test_lodging_prompt_separates_fee_and_client_consulting_budget(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'lodging1','name':'Lodging Agent','service_type':'lodging','posted_price_usd':'3.50'})
    async def public(path):
        return {'transactions':[]} if 'history' in path else {'agents':[{'agent_id':'lodging1','reputation':50}]}
    monkeypatch.setattr(agent,'public_market',public)
    prompts=[];sys=[]
    def fake_decide(system,prompt):
        sys.append(system);prompts.append(prompt)
        return {'decision':'accept','price_usd':prompt['offered_service_fee_usd'],'reason':'Consulting fee is fair'}
    monkeypatch.setattr(agent,'decide',fake_decide)
    data={'action':'invite','job_id':'JOB-A','job':{'budget_usd':'10.00','origin':'Dallas'},
      'ensemble_id':'ENS-A','lead_id':'travel1','offer_usd':'3.50','offered_service_fee_usd':'3.50',
      'client_consulting_budget_usd':'10.00','round':1,'history':[],'negotiation_id':'NEG-A'}
    answer=asyncio.run(agent.invite(data))
    assert answer['decision']=='accept'
    assert prompts[0]['client_consulting_budget_usd']=='10.00'
    assert prompts[0]['offered_service_fee_usd']=='3.50'
    assert 'hotel' in prompts[0]['service_scope'].lower()
    assert 'do NOT book' in sys[0]


def test_rejected_confused_response_receives_corrective_feedback(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'lodging1','name':'Lodging Agent','service_type':'lodging','posted_price_usd':'3.50'})
    async def public(path):return {'transactions':[]} if 'history' in path else {'agents':[{'agent_id':'lodging1','reputation':50}]}
    monkeypatch.setattr(agent,'public_market',public)
    seen=[]
    def decide(system,prompt):
        seen.append(prompt)
        if len(seen)==1:
            return {'decision':'reject','price_usd':'10.00','reason':'Hotel too expensive'}
        return {'decision':'reject','price_usd':'3.50','reason':'Our service strategy declines this fee'}
    monkeypatch.setattr(agent,'decide',decide)
    ans=asyncio.run(agent.invite({'job_id':'JOB-A','job':{'budget_usd':'10.00'},'ensemble_id':'ENS-A',
        'lead_id':'travel1','offer_usd':'3.50','round':1,'history':[]}))
    assert ans['decision']=='reject' and ans['price_usd']=='3.50'
    assert 'reject must repeat' in seen[1]['validation_feedback']
    ev=db.read("SELECT * FROM trace_outbox WHERE event_type='negotiation_validation'")
    assert len(ev)==2


def test_specialist_counteroffer_acceptance_requires_exact_confirmation(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'lodging1','name':'Lodging','service_type':'lodging','posted_price_usd':'3.50'})
    async def public(path):return {'transactions':[]} if 'history' in path else {'agents':[{'agent_id':'lodging1','reputation':50}]}
    monkeypatch.setattr(agent,'public_market',public)
    monkeypatch.setattr(agent,'decide',lambda *a:{'decision':'counter','price_usd':'4.00','reason':'Premium service fee'})
    base={'job_id':'JOB-X','ensemble_id':'ENS-X','lead_id':'travel1','negotiation_id':'NEG-X'}
    asyncio.run(agent.invite({**base,'job':{'budget_usd':'10.00'},'offer_usd':'3.50','round':1,'history':[]}))
    with pytest.raises(ValueError):asyncio.run(agent.confirm_counter({**base,'agreed_service_fee_usd':'4.50'}))
    assert asyncio.run(agent.confirm_counter({**base,'agreed_service_fee_usd':'4.00'}))['decision']=='accept'
    with pytest.raises(ValueError):asyncio.run(agent.confirm_counter({**base,'agreed_service_fee_usd':'4.00'}))


def test_failed_formation_reports_actual_rejection(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'travel1','name':'Travel','service_type':'travel','posted_price_usd':'2.50'})
    async def public(path):return {'agents':[{'agent_id':'lodging1','url':'http://localhost:8000','service_type':'lodging','price_cents':350,'reputation':50,'active':1}]}
    async def market(method,path,payload=None):return {'reserved':True} if path.endswith('/claim') else {'released':True}
    async def peer(*args):return {'decision':'reject','price_usd':'3.50','reason':'Too low'}
    monkeypatch.setattr(agent,'public_market',public)
    monkeypatch.setattr(agent,'market_api',market)
    monkeypatch.setattr(agent,'send_peer',peer)
    result=asyncio.run(agent.start_bid({'job_id':'JOB-B','job':{'budget_usd':'10.00'}}))
    assert result['status']=='no_bid'
    assert result['failed_stage']=='lodging_negotiation_or_reservation'
    assert result['formation_diagnostics'][0]['candidates'][0]['outcome']=='not_agreed'
    assert db.one("SELECT COUNT(*) n FROM trace_outbox WHERE event_type='ensemble_formation_failed'")['n']==1


def test_travel_accepts_counter_with_confirmation_not_new_invite(monkeypatch):
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'travel1','name':'Travel','service_type':'travel','posted_price_usd':'2.50'})
    calls=[]
    async def peer(url,payload):
        calls.append(payload)
        if payload['action']=='invite':
            return {'decision':'counter','price_usd':'4.00','reason':'Premium advice','strategy_hash':'abc'}
        if payload['action']=='confirm_counter':
            return {'decision':'accept','price_usd':'4.00','reason':'Confirmed'}
        raise AssertionError(payload)
    monkeypatch.setattr(agent,'send_peer',peer)
    monkeypatch.setattr(agent,'decide',lambda system,prompt:{'decision':'accept','price_usd':'4.00','reason':'Agree'})
    candidate={'agent_id':'lodging1','url':'https://example.org','service_type':'lodging','price_cents':350,'active':1,'reputation':50}
    result=asyncio.run(agent.negotiate_with_agent(candidate,'JOB-X',{'budget_usd':'10.00'},'ENS-X'))
    assert result['price_cents']==400 and result['strategy_hash']=='abc'
    assert [p['action'] for p in calls]==['invite','confirm_counter']
