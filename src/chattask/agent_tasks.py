"""Durable generic tasks: an execution engine, an image profile, and results."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import uuid
from .workflow import TaskStore, now

class AgentTaskService:
    def __init__(self, store:TaskStore, executor=None):
        self.store=store;self.executor=executor;self.tasks={}

    async def recover(self):
        """Reconcile a restarted process without replaying unknown side effects."""
        with self.store.locked():
            for task in self.store.list('runs'):
                if task.get('engine')=='codex' and task.get('status') in ('queued','running','cancel_requested') and task['id'] not in self.tasks:
                    task.update(status='interrupted',error='ExecutorRestarted',updated_at=now())
                    # Preserve unknown cleanup; bounded TTL is not deletion evidence.
                    self.store.put('runs',task['id'],task)

    def images(self):
        return [{'id':name,'image':image} for name,image in (self.executor.images.items() if self.executor else [])]

    def get(self,owner,ident):
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise KeyError('task not found')
        task=self.store.get('runs',ident)
        if task.get('owner')!=owner or task.get('engine')!='codex':raise KeyError('task not found')
        return {k:v for k,v in task.items() if k not in ('request_fingerprint','capability')}

    def list(self,owner):
        tasks=[t for t in self.store.list('runs') if t.get('owner')==owner and t.get('engine')=='codex']
        return [self.get(owner,t['id']) for t in sorted(tasks,key=lambda t:t.get('created_at',''),reverse=True)[:100]]

    async def create(self,owner,body):
        return await self._create(owner,body,None)

    async def create_internal(self,owner,body,capability):
        if not isinstance(capability,dict) or capability.get('mode') not in ('prepare','publish') or not isinstance(capability.get('plan'),dict):
            raise ValueError('invalid internal capability')
        if capability['mode']=='prepare' and set(capability)!={'mode','plan'} or capability['mode']=='publish' and (set(capability)!={'mode','plan','spec','plan_sha256'} or not isinstance(capability['spec'],dict) or not isinstance(capability['plan_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',capability['plan_sha256'])):
            raise ValueError('invalid internal capability')
        return await self._create(owner,body,capability)

    async def _create(self,owner,body,capability):
        if not isinstance(body,dict) or set(body)-{'request_id','instructions','image_profile','input_task_id'}:raise ValueError('unknown task field')
        request_id=body.get('request_id');instructions=body.get('instructions');image=body.get('image_profile')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}',request_id):raise ValueError('stable request_id required')
        if not isinstance(instructions,str) or not instructions.strip() or len(instructions)>16000:raise ValueError('task instructions must be 1–16000 characters')
        if not self.executor or image not in self.executor.images:raise ValueError('configured image profile required')
        input_task_id=body.get('input_task_id')
        if input_task_id is not None:
            source=self.get(owner,input_task_id)
            if source.get('status')!='completed' or not any(a['name']=='result.tar.gz' for a in source.get('artifacts',[])):raise ValueError('input task has no completed result archive')
        digest=hashlib.sha256(json.dumps({'body':body,'capability':capability},sort_keys=True).encode()).hexdigest()
        with self.store.locked():
            for old in self.store.list('runs'):
                if old.get('owner')==owner and old.get('request_id')==request_id:
                    if old.get('engine')!='codex' or old.get('request_fingerprint')!=digest:raise ValueError('request_id input conflict')
                    if old.get('status')=='blocked' and old.get('error')=='GatewayUnknown' and not old.get('artifacts') and old['id'] not in self.tasks:
                        old.update(status='queued',error=None,updated_at=now())
                        self.store.put('runs',old['id'],old)
                        self.tasks[old['id']]=asyncio.create_task(self._execute(old['id']))
                    return self.get(owner,old['id'])
            ident=uuid.uuid4().hex
            task={'id':ident,'owner':owner,'engine':'codex','template_id':'codex-task','template_revision':1,
                  'request_id':request_id,'request_fingerprint':digest,'instructions':instructions,
                  'image_profile':image,'status':'queued','created_at':now(),'updated_at':now(),
                  'steps':[{'kind':kind,'status':'pending'} for kind in ['prepare','codex','collect']],
                  'result':None,'artifacts':[],'cleanup':None,'input_task_id':input_task_id,'capability':capability}
            self.store.put('runs',ident,task)
        self.tasks[ident]=asyncio.create_task(self._execute(ident))
        return self.get(owner,ident)

    async def _execute(self,ident):
        task=self.store.get('runs',ident)
        if task['status']=='cancel_requested':
            task.update(status='cancelled',cleanup={'container_absent':True,'not_created':True},updated_at=now())
            self.store.put('runs',ident,task);self.tasks.pop(ident,None);return
        task.update(status='running',updated_at=now());self.store.put('runs',ident,task)
        try:
            async def update(step):
                current=self.store.get('runs',ident)
                current['steps'][step['index']].update({k:v for k,v in step.items() if k!='index'})
                current['updated_at']=now();self.store.put('runs',ident,current)
            invocation=dict(task)
            if task.get('input_task_id'):
                source=self.get(task['owner'],task['input_task_id'])
                expected=next(a for a in source['artifacts'] if a['name']=='result.tar.gz')
                payload=self.store.artifact(source['id'],'result.tar.gz')
                if len(payload)!=expected['size'] or hashlib.sha256(payload).hexdigest()!=expected['sha256']:raise ValueError('input artifact digest mismatch')
                invocation['_input_archive']=payload
            result=await self.executor.execute(invocation,update,lambda:self.store.get('runs',ident)['status']=='cancel_requested')
            current=self.store.get('runs',ident)
            for item in result.get('artifacts',[]):
                clean={k:v for k,v in item.items() if k!='data'}
                self.store.put_artifact(ident,item['name'],item['data']);current['artifacts'].append(clean)
            for key in ['sandbox_id','runtime','codex_session_id','cleanup','error','diagnostic']:
                if key in result:current[key]=result[key]
            current['result']=result.get('final')
            cancelled=current['status']=='cancel_requested'
            clean=bool(result.get('cleanup',{}).get('container_absent'))
            current['status']='cancelled' if cancelled and clean else 'completed' if result.get('ok') and clean else 'blocked'
            current['updated_at']=now();self.store.put('runs',ident,current)
        except asyncio.CancelledError:
            current=self.store.get('runs',ident);current.update(status='interrupted',updated_at=now());self.store.put('runs',ident,current);raise
        except Exception as exc:
            current=self.store.get('runs',ident);current.update(status='blocked',error=type(exc).__name__,updated_at=now());self.store.put('runs',ident,current)
        finally:self.tasks.pop(ident,None)

    async def wait(self,ident):
        task=self.tasks.get(ident)
        if task:await task

    async def cancel(self,owner,ident):
        with self.store.locked():
            task=self.get(owner,ident)
            if task['status'] not in ['queued','running']:raise ValueError('task is not executing')
            original=self.store.get('runs',ident);original.update(status='cancel_requested',updated_at=now());self.store.put('runs',ident,original)
        return self.get(owner,ident)

    async def shutdown(self):
        pending=list(self.tasks.values())
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)


async def run_task(settings, instructions:str, *, image_profile:str, request_id:str, input_task_id:str|None=None, owner:str='operator') -> dict:
    """Execute and persist one generic task through the same engine as the Web API."""
    from .settings import state_paths
    from .service import TaskService
    from .codex_tasks import configured_executor
    sandbox=TaskService(settings)
    tasks=AgentTaskService(TaskStore(state_paths(home=settings.home)['state_dir']),configured_executor(settings,sandbox))
    try:
        body={'request_id':request_id,'instructions':instructions,'image_profile':image_profile}
        if input_task_id:body['input_task_id']=input_task_id
        task=await tasks.create(owner,body)
        await tasks.wait(task['id'])
        return tasks.get(owner,task['id'])
    finally:
        await tasks.shutdown()
        await sandbox.shutdown()
