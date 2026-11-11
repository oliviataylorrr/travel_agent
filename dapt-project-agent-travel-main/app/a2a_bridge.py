"""Official a2a-sdk 1.3.0 JSON-RPC transport, not custom A2A JSON-RPC.
The SDK handles Task lifecycle and JSON-RPC; the application validates business messages.
"""
import asyncio,json,hmac,uuid,logging
import httpx
from .settings import A2A_SHARED_SECRET,PUBLIC_BASE_URL,DATA_DIR
log=logging.getLogger(__name__)

def bearer_guard(request):
    from fastapi.responses import JSONResponse
    if len(A2A_SHARED_SECRET)<16:
        return JSONResponse({'error':'A2A_SHARED_SECRET must be at least 16 characters'},status_code=503)
    if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+A2A_SHARED_SECRET):
        return JSONResponse({'error':'Unauthorized'},status_code=401)
    return None

def protocol_json(message):
    """Render the SDK's Protobuf A2A message in its JSON wire representation."""
    try:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(message,preserving_proto_field_name=False)
    except Exception:
        return {'warning':'SDK protobuf serialization unavailable','representation':str(message)[:4000]}

def audit_trace(event_type,content,details):
    try:
        from .tracing import record
        return record(event_type,content,**details)
    except Exception:
        log.exception('Local A2A audit trace could not be recorded')
        return None

async def send_peer(base_url,payload,timeout_seconds=540):
    """Non-streaming SDK task polling. One business message -> one SDK task."""
    from a2a.client import ClientConfig,create_client
    from a2a.types import (AgentCard,AgentCapabilities,AgentInterface,Message,Part,
                           Role,SendMessageRequest,SendMessageConfiguration,GetTaskRequest,TaskState)
    endpoint=base_url.rstrip('/')+'/a2a'
    card=AgentCard(name='Configured peer',description='Known class agent',version='1.0.0',
        capabilities=AgentCapabilities(streaming=False),default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],supported_interfaces=[AgentInterface(
        protocol_binding='JSONRPC',protocol_version='1.0',url=endpoint)])
    message=Message(role=Role.ROLE_USER,message_id=uuid.uuid4().hex,parts=[Part(text=json.dumps(payload,default=str))])
    req=SendMessageRequest(message=message,configuration=SendMessageConfiguration(return_immediately=True))
    details={'job_id':payload.get('job_id'),'ensemble_id':payload.get('ensemble_id'),
        'negotiation_id':payload.get('negotiation_id'),
        'strategy_hash':payload.get('strategy_hash'),
        'recipient':base_url,'trace_id':payload.get('job_id')}
    audit_trace('a2a_outbound_message',{'protocol_message':protocol_json(message),'business_action':payload.get('action')},details)
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+A2A_SHARED_SECRET},timeout=35) as http:
        peer=await create_client(card,client_config=ClientConfig(streaming=False,polling=True,httpx_client=http))
        try:
            first=None
            async for ev in peer.send_message(req):
                if ev.HasField('task'):first=ev.task;break
                if ev.HasField('message'):
                    parts=ev.message.parts
                    if parts and parts[0].HasField('text'):
                        result=json.loads(parts[0].text)
                        audit_trace('a2a_inbound_result',{'protocol_message':protocol_json(ev.message)},details)
                        return result
                    raise RuntimeError('Empty A2A response')
            if first is None:raise RuntimeError('Peer returned no A2A task')
            task=first;deadline=asyncio.get_running_loop().time()+timeout_seconds
            details['task_id']=first.id;details['context_id']=first.context_id
            audit_trace('a2a_task_started',{'task':protocol_json(first)},details)
            while True:
                if task.status.state==TaskState.TASK_STATE_COMPLETED:
                    for artifact in task.artifacts:
                        for part in artifact.parts:
                            if part.HasField('text'):
                                result=json.loads(part.text)
                                audit_trace('a2a_inbound_result',{'task':protocol_json(task),'result':result},details)
                                return result
                    raise RuntimeError('Completed A2A task has no result artifact')
                if task.status.state in (TaskState.TASK_STATE_FAILED,TaskState.TASK_STATE_CANCELED,TaskState.TASK_STATE_REJECTED):
                    msg='A2A task failed'
                    if task.status.HasField('message'):
                        ps=task.status.message.parts
                        if ps and ps[0].HasField('text'):msg+=': '+ps[0].text[:800]
                    raise RuntimeError(msg)
                if asyncio.get_running_loop().time()>deadline:raise TimeoutError('Peer task exceeded timeout')
                await asyncio.sleep(1.5)
                task=await peer.get_task(GetTaskRequest(id=first.id))
        finally:await peer.close()

