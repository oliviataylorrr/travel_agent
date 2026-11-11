"""One student agent for Travel, Lodging, Interview and Mineral service types.
LLM chooses negotiation and research. Python enforces the workflow and payment amounts.
"""
import asyncio,json,uuid
from decimal import Decimal
from pathlib import Path
import httpx
from .settings import ROOT,MARKETPLACE_URL,A2A_SHARED_SECRET,PAYMENT_MODE,PUBLIC_BASE_URL
from .llm import decide,instructions
from .db import transaction,one,read,event,utcnow
from .a2a_bridge import send_peer
from .tracing import snapshot,record

SERVICES=('travel','lodging','interview','mineral')
def profile():
    data=json.loads((ROOT/'config'/'agent.json').read_text(encoding='utf-8'))
    if data.get('service_type') not in SERVICES:raise ValueError('config/agent.json service_type must be travel/lodging/interview/mineral')
    if not data.get('agent_id') or data['agent_id']=='replace-with-unique-student-agent-id':raise ValueError('Configure unique agent_id in config/agent.json')
    return data

def to_cents(amount):
    try:
        d=Decimal(str(amount))
        if not d.is_finite() or d<0 or d.as_tuple().exponent< -2:raise ValueError()
        return int(d*100)
    except Exception:raise ValueError('USD amount must be nonnegative and at most two decimal places')
def usd(cents):return f'{Decimal(int(cents))/100:.2f}'

def record_task(job_id,kind,request,result=None,error=''):
    tid='LOCAL-'+uuid.uuid4().hex
    with transaction() as cn:
        cn.execute('INSERT INTO tasks(id,created_at,job_id,kind,status,input_json,result_json,error) VALUES(?,?,?,?,?,?,?,?)',
                   (tid,utcnow(),job_id,kind,'failed' if error else 'completed',json.dumps(request,default=str),json.dumps(result,default=str) if result is not None else None,error[:1000]))
    return tid

async def market_api(method,path,payload=None):
    if not MARKETPLACE_URL:raise RuntimeError('Set MARKETPLACE_URL')
    headers={'Authorization':'Bearer '+A2A_SHARED_SECRET}
    async with httpx.AsyncClient(timeout=35,headers=headers,follow_redirects=False) as client:
        r=await client.request(method,MARKETPLACE_URL+path,json=payload)
        r.raise_for_status()
        output=r.json()
        if isinstance(payload,dict) and payload.get('job_id'):
            record('marketplace_api',{'method':method,'path':path,'request':payload,'response':output},
              job_id=payload['job_id'],ensemble_id=payload.get('ensemble_id'),recipient='marketplace')
        return output

async def public_market(path):
    async with httpx.AsyncClient(timeout=25) as client:
        r=await client.get(MARKETPLACE_URL+path);r.raise_for_status();return r.json()

async def register():
    p=profile()
    # Wallet address remains external to OpenAI; file may be copied into mounted wallet dir.
    from .wallet import public_wallet_address
    body={'agent_id':p['agent_id'],'url':PUBLIC_BASE_URL,'name':p['name'],
      'description':p['description'],'service_type':p['service_type'],'price_usd':p['posted_price_usd'],
      'token_address':p.get('token_address',''),'wallet_address':public_wallet_address() or p.get('wallet_address','')}
    return await market_api('POST','/internal/register',body)

# Non-negotiable service definitions. Student instructions still control pricing discretion.
# These definitions are sent to the model independently of student-authored strategy.
SERVICE_SCOPES={
    'travel':'Research and recommend flights/itineraries; do NOT buy airfare or pay transportation costs.',
    'lodging':'Research and recommend accommodations; do NOT book rooms or pay hotel costs.',
    'interview':'Provide role-specific interview advice and assemble recommendations; do NOT pay client expenses.',
    'mineral':'Provide chemical and commercial mineral research; do NOT purchase minerals.',
}
SCOPE_RULES=(
    'You are selling information, research and recommendations ONLY, never physical travel, hotel rooms or reservations. '
    'The client consulting budget is the ceiling for TOTAL ensemble consulting fees, not the cost of travel or lodging. '
    'The specialist offer is payment for that specialist advice alone. '
    'Real airfare and hotel prices are facts to report in the deliverable, NOT expenditures funded from a consulting fee. '
    'Negotiation decisions must concern only the service fee, and must respect the student pricing strategy.'
)

