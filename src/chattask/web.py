"""Optional browser/API adapter; domain execution remains in TaskService."""
from __future__ import annotations
import asyncio
import hmac
import time
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
               analysis: WorkspaceAnalysis | None=None, auth_adapter=None) -> FastAPI:
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
    from .package_service import PackageService
    from .package_template import form_schema, intent_prefill
    public_dependencies=getattr(getattr(workflow,'executor',None),'dependency_archive',None)
    if public_dependencies is None:
        public_dependencies=load_guest_dependencies(settings)
    packages = PackageService(workflow.store, agent_tasks, broker_required=True,public_dependencies=public_dependencies)
    analysis = analysis if analysis is not None else WorkspaceAnalysis(workflow.store, profile=settings.analysis_profile, home=settings.home)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await workflow.recover()
        await analysis.recover()
        if callable(getattr(agent_tasks,'recover',None)):
            await agent_tasks.recover()
        sweep = asyncio.create_task(service.sweep())
        try:
            yield
        finally:
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
                        return RedirectResponse('/auth/?next=/', status_code=303)
                    raise
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'error': 'invalid content length'}, status_code=400)
        limit = 65536 if integration or request.url.path == '/api/agent/tasks' or request.url.path.startswith('/api/package/') else 4096
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
