import asyncio
import os
from pathlib import Path
import tempfile
import unittest
import httpx
from chattask import TaskSettings,create_app
from chattask.workflow import TaskStore,WorkflowService
from chattask.agent_tasks import AgentTaskService

class AgentTaskWebTests(unittest.TestCase):
    def test_generic_task_api_uses_host_auth_and_never_requires_repository(self):
        class Runner:
            images={'python':'example.test/codex:1'}
            async def execute(self,task,update,cancelled):
                return {'ok':True,'final':'generic result','artifacts':[],'cleanup':{'container_absent':True}}
        class Service:
            session=None
            async def sweep(self):await asyncio.sleep(100)
            async def shutdown(self):pass
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(Path(root)/'chattask');tasks=AgentTaskService(store,Runner())
            app=create_app(TaskSettings('http://127.0.0.1:12345','synthetic',origin='http://testserver',username='operator',password='synthetic',home=root),service=Service(),workflow=WorkflowService(store),agent_tasks=tasks)
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    self.assertEqual((await c.get('/api/agent/images')).status_code,401)
                    login=await c.post('/auth/login',headers={'Origin':'http://testserver'},json={'username':'operator','password':'synthetic','next':'/'})
                    headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf_token']}
                    self.assertEqual((await c.get('/api/agent/images')).json()['images'][0]['id'],'python')
                    body={'request_id':'web-task-1','instructions':'Write a report.','image_profile':'python'}
                    self.assertEqual((await c.post('/api/agent/tasks',json=body)).status_code,403)
                    created=await c.post('/api/agent/tasks',headers=headers,json=body)
                    self.assertEqual(created.status_code,200,created.text)
                    task=created.json();await tasks.wait(task['id'])
                    self.assertEqual((await c.get('/api/agent/tasks/'+task['id'])).json()['result'],'generic result')
                    self.assertEqual(len((await c.get('/api/agent/tasks')).json()['tasks']),1)
            asyncio.run(run())