def response_dict(answer,allowed):
    if not isinstance(answer,dict) or answer.get('decision') not in allowed:
        raise ValueError('LLM returned invalid decision')
    return answer

def validate_negotiation_response(answer, current_offer_cents):
    """Normalize LLM proposal; do not silently change an economic decision.

    On reject the price repeats the last offer as reference only; to demand a
    different price use counter, so the interpretation never depends on prose.
    """
    response_dict(answer,('accept','reject','counter'))
    reason=answer.get('reason')
    if not isinstance(reason,str) or not reason.strip():
        raise ValueError('Negotiation reason is required')
    price=to_cents(answer.get('price_usd'))
    choice=answer['decision']
    if choice in ('accept','reject') and price!=current_offer_cents:
        raise ValueError(f'{choice} must repeat the currently offered ${usd(current_offer_cents)}; use counter to propose ${usd(price)}')
    if choice=='counter' and (price<=0 or price==current_offer_cents):
        raise ValueError('Counteroffer must be a different, strictly positive service fee')
    return {'decision':choice,'price_usd':usd(price),'reason':reason.strip()[:1000]}

async def specialist_decision(p,active,prompt,offer,job_id,ensemble_id,neg_id):
    """Retry malformed/confused LLM decisions with explicit, auditable feedback."""
    feedback=''
    for attempt in range(1,4):
        candidate_prompt=dict(prompt)
        candidate_prompt['validation_feedback']=feedback
        candidate_prompt['attempt']=attempt
        try:
            raw=await asyncio.to_thread(decide,
                'You are '+p['name']+'.\n'+SCOPE_RULES+'\n'+SERVICE_SCOPES[p['service_type']]+'\n'+active['text'],candidate_prompt)
            answer=validate_negotiation_response(raw,offer)
            record('negotiation_validation',{'attempt':attempt,'valid':True,'offer_usd':usd(offer),
                'decision':answer},job_id=job_id,ensemble_id=ensemble_id,negotiation_id=neg_id,
                strategy_hash=active['hash'],sender=p['agent_id'])
            return answer
        except (ValueError,TypeError,KeyError) as exc:
            feedback=str(exc)
            record('negotiation_validation',{'attempt':attempt,'valid':False,'offer_usd':usd(offer),
                'reason':feedback[:400]},job_id=job_id,ensemble_id=ensemble_id,negotiation_id=neg_id,
                strategy_hash=active['hash'],sender=p['agent_id'])
    raise ValueError('Invalid negotiation response after 3 attempts: '+feedback)

