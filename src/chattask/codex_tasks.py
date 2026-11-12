"""Codex execution engine. Tasks and images supply domain behavior, not this module."""
from __future__ import annotations
import asyncio
import base64
from dataclasses import dataclass,field,replace
from datetime import timedelta
import hashlib
import io
import ipaddress
import json
import re
import shlex
import tarfile
import time
import uuid
from urllib.parse import urlsplit
from opensandbox.models.execd import RunCommandOpts
from .service import TaskService

@dataclass(frozen=True)
class CodexRuntime:
    base_url:str
    access_token:str=field(repr=False)
    images:dict[str,str]=field(default_factory=dict)
    tool_base_url:str=''
    tool_access_token:str=field(default='',repr=False)
    model:str='gpt-6-sol'
    seconds:int=900
    ca_certificate:bytes=field(default=b'',repr=False)

    def __post_init__(self):
        for endpoint in [self.base_url,*([self.tool_base_url] if self.tool_base_url else [])]:
            parsed=urlsplit(endpoint)
            if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('invalid configured runtime endpoint')
        if bool(self.tool_base_url)!=bool(self.tool_access_token):raise ValueError('tool endpoint and separate restricted tool token must be configured together')
        if not self.access_token or not self.images or not 30<=self.seconds<=3600:raise ValueError('runtime token, images and bounded duration required')
        if any(not re.fullmatch('[A-Za-z0-9._-]{1,60}',name) or not isinstance(image,str) or not image or any(c.isspace() for c in image) for name,image in self.images.items()):raise ValueError('invalid image profile')

    def guest_env(self):
        result={'CHATTASK_CODEX_API_KEY':self.access_token,'CODEX_HOME':'/workspace/codex-home',
                'HOME':'/workspace/home','PATH':'/usr/local/bin:/usr/bin:/bin',
                'PIP_DISABLE_PIP_VERSION_CHECK':'1','PIP_CONFIG_FILE':'/dev/null'}
        if self.tool_base_url and self.tool_access_token:result.update(CHATTASK_TOOL_BASE_URL=self.tool_base_url,CHATTASK_TOOL_ACCESS_TOKEN=self.tool_access_token)
        if self.ca_certificate:result.update(SSL_CERT_FILE='/workspace/task-ca.pem',CODEX_CA_CERTIFICATE='/workspace/task-ca.pem',REQUESTS_CA_BUNDLE='/workspace/task-ca.pem')
        return result

    def redact(self,text):
        for secret in (self.access_token,self.tool_access_token):
            if secret:
                text=text.replace(secret,'[REDACTED]')
                text=text.replace(base64.b64encode(secret.encode()).decode(),'[REDACTED]')
        return text

TOOL_CLIENT='''import json,os,sys,urllib.request,urllib.error
route=sys.argv[1]
if not route.startswith('/') or route.startswith('//') or '?' in route:raise SystemExit('invalid tool route')
data=json.loads(sys.argv[2]) if len(sys.argv)>2 else None
request=urllib.request.Request(os.environ['CHATTASK_TOOL_BASE_URL']+route,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+os.environ['CHATTASK_TOOL_ACCESS_TOKEN'],'Content-Type':'application/json'})
try:
 with urllib.request.urlopen(request,timeout=60) as response:print(response.read().decode())
except urllib.error.HTTPError as exc:
 print(json.dumps({'http_status':exc.code,'detail':exc.read().decode()[:300]}));raise SystemExit(1)
'''
COLLECTOR='''import json,tarfile
from pathlib import Path
root=Path('/workspace/result');total=0;files=[]
for p in sorted(root.rglob('*')):
 if p.is_symlink():raise SystemExit('result symlink rejected')
 if p.is_file():
  total+=p.stat().st_size
  if len(files)>=1000 or p.stat().st_size>10*1024*1024 or total>20*1024*1024:raise SystemExit('result budget exceeded')
  files.append(p)
with tarfile.open('/workspace/result.tar.gz','w:gz') as archive:
 for p in files:archive.add(p,arcname=str(p.relative_to(root)),recursive=False)
print(json.dumps({'files':len(files),'bytes':total}))
'''


