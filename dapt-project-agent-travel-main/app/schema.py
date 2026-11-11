from .db import transaction
DDL='''
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,username TEXT NOT NULL,csrf TEXT NOT NULL,last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,job_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,input_json TEXT NOT NULL,result_json TEXT,error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_student_tasks_job ON tasks(job_id);
CREATE TABLE IF NOT EXISTS invitations(job_id TEXT NOT NULL,ensemble_id TEXT NOT NULL,lead_id TEXT NOT NULL,offer_cents INTEGER NOT NULL,round_number INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(job_id,ensemble_id,round_number));
CREATE TABLE IF NOT EXISTS payments(payment_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,kind TEXT NOT NULL,amount_cents INTEGER NOT NULL,recipient TEXT NOT NULL,status TEXT NOT NULL,tx_reference TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payout_outbox(payment_id TEXT PRIMARY KEY REFERENCES payments(payment_id), attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL,created_at TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_usage(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,model TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_snapshots(job_id TEXT NOT NULL,ensemble_id TEXT NOT NULL,negotiation_id TEXT NOT NULL,strategy_hash TEXT NOT NULL,strategy_text TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(job_id,ensemble_id,negotiation_id));
CREATE TABLE IF NOT EXISTS trace_outbox(event_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,job_id TEXT NOT NULL,ensemble_id TEXT,negotiation_id TEXT,trace_id TEXT,event_type TEXT NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,task_id TEXT,context_id TEXT,strategy_hash TEXT,payload_json TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_trace_outbox_pending ON trace_outbox(delivered,created_at);
CREATE INDEX IF NOT EXISTS ix_trace_outbox_job ON trace_outbox(job_id,created_at);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,kind TEXT NOT NULL,job_id TEXT,agent_id TEXT,detail TEXT NOT NULL);
'''
def init_db():
    with transaction() as c:
        for statement in DDL.split(';'):
            if statement.strip():c.execute(statement)
