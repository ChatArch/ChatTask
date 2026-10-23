"""Optional browser/API adapter; domain execution remains in TaskService."""
from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from . import __version__
from .settings import TaskSettings, load_settings
from .service import TaskService
from .auth import create_auth

ROOT=Path(__file__).resolve().parent

class TaskInput(BaseModel):
    values: list[int] = Field(min_length=1,max_length=8)

class CommandInput(BaseModel):
    command: str = Field(min_length=1,max_length=256)

def create_app(settings: TaskSettings | None=None, *, service: TaskService | None=None) -> FastAPI:
    """Construct explicitly; importing this module does not connect/start anything."""
    settings = settings if settings is not None else load_settings()
    auth = create_auth(settings)
    service = service if service is not None else TaskService(settings)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sweep = asyncio.create_task(service.sweep())
        try:
            yield
        finally:
            sweep.cancel()
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
        try:
            auth._check_host(request)
            if request.url.path.startswith('/api/'):
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
        if length > 4096 or length < 0:
            return JSONResponse({'error': 'request too large'}, status_code=413)
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
    return app
