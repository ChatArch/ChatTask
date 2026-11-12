"""Independent task verification in fresh, existing OpenSandbox environments.

Submitted programs and verifier scripts never run on the host. The selected
operator image must supply its verifier dependencies; missing tools are errors,
not scores. This is the existing bounded runc contract, not a hostile multi-tenant
security boundary or a proof that model judgements are objective truth.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import io
import json
import math
import re
from pathlib import PurePosixPath
import shlex
import tarfile

from opensandbox.models.execd import RunCommandOpts
from .service import TaskService
from .task_packages import tomllib, validate_files, validate_reward


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON number')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    except (TypeError, RecursionError) as exc:
        raise ValueError('invalid bounded JSON result') from exc


def submission_files(artifacts):
    """Decode only regular, bounded relative result files; never extract to host."""
    files = {}
    total = 0
    for artifact in artifacts:
        name, data = artifact['name'], artifact['data']
        if not isinstance(data, bytes):
            raise ValueError('submission bytes required')
        if name != 'result.tar.gz':
            if name.endswith('.events.jsonl') or name in ('checkpoint.tar.gz', 'task-data.zip', 'task-definition.zip', 'trajectory.json'):
                continue
            if '/' in name or '\\' in name or ':' in name or name in ('.', '..'):
                raise ValueError('invalid submission name')
            files['task-results/' + name] = data
            total += len(data)
            continue
        if len(data) > 20 * 1024 * 1024:
            raise ValueError('result archive budget exceeded')
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                for entry in archive:
                    path = entry.name
                    parts = path.split('/')
                    if (not entry.isfile() or path.startswith('/') or '\\' in path or ':' in path or
                            any(not p or p in ('.', '..') for p in parts) or path in files):
                        raise ValueError('unsafe result archive member')
                    total += entry.size
                    if len(files) >= 1000 or not 0 <= entry.size <= 10 * 1024 * 1024 or total > 24 * 1024 * 1024:
                        raise ValueError('submission budget exceeded')
                    stream = archive.extractfile(entry)
                    if stream is None:
                        raise ValueError('missing result archive body')
                    body = stream.read(10 * 1024 * 1024 + 1)
                    if len(body) != entry.size:
                        raise ValueError('result archive size mismatch')
                    files[path] = body
        except tarfile.TarError as exc:
            raise ValueError('invalid result archive') from exc
    if total > 24 * 1024 * 1024 or len(files) > 1000:
        raise ValueError('submission budget exceeded')
    return files


def criterion_results(details, allowed_ids):
    """Read explicit Rewardkit Score IDs/names and normalized values only."""
    found = {}
    visited = 0
    def walk(node, depth=0):
        nonlocal visited
        visited += 1
        if depth > 32 or visited > 20000:
            raise ValueError('reward detail budget exceeded')
        if isinstance(node, dict):
            ident = node.get('id') or node.get('criterion_id') or node.get('name')
            value = node.get('value', node.get('score'))
            if ident in allowed_ids and type(value) in (int, float) and math.isfinite(value):
                item = {'passed': value >= 1.0, 'value': value,
                        'reasoning': str(node.get('reasoning', ''))[:8000]}
                if node.get('error'):
                    item['passed'] = None
                    item['error'] = 'criterion reported an error'
                if ident in found and found[ident]['passed'] != item['passed']:
                    found[ident] = {'passed': None, 'error': 'ambiguous repeated criterion scores'}
                elif ident not in found:
                    found[ident] = item
            for item in node.values():
                if isinstance(item, (dict, list)):
                    walk(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, depth + 1)
    walk(details)
    return found


_SNAPSHOT = '''import json,os
from pathlib import Path
root=Path('/logs/verifier')
assert not Path('/logs').is_symlink() and not root.is_symlink()
result={}
for name in ('reward.json','reward-details.json'):
 p=root/name
 if not p.exists():
  if name=='reward-details.json':continue
  raise RuntimeError('verifier produced no reward.json')
 assert not p.is_symlink() and p.is_file()
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:data=f.read(512*1024+1)
 assert len(data)<=512*1024
 result[name]=data.decode('utf-8')
print(json.dumps(result,ensure_ascii=False))
'''


def judge_codex_wrapper(runtime, binary):
    """Rewardkit owns a fresh CODEX_HOME; bind routing explicitly per invocation."""
    if not isinstance(binary,str) or not re.fullmatch(r'(/[A-Za-z0-9._+-]+)+',binary):
        raise ValueError('operator image must supply an absolute Codex binary')
    from .codex_tasks import task_model_argv
    argv=task_model_argv(runtime)
    argv[0]=binary
    return '#!/bin/sh\nexec '+shlex.join(argv+['-c','model='+json.dumps(runtime.model)])+' "$@"\n'


class SandboxPackageVerifier:
    """Use the existing TaskService resource/lifetime/ownership contract."""
    def __init__(self, parent, runtime, *, gateway=None, hosts=None, seconds=120):
        if type(seconds) is not int or not 30 <= seconds <= 300:
            raise ValueError('verifier budget must be 30–300 seconds')
        self.parent, self.runtime, self.gateway = parent, runtime, gateway
        self.hosts, self.seconds = hosts or {}, seconds

    def _judge_policy(self, package):
        judges = []
        for name, text in package['files'].items():
            if name.startswith('tests/') and name.endswith('.toml'):
                config = tomllib.loads(text)
                judge = config.get('judge')
                if judge is None:
                    continue
                if not isinstance(judge, dict) or judge.get('judge') != 'codex':
                    raise ValueError('this adapter requires an explicit configured codex judge')
                if judge.get('model', self.runtime.model) != self.runtime.model:
                    raise ValueError('judge model differs from the approved runtime model')
                if judge.get('version'):
                    raise ValueError('Codex versions must be supplied by the operator image, not installed by a task')
                if judge.get('mcp_servers') or judge.get('isolated') or judge.get('samples', 1) != 1 or judge.get('mode', 'batched') != 'batched':
                    raise ValueError('this sandbox adapter supports one batched judge without extra tool servers or mounts')
                for criterion in config.get('criterion', []):
                    if not isinstance(criterion, dict):
                        raise ValueError('invalid judge criterion')
                judges.append(config)
        if len(judges) > 1:
            raise ValueError('multiple agent judges require a separately provisioned isolation adapter')
        return bool(judges)

    async def reconcile(self, evaluation):
        """Read back absence before clearing an interrupted verifier fence."""
        try:
            if await self.parent.find_run_sandboxes(evaluation['id']):
                return False
            if self.gateway:
                if await asyncio.to_thread(self.gateway.close, evaluation['id']) != {'closed': True}:
                    return False
            return True
        except Exception:
            return False

    async def evaluate(self, package, artifacts, *, task, run):
        package = validate_files(package['files'])
        files = submission_files(artifacts)
        model_required = self._judge_policy(package)
        if 'tests/test.sh' not in package['files']:
            return {'status': 'unavailable', 'error': 'This task has no automated verifier entrypoint'}
        image = self.runtime.images.get(task['image_profile'])
        if not image:
            raise ValueError('operator-configured verifier image required')
        evaluation = run['evaluation']
        ident = evaluation['id']
        service = TaskService(replace(self.parent.settings, image=image), docker_client=self.parent.docker)
        sandbox = cfg = lease = None
        intent = False
        create_attempted = False
        outcome = {'status': 'error', 'error': 'Verifier did not finish'}
        env = {'HOME': '/workspace/home', 'CODEX_HOME': '/workspace/codex-home',
               'PATH': '/usr/local/bin:/usr/bin:/bin', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
               'PYTHONDONTWRITEBYTECODE': '1', 'LITELLM_LOCAL_MODEL_COST_MAP': 'true',
               'LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS': 'true'}
        secrets = [self.runtime.access_token, self.runtime.tool_access_token]
        diagnostic = ''
        async with self.parent.lock:
            try:
                runtime = self.runtime
                if model_required:
                    if self.gateway:
                        intent = True
                        lease = await asyncio.to_thread(self.gateway.create, ident, 'generic', None)
                        runtime = replace(runtime, base_url=lease.model_base_url, access_token=lease.model_access_token)
                    secrets.append(runtime.access_token)
                    env.update(OPENAI_API_KEY=runtime.access_token, CHATTASK_CODEX_API_KEY=runtime.access_token,
                               OPENAI_BASE_URL=runtime.base_url, REWARDKIT_JUDGE='codex', REWARDKIT_MODEL=runtime.model)
                create_attempted = True
                sandbox, cfg, details = await service.create(ttl_seconds=self.seconds + 90, durable_run_id=ident)
                async def command(argv, timeout=30, cwd='/'):
                    nonlocal diagnostic
                    output = await asyncio.wait_for(sandbox.commands.run(shlex.join(argv), opts=RunCommandOpts(
                        timeout=timedelta(seconds=timeout), working_directory=cwd, envs=env)), timeout + 10)
                    receipt = service.output(output)
                    if receipt['exit_code'] != 0 or receipt.get('error'):
                        text = '\n'.join(receipt[k] for k in ('stderr','stdout','error') if isinstance(receipt.get(k),str))
                        for secret in secrets:
                            if secret:
                                text = text.replace(secret,'[REDACTED]').replace(base64.b64encode(secret.encode()).decode(),'[REDACTED]')
                        diagnostic = text[:2000]
                        raise RuntimeError('verifier command failed')
                    return output
                await command(['mkdir', '-p', '/app', '/tests', '/logs/verifier', '/workspace/home', '/workspace/codex-home'])
                for name, body in files.items():
                    target = '/app/' + name
                    await command(['mkdir', '-p', target.rsplit('/', 1)[0]])
                    await sandbox.files.write_file(target, body, mode=444)
                for name, text in package['files'].items():
                    if name.startswith('tests/'):
                        target = '/' + name
                    else:
                        target = '/workspace/task-definition/' + name
                    await command(['mkdir', '-p', target.rsplit('/', 1)[0]])
                    await sandbox.files.write_file(target, text, mode=444)
                if model_required:
                    config = ('model = '+json.dumps(runtime.model)+'\nmodel_provider = "task"\n'
                              'approval_policy = "never"\n[model_providers.task]\nname = "Task verifier gateway"\n'
                              'wire_api = "responses"\nenv_key = "CHATTASK_CODEX_API_KEY"\n'
                              'base_url = '+json.dumps(runtime.base_url)+'\nrequest_max_retries = 1\nstream_max_retries = 1\n'
                              '[features]\nunbounded_connection_retries = false\nmulti_agent = false\n')
                    await sandbox.files.write_file('/workspace/codex-home/config.toml', config, mode=600)
                    if self.hosts:
                        lines = ''.join(ip+' '+host+'\n' for host, ip in sorted(self.hosts.items()))
                        await command(['python', '-I', '-c', 'from pathlib import Path; Path("/etc/hosts").open("a").write('+repr(lines)+')'])
                    if runtime.ca_certificate:
                        await sandbox.files.write_file('/workspace/public-ca.pem', runtime.ca_certificate, mode=644)
                        await command(['python', '-I', '-c', 'from pathlib import Path; Path("/workspace/verifier-ca.pem").write_bytes(Path("/etc/ssl/certs/ca-certificates.crt").read_bytes()+b"\\n"+Path("/workspace/public-ca.pem").read_bytes())'])
                        env.update(SSL_CERT_FILE='/workspace/verifier-ca.pem', REQUESTS_CA_BUNDLE='/workspace/verifier-ca.pem',
                                   CODEX_CA_CERTIFICATE='/workspace/verifier-ca.pem')
                    located = await command(['sh','-c','command -v codex'])
                    binary = service.output(located)['stdout'].strip()
                    await command(['mkdir','-p','/workspace/judge-bin'])
                    await sandbox.files.write_file('/workspace/judge-bin/codex',judge_codex_wrapper(runtime,binary),mode=700)
                    env['PATH']='/workspace/judge-bin:'+env['PATH']
                await command(['sh', '/tests/test.sh'], timeout=self.seconds, cwd='/app')
                output = await command(['python', '-I', '-c', _SNAPSHOT])
                pieces = []
                size = 0
                for log in output.logs.stdout:
                    size += len(log.text.encode())
                    if size > 2 * 1024 * 1024:
                        raise ValueError('verifier output budget exceeded')
                    pieces.append(log.text)
                raw = '\n'.join(pieces)
                if any(secret and (secret in raw or base64.b64encode(secret.encode()).decode() in raw) for secret in secrets):
                    raise ValueError('credential appeared in verifier output')
                captured = strict_json(raw)
                reward = validate_reward(strict_json(captured['reward.json']))
                detail = strict_json(captured.get('reward-details.json', '{}'))
                allowed = {c['id'] for c in package['acceptance']}
                criteria = criterion_results(detail, allowed)
                automated = [c for c in package['acceptance'] if c['method'] != 'human']
                if len(automated) == 1 and automated[0]['method'] == 'script' and automated[0]['id'] not in criteria and 'reward' in reward:
                    criteria[automated[0]['id']] = {'passed': reward['reward'] >= 1.0, 'value': reward['reward'],
                                                   'reasoning': 'Single declared script criterion, bound to the verifier entrypoint reward'}
                outcome = {'status': 'completed', 'reward': reward, 'details': detail, 'criteria': criteria,
                           'verifier': {'adapter': 'OpenSandbox', 'image_id': details['image_id'],
                                        'memory_bytes': details['memory_bytes'], 'nano_cpus': details['nano_cpus'],
                                        'requested_judge_model': runtime.model if model_required else None}}
            except Exception as exc:
                outcome = {'status': 'error', 'error': type(exc).__name__}
                if diagnostic:outcome['diagnostic'] = diagnostic
            finally:
                absent = not create_attempted
                if sandbox is not None:
                    try:
                        await service.dispose(sandbox, cfg)
                        absent = not any(c.name == 'sandbox-' + sandbox.id for c in service.docker.containers.list(all=True))
                    except Exception:
                        absent = False
                closed = not intent
                if intent:
                    try:
                        closed = await asyncio.to_thread(self.gateway.close, ident) == {'closed': True}
                    except Exception:
                        closed = False
                outcome['cleanup'] = {'container_absent': absent, 'credential_closed': closed}
                outcome['reconciliation_required'] = not (absent and closed)
                if outcome['reconciliation_required']:
                    outcome.update(status='error', error='Verifier cleanup or lease closure is unconfirmed')
        return outcome
