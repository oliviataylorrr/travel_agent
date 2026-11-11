"""Student chatbot reads its own records without changing negotiation state."""
from app import db
from app.schema import init_db
from app.chat_context import student_context

def test_student_context_read_only(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'student.sqlite')
    init_db()
    with db.transaction() as c:
        c.execute('INSERT INTO tasks(id,created_at,job_id,kind,status,input_json,result_json,error) VALUES(?,?,?,?,?,?,?,?)',
                  ('T-1','2026-11-09T10:00:00+00:00','JOB-XYZ','negotiation','completed','{}','{}',''))
        c.execute('INSERT INTO payments(payment_id,job_id,kind,amount_cents,recipient,status,tx_reference,created_at) VALUES(?,?,?,?,?,?,?,?)',
                  ('P-1','JOB-XYZ','simulation',300,'student','simulated','SIM-1','2026-11-09T10:00:00+00:00'))
    s=student_context('Explain JOB-XYZ')
    assert 'JOB-XYZ' in s and '"amount_cents": 300' in s
    assert db.one('SELECT COUNT(*) n FROM tasks')['n']==1
