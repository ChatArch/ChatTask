"""Bounded, non-executing trusted verification of a package preparation archive."""
from __future__ import annotations
import ast
import base64
import hashlib
import io
import json
import re
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from .package_template import policy

HEX = re.compile(r'[0-9a-f]{64}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_PLAN = 10*1024*1024
MAX_FILE = 200000
SECRET = (b'-----BEGIN PRIVATE KEY-----',b'ghp_',b'github_pat_',b'pypi-AgEI',b'sk-proj-')

def _json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    return json.loads(data,object_pairs_hook=unique)

def _path(value):
    if not isinstance(value,str) or not value or len(value)>200 or value.startswith('/') or '\\' in value:
        raise ValueError('unsafe source path')
    parts=value.split('/')
    if any(part in ('.','..','') for part in parts) or any(part in ('.env','.git') for part in parts) or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ValueError('unsafe source path')
    if value.endswith(('.pem','.key')) or '/.env' in value:
        raise ValueError('credential-like source path')
    return value

def _bytes(entry,limit):
    if not isinstance(entry,dict) or not isinstance(entry.get('sha256'),str) or not HEX.fullmatch(entry['sha256']):
        raise ValueError('missing content digest')
    encoded=entry.get('content_base64')
    if not isinstance(encoded,str) or len(encoded)>limit*2:
        raise ValueError('invalid encoded content')
    try:
        data=base64.b64decode(encoded,validate=True)
    except (ValueError,base64.binascii.Error):
        raise ValueError('invalid base64 content') from None
    if len(data)>limit or hashlib.sha256(data).hexdigest()!=entry['sha256'] or any(marker in data for marker in SECRET):
        raise ValueError('source/artifact integrity or credential check failed')
    return data

def _distribution(filename,data,spec,main_files):
    name=re.sub(r'[-_.]+','_',spec['package_name']).lower()
    version='0.0.1'
    if filename.endswith('.whl'):
        if not filename.lower().startswith(name+'-'+version+'-'):
            raise ValueError('placeholder wheel filename mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            members=wheel.infolist()
            if len(members)>200 or len({x.filename for x in members})!=len(members) or sum(x.file_size for x in members)>5*1024*1024 or any(stat.S_ISLNK(x.external_attr>>16) or x.filename.startswith('/') or any(p in ('','.', '..') for p in x.filename.rstrip('/').split('/')) for x in members):
                raise ValueError('unsafe wheel contents')
            matches=[x for x in members if x.filename.endswith('.dist-info/METADATA')]
            if len(matches)!=1:
                raise ValueError('wheel metadata missing')
            metadata=wheel.read(matches[0]).decode('utf-8')
            expected={path[4:]:body for path,body in main_files.items() if path.startswith('src/'+spec['module']+'/')}
            if not expected or any(name not in wheel.namelist() or wheel.read(name)!=body for name,body in expected.items()):
                raise ValueError('placeholder wheel differs from main source')
            metadata_root=matches[0].filename.rsplit('/',1)[0]+'/'
            payload={item.filename for item in members if not item.is_dir() and not item.filename.startswith(metadata_root)}
            if payload!=set(expected) or any(name.endswith('.pth') for name in wheel.namelist()):
                raise ValueError('placeholder wheel has unreviewed executable or data content')
            for item in members:
                if item.is_dir() or not item.filename.startswith(metadata_root):continue
                relative=item.filename[len(metadata_root):]
                if relative in ('METADATA','WHEEL','RECORD','entry_points.txt','top_level.txt'):continue
                if relative.startswith('licenses/'):
                    source=relative.removeprefix('licenses/')
                    if source in main_files and wheel.read(item)==main_files[source]:continue
                raise ValueError('placeholder wheel contains unbound metadata content')
    elif filename.endswith('.tar.gz'):
        from packaging.utils import parse_sdist_filename,canonicalize_name
        try:distribution,parsed_version=parse_sdist_filename(filename)
        except ValueError:raise ValueError('placeholder sdist filename mismatch') from None
        if distribution!=canonicalize_name(spec['package_name']) or str(parsed_version)!=version:
            raise ValueError('placeholder sdist filename mismatch')
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            members=archive.getmembers()
            if len(members)>200 or len({x.name for x in members})!=len(members) or sum(x.size for x in members)>5*1024*1024 or any(not (x.isfile() or x.isdir()) or x.name.startswith('/') or any(p in ('','.', '..') for p in x.name.rstrip('/').split('/')) for x in members):
                raise ValueError('unsafe sdist contents')
            matches=[x for x in members if x.name.endswith('/PKG-INFO') and x.name.count('/')==1]
            if len(matches)!=1:
                raise ValueError('sdist metadata missing')
            metadata=archive.extractfile(matches[0]).read(50000).decode('utf-8')
            expected={path:body for path,body in main_files.items() if path=='pyproject.toml' or path.startswith('src/'+spec['module']+'/')}
            roots={m.name.split('/',1)[0] for m in members}
            if len(roots)!=1:raise ValueError('sdist has multiple roots')
            egg=re.sub(r'[-_.]+','_',spec['package_name']).lower()+'.egg-info/'
            for member in members:
                if not member.isfile():continue
                relative=member.name.split('/',1)[1] if '/' in member.name else ''
                content=archive.extractfile(member).read()
                if relative in main_files and content==main_files[relative]:continue
                if relative=='PKG-INFO':continue
                lower=relative.lower()
                if lower.startswith('src/'+egg) and lower.removeprefix('src/'+egg) in ('pkg-info','sources.txt','requires.txt','dependency_links.txt','entry_points.txt','top_level.txt','zip-safe','not-zip-safe'):continue
                if relative=='setup.cfg':
                    import configparser
                    config=configparser.ConfigParser(interpolation=None);config.read_string(content.decode())
                    if config.sections()==['egg_info'] and dict(config['egg_info'])=={'tag_build':'','tag_date':'0'}:continue
                raise ValueError('placeholder sdist contains unreviewed content')
            for path,body in expected.items():
                found=[m for m in members if m.name.endswith('/'+path)]
                if len(found)!=1 or archive.extractfile(found[0]).read()!=body:
                    raise ValueError('placeholder sdist differs from main source')
    else:
        raise ValueError('unsupported placeholder artifact')
    if not re.search(r'^Name: '+re.escape(spec['package_name'])+r'$',metadata,re.M|re.I) or not re.search(r'^Version: 0\.0\.1$',metadata,re.M):
        raise ValueError('placeholder distribution metadata mismatch')

def _declared_version(project,document,files,module):
    if isinstance(project.get('version'),str):
        return project['version']
    if 'version' not in project.get('dynamic',[]):
        return None
    attr=document.get('tool',{}).get('setuptools',{}).get('dynamic',{}).get('version',{}).get('attr')
    if attr!=module+'.__version__':
        return None
    init=files.get('src/'+module+'/__init__.py')
    if init is None:
        return None
    tree=ast.parse(init.decode('utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets):
            value=ast.literal_eval(node.value)
            return value if isinstance(value,str) else None
    return None

def manifest_digest(files):
    return hashlib.sha256(json.dumps(sorted((f['path'],f['sha256']) for f in files),separators=(',',':')).encode()).hexdigest()

def _workflow_contract(files):
    import yaml
    for path in ['.github/workflows/ci.yml','.github/workflows/publish.yml']:
        if path not in files:raise ValueError('exact standard CI/publish.yml workflows required')
    ci=yaml.safe_load(files['.github/workflows/ci.yml'])
    if not isinstance(ci,dict) or ci.get('name')!='CI':raise ValueError('project CI workflow must be named CI')
    events=ci.get('on',ci.get(True))
    event_names=set(events) if isinstance(events,(dict,list)) else {events}
    job=(ci.get('jobs') or {}).get('test')
    if not {'push','pull_request'}<=event_names or not isinstance(job,dict) or job.get('name','test')!='test' or 'matrix' in (job.get('strategy') or {}) and 'name' not in job:
        raise ValueError('exact project test check and PR/main CI triggers required')
    text=files['.github/workflows/publish.yml'].decode('utf-8')
    # Match the deployed tag gate before any irreversible name registration.
    if not all(value in text for value in ['id-token: write','pypa/gh-action-pypi-publish','on:\n  push:\n    tags:','v*']):
        raise ValueError('deployed tag-only OIDC workflow contract missing')
    publish=yaml.safe_load(text)
    if not isinstance(publish,dict):raise ValueError('invalid publish workflow')
    trigger=publish.get('on',publish.get(True))
    if not isinstance(trigger,dict) or set(trigger)!={'push'} or not isinstance(trigger['push'],dict) or set(trigger['push'])!={'tags'} or not isinstance(trigger['push']['tags'],list) or 'v*' not in trigger['push']['tags']:
        raise ValueError('publish.yml must be tag-push only')
    candidates=[]
    for name,definition in (publish.get('jobs') or {}).items():
        if not isinstance(definition,dict):raise ValueError('invalid publish job')
        steps=definition.get('steps',[])
        for step in steps:
            if not isinstance(step,dict):raise ValueError('invalid publish step')
            if step.get('uses')=='pypa/gh-action-pypi-publish@release/v1':
                permissions=definition.get('permissions',publish.get('permissions',{}))
                label=step.get('name') or 'Run '+step['uses']
                labels=[s.get('name') or 'Run '+s.get('uses','') for s in steps]
                if not isinstance(permissions,dict) or permissions.get('id-token')!='write' or labels.count(label)!=1 or definition.get('strategy'):
                    raise ValueError('unambiguous OIDC upload step and permission required')
                candidates.append((name,label))
    if len(candidates)!=1:raise ValueError('exactly one standard PyPI publish action required')

def _verify_plan(archive_bytes,spec):
    if not isinstance(archive_bytes,bytes) or len(archive_bytes)>20*1024*1024:
        raise ValueError('preparation archive too large')
    with tarfile.open(fileobj=io.BytesIO(archive_bytes),mode='r:gz') as archive:
        members=archive.getmembers()
        if len(members)>100 or len({x.name for x in members})!=len(members) or sum(x.size for x in members)>20*1024*1024 or any(not x.isfile() or x.size>MAX_PLAN or x.name.startswith('/') or any(p in ('','.', '..') for p in x.name.split('/')) or x.name.endswith(('.pem','.key')) or '/.env' in x.name for x in members):
            raise ValueError('unsafe preparation archive')
        for member in members:
            if member.name!='package-plan.json' and any(marker in archive.extractfile(member).read() for marker in SECRET):
                raise ValueError('credential-like preparation content')
        plans=[x for x in members if x.name=='package-plan.json']
        if len(plans)!=1:
            raise ValueError('package-plan.json missing or duplicated')
        raw=archive.extractfile(plans[0]).read(MAX_PLAN+1)
    if len(raw)>MAX_PLAN:
        raise ValueError('package plan too large')
    plan=_json(raw)
    if not isinstance(plan,dict) or set(plan)!={'spec','sources','placeholder_artifacts','validation'} or plan['spec']!=spec:
        raise ValueError('preparation spec mismatch')
    sources=plan['sources']
    expected={'release/'+spec['version']}|({'main'} if spec['mode']=='new' else set())
    if not isinstance(sources,dict) or set(sources)!=expected:
        raise ValueError('source branches mismatch')
    digests={};source_bytes={}
    for branch,entry in sources.items():
        if not isinstance(entry,dict) or set(entry)!={'files','base_sha'}:
            raise ValueError('source entry fields mismatch')
        base=entry['base_sha']
        if spec['mode']=='new' and base is not None or spec['mode']=='existing' and (not isinstance(base,str) or not SHA.fullmatch(base)):
            raise ValueError('source base SHA mismatch')
        files=entry['files']
        if not isinstance(files,list) or not 1<=len(files)<=100:
            raise ValueError('source file count invalid')
        found={};total=0
        for file in files:
            if not isinstance(file,dict) or set(file)!={'path','content_base64','sha256'}:
                raise ValueError('source file fields mismatch')
            path=_path(file['path'])
            if path in found:
                raise ValueError('duplicate source path')
            data=_bytes(file,MAX_FILE);total+=len(data)
            if total>2000000:
                raise ValueError('source bytes limit exceeded')
            found[path]=data
        if 'pyproject.toml' not in found:
            raise ValueError('pyproject.toml required')
        document=tomllib.loads(found['pyproject.toml'].decode('utf-8'))
        project=document.get('project',{})
        version='0.0.1' if branch=='main' else spec['version']
        if project.get('name')!=spec['package_name'] or _declared_version(project,document,found,spec['module'])!=version or project.get('requires-python')!='>='+spec['python_min'] or project.get('license')!=spec['license']:
            raise ValueError('source project/version/Python metadata mismatch')
        scripts=project.get('scripts',{})
        if spec['cli'] not in scripts or not str(scripts[spec['cli']]).startswith(spec['module']+'.'):
            raise ValueError('CLI metadata mismatch')
        if spec['docs']=='bilingual_mkdocs' and not ('mkdocs.yml' in found and any(x.startswith('docs/en/') for x in found) and any(x.startswith('docs/zh/') for x in found)):
            raise ValueError('bilingual MkDocs source missing')
        _workflow_contract(found)
        digests[branch]={'digest':manifest_digest(files),'base_sha':base}
        source_bytes[branch]=found
    artifacts=plan['placeholder_artifacts']
    if not isinstance(artifacts,list) or (spec['mode']=='existing' and artifacts) or (spec['mode']=='new' and len(artifacts)!=2):
        raise ValueError('placeholder artifact count mismatch')
    artifact_digests={}
    for item in artifacts:
        if not isinstance(item,dict) or set(item)!={'filename','content_base64','sha256'}:
            raise ValueError('placeholder artifact fields mismatch')
        filename=item['filename']
        if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}',filename) or filename in artifact_digests:
            raise ValueError('invalid placeholder filename')
        data=_bytes(item,5*1024*1024)
        _distribution(filename,data,spec,source_bytes['main'])
        artifact_digests[filename]=item['sha256']
    checks=plan['validation']
    if not isinstance(checks,dict) or set(checks)!=set(('tools','tests','build','metadata','target')):
        raise ValueError('validation receipts incomplete')
    for item in checks.values():
        if not isinstance(item,dict) or set(item)!={'status','evidence'} or item['status']!='passed' or not isinstance(item['evidence'],str) or not 8<=len(item['evidence'])<=2000:
            raise ValueError('validation check not passed')
    result={'plan_sha256':hashlib.sha256(raw).hexdigest(),'archive_sha256':hashlib.sha256(archive_bytes).hexdigest(),
            'source_manifests':digests,'artifact_digests':artifact_digests,'validation':checks,
            'policy':policy(spec,sources=digests,artifact_digests=artifact_digests)}
    return result

def verify_plan(archive_bytes,spec):
    try:
        return _verify_plan(archive_bytes,spec)
    except ValueError:
        raise
    except Exception:
        raise ValueError('invalid package preparation content') from None
