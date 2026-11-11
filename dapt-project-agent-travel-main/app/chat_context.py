"""Read-only, bounded SQLite context for the authenticated OpenAI chatbot.

Only curated columns are sent to the model. Credentials, session cookies, private keys,
full client profiles and raw A2A payloads are deliberately excluded.
"""
import json
import re
from datetime import datetime,timedelta,timezone
from .db import read,one


def _recent(sql,params=(),limit=15):
    # SQL is static and curated by the application, never authored by a user or LLM.
    return read(sql,params)[:limit]


def _json(data,limit=22000):
    return json.dumps(data,ensure_ascii=False,default=str)[:limit]


def _tail(history,limit=12):
    return [{'role':x['role'],'content':x['content'][:2500]} for x in history[-limit:]]


def student_context(question):
    since=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    summary={
        'task_statuses':read('SELECT kind,status,COUNT(*) n FROM tasks GROUP BY kind,status'),
        'payment_totals':one('SELECT COUNT(*) n, COALESCE(SUM(amount_cents),0) amount_cents FROM payments'),
        'last_7_days_payment_totals':one('SELECT COUNT(*) n, COALESCE(SUM(amount_cents),0) amount_cents FROM payments WHERE created_at>=?',(since,)),
        'invitation_outcomes':read('SELECT status,COUNT(*) n FROM invitations GROUP BY status'),
        'trace_delivery':one('SELECT COUNT(*) n, SUM(delivered=0) pending FROM trace_outbox'),
    }
    context={'accounting_notes':'Payments are simulated USD records unless explicitly confirmed on chain. Do not call any amounts real income or actual wallet balances.',
        'summary':summary,
        'recent_tasks':_recent('SELECT job_id,kind,status,error,created_at FROM tasks ORDER BY created_at DESC LIMIT 25',limit=25),
        'recent_invitations':_recent('SELECT job_id,ensemble_id,lead_id,offer_cents,round_number,status,created_at FROM invitations ORDER BY created_at DESC LIMIT 25',limit=25),
        'recent_payments':_recent('SELECT job_id,recipient,amount_cents,status,created_at FROM payments ORDER BY created_at DESC LIMIT 25',limit=25),
        'recent_events':_recent('SELECT created_at,job_id,kind,detail FROM events ORDER BY id DESC LIMIT 20',limit=20),
        'strategy_history':_recent('SELECT job_id,ensemble_id,negotiation_id,strategy_hash,created_at FROM strategy_snapshots ORDER BY created_at DESC LIMIT 20',limit=20)
    }
    tokens=re.findall(r'\b(?:JOB-[A-Za-z0-9-]+)\b',question,re.I)
    if tokens:
        jid=tokens[0][:100]
        context['selected_job']={
            'tasks':_recent('SELECT job_id,kind,status,result_json,error FROM tasks WHERE job_id=? LIMIT 20',(jid,),20),
            'offers':_recent('SELECT ensemble_id,lead_id,offer_cents,round_number,status FROM invitations WHERE job_id=? LIMIT 20',(jid,),20),
            'payments':_recent('SELECT recipient,amount_cents,status,tx_reference FROM payments WHERE job_id=? LIMIT 20',(jid,),20),
            'events':_recent('SELECT created_at,kind,detail FROM events WHERE job_id=? LIMIT 30',(jid,),30),
        }
    return _json(context)
