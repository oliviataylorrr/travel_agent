"""Student service agent with human login, dashboard, A2A and marketplace registration."""
import json,asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from .schema import init_db
from .auth import login,session,require_session,check_csrf,execute_logout,sign_sid,raw_sid
from .settings import ROOT,PUBLIC_BASE_URL
from .db import read,transaction,utcnow,one
from .llm import chat,instructions
from .chat_context import student_context
from .wallet import wallet_status
from .agent import profile,register,handle
from .a2a_bridge import attach_sdk
from .tracing import flush_loop
from .payouts import deliver_loop
ROOT=Path(__file__).resolve().parents[1]
templates=Jinja2Templates(directory=str(ROOT/'templates'))
_engine,_task_store=None,None
_trace_stop=None
@asynccontextmanager
async def lifespan(app):
    init_db()
    await _task_store.initialize()
    stop=asyncio.Event()
    worker=asyncio.create_task(flush_loop(stop))
    payouts=asyncio.create_task(deliver_loop(stop))
    try:yield
    finally:
        stop.set()
        payouts.cancel()
        await asyncio.gather(payouts,return_exceptions=True)
        await worker
        if _engine:await _engine.dispose()
app=FastAPI(title='DAPT Student Agent',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')

@app.get('/health')
def health():return {'status':'ok','app':'student','service_type':profile()['service_type']}
@app.get('/login',response_class=HTMLResponse)
def login_get(request:Request):return templates.TemplateResponse(request,'login.html',{'error':''})
@app.post('/login')
def login_post(request:Request,username:str=Form(...),password:str=Form(...)):
    sid=login(username,password)
    if not sid:return templates.TemplateResponse(request,'login.html',{'error':'Invalid username or password'})
    res=RedirectResponse('/',303);res.set_cookie('agent_session',sign_sid(sid),httponly=True,
         samesite='lax',secure=PUBLIC_BASE_URL.startswith('https://'),max_age=3600)
    return res
@app.post('/logout')
def logout(request:Request,csrf:str=Form(...)):
    s=require_session(request);check_csrf(s,csrf)
    execute_logout(raw_sid(request.cookies.get('agent_session','')))
    res=RedirectResponse('/login',303);res.delete_cookie('agent_session');return res
@app.get('/')
def dashboard(request:Request):
    s=session(request)
    if not s:return RedirectResponse('/login',303)
    p=profile()
    rows=read('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 60')
    events=read('SELECT * FROM events ORDER BY id DESC LIMIT 60')
    agent_id=p['agent_id']
    received=read("SELECT * FROM payments WHERE kind='payout' AND recipient=? ORDER BY created_at DESC LIMIT 60",(agent_id,))
    sent=read("""SELECT payments.*, payout_outbox.payment_id AS pending_notice
                 FROM payments LEFT JOIN payout_outbox USING (payment_id)
                 WHERE kind='payout' AND recipient<>? ORDER BY created_at DESC LIMIT 60""",(agent_id,))
    history=read('SELECT role,content FROM chats WHERE username=? ORDER BY id DESC LIMIT 20',(s['username'],))
    overview={'task_count':one('SELECT COUNT(*) n FROM tasks')['n'],
              'completed_count':one("SELECT COUNT(*) n FROM tasks WHERE status='completed'")['n'],
              'payment_cents':one("SELECT COALESCE(SUM(amount_cents),0) n FROM payments WHERE kind='payout' AND recipient=?",(agent_id,))['n']}
    return templates.TemplateResponse(request,'agent.html',{'session':s,'overview':overview,'profile':p,'rows':rows,'events':events,'received':received,'sent':sent,
        'trace_pending':one('SELECT COUNT(*) n FROM trace_outbox WHERE delivered=0')['n'],
        'strategies':read('SELECT job_id,ensemble_id,negotiation_id,strategy_hash,created_at FROM strategy_snapshots ORDER BY created_at DESC LIMIT 60'),
        'history':list(reversed(history)),'wallet':wallet_status(),'feedback':'',
        'usage':one('SELECT COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens FROM llm_usage')})
@app.post('/register')
async def register_action(request:Request,csrf:str=Form(...)):
    s=require_session(request);check_csrf(s,csrf)
    try:result=await register()
    except Exception as ex:raise HTTPException(502,'Registration failed: '+str(ex)[:300])
    return RedirectResponse('/',303)
@app.post('/chat')
async def chat_action(request:Request,csrf:str=Form(...),question:str=Form(...)):
    s=require_session(request);check_csrf(s,csrf)
    question=question.strip()[:6000]
    if not question:return RedirectResponse('/',303)
    p=profile()
    rows=read('SELECT role,content FROM chats WHERE username=? ORDER BY id DESC LIMIT 20',(s['username'],))
    history=[{'role':x['role'],'content':x['content']} for x in reversed(rows)]
    system=(f"You are the operator-facing management assistant for {p['name']} ({p['service_type']}). "
        'The person logged in is the STUDENT/OWNER who operates this agent, NOT a travel customer or client requesting service. '
        'Speak as an operations analyst and peer to the student operator, in first person as the agent where helpful. '
        'Do not offer to book travel, find hotels for the operator, or recruit agents unless describing documented capabilities. '
        'Use the current agent configuration and instructions to explain your identity, intended strategy and capabilities. '
        'Use the SQLite evidence below to explain actual negotiations, task outcomes, payouts and strategy history. '
        'Never claim a booking or a live blockchain payment without a corresponding confirmed record. '
        'Never send A2A messages, submit bids, change configuration, modify strategies or make payments on chat requests. '
        'When a job or decision is not recorded, explicitly say so. Do not invent transaction prices or reputations.\n'
        f'Agent profile: {json.dumps(p)}\nInstructions and skills: {instructions()[:14000]}\n'
        'Recorded database evidence: '+student_context(question))
    try:answer=await asyncio.to_thread(chat,system,question,history,False)
    except Exception as ex:answer='OpenAI call failed: '+str(ex)[:300]
    with transaction() as cn:
        for role,content in [('user',question),('assistant',answer)]:
            cn.execute('INSERT INTO chats(username,created_at,role,content) VALUES(?,?,?,?)',(s['username'],utcnow(),role,content))
    return RedirectResponse('/',303)

p=profile()
_engine,_task_store=attach_sdk(app,handle,p['name'],p['description'],p['service_type'])
