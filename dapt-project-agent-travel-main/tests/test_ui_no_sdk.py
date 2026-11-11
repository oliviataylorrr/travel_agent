import importlib
from app import db,auth,a2a_bridge,agent
from app.schema import init_db
from app.auth import password_hash
class StubStore:
    async def initialize(self):pass
class StubEngine:
    async def dispose(self):pass

def test_student_login_dashboard(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'test_ui.sqlite3')
    monkeypatch.setattr(auth,'SESSION_SECRET_KEY','this-is-a-long-test-session-key-123456')
    monkeypatch.setattr(agent,'profile',lambda:{'agent_id':'student1','name':'Student 1','service_type':'mineral',
        'description':'Researches minerals','posted_price_usd':'2.50'})
    monkeypatch.setattr(a2a_bridge,'attach_sdk',lambda *args,**kw:(StubEngine(),StubStore()))
    main=importlib.import_module('app.main')
    monkeypatch.setattr(main,'profile',agent.profile)
    init_db()
    with db.transaction() as c:c.execute('INSERT INTO users(username,password_hash) VALUES(?,?)',('student',password_hash('classroom-password')))
    with TestClient(main.app, base_url="https://testserver") as client:
        assert client.get('/login').status_code==200
        assert client.post('/login',data={'username':'student','password':'classroom-password'},follow_redirects=False).status_code==303
        res=client.get('/')
        assert res.status_code==200
        assert 'Student 1' in res.text