async def invite(data):
    p=profile()
    if p['service_type']=='travel':raise ValueError('Travel agents initiate, not accept, invitations in this version')
    job_id=data['job_id']
    offer=to_cents(data.get('offered_service_fee_usd',data.get('offer_usd')))
    if 'offer_usd' in data and to_cents(data['offer_usd'])!=offer:
        raise ValueError('Ambiguous offer_usd and offered_service_fee_usd values')
    history=data.get('history',[])
    neg_id=data.get('negotiation_id') or 'NEG-'+str(data['lead_id'])+'-'+p['agent_id']
    active=snapshot(job_id,data['ensemble_id'],neg_id)
    market_history=await public_market('/api/history?service_type='+p['service_type'])
    roster=await public_market('/api/registry?service_type='+p['service_type'])
    self_entry=next((a for a in roster['agents'] if a['agent_id']==p['agent_id']),{})
    job=data['job']
    consult_budget=job.get('budget_usd','')
    if data.get('client_consulting_budget_usd') not in (None,''):
        if to_cents(data['client_consulting_budget_usd'])!=to_cents(consult_budget):
            raise ValueError('Client consulting budget conflicts with job record')
    prompt={
      'task':'Negotiate the fee for this one specialist research/advice service. Choose accept, reject or counter.',
      'job':job,'job_id':job_id,'round':data['round'],
      'client_consulting_budget_usd':str(consult_budget),
      'offered_service_fee_usd':usd(offer),
      'offer_usd':usd(offer),  # legacy alias for student test fixtures and older prompts
      'posted_service_fee_usd':p['posted_price_usd'],
      'service_scope':SERVICE_SCOPES[p['service_type']],
      'fee_distinction':'Service fee compensates advice only. Flight/hotel purchase prices are NOT included.',
      'own_reputation':self_entry.get('reputation',50),
      'history':history,'market_comparables':market_history.get('transactions',[])[:30],
      'required_output':{'decision':'accept|reject|counter','price_usd':'USD fee for this ONE specialist service',
                         'reason':'brief reason referencing consulting fee only'},
      'validation_rules':{'accept':'repeat offered_service_fee_usd exactly',
           'reject':'repeat offered_service_fee_usd; no deal',
           'counter':'propose a DIFFERENT positive service fee'},
    }
    answer=await specialist_decision(p,active,prompt,offer,job_id,data['ensemble_id'],neg_id)
    answer['strategy_hash']=active['hash'];answer['negotiation_id']=neg_id
    record('negotiation_decision',{'round':data['round'],'offered_service_fee_usd':usd(offer),
       'client_consulting_budget_usd':str(consult_budget),'answer':answer},
       job_id=job_id,ensemble_id=data['ensemble_id'],negotiation_id=neg_id,
       strategy_hash=active['hash'],sender=p['agent_id'],recipient=data['lead_id'])
    with transaction() as c:
        c.execute('INSERT OR REPLACE INTO invitations(job_id,ensemble_id,lead_id,offer_cents,round_number,status,created_at) VALUES(?,?,?,?,?,?,?)',
             (job_id,data['ensemble_id'],data['lead_id'],
              to_cents(answer['price_usd']) if answer['decision']=='counter' else offer,
              data['round'],answer['decision'],utcnow()))
    event('invitation',{'offered_service_fee_usd':usd(offer),'answer':answer,'round':data['round']},job_id,p['agent_id'])
    return answer

async def confirm_counter(data):
    """Travel formally accepts a specialist counteroffer without prompting it again."""
    p=profile()
    if p['service_type']=='travel':raise ValueError('Travel cannot confirm its own counter')
    job_id=data['job_id'];ens=data['ensemble_id'];lead=data['lead_id']
    agreed=to_cents(data['agreed_service_fee_usd'])
    row=one('SELECT * FROM invitations WHERE job_id=? AND ensemble_id=? AND lead_id=?',(job_id,ens,lead))
    if not row or row['status']!='counter' or row['offer_cents']!=agreed:
        raise ValueError('No matching outstanding specialist counteroffer')
    with transaction() as c:
        changed=c.execute("UPDATE invitations SET status='accept' WHERE job_id=? AND ensemble_id=? AND lead_id=? AND status='counter' AND offer_cents=?",
                 (job_id,ens,lead,agreed)).rowcount
    if changed!=1:raise ValueError('Counteroffer was already resolved')
    answer={'decision':'accept','price_usd':usd(agreed),'reason':'Travel lead accepted the existing specialist counteroffer'}
    record('negotiation_counter_confirmed',{'agreed_service_fee_usd':usd(agreed),'lead_id':lead},
       job_id=job_id,ensemble_id=ens,negotiation_id=data.get('negotiation_id'),
       sender=p['agent_id'],recipient=lead)
    event('counter_accepted',{'agreed_service_fee_usd':usd(agreed)},job_id,p['agent_id'])
    return answer

