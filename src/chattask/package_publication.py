"""Trusted package postprocessors and strict publication evidence checks."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import socket
import ssl
import urllib.error
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.request
from urllib.parse import urlsplit

HEX40=re.compile(r'[0-9a-f]{40}\Z')
HEX64=re.compile(r'[0-9a-f]{64}\Z')

def _sha(value):
    return isinstance(value,str) and bool(HEX40.fullmatch(value))

def verify_publication_receipts(policy,status,registry):
    """Accept only scoped, exact-head release and actual OIDC publish receipts."""
    try:
        repo=policy['repository'];release=policy['release_branch'];tag=policy['tag']
        if not isinstance(status,dict) or status.get('repository')!=repo:
            raise ValueError('scoped repository receipt mismatch')
        branches=status['branches'];release_info=branches[release];main=branches['main']
        head=release_info['sha'];merged=main['sha']
        if not _sha(head) or not _sha(merged):
            raise ValueError('branch SHA missing')
        if release_info['manifest_digest']!=policy['sources'][release]['digest'] or release_info.get('base_sha')!=policy['sources'][release]['base_sha']:
            raise ValueError('released source manifest/base mismatch')
        if main.get('manifest_digest')!=policy['sources'][release]['digest']:
            raise ValueError('merged main source manifest mismatch')
        pr=status['pull_request']
        if (pr.get('head'),pr.get('base'),pr.get('head_sha'),pr.get('merged'),pr.get('merge_commit_sha'))!=(release,'main',head,True,merged) or type(pr.get('number')) is not int or pr['number']<1:
            raise ValueError('exact-head squash merge receipt mismatch')
        ci=status['ci']
        if (ci['release'].get('head_sha'),ci['release'].get('conclusion'))!=(head,'success') or (ci['main'].get('head_sha'),ci['main'].get('conclusion'))!=(merged,'success'):
            raise ValueError('exact-head CI did not pass')
        if status['tag']!={'name':tag,'sha':merged}:
            raise ValueError('tag does not bind merged tree')
        publish=status['publish'];job=publish['job'];step=job['step']
        if (publish.get('workflow'),publish.get('head_sha'),publish.get('conclusion'))!=('publish.yml',merged,'success') or not isinstance(job.get('name'),str) or not job['name'] or job.get('conclusion')!='success' or not isinstance(step.get('name'),str) or not step['name'] or (step.get('conclusion'),step.get('uses'))!=('success','pypa/gh-action-pypi-publish@release/v1'):
            raise ValueError('actual Publish-to-PyPI job/step missing or failed')
        info=registry['info']
        if not isinstance(info,dict) or re.sub(r'[-_.]+','-',info.get('name','')).lower()!=re.sub(r'[-_.]+','-',policy['project']).lower() or info.get('version')!=policy['version']:
            raise ValueError('PyPI target/version mismatch')
        urls=registry['urls']
        if not isinstance(urls,list) or not 2<=len(urls)<=20:
            raise ValueError('PyPI release files missing')
        kinds=set();files=[]
        for entry in urls:
            name=entry['filename'];digest=entry['digests']['sha256']
            if not _safe_filename(name) or not name.startswith(re.sub(r'[-_.]+','_',policy['project']).lower()+'-'+policy['version']) and not name.lower().startswith(policy['project'].lower()+'-'+policy['version']):
                raise ValueError('PyPI filename target mismatch')
            if not isinstance(digest,str) or not HEX64.fullmatch(digest):
                raise ValueError('PyPI file digest missing')
            kind='wheel' if name.endswith('.whl') else 'sdist' if name.endswith('.tar.gz') else ''
            if not kind or kind in kinds:
                raise ValueError('PyPI wheel/sdist mismatch')
            kinds.add(kind);files.append({'filename':name,'sha256':digest,'url':entry.get('url','')})
        if kinds!={'wheel','sdist'}:
            raise ValueError('PyPI wheel and sdist required')
        return {'repository':repo,'release_branch':release,'release_head_sha':head,
                'merged_sha':merged,'tag':tag,'version':policy['version'],'files':files,
                'publish_job':job['name'],'publish_step':step['name']}
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('publication receipt incomplete') from None

def _read_only(request,limit,phase,*,timeout=12):
    # Two bounded attempts; only transport failures are retried.
    host=urlsplit(request.full_url).hostname or 'unknown'
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:
                length=response.headers.get('content-length','0')
                if response.status!=200 or not str(length).isdigit() or int(length)>limit:
                    raise ValueError(phase+' readback rejected by '+host)
                final=urlsplit(response.geturl())
                original=urlsplit(request.full_url)
                if final.scheme!='https' or final.hostname!=original.hostname:
                    raise ValueError(phase+' readback target changed at '+host)
                data=response.read(limit+1)
            if len(data)>limit:
                raise ValueError(phase+' readback too large at '+host)
            return data
        except urllib.error.HTTPError as exc:
            raise ValueError(phase+' HTTP '+str(exc.code)+' at '+host) from None
        except urllib.error.URLError as exc:
            reason=exc.reason
            if isinstance(reason,ssl.SSLError):
                raise ValueError(phase+' TLS verification failed at '+host) from None
            if not isinstance(reason,(TimeoutError,ConnectionError,socket.gaierror,OSError)):
                raise ValueError(phase+' network rejected at '+host) from None
            if attempt:raise ValueError(phase+' network unavailable after 2 attempts at '+host) from None
        except ssl.SSLError:
            raise ValueError(phase+' TLS verification failed at '+host) from None
        except (TimeoutError,ConnectionError,socket.gaierror,OSError):
            if attempt:raise ValueError(phase+' network unavailable after 2 attempts at '+host) from None

def _bounded_get(url,limit):
    request=urllib.request.Request(url,headers={'Accept':'application/json'})
    return _read_only(request,limit,'public registry')

def _scoped_request(route,accept):
    base=os.environ['CHATTASK_TOOL_BASE_URL'];token=os.environ['CHATTASK_TOOL_ACCESS_TOKEN']
    if not base.startswith('https://') or not route.startswith('/') or not token:
        raise ValueError('scoped tool missing')
    return urllib.request.Request(base+route,headers={'Authorization':'Bearer '+token,'Accept':accept})

def _guest_tool(route,*,limit=500000,timeout=60):
    return json.loads(_read_only(_scoped_request(route,'application/json'),limit,'scoped readback',timeout=timeout))

def _guest_bytes(digest):
    if not isinstance(digest,str) or not HEX64.fullmatch(digest):
        raise ValueError('PyPI file digest missing')
    request=_scoped_request('/pypi/api/artifacts/'+digest,'application/octet-stream')
    return _read_only(request,10*1024*1024,'scoped artifact',timeout=12)

def _release_json(mode):
    return _guest_tool('/pypi/api/release',limit=1000000,timeout=12)

def _placeholder_json(mode):
    return _guest_tool('/pypi/api/placeholder',limit=1000000,timeout=12) if mode=='new' else None

def _safe_filename(name):
    return isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,254}',name)) and name not in ('.','..') and Path(name).name==name

def _fetch_artifact(item,published):
    name=item['filename'];digest=item['sha256']
    if not _safe_filename(name) or not isinstance(digest,str) or not HEX64.fullmatch(digest):
        raise ValueError('PyPI filename or digest rejected')
    parsed=urlsplit(item.get('url',''))
    if parsed.scheme!='https' or parsed.hostname!='files.pythonhosted.org' or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('PyPI file URL rejected')
    data=_guest_bytes(digest)
    if hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError('PyPI file bytes mismatch')
    published.mkdir(exist_ok=True)
    destination=published/name
    destination.write_bytes(data)
    return destination

def _dependency_provenance(digest):
    return {'source':'pinned-wheelhouse','sha256':digest} if digest else {'source':'public-index','sha256':None}

def _stage_wheelhouse(blob,expected,destination):
    if not isinstance(blob,bytes) or not blob or len(blob)>32*1024*1024 or not isinstance(expected,str) or not HEX64.fullmatch(expected) or hashlib.sha256(blob).hexdigest()!=expected:
        raise ValueError('public dependency archive digest or size mismatch')
    wheels=[];seen=set();total=0
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as archive:
        members=archive.getmembers()
        if len(members)>5000:
            raise ValueError('public dependency archive member count exceeded')
        for member in members:
            name=member.name.rstrip('/') if member.isdir() else member.name
            parts=name.split('/')
            if not name or name.startswith('/') or any(part in ('','.','..') for part in parts) or '\\' in name or parts[0]!='deps':
                raise ValueError('public dependency archive path rejected')
            if name in seen:
                raise ValueError('public dependency archive duplicate member')
            seen.add(name)
            if not (member.isfile() or member.isdir()) or member.size<0:
                raise ValueError('public dependency archive member rejected')
            total+=member.size
            if total>128*1024*1024:
                raise ValueError('public dependency archive expanded size exceeded')
            if parts[:2]==['deps','wheelhouse'] and member.isfile():
                if len(parts)!=3 or not _safe_filename(parts[2]) or not parts[2].endswith('.whl') or member.size>32*1024*1024:
                    raise ValueError('public dependency wheelhouse member rejected')
                wheels.append(member)
        if not wheels:
            raise ValueError('public dependency wheelhouse empty')
        destination.mkdir()
        for member in wheels:
            data=archive.extractfile(member).read(member.size+1)
            if len(data)!=member.size:
                raise ValueError('public dependency wheelhouse member truncated')
            (destination/member.name.split('/')[-1]).write_bytes(data)
    return _dependency_provenance(expected)

def _install_argv(venv,wheel,wheelhouse):
    base=[venv+'/bin/python','-m','pip','install','--no-input']
    if wheelhouse is not None:
        return base+['--no-index','--find-links',str(wheelhouse),str(wheel)]
    return base+['--index-url','https://pypi.org/simple',str(wheel)]

def _run(argv,seconds,log,expected=None):
    allowed=('PATH','HOME','SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CODEX_CA_CERTIFICATE')
    env={key:os.environ[key] for key in allowed if key in os.environ}
    env.update(PIP_CONFIG_FILE='/dev/null',PIP_DISABLE_PIP_VERSION_CHECK='1',PYTHONNOUSERSITE='1')
    if '--no-index' in argv:
        env['PIP_NO_INDEX']='1'
    else:
        env['PIP_INDEX_URL']='https://pypi.org/simple'
    result=subprocess.run(argv,capture_output=True,text=True,timeout=seconds,env=env,cwd='/workspace')
    output=(result.stdout+'\n'+result.stderr)[-50000:]
    with log.open('a') as stream:
        stream.write('$ '+argv[0]+' [arguments omitted]\n'+output+'\n')
    if result.returncode:
        raise ValueError('clean install/CLI command failed')
    if expected is not None and not re.search(r'(?<![0-9.])'+re.escape(expected)+r'(?![0-9.])',result.stdout):
        raise ValueError('installed CLI version does not match the release')
    if '--tree' in argv and not result.stdout.strip():
        raise ValueError('installed CLI tree is empty')
    return hashlib.sha256(output.encode()).hexdigest()

def guest_main():
    context=json.loads(Path('/workspace/publication-policy.json').read_text())
    policy=context['policy'];expected=context['plan_sha256'];spec=context['spec']
    archive=Path('/workspace/input/previous-result.tar.gz').read_bytes()
    if len(archive)>20*1024*1024:
        raise ValueError('input archive too large')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        members=[m for m in tar if m.name=='package-plan.json' and m.isfile() and m.size<=10*1024*1024]
        if len(members)!=1:
            raise ValueError('approved plan missing')
        raw=tar.extractfile(members[0]).read(10*1024*1024+1)
    if len(raw)>10*1024*1024 or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('approved plan digest mismatch')
    plan=json.loads(raw)
    if plan.get('spec')!=spec:
        raise ValueError('approved spec mismatch')
    status=_guest_tool('/github/api/status')
    registry=_release_json(policy['mode'])
    receipt=verify_publication_receipts(policy,status,registry)
    if policy['mode']=='new':
        placeholder=_placeholder_json(policy['mode'])
        found={entry['filename']:entry['digests']['sha256'] for entry in placeholder.get('urls',[])}
        if placeholder.get('info',{}).get('version')!='0.0.1' or found!=policy['artifact_digests']:
            raise ValueError('0.0.1 registration placeholder readback mismatch')
    published=Path('/workspace/published')
    file_digests={};wheel=None
    for item in receipt['files']:
        target=_fetch_artifact(item,published)
        file_digests[item['filename']]=item['sha256']
        if item['filename'].endswith('.whl'):
            wheel=target
    if wheel is None:
        raise ValueError('PyPI wheel missing')
    dependency_sha=context.get('public_dependencies_sha256')
    wheelhouse=None
    if dependency_sha is not None:
        dependencies=Path('/workspace/public-deps.tar.gz').read_bytes()
        dependency_source=_stage_wheelhouse(dependencies,dependency_sha,Path('/workspace/verification-deps'))
        wheelhouse=Path('/workspace/verification-deps')
    else:
        dependency_source=_dependency_provenance(None)
    root=Path('/workspace/result');root.mkdir(exist_ok=True)
    log=root/'publication-install.log'
    venv='/workspace/publication-venv'
    _run([sys.executable,'-m','venv',venv],45,log)
    pip_hash=_run(_install_argv(venv,wheel,wheelhouse),180,log)
    _run([venv+'/bin/python','-m','pip','check'],30,log)
    cli=spec['cli']
    version_hash=_run([venv+'/bin/'+cli,'--version'],30,log,expected=policy['version'])
    tree_hash=_run([venv+'/bin/'+cli,'--tree'],30,log)
    receipt.update(plan_sha256=expected,pypi_file_digests=file_digests,
                   clean_install_sha256=pip_hash,cli_version_sha256=version_hash,cli_tree_sha256=tree_hash,
                   dependency_source=dependency_source)
    (root/'trusted-publication.json').write_text(json.dumps(receipt,sort_keys=True,separators=(',',':')))
    print(json.dumps({'plan_sha256':expected,'tag':receipt['tag'],'merged_sha':receipt['merged_sha']}))

async def prepare_postprocess(sandbox,command,task,outcome,artifact,runtime):
    from .package_validation import PREPARE_VALIDATOR
    await sandbox.files.write_file('/workspace/validate_package.py',PREPARE_VALIDATOR,mode=600)
    await command(['python','/workspace/validate_package.py'],seconds=550)
    attestation=await sandbox.files.read_file('/workspace/trusted-validation.json')
    artifact(outcome,'trusted-validation.json',runtime.redact(attestation).encode())

async def publish_postprocess(sandbox,command,task,outcome,artifact,runtime,*,public_dependencies=None):
    from .package_plan import verify_plan
    capability=task['capability']
    archive=task.get('_input_archive')
    if not isinstance(archive,bytes):
        raise ValueError('publish input archive missing')
    checked=verify_plan(archive,capability['spec'])
    if checked['plan_sha256']!=capability['plan_sha256'] or checked['policy']!=capability['plan']:
        raise ValueError('approved publication manifest changed')
    await sandbox.files.write_file('/workspace/package_publication.py',Path(__file__).read_bytes(),mode=600)
    context={'policy':capability['plan'],'spec':capability['spec'],'plan_sha256':capability['plan_sha256']}
    if public_dependencies is not None:
        if not isinstance(public_dependencies,bytes) or not public_dependencies or len(public_dependencies)>32*1024*1024:
            raise ValueError('public dependency archive size rejected')
        context['public_dependencies_sha256']=hashlib.sha256(public_dependencies).hexdigest()
        await sandbox.files.write_file('/workspace/public-deps.tar.gz',public_dependencies,mode=600)
    await sandbox.files.write_file('/workspace/publication-policy.json',json.dumps(context,sort_keys=True),mode=600)
    try:
        await command(['python','/workspace/package_publication.py','--guest'],seconds=470)
        raw=await sandbox.files.read_file('/workspace/result/trusted-publication.json')
        receipt=json.loads(raw)
        if (receipt.get('plan_sha256')!=capability['plan_sha256'] or
            receipt.get('repository')!=capability['plan']['repository'] or
            receipt.get('version')!=capability['plan']['version'] or
            receipt.get('dependency_source')!=_dependency_provenance(context.get('public_dependencies_sha256'))):
            raise ValueError('trusted publication receipt mismatch')
        artifact(outcome,'trusted-publication.json',runtime.redact(raw).encode())
    finally:
        for name in ('publication-install.log','publication-validation.log'):
            try:
                log=await sandbox.files.read_file('/workspace/result/'+name)
                artifact(outcome,name,runtime.redact(log).encode()[:200000])
            except Exception:
                pass

if __name__=='__main__' and sys.argv[1:]==['--guest']:
    try:
        guest_main()
    except Exception as exc:
        failure='trusted publication blocked: '+str(exc)[:500]
        Path('/workspace/result').mkdir(exist_ok=True)
        Path('/workspace/result/publication-validation.log').write_text(failure+'\n')
        print(failure,file=sys.stderr)
        raise SystemExit(1)