def attach_sdk(app, executor,agent_name,description,service_type):
    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.agent_execution.context import RequestContext
    from a2a.server.events.event_queue import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes,create_jsonrpc_routes
    from a2a.server.tasks.database_task_store import DatabaseTaskStore
    from a2a.server.tasks.task_updater import TaskUpdater
    from a2a.types import (AgentCard,AgentCapabilities,AgentInterface,AgentSkill,
                           Part,Task,TaskStatus,TaskState)
    from sqlalchemy.ext.asyncio import create_async_engine

    class BusinessExecutor(AgentExecutor):
        async def execute(self,context,event_queue):
            tid=context.task_id;cid=context.context_id
            if not tid or not cid:raise ValueError('Missing A2A task identifiers')
            await event_queue.enqueue_event(Task(id=tid,context_id=cid,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message else []))
            updater=TaskUpdater(event_queue=event_queue,task_id=tid,context_id=cid)
            await updater.start_work()
            try:
                raw=context.get_user_input()
                if not raw or len(raw)>200000:raise ValueError('Invalid request length')
                request=json.loads(raw)
                if isinstance(request,dict):
                    audit_trace('a2a_inbound_message',{'protocol_message':protocol_json(context.message),'business_action':request.get('action')},
                        {'job_id':request.get('job_id'),'ensemble_id':request.get('ensemble_id'),
                         'sender':request.get('lead_id') or '', 'recipient':agent_name,
                         'negotiation_id':request.get('negotiation_id'), 'strategy_hash':request.get('strategy_hash'),
                         'task_id':tid,'context_id':cid,'trace_id':request.get('job_id')})
                if not isinstance(request,dict):raise ValueError('A2A business payload must be an object')
                result=await executor(request)
                await updater.add_artifact(parts=[Part(text=json.dumps(result,default=str))],name='business_result',last_chunk=True)
                if isinstance(request,dict):
                    audit_trace('a2a_outbound_result',{'artifact':{'name':'business_result','parts':[{'text':json.dumps(result,default=str)}]}},
                        {'job_id':request.get('job_id'),'ensemble_id':request.get('ensemble_id'),
                         'sender':agent_name,'task_id':tid,'context_id':cid,'trace_id':request.get('job_id'),
                         'negotiation_id':request.get('negotiation_id'),'strategy_hash':result.get('strategy_hash') if isinstance(result,dict) else None})
                await updater.complete()
            except Exception as exc:
                log.exception('Business A2A task %s failed',tid)
                await updater.failed(message=updater.new_agent_message(parts=[Part(text=str(exc)[:900])]))
        async def cancel(self,context,event_queue):
            raise NotImplementedError('Cancellation is not supported in this classroom version')

    card=AgentCard(name=agent_name,description=description,version='1.0.0',
        capabilities=AgentCapabilities(streaming=False,push_notifications=False),
        default_input_modes=['text/plain'],default_output_modes=['text/plain'],
        skills=[AgentSkill(id=f'{service_type}-work',name=agent_name,description=description,tags=[service_type,'marketplace'])],
        supported_interfaces=[AgentInterface(protocol_binding='JSONRPC',protocol_version='1.0',url=PUBLIC_BASE_URL+'/a2a')])
    # SDK state lives in its own SQLite DB to minimize write contention with business records.
    engine=create_async_engine('sqlite+aiosqlite:///'+str(DATA_DIR/'sdk_tasks.sqlite3'),connect_args={'timeout':40})
    store=DatabaseTaskStore(engine=engine)  # standard table avoids dynamic model duplication
    handler=DefaultRequestHandler(agent_executor=BusinessExecutor(),task_store=store,agent_card=card)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler,rpc_url='/a2a'))
    @app.middleware('http')
    async def guard(request,call_next):
        if request.url.path.rstrip('/')=='/a2a':
            result=bearer_guard(request)
            if result:return result
        return await call_next(request)
    return engine,store