async def negotiate_with_agent(agent,job_id,job,ens):
    offer=agent['price_cents'];history=[];p=profile()
    neg_id='NEG-'+p['agent_id']+'-'+agent['agent_id']
    active=snapshot(job_id,ens,neg_id)
    for attempt in range(1,4):
        invitation={
          'action':'invite','job_id':job_id,'job':job,'ensemble_id':ens,'lead_id':p['agent_id'],
          'offer_usd':usd(offer),'offered_service_fee_usd':usd(offer),
          'client_consulting_budget_usd':str(job.get('budget_usd','')),
          'service_scope':SERVICE_SCOPES[agent['service_type']],
          'posted_service_fee_usd':usd(agent['price_cents']),
          'round':attempt,'history':history,
          'recipient_id':agent['agent_id'],'negotiation_id':neg_id,'strategy_hash':active['hash']}
        record('negotiation_offer_sent',{
          'round':attempt,'offered_service_fee_usd':usd(offer),
          'client_consulting_budget_usd':str(job.get('budget_usd','')),
          'specialist_agent_id':agent['agent_id']},job_id=job_id,ensemble_id=ens,
           negotiation_id=neg_id,strategy_hash=active['hash'],recipient=agent['agent_id'])
        raw=await send_peer(agent['url'],invitation)
        answer=validate_negotiation_response(raw,offer)
        choice=answer['decision'];amount=to_cents(answer['price_usd'])
        history.append({'round':attempt,'offered_service_fee_usd':usd(offer),'answer':answer})
        record('negotiation_response_received',{'round':attempt,'offer_usd':usd(offer),
          'decision':answer,'specialist_agent_id':agent['agent_id']},job_id=job_id,
          ensemble_id=ens,negotiation_id=neg_id,strategy_hash=raw.get('strategy_hash'),
          sender=agent['agent_id'],recipient=p['agent_id'])
        if choice=='reject':return {'status':'declined','reason':answer['reason'],'round':attempt}
        if choice=='accept':
            return {'price_cents':amount,'strategy_hash':raw.get('strategy_hash','')}
        if attempt==3:return {'status':'declined','reason':'Negotiation reached maximum of three rounds','round':attempt}
        prompt={'task':'Assess specialist counteroffer and decide accept/counter/reject. Only consulting advice fees are negotiable.',
                'specialist':agent,'job':job,'negotiation_history':history,
                'current_counter_usd':usd(amount),
                'client_consulting_budget_usd':str(job.get('budget_usd','')),
                'service_scope':SERVICE_SCOPES[agent['service_type']],
                'required_output':{'decision':'accept|counter|reject','price_usd':'USD fee','reason':'brief reason'}}
        lead_feedback=''
        for retry in range(1,4):
            try:
                proposed=await asyncio.to_thread(decide,
                    'You are the Travel ensemble lead. '+SCOPE_RULES+'\n'+active['text'],
                    {**prompt,'validation_feedback':lead_feedback,'attempt':retry})
                next_step=validate_negotiation_response(proposed,amount)
                break
            except (ValueError,TypeError,KeyError) as exc:
                lead_feedback=str(exc)
                record('negotiation_validation',{'valid':False,'attempt':retry,'error':lead_feedback[:400]},
                  job_id=job_id,ensemble_id=ens,negotiation_id=neg_id,strategy_hash=active['hash'])
        else:raise ValueError('Travel counteroffer decision invalid after 3 attempts: '+lead_feedback)
        if next_step['decision']=='reject':return {'status':'declined','reason':next_step['reason'],'round':attempt}
        if next_step['decision']=='accept':
            confirmation=await send_peer(agent['url'],{
               'action':'confirm_counter','job_id':job_id,'ensemble_id':ens,'lead_id':p['agent_id'],
               'agreed_service_fee_usd':usd(amount),'negotiation_id':neg_id,
               'recipient_id':agent['agent_id']})
            if confirmation.get('decision')!='accept' or to_cents(confirmation.get('price_usd'))!=amount:
                raise ValueError('Specialist did not confirm counteroffer acceptance')
            record('negotiation_agreed',{'agreed_service_fee_usd':usd(amount),
                'specialist_agent_id':agent['agent_id']},job_id=job_id,ensemble_id=ens,
                negotiation_id=neg_id,sender=p['agent_id'],recipient=agent['agent_id'])
            return {'price_cents':amount,'strategy_hash':raw.get('strategy_hash','')}
        offer=to_cents(next_step['price_usd'])
    return None