def native_resume_argv(common, model, session_id, help_text, root_help):
    """Build only the flags confirmed by the installed guest CLI help."""
    try:
        uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('invalid native Codex session id') from exc
    if not all(flag in help_text for flag in ('SESSION_ID','--json','--output-last-message','--model','--skip-git-repo-check')) or not all(flag in root_help for flag in ('--cd','--sandbox')):
        raise RuntimeError('guest Codex lacks required native resume flags')
    return [*common,'--sandbox','danger-full-access','--cd','/workspace/work','exec','resume',
            '--skip-git-repo-check','--model',model,'--json',
            '--output-last-message','/workspace/agent-final.md',session_id,'-']


def task_model_argv(runtime):
    return ['codex','--disable','unbounded_connection_retries','-c','model_provider="task"',
            '-c','model_providers.task.name="Task gateway"','-c','model_providers.task.wire_api="responses"',
            '-c','model_providers.task.env_key="CHATTASK_CODEX_API_KEY"',
            '-c','model_providers.task.base_url='+json.dumps(runtime.base_url),
            '-c','model_providers.task.request_max_retries=1','-c','model_providers.task.stream_max_retries=1',
            '-c','model_providers.task.stream_idle_timeout_ms=90000','-c','model_reasoning_effort="xhigh"']

