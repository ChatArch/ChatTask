"""The lease identity must not be overwritten by a native Codex event ID."""
import asyncio,json,unittest
from types import SimpleNamespace as N
from unittest.mock import patch
from chattask.codex_tasks import CodexRuntime,CodexTaskExecutor
from chattask.broker import GatewaySession
from chattask.settings import TaskSettings
class GatewayLeaseIdentityTests(unittest.TestCase):
 def test_missing_native_thread_still_closes_issued_gateway_lease(self):
  closed=[];disposed=[]
  class Commands:
   async def run(self,command,opts):return N()
  class Files:
   async def write_file(self,*args,**kwargs):pass
   async def read_file(self,path):
    return {'/workspace/agent-events.jsonl':json.dumps({'type':'turn.completed'}),'/workspace/agent-final.md':'actual fixture result','/workspace/agent-started.txt':'/workspace/work','/workspace/agent-stderr.txt':''}.get(path,'')
  box=N(id='sandboxfixture',commands=Commands(),files=Files())
  class Service:
   docker=N(containers=N(list=lambda **kwargs:[]))
   async def create(self,**kwargs):return box,None,{}
   async def dispose(self,*args):disposed.append(True)
   def output(self,execution):return {'exit_code':0,'stdout':'','stderr':'','error':None}
  class Gateway:
   def create(self,ident,mode,plan):return GatewaySession(ident,'https://gateway.example.test/sessions/'+ident+'/v1','example-model-token','https://gateway.example.test/sessions/'+ident,'example-tool-token','2099-01-01T00:00:00Z')
   def close(self,ident):closed.append(ident);return {'closed':True}
  async def run():
   parent=N(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','example-sandbox-key'),docker=Service.docker)
   executor=CodexTaskExecutor(parent,CodexRuntime('https://gateway.example.test/v1','pending',images={'python':'image'}),gateway=Gateway())
   async def update(step):pass
   with patch('chattask.codex_tasks.TaskService',return_value=Service()):return await executor.execute({'id':'a'*32,'image_profile':'python','instructions':'fixture','capability':{'mode':'publish','plan':{}}},update,lambda:False)
  result=asyncio.run(run());self.assertFalse(result['ok']);self.assertIn('native completion',result['error']);self.assertTrue(disposed);self.assertEqual(closed,['a'*32]);self.assertEqual(result['credential_closure'],{'closed':True})
 def test_invalid_verify_input_is_rejected_before_grant(self):
  issued=[]
  class Gateway:
   def create(self,ident,mode,plan):issued.append(ident);return GatewaySession(ident,'','','https://gateway.example.test/sessions/'+ident,'example-tool-token','2099-01-01T00:00:00Z')
  async def run():
   parent=N(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','example-sandbox-key'),docker=N())
   executor=CodexTaskExecutor(parent,CodexRuntime('https://gateway.example.test/v1','pending',images={'python':'image'}),gateway=Gateway())
   async def hook(*args):pass
   async def update(step):pass
   executor.register_postprocessor('verify',hook,480)
   with self.assertRaises(ValueError):await executor.execute({'id':'a'*32,'image_profile':'python','instructions':'fixture','capability':{'mode':'verify','plan':{},'source_task_id':'b'*32}},update,lambda:False)
  asyncio.run(run());self.assertEqual(issued,[])
 def test_cancelled_lock_waiter_has_not_issued_a_lease(self):
  issued=[]
  class Gateway:
   def create(self,ident,mode,plan):issued.append(ident);return GatewaySession(ident,'https://gateway.example.test/sessions/'+ident+'/v1','example-model-token','','','2099-01-01T00:00:00Z')
  async def inline(function,*args):return function(*args)
  async def run():
   lock=asyncio.Lock();await lock.acquire();parent=N(lock=lock,settings=TaskSettings('http://127.0.0.1:8000','example-sandbox-key'),docker=N())
   executor=CodexTaskExecutor(parent,CodexRuntime('https://gateway.example.test/v1','pending',images={'python':'image'}),gateway=Gateway())
   async def update(step):pass
   with patch('chattask.codex_tasks.asyncio.to_thread',inline):
    task=asyncio.create_task(executor.execute({'id':'a'*32,'image_profile':'python','instructions':'fixture'},update,lambda:False));await asyncio.sleep(0);task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
   lock.release()
  asyncio.run(run());self.assertEqual(issued,[])