async def start_bid(data):
    p=profile()
    if p['service_type']!='travel':raise ValueError('Only Travel agents can assemble and bid')
    job_id=data['job_id'];job=data['job'];ens='ENS-'+uuid.uuid4().hex[:22]
    lead_strategy=snapshot(job_id,ens,'lead-bidding')
    selected=[]
    async def reserve(agent_id):
        result=await market_api('POST','/internal/reservations/claim',{'job_id':job_id,'agent_id':agent_id,'ensemble_id':ens})
        return result.get('reserved',False)
    if not await reserve(p['agent_id']):return {'status':'no_bid','reason':'Travel lead already committed for this job'}
    selected.append({'agent_id':p['agent_id'],'service_type':'travel','price_cents':to_cents(p['posted_price_usd']),'strategy_hash':lead_strategy['hash']})
    try:
        directory=(await public_market('/api/registry'))['agents']
        formation_diagnostics=[]
        for service in ('lodging','interview','mineral'):
            # Price + reputation based screening; pricing discretion exercised via A2A negotiation.
            available=sorted((a for a in directory if a['service_type']==service and a['active']),
                key=lambda a:(a['price_cents'],-a['reputation']))
            chosen=None
            diagnostic={'service_type':service,'candidates_found':len(available),'candidates':[]}
            for agent in available:
                try:
                    agreed=await negotiate_with_agent(agent,job_id,job,ens)
                    if agreed is None or agreed.get('status')=='declined':
                        diagnostic['candidates'].append({'agent_id':agent['agent_id'],'outcome':'not_agreed',
                            'reason':(agreed or {}).get('reason','Negotiation did not reach an agreement')})
                        continue
                    if not await reserve(agent['agent_id']):
                        diagnostic['candidates'].append({'agent_id':agent['agent_id'],'outcome':'reservation_denied','reason':'Already reserved for another ensemble'})
                        continue
                    chosen={'agent_id':agent['agent_id'],'service_type':service,'price_cents':agreed['price_cents'],'strategy_hash':agreed.get('strategy_hash','')}
                    diagnostic['candidates'].append({'agent_id':agent['agent_id'],'outcome':'accepted','agreed_service_fee_usd':usd(agreed['price_cents'])})
                    break
                except Exception as exc:
                    diagnostic['candidates'].append({'agent_id':agent['agent_id'],'outcome':'error','reason':str(exc)[:500]})
                    event('negotiation_error',{'service':service,'agent':agent['agent_id'],'error':str(exc)},job_id,p['agent_id'])
            formation_diagnostics.append(diagnostic)
            record('ensemble_formation_stage',diagnostic,job_id=job_id,ensemble_id=ens,recipient='marketplace')
            if not chosen:
                reason=('No registered active '+service+' specialist' if not available
                    else 'No '+service+' specialist agreed and could be reserved')
                details={'status':'no_bid','reason':reason,'failed_stage':service+'_negotiation_or_reservation',
                         'formation_diagnostics':formation_diagnostics}
                event('ensemble_formation_failed',details,job_id,p['agent_id'])
                record('ensemble_formation_failed',details,job_id=job_id,ensemble_id=ens,recipient='marketplace')
                return details
            selected.append(chosen)
        result=await market_api('POST','/internal/bids',{'job_id':job_id,'lead_id':p['agent_id'],'ensemble_id':ens,'participants':selected})
        event('bid_submitted',{'ensemble_id':ens,'participants':selected,'total_usd':result['total_usd']},job_id,p['agent_id'])
        record('ensemble_bid',{'participants':selected,'bid_id':result['bid_id'],'total_usd':result['total_usd']},job_id=job_id,ensemble_id=ens,strategy_hash=lead_strategy['hash'],recipient='marketplace')
        return {'status':'bid_submitted','bid_id':result['bid_id'],'ensemble_id':ens,'total_usd':result['total_usd']}
    except Exception as exc:
        event('bid_error',{'ensemble_id':ens,'error':str(exc)},job_id,p['agent_id'])
        raise
    finally:
        # Keep reservations for successfully formed ensembles until award; release failed formations.
        if len(selected)!=4:
            try:await market_api('POST','/internal/reservations/release',{'job_id':job_id,'ensemble_id':ens})
            except Exception:pass

