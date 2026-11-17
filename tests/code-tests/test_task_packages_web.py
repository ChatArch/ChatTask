"""Actual ASGI routes with synthetic principals; no live models or accounts."""
import asyncio
import io
import zipfile
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, HTTPException
from chattask import TaskSettings, create_app
from chattask.workflow import TaskStore, WorkflowService
from chattask.durable import DurableTaskService
from chattask.agent_tasks import AgentTaskService


class Auth:
    router=APIRouter()
    def _check_host(self,request):pass
    async def current_user(self,request):
        actor=request.headers.get('X-Test-Principal')
        if not actor:raise HTTPException(401)
        return SimpleNamespace(user_id=actor)
    async def csrf_user(self,request):
        if request.headers.get('X-CSRF-Token')!='synthetic-csrf':raise HTTPException(403)
        return await self.current_user(request)


class Sandbox:
    session=None
    async def sweep(self):await asyncio.sleep(100)
    async def shutdown(self):pass


class Executor:
    images={'python':'synthetic-image'}


def app_for(tmp_path):
    store=TaskStore(tmp_path/'state');executor=Executor()
    durable=DurableTaskService(store,executor)
    app=create_app(TaskSettings('http://127.0.0.1:12345','synthetic',origin='http://testserver',
                               username='operator',password='synthetic',home=str(tmp_path)),
                   service=Sandbox(),workflow=WorkflowService(store),
                   agent_tasks=AgentTaskService(store,executor),durable_tasks=durable,auth_adapter=Auth())
    return app,durable


def test_definition_routes_are_private_and_export_a_real_package(tmp_path):
    app,durable=app_for(tmp_path)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            owner={'X-Test-Principal':'owner','X-CSRF-Token':'synthetic-csrf'}
            other={'X-Test-Principal':'other','X-CSRF-Token':'synthetic-csrf'}
            created=await client.post('/api/durable/tasks',headers=owner,json={'request_id':'one','instructions':'Write a report','image_profile':'python'})
            assert created.status_code==200
            ident=created.json()['id'];url=f'/api/durable/tasks/{ident}'
            response=await client.get(url+'/package',headers=owner)
            assert response.status_code==200,'Task package Web API is not implemented'
            package=response.json();assert package['purpose']=='Write a report'
            assert (await client.get(url+'/package',headers=other)).status_code==404
            assert (await client.get(url+'/package')).status_code==401
            exported=await client.get(url+'/package.zip',headers=owner)
            assert exported.status_code==200
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                assert 'task.toml' in archive.namelist()
            assert (await client.put(url+'/package',headers={'X-Test-Principal':'owner'},json={})).status_code==403
            invalid=await client.put(url+'/package',headers=owner,json={'request_id':'edit','expected_revision':'wrong','definition':package['files']})
            assert invalid.status_code==409
            reused=await client.post(url+'/reuse',headers=owner,json={'request_id':'reuse'})
            assert reused.status_code==200 and reused.json()['id']!=ident
    asyncio.run(exercise())


def test_task_page_exposes_definition_and_review_controls(tmp_path):
    app,_=app_for(tmp_path)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            # Static application assets retain their existing authentication boundary.
            response=await client.get('/',headers={'X-Test-Principal':'owner'})
            assert response.status_code==200
            assert 'durable-package-files' in response.text,'No task-package creation/import control'
            assert 'durable-package-purpose' in response.text
            assert 'durable-package-acceptance' in response.text
            assert 'durable-package-export' in response.text
            assert 'task-template-picker' in response.text,'No task-template selection UI'
            assert 'python-task-resources' in response.text,'Python releases have no task resource display'
            assert 'python-task-export' in response.text
    asyncio.run(exercise())


def test_conversation_intake_creates_owned_task_and_returns_exact_web_link(tmp_path):
    app,durable=app_for(tmp_path)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            headers={'X-Test-Principal':'owner','X-CSRF-Token':'synthetic-csrf'}
            body={'request_id':'conversation-message-1','instructions':'实现一个有测试的小工具'}
            first=await client.post('/api/task-intake',headers=headers,json=body)
            assert first.status_code==200,first.text
            value=first.json();ident=value['task_id']
            assert value['url']=='http://testserver/?task='+ident
            assert value['task']['owner']=='owner' and value['status']=='queued'
            again=await client.post('/api/task-intake',headers=headers,json=body)
            assert again.json()['task_id']==ident and len(durable.list('owner'))==1
            assert (await client.post('/api/task-intake',json=body)).status_code in (401,403)
            assert (await client.post('/api/task-intake',headers=headers,json={**body,'owner':'other'})).status_code==422
            assert (await client.get('/?task='+ident)).headers['location'].endswith('next=%2F%3Ftask%3D'+ident)
    asyncio.run(exercise())


def test_conversation_intake_can_create_release_draft_without_publication(tmp_path):
    app,_=app_for(tmp_path)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            headers={'X-Test-Principal':'owner','X-CSRF-Token':'synthetic-csrf'}
            spec={'mode':'existing','package_name':'example-taskpkg','module':'example_taskpkg','cli':'taskpkg','owner':'example-team','repository':'example-taskpkg','description':'Example','requirements':'Add tests','version':'0.2.1'}
            response=await client.post('/api/task-intake',headers=headers,json={'request_id':'release-chat','template_id':'python-package','template_revision':1,'parameters':spec})
            assert response.status_code==200,response.text
            data=response.json();assert data['kind']=='python-package' and data['status']=='draft'
            assert data['url']=='http://testserver/#package='+data['task_id']
            assert data['task']['publish_task_id'] is None
    asyncio.run(exercise())


def test_template_catalog_python_task_resources_and_library_are_authenticated(tmp_path):
    app,_=app_for(tmp_path)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            owner={'X-Test-Principal':'owner','X-CSRF-Token':'synthetic-csrf'}
            catalog=await client.get('/api/task-templates',headers=owner)
            assert catalog.status_code==200,'No shared Task Template API'
            assert {'research-report','python-package'} <= {t['id'] for t in catalog.json()['templates']}
            parameters={'mode':'existing','package_name':'example-taskpkg','module':'example_taskpkg','cli':'taskpkg',
                        'owner':'example-team','repository':'example-taskpkg','description':'A release task',
                        'requirements':'Ship a tested utility','version':'0.2.1'}
            created=await client.post('/api/task-templates/python-package/instantiate',headers=owner,
                json={'request_id':'python-task','template_revision':1,'parameters':parameters})
            assert created.status_code==200,created.text
            ident=created.json()['id']
            definition=await client.get(f'/api/package/drafts/{ident}/task-package',headers=owner)
            assert definition.status_code==200 and definition.json()['template']['id']=='python-package'
            exported=await client.get(f'/api/package/drafts/{ident}/task-package.zip',headers=owner)
            assert exported.status_code==200 and exported.content.startswith(b'PK')
            data_export=await client.get(f'/api/package/drafts/{ident}/task-data.zip',headers=owner)
            assert data_export.status_code==200 and data_export.content.startswith(b'PK')
            assert (await client.get(f'/api/package/drafts/{ident}/task-package',headers={'X-Test-Principal':'other'})).status_code==404
            resources=await client.get('/api/task-resources',headers=owner)
            assert resources.status_code==200 and resources.json()['credentials_are_server_side'] is True
            library=await client.get('/api/task-library',headers=owner)
            assert library.status_code==200
            assert any(t['kind']=='python-package' and t['id']==ident for t in library.json()['tasks'])
            assert (await client.get('/api/task-templates')).status_code==401
    asyncio.run(exercise())

