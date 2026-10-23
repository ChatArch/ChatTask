"""Real tracked-source build/check execution inside one owned OpenSandbox."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import shlex
import time
from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import DirectoryListEntry


class SandboxExecutor:
    def __init__(self, task_service, *, total_seconds=520, dependency_archive: bytes | None=None):
        self.task_service = task_service
        self.total_seconds = total_seconds
        self.dependency_archive = dependency_archive

    async def execute(self, run: dict, archive: bytes, on_step, cancelled, tool_client) -> dict:
        async with self.task_service.lock:
            self._deadline = time.monotonic() + self.total_seconds
            return await self._execute(run, archive, on_step, cancelled, tool_client)

    async def _execute(self, run, archive, on_step, cancelled, tool_client):
        sandbox = cfg = None
        artifacts = []
        runtime = None
        sandbox_id = None
        cleanup = {'requested': False, 'container_absent': True, 'not_created': True}
        error = None
        current_index = None
        try:
            for index, step in enumerate(run['steps']):
                if cancelled():
                    break
                current_index = index
                kind = step['kind']
                await on_step({'index': index, 'status': 'running', 'started_at': self._now()})
                if kind == 'probe':
                    if tool_client is None:
                        raise RuntimeError('ChatPyPI tool connection unavailable')
                    receipt = await asyncio.wait_for(
                        tool_client.call('pkg_probe', {'package_name': run['package_name']}, mutation=False),
                        timeout=min(15, self._remaining()))
                    # pkg_probe exit 1 means an occupied distribution name; it is
                    # recorded as metadata rather than fabricated test success.
                    if receipt['exit_code'] not in (0, 1):
                        raise RuntimeError(f"pkg_probe failed with exit {receipt['exit_code']}")
                    if receipt['exit_code'] == 1 and not any(marker in receipt['stdout'].lower() for marker in ('already exists', 'occupied')):
                        raise RuntimeError('pkg_probe result inconclusive; inspect the tool receipt')
                    if receipt['exit_code'] == 0 and not any(marker in receipt['stdout'].lower() for marker in ('available', 'not found')):
                        raise RuntimeError('pkg_probe failed to establish name availability; inspect the tool receipt')
                    receipt['interpretation'] = 'available' if receipt['exit_code'] == 0 else 'occupied'
                else:
                    if sandbox is None:
                        try:
                            sandbox, cfg, runtime = await asyncio.wait_for(
                                self.task_service.create(), timeout=min(50, self._remaining()))
                        except Exception:
                            cleanup = {'requested': False, 'container_absent': False, 'creation_failed': True}
                            raise
                        sandbox_id = sandbox.id
                        cleanup = {'requested': False, 'container_absent': False, 'not_created': False}
                        await self._command(sandbox, ['mkdir', '-p', '/workspace/src', '/workspace/dist'], 20)
                        await asyncio.wait_for(sandbox.files.write_file('/workspace/source.tar', archive, mode=600),
                                               timeout=min(30, self._remaining()))
                        await self._command(sandbox, ['tar', '-xf', '/workspace/source.tar', '-C', '/workspace/src',
                                                      '--no-same-owner', '--no-same-permissions'], 30)
                        if self.dependency_archive is not None:
                            await asyncio.wait_for(sandbox.files.write_file('/workspace/public-deps.tar.gz', self.dependency_archive, mode=600),
                                                   timeout=min(30, self._remaining()))
                            await self._command(sandbox, ['tar', '-xzf', '/workspace/public-deps.tar.gz', '-C', '/workspace',
                                                          '--no-same-owner', '--no-same-permissions'], 45)
                    if kind == 'test':
                        install = (None if self.dependency_archive is not None else
                                   await self._command(sandbox, self._pip('build', 'twine', 'pytest'), 140))
                        extras = run.get('install_extras', '')
                        project_command = (['python','-m','pip','install','--no-input','--no-index','--no-build-isolation',
                                            '.[' + extras + ']' if extras else '.'] if self.dependency_archive is not None
                                           else self._pip('.[' + extras + ']' if extras else '.'))
                        package = await self._command(sandbox, project_command, 140, cwd='/workspace/src')
                        suite = step.get('suite') or 'pytest'
                        argv = {'pytest': ['python', '-m', 'pytest', '-q'],
                                'unittest': ['python', '-m', 'unittest', 'discover', '-v'],
                                'compileall': ['python', '-m', 'compileall', '-q', '.']}[suite]
                        tested = await self._command(sandbox, argv, 120, cwd='/workspace/src')
                        if suite == 'unittest':
                            import re
                            summary = tested['stdout'] + '\n' + tested['stderr']
                            if not re.search(r'Ran [1-9][0-9]* tests?', summary):
                                raise RuntimeError('unittest reported no executed tests')
                        receipt = {'install_tools': install, 'install_project': package, 'test': tested, 'suite': suite}
                    elif kind == 'build':
                        install = (None if self.dependency_archive is not None else
                                   await self._command(sandbox, self._pip('build', 'twine'), 140))
                        build_command = ['python', '-m', 'build', '--no-isolation', '--sdist', '--wheel',
                                         '--outdir', '/workspace/dist'] if self.dependency_archive is not None else [
                                         'python', '-m', 'build', '--sdist', '--wheel', '--outdir', '/workspace/dist']
                        built = await self._command(sandbox, build_command, 130, cwd='/workspace/src')
                        artifacts = await self._read_artifacts(sandbox, run['version'])
                        receipt = {'install_tools': install, 'build': built,
                                   'artifacts': [{k: v for k, v in item.items() if k != 'data'} for item in artifacts]}
                    elif kind == 'check':
                        if not artifacts:
                            raise RuntimeError('check requires actual built artifacts')
                        checked = await self._command(sandbox, ['python', '-m', 'twine', 'check',
                                                                *('/workspace/dist/' + item['name'] for item in artifacts)],
                                                      45, cwd='/workspace/src')
                        receipt = {'check': checked}
                    else:
                        raise RuntimeError('unsupported step')
                await on_step({'index': index, 'status': 'passed', 'ended_at': self._now(), 'receipt': receipt})
                current_index = None
        except Exception as exc:
            error = (str(exc) or type(exc).__name__)[:500]
            if current_index is not None:
                await on_step({'index': current_index, 'status': 'failed', 'ended_at': self._now(), 'error': error})
        finally:
            if sandbox is not None:
                try:
                    await self.task_service.dispose(sandbox, cfg)
                    cleanup['requested'] = True
                    cleanup['container_absent'] = not any(c.name == 'sandbox-' + sandbox_id
                        for c in self.task_service.docker.containers.list(all=True))
                except Exception as exc:
                    cleanup['error'] = type(exc).__name__
                    cleanup['container_absent'] = False
                if not cleanup['container_absent']:
                    error = (error + '; ' if error else '') + 'sandbox cleanup not independently verified'
        return {'ok': error is None and not cancelled() and cleanup['container_absent'],
                'cancelled': cancelled(), 'error': error, 'artifacts': artifacts if error is None else [],
                'cleanup': cleanup, 'sandbox_id': sandbox_id, 'runtime': runtime}

    def _remaining(self):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError('Sandbox execution total deadline exceeded')
        return left

    @staticmethod
    def _now():
        from .workflow import now
        return now()

    @staticmethod
    def _pip(*packages):
        return ['python', '-m', 'pip', 'install', '--no-input', '--no-cache-dir',
                '--disable-pip-version-check', '--retries', '2', '--timeout', '15',
                '--index-url', 'https://pypi.org/simple', *packages]

    async def _command(self, sandbox, argv, seconds, *, cwd=None):
        limit = min(seconds, self._remaining())
        opts = RunCommandOpts(timeout=timedelta(seconds=limit), working_directory=cwd,
                              envs={'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
                                    'PYTHONNOUSERSITE': '1',
                                    'PYTHONPATH': '/workspace/deps' if self.dependency_archive is not None else '',
                                    'PATH': '/usr/local/bin:/usr/bin:/bin'})
        command=shlex.join(argv)
        output = await asyncio.wait_for(sandbox.commands.run(command, opts=opts),
                                        timeout=min(limit + 8, self._remaining() + 2))
        receipt = self.task_service.output(output)
        if receipt['error'] or receipt['exit_code'] != 0:
            raise RuntimeError(f"sandbox command {argv[0]} exited {receipt['exit_code']}: " +
                               (receipt['stderr'] or receipt['stdout'] or receipt['error'] or '')[:300])
        return receipt

    async def _read_artifacts(self, sandbox, target_version):
        entries = await asyncio.wait_for(sandbox.files.list_directory(DirectoryListEntry(path='/workspace/dist')),
                                         timeout=min(20, self._remaining()))
        names = sorted(e.path.rsplit('/', 1)[-1] for e in entries if e.path.endswith(('.whl', '.tar.gz')))
        if len(names) != 2 or sum(name.endswith('.whl') for name in names) != 1 or sum(name.endswith('.tar.gz') for name in names) != 1:
            raise RuntimeError('build must produce exactly one wheel and one sdist')
        result = []
        for name in names:
            if not name or len(name) > 128 or '/' in name:
                raise RuntimeError('invalid artifact name')
            try:
                version = parse_wheel_filename(name)[1] if name.endswith('.whl') else parse_sdist_filename(name)[1]
            except Exception as exc:
                raise RuntimeError('artifact filename is not valid distribution metadata') from exc
            if version != Version(target_version):
                raise RuntimeError('built artifact version does not match requested target')
            data = await asyncio.wait_for(sandbox.files.read_bytes('/workspace/dist/' + name),
                                          timeout=min(20, self._remaining()))
            if not data or len(data) > 32 * 1024 * 1024:
                raise RuntimeError('artifact empty or exceeds 32 MiB')
            result.append({'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
                           'size': len(data), 'data': data})
        return result