async def fulfill(data):
    p=profile();service=p['service_type']
    if data.get('service_type')!=service:raise ValueError('Wrong service type for this agent')
    job=data['job']
    fields={
      'travel':{
          'flight_options':('array','Flight suggestions with carrier, route, dates and evidence URLs in each entry'),
          'notes':('string','Caveats about availability and pricing'),
      },
      'lodging':{
          'hotel_options':('array','Hotel suggestions with location, dates, caveats and URLs in each entry'),
      },
      'interview':{
          'role':('string','Requested job role'),
          'advice':('array','Preparation steps and possible interview questions'),
      },
      'mineral':{
          'mineral':('string','Requested mineral name'),
          'chemical_facts':('array','Chemical facts with source URLs'),
          'commercial_facts':('array','Commercial facts with source URLs'),
      },
    }[service]
    properties={}
    for name,(kind,description) in fields.items():
        properties[name]={'type':kind,'description':description}
        if kind == 'array':
            properties[name]['items']={'type':'string'}
    schema={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    findings = {
        'travel': (('flight_options',), 'No flight recommendations found.'),
        'lodging': (('hotel_options',), 'No lodging recommendations found.'),
        'interview': (('advice',), 'No relevant interview advice found.'),
        'mineral': (('chemical_facts', 'commercial_facts'), 'No reliable mineral information found.'),
    }
    result_fields, no_findings_message = findings[service]
    research_job = {key: value for key, value in job.items() if key != 'budget_usd'}
    research_prompt = (
        'You provide '+service+' research. '+instructions()+
        '\nNever claim to have booked or purchased travel or lodging. Do not invent live prices. '
        'Provide sources with URLs when available. The consulting fee is separate from travel and hotel costs. '
        'If exact-date prices or availability cannot be verified, label them unverified. '
        'Return an empty list for any findings field with no usable results. Do not insert blank placeholders '
        'or put a no-results message in a findings list.'
    )
    for attempt in (1, 2):
        task = 'Research your service for this client. Make practical, sourced recommendations.'
        if attempt == 2:
            task += ' The first search found no usable results. Try different search terms and credible sources.'
        out = await asyncio.to_thread(
            decide,research_prompt,{'task':task,'service_type':service,'job':research_job},
            True,output_schema=schema,job_id=data['job_id'],
        )
        for field in result_fields:
            out[field] = [entry.strip() for entry in out[field] if entry.strip()]
        if any(out[field] for field in result_fields):
            event('service_delivered',{'service_type':service,'job_id':data['job_id']},data['job_id'],p['agent_id'])
            break
        event('service_no_usable_findings',{'service_type':service,'attempt':attempt},data['job_id'],p['agent_id'])
    else:
        out['status'] = 'no_findings'
        out['message'] = no_findings_message

    return {'service_type':service,'content':out,'agent_id':p['agent_id']}

async def award(data):
    p=profile()
    if p['service_type']!='travel':raise ValueError('Only Travel lead receives awards')
    job_id=data['job_id'];members=data['participants'];job=data['job']
    directory=(await public_market('/api/registry'))['agents'];by_id={a['agent_id']:a for a in directory}
    content={}
    for service in SERVICES:
        entry=next(p for p in members if p['service_type']==service)
        if service=='travel':result=await fulfill({'job_id':job_id,'job':job,'service_type':service})
        else:result=await send_peer(by_id[entry['agent_id']]['url'],
              {'action':'fulfill','job_id':job_id,'job':job,'service_type':service})
        content[service]=result['content']
    interview=next(p for p in members if p['service_type']=='interview')
    result=await send_peer(by_id[interview['agent_id']]['url'],
          {'action':'assemble','job_id':job_id,'ensemble_id':data['ensemble_id'],'content':content})
    if not result.get('posted'):raise RuntimeError('Interview agent failed to post package')
    event('award_fulfilled',{'ensemble_id':data['ensemble_id']},job_id,p['agent_id'])
    return {'fulfilled':True,'job_id':job_id,'ensemble_id':data['ensemble_id']}

async def assemble(data):
    if profile()['service_type']!='interview':raise ValueError('Only Interview agent can post final package')
    content=data.get('content',{})
    if not isinstance(content,dict) or set(content)!=set(SERVICES):raise ValueError('Incomplete package')
    await market_api('POST','/internal/package',{'job_id':data['job_id'],'ensemble_id':data['ensemble_id'],'package':content})
    event('package_posted',{'ensemble_id':data['ensemble_id']},data['job_id'],profile()['agent_id'])
    return {'posted':True}

async def settle(data):
    if profile()['service_type']!='interview':raise ValueError('Only Interview agent can distribute funds')
    if PAYMENT_MODE!='simulated':
        raise NotImplementedError('Base Sepolia payment adapter is not yet implemented; keep PAYMENT_MODE=simulated')
    parts=data['participants'];myid=profile()['agent_id'];job=data['job_id']
    if len(parts)!=4 or sum(1 for p in parts if p['agent_id']==myid and p['service_type']=='interview')!=1:
        raise ValueError('Invalid payment roster')
    from .payouts import enqueue_notice
    results=[]
    for part in parts:
        amount=usd(part['price_cents'])
        receipt=await market_api('POST','/internal/payments',{'kind':'payout','job_id':job,
            'payer':myid,'recipient':part['agent_id'],'amount_usd':amount})
        results.append({'agent_id':part['agent_id'],'payment':receipt})
        with transaction() as cn:
            cn.execute('INSERT OR IGNORE INTO payments(payment_id,job_id,kind,amount_cents,recipient,status,tx_reference,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (receipt['payment_id'],job,'payout',part['price_cents'],part['agent_id'],receipt['status'],receipt['reference'],utcnow()))
            if part['agent_id'] != myid:
                enqueue_notice(cn,receipt['payment_id'])
    event('payouts_finished',{'receipts':results},job,myid)
    return {'distributed':True,'receipts':results}

async def receive_payout(data):
    from .payouts import store_receipt
    return await store_receipt(data)


HANDLERS={'payment_received':receive_payout,'invite':invite,'confirm_counter':confirm_counter,'start_bid':start_bid,'fulfill':fulfill,'award':award,'assemble':assemble,'settle':settle}
async def handle(data):
    action=data.get('action')
    if action not in HANDLERS:raise ValueError('Unsupported A2A business action')
    job_id=data.get('job_id') or 'NONE'
    try:
        result=await HANDLERS[action](data)
        await asyncio.to_thread(record_task,job_id,action,data,result)
        return result
    except Exception as exc:
        await asyncio.to_thread(record_task,job_id,action,data,error=str(exc))
        raise
