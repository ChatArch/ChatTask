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
            if len(members)>200 or sum(x.file_size for x in members)>5*1024*1024 or any(stat.S_ISLNK(x.external_attr>>16) or x.filename.startswith('/') or any(p in ('','.', '..') for p in x.filename.rstrip('/').split('/')) for x in members):
                raise ValueError('unsafe wheel contents')
            matches=[x for x in members if x.filename.endswith('.dist-info/METADATA')]
            if len(matches)!=1:
                raise ValueError('wheel metadata missing')
            metadata=wheel.read(matches[0]).decode('utf-8')
            expected={path[4:]:body for path,body in main_files.items() if path.startswith('src/'+spec['module']+'/') and path.endswith('.py')}
            if not expected or any(name not in wheel.namelist() or wheel.read(name)!=body for name,body in expected.items()):
                raise ValueError('placeholder wheel differs from main source')
            if set(name for name in wheel.namelist() if name.endswith('.py'))!=set(expected) or any(name.endswith('.pth') for name in wheel.namelist()):
                raise ValueError('placeholder wheel has extra package code')
    elif filename.endswith('.tar.gz'):
        if not filename.lower().startswith(spec['package_name'].lower()+'-'+version):
            raise ValueError('placeholder sdist filename mismatch')
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            members=archive.getmembers()
            if len(members)>200 or sum(x.size for x in members)>5*1024*1024 or any(not (x.isfile() or x.isdir()) or x.name.startswith('/') or any(p in ('','.', '..') for p in x.name.rstrip('/').split('/')) for x in members):
                raise ValueError('unsafe sdist contents')
            matches=[x for x in members if x.name.endswith('/PKG-INFO')]
            if len(matches)!=1:
                raise ValueError('sdist metadata missing')
            metadata=archive.extractfile(matches[0]).read(50000).decode('utf-8')
            expected={path:body for path,body in main_files.items() if path=='pyproject.toml' or path.startswith('src/'+spec['module']+'/') and path.endswith('.py')}
            for member in members:
                if member.isfile() and member.name.endswith('.py') and not any(member.name.endswith('/'+path) for path in main_files if path.endswith('.py')):
                    raise ValueError('placeholder sdist has extra code')
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
        workflows=[path for path in found if path.startswith('.github/workflows/') and path.endswith(('.yml','.yaml'))]
        if not any('publish' in x for x in workflows) or not any('ci' in x or 'test' in x for x in workflows):
            raise ValueError('standard CI/OIDC workflow files required')
        publish=b'\n'.join(found[x] for x in workflows if 'publish' in x)
        if b'id-token: write' not in publish or b'pypa/gh-action-pypi-publish' not in publish:
            raise ValueError('OIDC publish workflow shape mismatch')
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
