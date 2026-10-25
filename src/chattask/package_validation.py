"""Trusted Sandbox-side test/build checks for package preparation."""

PREPARE_VALIDATOR = r"""import base64,hashlib,json,os,re,subprocess,sys,tarfile,zipfile,urllib.request
from pathlib import Path
from urllib.request import Request,urlopen
root=Path('/workspace/result')
path=root/'package-plan.json'
raw=path.read_bytes()
if len(raw)>10*1024*1024:raise SystemExit('plan too large')
plan=json.loads(raw)
spec=plan['spec']
expected={'release/'+spec['version']}|({'main'} if spec['mode']=='new' else set())
if set(plan['sources'])!=expected:raise SystemExit('branch mismatch')
def tool(route,payload=None):
 base=os.environ.get('CHATTASK_TOOL_BASE_URL','')
 token=os.environ.get('CHATTASK_TOOL_ACCESS_TOKEN','')
 if not base or not token:raise SystemExit('read-only tool session missing')
 data=json.dumps(payload).encode() if payload is not None else None
 req=urllib.request.Request(base+route,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response:
  if response.status!=200:raise SystemExit('tool response failed')
  body=response.read(100001)
 if len(body)>100000:raise SystemExit('tool response too large')
 return json.loads(body)
catalog=tool('/tools/catalog')
catalog_text=json.dumps(catalog)
for required in ('/pypi/api/pkg/probe','/github/api/repo/snapshot'):
 if required not in catalog_text:raise SystemExit('read-only tool catalog incomplete')
probe=tool('/pypi/api/pkg/probe',{'package_name':spec['package_name']})
if not isinstance(probe,dict):raise SystemExit('invalid PyPI probe')
normalized=re.sub(r'[-_.]+','-',spec['package_name']).lower()
if probe.get('normalized_name')!=normalized or type(probe.get('exists')) is not bool or not isinstance(probe.get('versions'),list):
 raise SystemExit('PyPI probe must return normalized_name, exists and versions')
if spec['mode']=='new' and probe['exists']:raise SystemExit('package name already registered')
if spec['mode']=='existing' and not probe['exists']:raise SystemExit('existing PyPI project missing')
if spec['version'] in probe['versions']:raise SystemExit('target version already registered')
repo=tool('/github/api/repo/view')
if not isinstance(repo,dict) or repo.get('repository')!=spec['owner']+'/'+spec['repository'] or type(repo.get('exists')) is not bool:
 raise SystemExit('GitHub view must return exact repository and exists')
if spec['mode']=='new' and repo['exists']:raise SystemExit('repository already exists')
if spec['mode']=='existing' and not repo['exists']:raise SystemExit('existing repository missing')
status=tool('/github/api/status')
if not isinstance(status,dict):raise SystemExit('GitHub status response invalid')
if spec['mode']=='existing':
 snapshot=tool('/github/api/repo/snapshot')
 if not isinstance(snapshot,dict) or snapshot.get('repository')!=spec['owner']+'/'+spec['repository']:
  raise SystemExit('snapshot target mismatch')
 if not re.fullmatch('[0-9a-f]{40}',str(snapshot.get('sha',''))):raise SystemExit('snapshot SHA missing')
 if snapshot['sha']!=plan['sources']['release/'+spec['version']]['base_sha']:
  raise SystemExit('snapshot base SHA changed')
target_evidence='Live scoped PyPI probe SHA256 '+hashlib.sha256(json.dumps(probe,sort_keys=True).encode()).hexdigest()+'; GitHub repo SHA256 '+hashlib.sha256(json.dumps(repo,sort_keys=True).encode()).hexdigest()+'; status SHA256 '+hashlib.sha256(json.dumps(status,sort_keys=True).encode()).hexdigest()
if spec['mode']=='existing':
 target_evidence+='; exact GitHub snapshot '+snapshot['sha']
receipts={}
def rebuilt_registration_artifacts(directory):
 result=[]
 for artifact in sorted(directory.iterdir()):
  if not artifact.is_file() or not artifact.name.endswith(('.whl','.tar.gz')):continue
  data=artifact.read_bytes()
  if len(data)>5*1024*1024:raise SystemExit('registration distribution too large')
  result.append({'filename':artifact.name,'content_base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()})
 if len(result)!=2:raise SystemExit('rebuilt registration wheel and sdist required')
 return result
def run(argv,cwd,limit,label):
 allowed=('PATH','HOME','SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CODEX_CA_CERTIFICATE')
 env={key:os.environ[key] for key in allowed if key in os.environ}
 env.update(PYTHONPATH=str(cwd/'src'),PIP_CONFIG_FILE='/dev/null',PIP_DISABLE_PIP_VERSION_CHECK='1',PYTHONNOUSERSITE='1')
 result=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=limit,env=env)
 log=(result.stdout+'\n'+result.stderr)[-200000:]
 (root/(label+'.log')).write_text(log)
 if result.returncode or 'Ran 0 tests' in log or 'no tests ran' in log.lower():
  raise SystemExit(label+' failed; see result log')
 return hashlib.sha256(log.encode()).hexdigest()
for branch,entry in plan['sources'].items():
 name=branch.replace('/','-')
 dest=Path('/workspace/validation')/name
 dest.mkdir(parents=True,exist_ok=False)
 files=entry['files']
 if not 1<=len(files)<=100:raise SystemExit('file count invalid')
 total=0
 for item in files:
  rel=item['path']
  parts=Path(rel).parts
  if not rel or rel.startswith('/') or '\\' in rel or '..' in parts or any(p in ('.env','.git') for p in parts):
   raise SystemExit('unsafe source path')
  data=base64.b64decode(item['content_base64'],validate=True)
  total+=len(data)
  if len(data)>200000 or total>2000000 or hashlib.sha256(data).hexdigest()!=item['sha256']:
   raise SystemExit('source digest or size mismatch')
  target=dest/rel
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes(data)
 if not (dest/'tests').is_dir():raise SystemExit('tests directory required')
 test_cmd=[sys.executable,'-m','pytest','-q']
 if subprocess.run([sys.executable,'-c','import pytest'],capture_output=True).returncode:
  test_cmd=[sys.executable,'-m','unittest','discover','-s','tests','-v']
 test_hash=run(test_cmd,dest,180,name+'-tests')
 build_hash=run([sys.executable,'-m','build','--no-isolation','--outdir','dist','.'],dest,180,name+'-build')
 dist=list((dest/'dist').iterdir())
 if len([x for x in dist if x.name.endswith('.whl')])!=1 or len([x for x in dist if x.name.endswith('.tar.gz')])!=1:
  raise SystemExit('wheel and sdist required')
 version='0.0.1' if branch=='main' else spec['version']
 for artifact in dist:
  if version not in artifact.name:raise SystemExit('distribution version mismatch')
  if artifact.suffix=='.whl':
   with zipfile.ZipFile(artifact) as z:
    names=[x for x in z.namelist() if x.endswith('.dist-info/METADATA')]
    if len(names)!=1:raise SystemExit('wheel metadata missing')
    metadata=z.read(names[0]).decode()
  else:
   with tarfile.open(artifact,'r:gz') as z:
    names=[x for x in z.getnames() if x.endswith('/PKG-INFO') and x.count('/')==1]
    if len(names)!=1:raise SystemExit('sdist metadata missing')
    metadata=z.extractfile(names[0]).read(50000).decode()
  if not re.search(r'^Name: '+re.escape(spec['package_name'])+r'$',metadata,re.M|re.I) or not re.search(r'^Version: '+re.escape(version)+r'$',metadata,re.M):
   raise SystemExit('distribution metadata mismatch')
 receipts[branch]={'tests_sha256':test_hash,'build_sha256':build_hash,'wheel_sdist':sorted(x.name for x in dist)}
 if branch=='main':plan['placeholder_artifacts']=rebuilt_registration_artifacts(dest/'dist')
validation=plan.get('validation',{})
validation['tools']={'status':'passed','evidence':'Read-only scoped tool catalog and probe completed in Sandbox'}
validation['target']={'status':'passed','evidence':target_evidence}
validation['tests']={'status':'passed','evidence':'Executed tests in Sandbox: '+json.dumps(receipts,sort_keys=True)}
validation['build']={'status':'passed','evidence':'Executed python -m build --no-isolation; wheel+sdist: '+json.dumps(receipts,sort_keys=True)}
validation['metadata']={'status':'passed','evidence':'Parsed built wheel and sdist Name/Version metadata for '+','.join(sorted(expected))}
plan['validation']=validation
final=json.dumps(plan,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
if len(final)>10*1024*1024:raise SystemExit('validated plan too large')
path.write_bytes(final)
att={'plan_sha256':hashlib.sha256(final).hexdigest(),'checks':validation,'branches':receipts}
Path('/workspace/trusted-validation.json').write_text(json.dumps(att,sort_keys=True))
print(json.dumps({'plan_sha256':att['plan_sha256'],'branches':sorted(expected)}))
"""
