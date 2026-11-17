"""Importable task/session orchestration over an explicitly configured OpenSandbox."""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from chatsandbox import create_backend_from_profile, create_docker_backend
from opensandbox.models import WriteEntry
from .settings import TaskSettings

@dataclass
class Session:
    id: str
    sandbox_id: str
    shell_id: str
    expires_at: float

class TaskService:
    def __init__(self, settings: TaskSettings, *, backend=None, docker_client=None):
        self.settings = settings
        self.session: Session | None = None
        self.lock = asyncio.Lock()
        if backend is not None and docker_client is not None:
            raise ValueError('Choose a Sandbox backend or a legacy Docker client')
        if backend is not None:
            self.backend = backend
        elif settings.sandbox_profile:
            if docker_client is not None:
                raise ValueError('A named Sandbox profile cannot use a legacy Docker client')
            self.backend = create_backend_from_profile(settings.sandbox_profile, home=settings.home)
        else:
            self.backend = create_docker_backend(settings.endpoint, settings.api_key,
                                                 settings.image, docker_client=docker_client)

    def connection(self):
        return self.backend.connection()

    def for_image(self, image: str):
        """Keep the selected provider when a task chooses an allowed image."""
        return type(self)(replace(self.settings, image=image), backend=self.backend.for_image(image))

    async def connect(self, sandbox_id: str, cfg):
        return await self.backend.connect(sandbox_id, cfg)

    def inspect_owned(self, sandbox_id: str) -> dict:
        return self.backend.inspect_owned(sandbox_id)

    def verify_absent(self, sandbox_id: str) -> bool:
        """Provider inventory must positively establish absence; errors propagate."""
        return self.backend.verify_absent(sandbox_id) is True

    async def find_run_sandboxes(self, run_id: str) -> list[str]:
        """Resolve task-owned metadata through the shared provider's inventory."""
        if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise ValueError('invalid durable Run id')
        return await self.backend.find_owned({'purpose': 'chattask', 'durable_run_id': run_id})

    async def create(self, *, ttl_seconds: int = 600, durable_run_id: str | None = None):
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 7200:
            raise ValueError('sandbox lifetime must be 60–7200 seconds')
        if durable_run_id is not None and (not isinstance(durable_run_id, str) or len(durable_run_id) != 32 or any(c not in '0123456789abcdef' for c in durable_run_id)):
            raise ValueError('invalid durable Run id')
        return await self.backend.create(ttl_seconds=ttl_seconds,
            metadata={'purpose': 'chattask', **({'durable_run_id': durable_run_id} if durable_run_id else {})})

    async def dispose(self, sandbox, cfg) -> None:
        await self.backend.dispose(sandbox, cfg)

    @staticmethod
    def output(execution) -> dict:
        return {'execution_id': execution.id, 'exit_code': execution.exit_code,
                'stdout': '\n'.join(x.text for x in execution.logs.stdout)[:4096],
                'stderr': '\n'.join(x.text for x in execution.logs.stderr)[:4096],
                'error': str(execution.error.value)[:500] if execution.error else None}

    async def once(self, values: list[int]) -> dict:
        if any(abs(v) > 10000 for v in values):
            raise ValueError('each number must be within -10000..10000')
        async with self.lock:
            sandbox, cfg, runtime = await self.create()
            sandbox_id = sandbox.id
            cleanup = {'requested': False, 'container_absent': False}
            result = None
            try:
                program = (
                    'import json\n'
                    'from pathlib import Path\n'
                    'numbers=json.loads(Path("/workspace/input.json").read_text())\n'
                    'result={"count":len(numbers),"sum":sum(numbers),"mean":sum(numbers)/len(numbers)}\n'
                    'Path("/workspace/result.json").write_text(json.dumps(result))\n'
                    'print(json.dumps(result, sort_keys=True))\n'
                )
                tests = (
                    'import json, unittest\n'
                    'from pathlib import Path\n'
                    'class TestResult(unittest.TestCase):\n'
                    ' def test_sum(self):\n'
                    '  data=json.loads(Path("/workspace/result.json").read_text())\n'
                    '  numbers=json.loads(Path("/workspace/input.json").read_text())\n'
                    '  self.assertEqual(data["sum"],sum(numbers))\n'
                    ' def test_count(self):\n'
                    '  data=json.loads(Path("/workspace/result.json").read_text())\n'
                    '  numbers=json.loads(Path("/workspace/input.json").read_text())\n'
                    '  self.assertEqual(data["count"],len(numbers))\n'
                    'if __name__=="__main__": unittest.main()\n'
                )
                await sandbox.commands.run('mkdir -p /workspace')
                await sandbox.files.write_files([
                    WriteEntry(path='/workspace/input.json', data=json.dumps(values)),
                    WriteEntry(path='/workspace/program.py', data=program),
                    WriteEntry(path='/workspace/test_task.py', data=tests),
                ])
                ran = await sandbox.commands.run('python /workspace/program.py')
                tested = await sandbox.commands.run('python -m unittest -v /workspace/test_task.py')
                content = await sandbox.files.read_file('/workspace/result.json')
                result = json.loads(content)
                result_ok = not ran.error and ran.exit_code == 0
                tests_ok = not tested.error and tested.exit_code == 0
                result = {'task_id': uuid.uuid4().hex, 'sandbox_id': sandbox_id,
                          'result': result, 'program': self.output(ran),
                          'tests': self.output(tested), 'passed': result_ok and tests_ok,
                          'runtime': runtime}
            finally:
                try:
                    await self.dispose(sandbox, cfg)
                    cleanup['requested'] = True
                    cleanup['container_absent'] = self.verify_absent(sandbox_id)
                except Exception as exc:
                    cleanup['error'] = type(exc).__name__
            if result is None:
                raise RuntimeError('sandbox task failed; inspect task-owned server logs')
            result['cleanup'] = cleanup
            if not cleanup['container_absent']:
                raise RuntimeError('sandbox cleanup not verified')
            return result

    async def open_session(self) -> dict:
        async with self.lock:
            if self.session is not None:
                raise ValueError('one demo session at a time; close the current session first')
            sandbox, cfg, runtime = await self.create()
            try:
                init = await sandbox.commands.run('mkdir -p /workspace/interactive')
                if init.error or init.exit_code != 0:
                    raise RuntimeError('workspace setup failed')
                shell_id = await sandbox.commands.create_session(working_directory='/workspace/interactive')
                self.session = Session(id=uuid.uuid4().hex, sandbox_id=sandbox.id,
                                       shell_id=shell_id, expires_at=time.time() + 300)
                return {'id': self.session.id, 'sandbox_id': sandbox.id,
                        'expires_in_seconds': 300, 'runtime': runtime}
            except BaseException:
                await self.dispose(sandbox, cfg)
                raise
            finally:
                await sandbox.close()
                await cfg.transport.aclose()

    async def run_session(self, session_id: str, command: str) -> dict:
        async with self.lock:
            s = self.session
            if not s or s.id != session_id:
                raise KeyError('session not found')
            if time.time() >= s.expires_at:
                await self._close_locked(s)
                raise KeyError('session expired')
            cfg = self.connection()
            try:
                sandbox = await self.connect(s.sandbox_id, cfg)
                try:
                    output = await asyncio.wait_for(
                        sandbox.commands.run_in_session(s.shell_id, command, timeout=timedelta(seconds=8)),
                        timeout=12)
                    return {'id': s.id, 'sandbox_id': s.sandbox_id, 'shell_id': s.shell_id,
                            'result': self.output(output)}
                finally:
                    await sandbox.close()
            finally:
                await cfg.transport.aclose()

    async def _close_locked(self, s: Session) -> dict:
        cfg = self.connection()
        deleted = False
        try:
            sandbox = await self.connect(s.sandbox_id, cfg)
            try:
                try:
                    await asyncio.wait_for(sandbox.commands.delete_session(s.shell_id), timeout=8)
                except Exception:
                    pass
                await self.dispose(sandbox, cfg)
                deleted = self.verify_absent(s.sandbox_id)
            finally:
                await sandbox.close()
        finally:
            await cfg.transport.aclose()
            self.session = None
        if not deleted:
            raise RuntimeError('sandbox deletion not verified')
        return {'id': s.id, 'sandbox_id': s.sandbox_id, 'deleted': True}

    async def close_session(self, session_id: str) -> dict:
        async with self.lock:
            if not self.session or self.session.id != session_id:
                raise KeyError('session not found')
            return await self._close_locked(self.session)

    async def sweep(self):
        while True:
            await asyncio.sleep(5)
            async with self.lock:
                if self.session and time.time() >= self.session.expires_at:
                    try:
                        await self._close_locked(self.session)
                    except Exception:
                        pass

    async def shutdown(self):
        """Explicitly retire this service's owned session and shared backend."""
        try:
            if self.session:
                await self.close_session(self.session.id)
        finally:
            await self.backend.close()

