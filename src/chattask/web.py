"""Optional browser/API adapter; domain execution remains in TaskService."""
from __future__ import annotations
import asyncio
import hmac
import re
import time
from urllib.parse import urlencode
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from . import __version__
from .settings import TaskSettings, load_settings, load_guest_dependencies
from .service import TaskService
from .auth import create_auth
from .settings import state_paths
from .workflow import TaskStore, WorkflowService
from .executor import SandboxExecutor
from .tool_client import ChatPyPITools
from .release import ChatGHReleaseAdapter
from .workspace_analysis import WorkspaceAnalysis, AnalysisConflict, AnalysisUnavailable

ROOT=Path(__file__).resolve().parent

class TaskInput(BaseModel):
    values: list[int] = Field(min_length=1,max_length=8)

class CommandInput(BaseModel):
    command: str = Field(min_length=1,max_length=256)

def create_app(settings: TaskSettings | None=None, *, service: TaskService | None=None,
               workflow: WorkflowService | None=None, agent_tasks=None,
               analysis: WorkspaceAnalysis | None=None, auth_adapter=None, durable_tasks=None) -> FastAPI:
    """Construct explicitly; importing this module does not connect/start anything."""
    settings = settings if settings is not None else load_settings()
    auth = auth_adapter if auth_adapter is not None else create_auth(settings)
    service = service if service is not None else TaskService(settings)
    workflow = workflow if workflow is not None else WorkflowService(
        TaskStore(state_paths(home=settings.home)['state_dir']),
        executor=SandboxExecutor(service,dependency_archive=load_guest_dependencies(settings)), tool_client=ChatPyPITools(settings.tool_profile),
        release_adapter=ChatGHReleaseAdapter())
    from .agent_tasks import AgentTaskService
    from .codex_tasks import configured_executor
    agent_tasks = agent_tasks if agent_tasks is not None else AgentTaskService(workflow.store, configured_executor(settings, service))
    from .durable import DurableTaskService, DurableConflict
    durable_tasks = durable_tasks if durable_tasks is not None else DurableTaskService(
        workflow.store, agent_tasks.executor, research_enabled=bool(settings.research_container and settings.research_origin),
        research_origin=settings.research_origin)
    from .package_service import PackageService
    from .package_template import form_schema, intent_prefill
    public_dependencies=getattr(getattr(workflow,'executor',None),'dependency_archive',None)
    if public_dependencies is None:
        public_dependencies=load_guest_dependencies(settings)
    packages = PackageService(workflow.store, agent_tasks, broker_required=True,public_dependencies=public_dependencies)
    from .task_templates import TaskTemplateService
    task_templates = TaskTemplateService(durable_tasks, packages)
    analysis = analysis if analysis is not None else WorkspaceAnalysis(workflow.store, profile=settings.analysis_profile, home=settings.home)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sweep = None
        try:
            await analysis.recover()
            await workflow.recover()
            if callable(getattr(agent_tasks,'recover',None)):
                await agent_tasks.recover()
            sweep = asyncio.create_task(service.sweep())
            yield
        finally:
            if sweep is not None:
                sweep.cancel()
            await analysis.shutdown()
            await agent_tasks.shutdown()
            await workflow.shutdown()
            try:
                await service.shutdown()
            except Exception:
                pass

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    app.include_router(auth.router)

    @app.middleware('http')
    async def site_guard(request: Request, call_next):
        if request.url.path == '/health' and request.method == 'GET':
            return JSONResponse({'status': 'ready', 'package': 'ChatTask', 'version': __version__, 'backend': 'OpenSandbox'})
        integration = request.url.path.startswith('/integrations/workspace/')
        try:
            auth._check_host(request)
            if integration:
                token = request.headers.get('authorization', '')
                expected = 'Bearer ' + settings.workspace_key
                if not settings.workspace_key or not hmac.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
                    raise HTTPException(401, detail='workspace service authentication required')
                origin = request.headers.get('origin')
                if origin and origin != settings.origin:
                    raise HTTPException(403, detail='untrusted origin')
            elif request.url.path.startswith('/api/'):
                if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                    await auth.csrf_user(request)
                else:
                    await auth.current_user(request)
            elif request.url.path == '/':
                try:
                    await auth.current_user(request)
                except HTTPException as exc:
                    if exc.status_code == 401:
                        target = '/'
                        ids = request.query_params.getlist('workspace_task')
                        if len(ids) == 1 and re.fullmatch(r'[0-9a-f]{32}', ids[0]):
                            target += '?workspace_task=' + ids[0]
                        return RedirectResponse('/auth/?' + urlencode({'next': target}), status_code=303)
                    raise
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'error': 'invalid content length'}, status_code=400)
        limit = 65536 if integration or request.url.path == '/api/agent/tasks' or request.url.path.startswith('/api/package/') or request.url.path.startswith('/api/durable/') or request.url.path.startswith('/api/task-templates/') else 4096
        if length > limit or length < 0:
            return JSONResponse({'error': 'request too large'}, status_code=413)
        if request.method in ('POST','PUT','PATCH'):
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit:return JSONResponse({'error':'request too large'},status_code=413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        return response

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'web_static/index.html')

    @app.get('/style.css')
    async def css():
        return FileResponse(ROOT / 'web_static/style.css')

    @app.get('/app.js')
    async def js():
        return FileResponse(ROOT / 'web_static/app.js')

    @app.get('/health')
    async def health():
        return {'status': 'ready', 'package': 'ChatTask', 'version': __version__, 'backend': 'OpenSandbox'}

    def workspace_error(exc):
        if isinstance(exc, KeyError):
            raise HTTPException(404, detail='workspace task or report not found') from None
        if isinstance(exc, AnalysisConflict):
            raise HTTPException(409, detail=str(exc)) from None
        if isinstance(exc, AnalysisUnavailable):
            raise HTTPException(503, detail=str(exc)) from None
        if isinstance(exc, ValueError):
            raise HTTPException(422, detail=str(exc)) from None
        raise HTTPException(503, detail='workspace analysis unavailable') from None

    @app.get('/integrations/workspace/capabilities')
    async def workspace_capabilities():
        return {'enabled': True, 'kind': 'workspace-review', 'scope': 'provided_materials_only',
                'model_configured': analysis.model_configured()}

    @app.post('/integrations/workspace/tasks')
    async def workspace_create_task(body: dict):
        try:
            task, created = analysis.create_task(body)
            return JSONResponse({'task': task}, status_code=201 if created else 200)
        except Exception as exc:
            workspace_error(exc)

    @app.get('/integrations/workspace/tasks')
    async def workspace_tasks(actor: str, proposal_id: str):
        try:
            return {'tasks': analysis.list_tasks(actor, proposal_id)}
        except Exception as exc:
            workspace_error(exc)

    @app.get('/integrations/workspace/tasks/{task_id}')
    async def workspace_task(task_id: str, actor: str):
        try:
            return {'task': analysis.get_task(actor, task_id)}
        except Exception as exc:
            workspace_error(exc)

    @app.post('/integrations/workspace/tasks/{task_id}/runs')
    async def workspace_create_run(task_id: str, body: dict):
        try:
            run, created = await analysis.create_run(task_id, body)
            return JSONResponse({'run': run}, status_code=201 if created else 200)
        except Exception as exc:
            workspace_error(exc)

    @app.post('/integrations/workspace/tasks/{task_id}/runs/{run_id}/cancel')
    async def workspace_cancel(task_id: str, run_id: str, body: dict):
        try:
            if not isinstance(body, dict) or set(body) != {'actor'}:
                raise ValueError('invalid cancel input')
            return {'run': await analysis.cancel(body['actor'], task_id, run_id)}
        except Exception as exc:
            workspace_error(exc)

    @app.get('/integrations/workspace/tasks/{task_id}/runs/{run_id}/report')
    async def workspace_report(task_id: str, run_id: str, actor: str):
        try:
            return analysis.report(actor, task_id, run_id)
        except Exception as exc:
            workspace_error(exc)

    @app.get('/api/workspace-tasks')
    async def native_workspace_tasks():
        return {'tasks': analysis.native_list()}

    @app.get('/api/workspace-tasks/{task_id}')
    async def native_workspace_task(task_id: str):
        try:
            return {'task': analysis.native_task(task_id)}
        except Exception as exc:
            workspace_error(exc)

    @app.get('/api/workspace-tasks/{task_id}/runs/{run_id}/report')
    async def native_workspace_report(task_id: str, run_id: str):
        try:
            return analysis.native_report(task_id, run_id)
        except Exception as exc:
            workspace_error(exc)

    @app.post('/api/tasks')
    async def task(body: TaskInput):
        try:
            return await service.once(body.values)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(503, detail=f'task failed: {type(exc).__name__}')

    @app.post('/api/sessions')
    async def open_session():
        try:
            return await service.open_session()
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(503, detail=f'create failed: {type(exc).__name__}')

    @app.get('/api/sessions/{session_id}')
    async def session_status(session_id: str):
        s = service.session
        if not s or s.id != session_id:
            raise HTTPException(404, detail='session not found')
        return {'id': s.id, 'sandbox_id': s.sandbox_id,
                'expires_in_seconds': max(0, int(s.expires_at - time.time()))}

    @app.post('/api/sessions/{session_id}/commands')
    async def command(session_id: str, body: CommandInput):
        try:
            return await service.run_session(session_id, body.command)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(503, detail=f'command failed: {type(exc).__name__}')

    @app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        try:
            return await service.close_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(503, detail=f'cleanup failed: {type(exc).__name__}')

    async def owner(request: Request) -> str:
        principal = await auth.current_user(request)
        return principal.user_id

    def durable_error(exc):
        if isinstance(exc, DurableConflict):
            raise HTTPException(409, detail=str(exc))
        if isinstance(exc, KeyError):
            raise HTTPException(404, detail='task not found')
        if isinstance(exc, ValueError):
            raise HTTPException(422, detail=str(exc))
        raise exc

    @app.get('/api/task-templates')
    async def task_template_catalog(request: Request):
        await owner(request)
        return {'templates': task_templates.catalog()}

    @app.post('/api/task-templates/{template_id}/instantiate')
    async def task_template_instantiate(request: Request, template_id: str, body: dict):
        try:
            return await task_templates.instantiate(await owner(request), template_id, body)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/task-library')
    async def task_library(request: Request):
        return {'tasks': task_templates.library(await owner(request)), 'limit': 100}

    @app.get('/api/task-resources')
    async def task_resources(request: Request):
        await owner(request)
        executor = getattr(agent_tasks, 'executor', None)
        runtime = getattr(executor, 'runtime', None)
        gateway = getattr(executor, 'gateway', None)
        return {'image_profiles': agent_tasks.images(),
                'model': {'configured': runtime is not None, 'model': getattr(runtime, 'model', None), 'verification': 'not-probed'},
                'gateway': {'configured': gateway is not None, 'verification': 'not-probed',
                            'authorization': 'prepare-read-only; publish-exact-plan-after-owner-confirmation'},
                'publisher': {'binding': 'existing-tag-triggered-OIDC', 'verification': 'resolved-in-preflight; no automatic reconfiguration'},
                'credentials_are_server_side': True,
                'note': 'Configured references are not proof of reachability or publication approval.'}

    @app.get('/api/package/drafts/{draft_id}/task-package')
    async def python_task_definition(request: Request, draft_id: str, revision: str | None = None):
        try:
            return packages.task_definition(await owner(request), draft_id, revision)
        except FileNotFoundError:
            raise HTTPException(404, detail='Python task definition is unavailable')
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/package/drafts/{draft_id}/task-data.zip')
    async def python_task_data_export(request: Request, draft_id: str):
        import hashlib
        try:
            data = packages.export_task_data(await owner(request), draft_id)
        except FileNotFoundError:
            raise HTTPException(404, detail='Python task evidence is unavailable')
        except (KeyError, ValueError) as exc:
            durable_error(exc)
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="python-task-data-{draft_id}.zip"',
            'X-Content-SHA256': hashlib.sha256(data).hexdigest()})

    @app.get('/api/package/drafts/{draft_id}/task-package.zip')
    async def python_task_definition_export(request: Request, draft_id: str, revision: str | None = None):
        import hashlib
        try:
            data = packages.export_task(await owner(request), draft_id, revision)
        except FileNotFoundError:
            raise HTTPException(404, detail='Python task definition is unavailable')
        except (KeyError, ValueError) as exc:
            durable_error(exc)
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="python-task-{draft_id}.zip"',
            'X-Content-SHA256': hashlib.sha256(data).hexdigest()})

    @app.post('/api/durable/research/auto')
    async def durable_auto_research(request: Request, body: dict):
        principal = await owner(request)
        if not isinstance(body, dict) or set(body) - {'request_id', 'instructions', 'image_profile', 'query', 'max_results'}:
            raise HTTPException(422, detail='invalid auto research fields')
        if not durable_tasks.research_enabled:
            raise HTTPException(422, detail='research workflow is not configured')
        try:
            return await durable_tasks.create_auto_research(principal, body)
        except (DurableConflict, ValueError, KeyError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks')
    async def durable_create(request: Request, body: dict):
        try:
            return durable_tasks.create(await owner(request), body)
        except (DurableConflict, ValueError, KeyError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks')
    async def durable_list(request: Request):
        return {'tasks': durable_tasks.list(await owner(request))}

    @app.get('/api/durable/worker-state')
    async def durable_worker_state(request: Request):
        await owner(request)
        return durable_tasks.queue_state()

    @app.get('/api/durable/tasks/{task_id}')
    async def durable_get(request: Request, task_id: str):
        try:
            return durable_tasks.get(await owner(request), task_id)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/runs')
    async def durable_runs(request: Request, task_id: str):
        try:
            return {'runs': durable_tasks.runs(await owner(request), task_id)}
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/package')
    async def durable_package(request: Request, task_id: str, revision: str | None = None):
        try:
            return durable_tasks.package(await owner(request), task_id, revision)
        except FileNotFoundError:
            raise HTTPException(409, detail='task definition artifact is unavailable')
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.put('/api/durable/tasks/{task_id}/package')
    async def durable_package_revise(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.revise_package(await owner(request), task_id, body)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/package.zip')
    async def durable_package_export(request: Request, task_id: str, revision: str | None = None):
        import hashlib
        try:
            data = durable_tasks.export_package(await owner(request), task_id, revision)
        except FileNotFoundError:
            raise HTTPException(409, detail='task definition artifact is unavailable')
        except (KeyError, ValueError) as exc:
            durable_error(exc)
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="task-{task_id}.zip"',
            'X-Content-SHA256': hashlib.sha256(data).hexdigest()})

    @app.post('/api/durable/tasks/{task_id}/reuse')
    async def durable_package_reuse(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.reuse_package(await owner(request), task_id, body)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/runs/{run_id}/evaluations')
    async def durable_evaluations(request: Request, task_id: str, run_id: str):
        try:
            return {'evaluations': durable_tasks.evaluations(await owner(request), task_id, run_id)}
        except FileNotFoundError:
            raise HTTPException(404, detail='round not found')
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/runs/{run_id}/evaluate')
    async def durable_evaluate(request: Request, task_id: str, run_id: str, body: dict):
        try:
            return await durable_tasks.evaluate_package(await owner(request), task_id, run_id, body)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/review')
    async def durable_review(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.review_package(await owner(request), task_id, body)
        except (KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/runs/{run_id}/data.zip')
    async def durable_data_export(request: Request, task_id: str, run_id: str):
        import hashlib
        try:
            principal = await owner(request)
            durable_tasks.get(principal, task_id)
            run = next(r for r in durable_tasks.runs(principal, task_id) if r['id'] == run_id)
            receipt = run.get('data_archive', {})
            if receipt.get('status') != 'ready':
                raise HTTPException(409, detail='round data archive is not ready')
            data = durable_tasks.store.artifact(run_id, receipt['name'])
            if len(data) != receipt['size'] or hashlib.sha256(data).hexdigest() != receipt['sha256']:
                raise HTTPException(409, detail='round data archive integrity check failed')
        except (KeyError, ValueError, FileNotFoundError, StopIteration):
            raise HTTPException(404, detail='round not found')
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="task-{task_id}-run-{run_id}.zip"',
            'X-Content-SHA256': receipt['sha256']})

    @app.post('/api/durable/tasks/{task_id}/resume')
    async def durable_resume(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.resume(await owner(request), task_id, body)
        except (DurableConflict, KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/pause')
    async def durable_pause(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.pause(await owner(request), task_id, body)
        except (DurableConflict, KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/complete')
    async def durable_complete(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.complete(await owner(request), task_id, body)
        except (DurableConflict, KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/reconcile-research')
    async def durable_reconcile_research(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.reconcile_research(await owner(request), task_id, body)
        except (DurableConflict, KeyError, ValueError) as exc:
            durable_error(exc)

    @app.post('/api/durable/tasks/{task_id}/members')
    async def durable_grant(request: Request, task_id: str, body: dict):
        try:
            return durable_tasks.grant(await owner(request), task_id, body)
        except (DurableConflict, KeyError, ValueError) as exc:
            durable_error(exc)

    @app.get('/api/durable/tasks/{task_id}/runs/{run_id}/artifacts/{name}')
    async def durable_artifact(request: Request, task_id: str, run_id: str, name: str):
        try:
            data, entry = durable_tasks.artifact(await owner(request), task_id, run_id, name)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            durable_error(exc)
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{name}"', 'X-Content-SHA256': entry['sha256']})

    @app.get('/api/agent/images')
    async def agent_images():
        return {'images': agent_tasks.images()}

    @app.post('/api/agent/tasks')
    async def create_agent_task(request: Request, body: dict):
        try:
            return await agent_tasks.create(await owner(request), body)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.get('/api/agent/tasks')
    async def list_agent_tasks(request: Request):
        return {'tasks': agent_tasks.list(await owner(request))}

    @app.get('/api/agent/tasks/{task_id}')
    async def get_agent_task(request: Request, task_id: str):
        try:
            return agent_tasks.get(await owner(request), task_id)
        except (KeyError, ValueError, FileNotFoundError):
            raise HTTPException(404, detail='task not found')

    @app.post('/api/agent/tasks/{task_id}/cancel')
    async def cancel_agent_task(request: Request, task_id: str):
        try:
            return await agent_tasks.cancel(await owner(request), task_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='task not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.get('/api/agent/tasks/{task_id}/artifacts/{name}')
    async def download_agent_artifact(request: Request, task_id: str, name: str):
        try:
            task = agent_tasks.get(await owner(request), task_id)
            entry = next(a for a in task['artifacts'] if a['name'] == name)
            data = agent_tasks.store.artifact(task_id, name)
            import hashlib
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('artifact digest mismatch')
        except (KeyError, ValueError, FileNotFoundError, StopIteration):
            raise HTTPException(404, detail='artifact not found')
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{name}"', 'X-Content-SHA256': entry['sha256']})

    @app.get('/api/package/schema')
    async def package_schema():
        return {**form_schema(), 'available': bool(getattr(getattr(agent_tasks,'executor',None),'gateway',None))}

    @app.post('/api/package/intent')
    async def package_intent(body: dict):
        if not isinstance(body, dict) or set(body) != {'text'}:
            raise HTTPException(422, detail='text required')
        try:
            return {'prefill': intent_prefill(body['text'])}
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.get('/api/package/drafts')
    async def package_drafts(request: Request):
        return {'drafts': packages.list(await owner(request))}

    @app.post('/api/package/drafts')
    async def package_create(request: Request, body: dict):
        try:
            return await packages.create(await owner(request), body)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.get('/api/package/drafts/{draft_id}')
    async def package_detail(request: Request, draft_id: str):
        try:
            return packages.get(await owner(request), draft_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='draft not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.put('/api/package/drafts/{draft_id}')
    async def package_update(request: Request, draft_id: str, body: dict):
        try:
            return await packages.update(await owner(request), draft_id, body)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='draft not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.post('/api/package/drafts/{draft_id}/prepare')
    async def package_prepare(request: Request, draft_id: str, body: dict):
        try:
            return await packages.prepare(await owner(request), draft_id, body)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='draft not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.post('/api/package/drafts/{draft_id}/confirm')
    async def package_confirm(request: Request, draft_id: str, body: dict):
        try:
            return await packages.confirm(await owner(request), draft_id, body)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='draft not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.post('/api/package/drafts/{draft_id}/recheck')
    async def package_recheck(request: Request, draft_id: str, body: dict):
        try:
            return await packages.recheck(await owner(request), draft_id, body)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='draft not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.get('/api/templates')
    async def templates():
        return {'templates': workflow.templates()}

    @app.post('/api/templates')
    async def create_template(body: dict):
        try:
            return workflow.save_template(body)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.put('/api/templates/{template_id}')
    async def revise_template(template_id: str, body: dict):
        if body.get('id') != template_id:
            raise HTTPException(422, detail='template ID mismatch')
        try:
            return workflow.save_template(body, update=True)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.get('/api/templates/{template_id}/revisions')
    async def template_revisions(template_id: str):
        try:
            return {'revisions': workflow.revisions(template_id)}
        except ValueError:
            raise HTTPException(404, detail='template not found')

    @app.get('/api/templates/{template_id}/revisions/{revision}')
    async def template_revision(template_id: str, revision: int):
        try:
            return workflow.template(template_id, revision)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, detail='template revision not found')

    @app.get('/api/repositories')
    async def repositories():
        return {'repositories': workflow.repositories()}

    @app.get('/api/tool-connections/status')
    async def tool_connection_status():
        if workflow.tool_client is None:
            return {'available': False, 'reason': 'tool client unavailable'}
        return await workflow.tool_client.status()

    @app.post('/api/runs')
    async def create_run(request: Request, body: dict):
        try:
            return await workflow.create_run(await owner(request), body)
        except FileNotFoundError:
            raise HTTPException(404, detail='template or repository not found')
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc))

    @app.get('/api/runs')
    async def runs(request: Request):
        return {'runs': workflow.list_runs(await owner(request))}

    @app.get('/api/runs/{run_id}')
    async def run_detail(request: Request, run_id: str):
        try:
            return workflow.get_run(await owner(request), run_id)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, detail='run not found')

    @app.post('/api/runs/{run_id}/cancel')
    async def cancel_run(request: Request, run_id: str):
        try:
            return await workflow.cancel(await owner(request), run_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='run not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.post('/api/runs/{run_id}/approve')
    async def approve_run(request: Request, run_id: str, body: dict):
        try:
            return await workflow.approve(await owner(request), run_id, body)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, detail='run not found')
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc))

    @app.get('/api/runs/{run_id}/artifacts/{name}')
    async def download_artifact(request: Request, run_id: str, name: str):
        try:
            data = workflow.artifact(await owner(request), run_id, name)
            record = workflow.get_run(await owner(request), run_id)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, detail='artifact not found or failed integrity check')
        digest = next(item['sha256'] for item in record['artifacts'] if item['name'] == name)
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{name}"', 'X-Content-SHA256': digest})
    return app