class CodexTaskExecutor:
    def __init__(self,task_service,runtime:CodexRuntime,*,gateway=None,hosts=None):
        self.task_service=task_service;self.runtime=runtime;self.images=dict(runtime.images)
        self.gateway=gateway;self.hosts=hosts or {};self.postprocessors={}
        from .task_verification import SandboxPackageVerifier
        self.package_verifier = (SandboxPackageVerifier(task_service,runtime,gateway=gateway,hosts=self.hosts)
                                 if task_service is not None else None)

    def register_postprocessor(self,mode,handler,max_seconds):
        if mode not in ('prepare','publish','verify') or not 1<=max_seconds<=900:
            raise ValueError('invalid trusted postprocessor')
        selected=(handler,max_seconds)
        if mode in self.postprocessors and self.postprocessors[mode]!=selected:
            raise ValueError('conflicting trusted postprocessor')
        self.postprocessors[mode]=selected

    async def execute_durable(self,task,run,checkpoint,save_checkpoint,progress):
        """Internal-only invocation; public generic create cannot supply these fields."""
        from .durable_archive import validate_checkpoint
        if checkpoint is not None:
            validate_checkpoint(checkpoint, expected=task['checkpoint'], session_id=run['session_id'])
        instructions=run['instructions']
        if run.get('workflow')=='research':
            from .research import research_instructions
            instructions=(research_instructions(task['goal'],run['source_records'],followup=run['number']>1)
                          +'\nPlace every result/research path under /workspace/result/research. '
                          'Only the provided, caller-verified source records are available; do not claim autonomous discovery.\n'
                          +'CURRENT ROUND REQUEST:\n'+instructions)
        invocation={'id':run['id'],'instructions':instructions,
                    'image_profile':task['image_profile'],
                    '_durable':{'checkpoint':checkpoint,'session_id':run['session_id'],
                                'save_checkpoint':save_checkpoint,
                                'prepare_definition':task.get('_prepare_definition'),
                                'definition_files':task.get('_definition_files')}}
        return await self.execute(invocation,progress,lambda: False)

    async def reconcile_durable(self,run):
        """Allow a new writer only after exact owned-container absence and lease closure."""
        sandbox_id, receipt = run.get('sandbox_id'), run.get('runtime_receipt')
        if not run.get('phase') and (not sandbox_id or not isinstance(receipt,dict)):
            return False
        try:
            finder=getattr(self.task_service,'find_run_sandboxes',None)
            if finder is not None:
                # Controller metadata catches a created Sandbox whose response/id was lost.
                if await finder(run['id']):return False
            elif not sandbox_id or not isinstance(receipt,dict):
                return False
            if sandbox_id:
                matched=[container for container in self.task_service.docker.containers.list(all=True)
                         if container.name=='sandbox-'+sandbox_id]
                if matched:return False
            if self.gateway:
                closed=await asyncio.to_thread(self.gateway.close,run['id'])
                if closed!={'closed':True}:
                    return False
            return True
        except Exception:
            return False

    async def execute(self,task,update,cancelled):
        runtime=self.runtime
        durable=task.get('_durable')
        model_quiescent=False
        lease=None
        capability=task.get('capability') or {}
        mode=capability.get('mode','generic')
        hook=self.postprocessors.get(mode)
        if mode=='verify' and (not self.gateway or not hook or not isinstance(task.get('_input_archive'),bytes)):
            raise ValueError('read-only verification requires gateway, trusted hook and approved input')
        service=None
        sandbox=cfg=None;outcome={'ok':False,'artifacts':[],'cleanup':{'container_absent':True,'not_created':True}}
        sandbox_create_attempted=False
        outcome['requested_model'] = runtime.model
        async with self.task_service.lock:
            gateway_intent=False
            try:
                if self.gateway:
                    if durable:
                        await update({'phase':'gateway_intent'})
                        gateway_intent=True
                    plan=capability.get('plan')
                    if mode=='verify':plan={**plan,'verification_of':capability['source_task_id']}
                    lease=await asyncio.to_thread(self.gateway.create,task['id'],mode,plan)
                    if durable:await update({'phase':'gateway_created'})
                if lease is not None:
                    if mode=='verify':
                        runtime=replace(runtime,tool_base_url=lease.tool_base_url,tool_access_token=lease.tool_access_token)
                    else:
                        runtime=replace(runtime,base_url=lease.model_base_url,access_token=lease.model_access_token,
                                        tool_base_url=lease.tool_base_url,tool_access_token=lease.tool_access_token)
                service=TaskService(replace(self.task_service.settings,image=self.images[task['image_profile']]),docker_client=self.task_service.docker)
                await update({'index':0,'status':'running'})
                if durable:await update({'phase':'sandbox_intent'})
                create_options={'ttl_seconds':(120 if mode=='verify' else runtime.seconds)+(hook[1] if hook else 0)+120}
                if durable:create_options['durable_run_id']=task['id']
                if durable:outcome['cleanup']={'container_absent':False,'creation_outcome_unknown':True}
                sandbox_create_attempted=True
                sandbox,cfg,details=await service.create(**create_options)
                outcome.update(sandbox_id=sandbox.id,runtime=details,cleanup={'container_absent':False})
                if durable:
                    await update({'phase':'sandbox_created','sandbox_id':sandbox.id,'runtime':details})
                async def command(argv,seconds=30,cwd='/workspace',full_stdout_limit=None):
                    nonlocal model_quiescent
                    env=runtime.guest_env()
                    if mode=='verify':env.pop('CHATTASK_CODEX_API_KEY',None)
                    model_command = argv[:2] == ['sh','-c'] and 'codex' in argv[2]
                    if model_command:
                        model_quiescent = False
                    output=await asyncio.wait_for(sandbox.commands.run(shlex.join(argv),opts=RunCommandOpts(
                        timeout=timedelta(seconds=seconds),working_directory=cwd,envs=env)),seconds+10)
                    receipt=service.output(output)
                    if argv[:2]==['sh','-c'] and 'codex' in argv[2]:
                        model_quiescent=True
                    for key in ['stdout','stderr','error']:
                        if isinstance(receipt.get(key),str):receipt[key]=runtime.redact(receipt[key])
                    if receipt['exit_code']!=0 or receipt.get('error'):raise RuntimeError('guest command failed: '+(receipt.get('stderr') or receipt.get('stdout') or str(receipt.get('error')))[:1800])
                    if full_stdout_limit is not None:
                        # CLI help is a trusted guest probe. Ordinary receipts stay diagnostic-sized.
                        chunks=[];size=0
                        for log in output.logs.stdout:
                            piece=log.text
                            size+=len(piece.encode('utf-8'))+(1 if chunks else 0)
                            if size>full_stdout_limit:raise RuntimeError('guest Codex help exceeds 16 KiB')
                            chunks.append(piece)
                        receipt['stdout']=runtime.redact('\n'.join(chunks))
                    return receipt
                await command(['mkdir','-p','/workspace/work','/workspace/result','/workspace/codex-home','/workspace/home'],cwd='/')
                if durable and durable['checkpoint'] is not None:
                    from .durable_archive import validate_checkpoint
                    validate_checkpoint(durable['checkpoint'],session_id=durable['session_id'],
                                        known_tokens=(runtime.access_token,runtime.tool_access_token))
                    await sandbox.files.write_file('/workspace/checkpoint.tar.gz',durable['checkpoint'],mode=600)
                    await command(['tar','-xzf','/workspace/checkpoint.tar.gz','-C','/workspace'])
                if self.hosts:
                    lines=''.join(ip+' '+host+'\n' for host,ip in sorted(self.hosts.items()))
                    await command(['python','-c','from pathlib import Path; p=Path("/etc/hosts"); p.open("a").write('+repr(lines)+')'])
                if runtime.ca_certificate:
                    await sandbox.files.write_file('/workspace/public-ca.pem',runtime.ca_certificate,mode=644)
                    await command(['python','-c','from pathlib import Path; system=Path("/etc/ssl/certs/ca-certificates.crt"); ca=Path("/workspace/public-ca.pem").read_bytes(); original=system.read_bytes(); assert original; system.open("ab").write(b"\\n"+ca); Path("/workspace/task-ca.pem").write_bytes(system.read_bytes())'])
                if mode!='verify':
                    version = await command(['codex','--version'])
                    outcome['codex_cli_version'] = version['stdout'].strip()
                if task.get('_input_archive') is not None:
                    await command(['mkdir','-p','/workspace/input'])
                    await sandbox.files.write_file('/workspace/input/previous-result.tar.gz',task['_input_archive'],mode=600)
                await sandbox.files.write_file('/workspace/task_tools.py',TOOL_CLIENT,mode=600)
                await sandbox.files.write_file('/workspace/collect_results.py',COLLECTOR,mode=600)
                await update({'index':0,'status':'passed','receipt':{'image_profile':task['image_profile'],'runtime':details}})
                if mode!='verify':
                    started = time.monotonic()
                    if durable and durable.get('prepare_definition'):
                        await update({'phase':'package_preparation_started'})
                        preparation = (
                            'Prepare a task package, NOT its solution. Do not perform the user task or external writes. '
                            'Do not call tools or run any commands: this phase only authors the provided-material contract, '
                            'not environment discovery, dependency imports, installation or solving. '
                            'Prefer a stdlib-only programmatic verifier for code/data tasks: tests/test.sh runs python -I /tests/checks.py; '
                            'checks.py reads actual deliverables under /app, computes each declared boolean criterion, creates '
                            '/logs/verifier, and writes reward.json as {"reward": 1.0 or 0.0} plus reward-details.json as '
                            '{"criteria":[{"id":"AC1","value":1.0 or 0.0,"reasoning":"actual observation"}]}. '
                            'A normal failing criterion writes zero and exits normally; an unexpected verifier error is not a pass. '
                            'Use Rewardkit only for genuinely model-judged criteria; no Rewardkit import is needed to author these files. '
                            'Return ONLY a JSON object with one key "files", whose value maps relative UTF-8 filenames to text. '
                            'Use Harbor task schema 1.4: task.toml, instruction.md, tests/test.sh, and appropriate checks/rubric. '
                            'Keep business fields in metadata.chatarch: purpose, deliverables, acceptance tables with stable id, '
                            'description, method (script/rubric/human), and required boolean. Do not invent extra hard requirements. '
                            'For model-judged work, use Rewardkit 0.2.4 tests/quality.toml with explicit judge="codex", stable criterion IDs '
                            'and binary/likert/rubric types. A Markdown judge guide may use prompt_template and must contain {criteria}. '
                            'tests/test.sh may run python -m rewardkit /tests. Verifier outputs live at /logs/verifier; '
                            'graded delivered files are restored under /app. Do not hardcode success, credentials, model keys, '
                            'host mounts or new provider URLs. Preserve the original goal and supplied factual source boundaries. '
                            'List required deliverables and observable acceptance; use human review where automation cannot establish truth. '
                            'This is preparation, not a claim that tests or the task have passed.\n\nUSER REQUEST:\n'+task['instructions']+
                            '\n\nINITIAL DRAFT FILES:\n'+json.dumps(durable.get('definition_files') or {},ensure_ascii=False))
                        await sandbox.files.write_file('/workspace/preparation-prompt.txt',preparation,mode=600)
                        prep_argv=task_model_argv(runtime)+['exec','--skip-git-repo-check','--sandbox','danger-full-access',
                            '--model',runtime.model,'--cd','/workspace/work','--json',
                            '--output-last-message','/workspace/prepared-definition.json','-']
                        shell=('env -u CHATTASK_TOOL_ACCESS_TOKEN -u CHATTASK_TOOL_BASE_URL '+shlex.join(prep_argv)+
                               ' < /workspace/preparation-prompt.txt > /workspace/preparation-events.jsonl 2> /workspace/preparation-stderr.txt')
                        await command(['sh','-c',shell],seconds=max(10,min(180,runtime.seconds//3)))
                        prep_events=runtime.redact(await sandbox.files.read_file('/workspace/preparation-events.jsonl'))
                        self._artifact(outcome,'package-preparation.events.jsonl',prep_events.encode()[:1024*1024])
                        records=[json.loads(line) for line in prep_events.splitlines() if line.strip()]
                        if not any(e.get('type')=='turn.completed' for e in records) or any(e.get('type')=='turn.failed' for e in records):
                            raise RuntimeError('native package preparation did not complete')
                        raw=await sandbox.files.read_file('/workspace/prepared-definition.json')
                        if len(raw.encode())>256*1024:
                            raise ValueError('prepared task definition exceeds limit')
                        if any(secret and (secret in raw or base64.b64encode(secret.encode()).decode() in raw)
                               for secret in (runtime.access_token,runtime.tool_access_token)):
                            raise ValueError('credential appeared in prepared task definition')
                        prepared_json=json.loads(raw)
                        if not isinstance(prepared_json,dict) or set(prepared_json)!={'files'}:
                            raise ValueError('preparation must return a files object')
                        prepared=await durable['prepare_definition'](prepared_json['files'])
                        durable['definition_files']=prepared['files']
                        outcome['preparation_receipt']={'definition_revision':prepared['revision'],
                            'native_session_id':next((e['thread_id'] for e in records if e.get('type')=='thread.started'),None),
                            'events_sha256':hashlib.sha256(prep_events.encode()).hexdigest(),'native_turn_completed':True}
                    if durable and durable.get('definition_files'):
                        from .task_packages import validate_files
                        definition=validate_files(durable['definition_files'])
                        for name,text in definition['files'].items():
                            target='/workspace/task-definition/'+name
                            await command(['mkdir','-p',target.rsplit('/',1)[0]])
                            await sandbox.files.write_file(target,text,mode=600)
                        task['instructions'] += ('\n\nFROZEN TASK CONTRACT:\n'+json.dumps({k:definition[k]
                            for k in ('purpose','deliverables','acceptance')},ensure_ascii=False)+
                            '\nThe exact definition and supplied inputs are under /workspace/task-definition. '
                            'Do not edit the frozen acceptance files. Put all deliverables under /workspace/result.')
                    prompt=(
                        'You execute a user task inside an owned container. Work under /workspace/work. '
                        'Put all deliverables under /workspace/result. Write /workspace/agent-started.txt with your cwd immediately. '
                        'Never print or copy environment secrets, credentials or authentication files into deliverables. '
                        'If a task tool service is configured, discover its /tools/catalog with '
                        '`python /workspace/task_tools.py /tools/catalog`, then call exact listed routes with an optional JSON argument. '
                        'Use those services for privileged external operations; do not request or recover upstream credentials. '
                        'An HTTP or tool error is not success; report required approval/blocked operations honestly. '
                        'Only perform the external actions explicitly described in the user task. '
                        'This image supplies infrastructure; do not install unrelated infrastructure or alter the host. '
                        'On completion give a factual final summary with completed results and any blocker. '
                        'Use shell login=false if supported. If this task selects previous results, they are available as /workspace/input/previous-result.tar.gz.\n\nUSER TASK:\n'+task['instructions'])
                    await sandbox.files.write_file('/workspace/task-prompt.txt',prompt,mode=600)
                    await update({'index':1,'status':'running'})
                    argv=task_model_argv(runtime)
                    if durable and durable['session_id']:
                        help_text=(await command(['codex','exec','resume','--help'],full_stdout_limit=16*1024))['stdout']
                        root_help=(await command(['codex','--help'],full_stdout_limit=16*1024))['stdout']
                        argv=native_resume_argv(argv,runtime.model,durable['session_id'],help_text,root_help)
                    else:
                        argv+=['exec','--skip-git-repo-check','--sandbox','danger-full-access',
                               '--model',runtime.model,'--cd','/workspace/work','--json',
                               '--output-last-message','/workspace/agent-final.md','-']
                    shell=shlex.join(argv)+' < /workspace/task-prompt.txt > /workspace/agent-events.jsonl 2> /workspace/agent-stderr.txt'
                    if durable:await update({'phase':'agent_execution_started'})
                    await command(['sh','-c',shell],seconds=max(1,int(runtime.seconds-(time.monotonic()-started))))
                    model_quiescent=True
                    events=runtime.redact(await sandbox.files.read_file('/workspace/agent-events.jsonl'))
                    session=None;completed=False;failed=False
                    for line in events.splitlines():
                        try:
                            event=json.loads(line)
                            if event.get('type')=='thread.started':session=event.get('thread_id')
                            if event.get('type')=='turn.completed':completed=True
                            if event.get('type')=='turn.failed':failed=True
                        except ValueError:pass
                    outcome['codex_session_id']=session
                    if durable and durable['session_id'] and session!=durable['session_id']:
                        raise RuntimeError('native resumed thread id changed')
                    # Preserve the native transcript even if action/readiness verification fails.
                    self._artifact(outcome,'codex.events.jsonl',events.encode()[:1024*1024])
                    final=runtime.redact(await sandbox.files.read_file('/workspace/agent-final.md'))
                    marker=await sandbox.files.read_file('/workspace/agent-started.txt')
                    if not session or not completed or failed or not marker.strip() or not final.strip():raise RuntimeError('Codex execution did not meet the native completion/write contract')
                    outcome['native_turn_completed']=True
                    if cancelled():raise RuntimeError('task cancellation requested')
                    outcome['final']=final[:16000]
                    self._artifact(outcome,'result.md',final.encode()[:1000000])
                    await update({'index':1,'status':'passed','receipt':{'session_id':session,'startup_marker':marker[:200],'native_turn_completed':True}})
                    await update({'index':2,'status':'running'})
                else:
                    await update({'index':1,'status':'running'})
                if hook:
                    await hook[0](sandbox,command,task,outcome,self._artifact,runtime)
                collection=await command(['python','/workspace/collect_results.py'])
                data=await sandbox.files.read_bytes('/workspace/result.tar.gz')
                if len(data)>20*1024*1024:raise RuntimeError('result archive too large')
                with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
                    expanded=0
                    for member in archive:
                        if not member.isfile() or member.name.startswith('/') or '..' in member.name.split('/'):raise RuntimeError('unsafe result member')
                        expanded+=member.size
                        if member.size>10*1024*1024 or expanded>20*1024*1024:raise RuntimeError('result budget exceeded')
                        body=archive.extractfile(member).read()
                        if any(secret and (secret.encode() in body or base64.b64encode(secret.encode()) in body) for secret in (runtime.access_token,runtime.tool_access_token)):raise RuntimeError('task credential appeared in results')
                self._artifact(outcome,'result.tar.gz',data)
                await update({'index':1 if mode=='verify' else 2,'status':'passed','receipt':json.loads(collection['stdout'])})
                if durable:
                    from .durable_archive import CAPTURE_SCRIPT,validate_checkpoint
                    await sandbox.files.write_file('/workspace/capture_checkpoint.py',CAPTURE_SCRIPT,mode=600)
                    await command(['python','/workspace/capture_checkpoint.py'])
                    checkpoint_data=await sandbox.files.read_bytes('/workspace/checkpoint.tar.gz')
                    validate_checkpoint(checkpoint_data,known_tokens=(runtime.access_token,runtime.tool_access_token),
                                        session_id=outcome['codex_session_id'])
                    await durable['save_checkpoint'](checkpoint_data,outcome['codex_session_id'])
                if mode=='verify':
                    outcome['final']='Read-only trusted publication verification passed.'
                outcome['ok']=True
            except Exception as exc:
                outcome['error']=runtime.redact(str(exc))[:1800]
                if durable and not gateway_intent and lease is None and sandbox is None and not sandbox_create_attempted:
                    outcome['cleanup']={'not_created':True,'container_absent':True}
                    outcome['credential_closure']={'not_created':True}
                if durable and model_quiescent and sandbox is not None and not outcome.get('codex_session_id'):
                    try:
                        partial=await sandbox.files.read_file('/workspace/agent-events.jsonl')
                        for line in partial.splitlines():
                            event=json.loads(line)
                            if event.get('type')=='thread.started':outcome['codex_session_id']=event.get('thread_id')
                    except Exception:pass
                if durable and model_quiescent and sandbox is not None and outcome.get('codex_session_id') and not any(a['name']=='result.tar.gz' for a in outcome['artifacts']):
                    try:
                        from .durable_archive import CAPTURE_SCRIPT,validate_checkpoint
                        await sandbox.files.write_file('/workspace/capture_checkpoint.py',CAPTURE_SCRIPT,mode=600)
                        await command(['python','/workspace/capture_checkpoint.py'])
                        checkpoint_data=await sandbox.files.read_bytes('/workspace/checkpoint.tar.gz')
                        validate_checkpoint(checkpoint_data,known_tokens=(runtime.access_token,runtime.tool_access_token),session_id=outcome['codex_session_id'])
                        await durable['save_checkpoint'](checkpoint_data,outcome['codex_session_id'])
                    except Exception:pass
                if sandbox is not None and mode!='verify':
                    if durable and durable.get('prepare_definition') and not any(a['name']=='package-preparation.events.jsonl' for a in outcome['artifacts']):
                        try:
                            partial=runtime.redact(await sandbox.files.read_file('/workspace/preparation-events.jsonl'))
                            self._artifact(outcome,'package-preparation.events.jsonl',partial.encode()[:1024*1024])
                            outcome.setdefault('preparation_receipt',{'native_turn_completed':False,'diagnostic_only':True,
                                'events_sha256':hashlib.sha256(partial.encode()).hexdigest()})
                        except Exception:pass
                    if not any(a['name']=='codex.events.jsonl' for a in outcome['artifacts']):
                        try:
                            partial=runtime.redact(await sandbox.files.read_file('/workspace/agent-events.jsonl'))
                            self._artifact(outcome,'codex.events.jsonl',partial.encode()[:1024*1024])
                        except Exception:pass
                    for diagnostic_path in ('/workspace/agent-stderr.txt','/workspace/preparation-stderr.txt'):
                        try:
                            diagnostic=runtime.redact(await sandbox.files.read_file(diagnostic_path))[-1500:]
                            if diagnostic:
                                outcome['diagnostic']=diagnostic
                                break
                        except Exception:pass
            finally:
                if sandbox is not None:
                    try:
                        await service.dispose(sandbox,cfg)
                        outcome['cleanup']={'requested':True,'container_absent':not any(c.name=='sandbox-'+sandbox.id for c in service.docker.containers.list(all=True))}
                    except Exception:outcome['cleanup']={'requested':True,'container_absent':False}
                    if not outcome['cleanup']['container_absent']:outcome['ok']=False
                if lease is not None or gateway_intent:
                    try:
                        closed=await asyncio.to_thread(self.gateway.close,task['id'])
                        if closed!={'closed':True}:raise ValueError('lease closure unconfirmed')
                        outcome['credential_closure']={'closed':True}
                    except Exception as exc:
                        outcome['credential_closure']={'closed':False,'error':type(exc).__name__}
                        outcome.setdefault('error','gateway lease closure unconfirmed')
                        outcome['ok']=False
        return outcome

    @staticmethod
    def _artifact(result,name,data):
        result['artifacts'].append({'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'data':data})


def configured_executor(settings,task_service):
    """Explicit factory. An unconfigured model leaves task submission unavailable."""
    if not settings.gateway_url and not settings.codex_base_url and not settings.codex_api_key:
        return None
    try:
        images=json.loads(settings.codex_images)
    except (TypeError,ValueError) as exc:
        raise ValueError('CHATTASK_CODEX_IMAGES must be a JSON profile mapping') from exc
    if not isinstance(images,dict):raise ValueError('CHATTASK_CODEX_IMAGES must be an object')
    if settings.gateway_url:
        from .broker import GatewayClient
        gateway=GatewayClient(settings.gateway_url,settings.gateway_client_token,ca_file=settings.gateway_ca_file)
        runtime=CodexRuntime(base_url=settings.gateway_url+'/v1',access_token='pending-task-session',images=images,model=settings.codex_model,ca_certificate=gateway.ca_certificate)
    else:
        gateway=None
        runtime=CodexRuntime(base_url=settings.codex_base_url,access_token=settings.codex_api_key,
                             images=images,tool_base_url=settings.codex_tools_url,tool_access_token=settings.codex_tools_token,model=settings.codex_model)
    try:
        hosts=json.loads(settings.codex_hosts)
    except (TypeError,ValueError) as exc:
        raise ValueError('CHATTASK_CODEX_HOSTS must be a JSON mapping') from exc
    if not isinstance(hosts,dict):raise ValueError('CHATTASK_CODEX_HOSTS must be a JSON mapping')
    for host,ip in hosts.items():
        if not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',host) or '..' in host:
            raise ValueError('invalid configured host mapping')
        try:ipaddress.ip_address(ip)
        except (ValueError,TypeError):raise ValueError('invalid configured host IP') from None
    return CodexTaskExecutor(task_service,runtime,gateway=gateway,hosts=hosts)
