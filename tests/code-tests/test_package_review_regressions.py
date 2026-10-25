import ast,base64,hashlib,io,json,tarfile,tempfile,unittest,zipfile,os
from pathlib import Path
import test_package_live as fixtures
from chattask.package_plan import verify_plan

class ReviewRegressionTests(unittest.TestCase):
 def plan(self):
  fixture=fixtures.PackageLiveTests()
  with tarfile.open(fileobj=io.BytesIO(fixture.plan_archive()),mode='r:gz') as t:return fixture.spec(),json.loads(t.extractfile('package-plan.json').read())
 def archive(self,plan):
  raw=json.dumps(plan).encode();out=io.BytesIO()
  with tarfile.open(fileobj=out,mode='w:gz') as t:
   info=tarfile.TarInfo('package-plan.json');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
  return out.getvalue()
 def test_exact_tag_only_publish_workflow_required_before_registration(self):
  for change in ['shadow','manual']:
   spec,plan=self.plan()
   for source in plan['sources'].values():
    item=next(x for x in source['files'] if x['path']=='.github/workflows/publish.yml')
    if change=='shadow':item['path']='.github/workflows/publish-shadow.yml'
    else:
     raw=base64.b64decode(item['content_base64']).replace(b'on:\n  push:\n    tags: ["v*"]',b'on:\n  workflow_dispatch:')
     item.update(content_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
   with self.subTest(change=change),self.assertRaises(ValueError):verify_plan(self.archive(plan),spec)
 def test_ci_must_allow_both_main_push_and_pull_request_without_skip_filters(self):
  variants=[{'push':{'tags':['v*']},'pull_request':None},{'push':{'branches':['dev']},'pull_request':None},{'push':{'branches':['main']},'pull_request':{'branches':['dev']}},{'push':{'branches':['main'],'paths':['unrelated/**']},'pull_request':None}]
  import yaml
  for events in variants:
   spec,plan=self.plan()
   for source in plan['sources'].values():
    item=next(x for x in source['files'] if x['path']=='.github/workflows/ci.yml');document=yaml.safe_load(base64.b64decode(item['content_base64']));document.pop(True,None);document['on']=events;raw=yaml.safe_dump(document).encode();item.update(content_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
   with self.subTest(events=events),self.assertRaises(ValueError):verify_plan(self.archive(plan),spec)
 def test_unreviewed_wheel_executables_and_duplicate_members_rejected(self):
  for path in ['demo_package/rogue.so','demo_package-0.0.1.data/scripts/rogue','demo_package/__init__.py']:
   spec,plan=self.plan();item=next(x for x in plan['placeholder_artifacts'] if x['filename'].endswith('.whl'));stream=io.BytesIO(base64.b64decode(item['content_base64']))
   with zipfile.ZipFile(stream,'a') as z:z.writestr(path,b'not reviewed source')
   raw=stream.getvalue();item.update(content_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
   with self.subTest(path=path),self.assertRaises(ValueError):verify_plan(self.archive(plan),spec)
 def test_normal_setuptools_sdist_root_and_egg_metadata_are_supported(self):
  spec,plan=self.plan();item=next(x for x in plan['placeholder_artifacts'] if x['filename'].endswith('.tar.gz'));output=io.BytesIO()
  with tarfile.open(fileobj=io.BytesIO(base64.b64decode(item['content_base64'])),mode='r:gz') as source,tarfile.open(fileobj=output,mode='w:gz') as target:
   for member in source:target.addfile(member,source.extractfile(member) if member.isfile() else None)
   data=b'Name: demo-package\nVersion: 0.0.1\n';member=tarfile.TarInfo('demo-package-0.0.1/src/demo_package.egg-info/PKG-INFO');member.size=len(data);target.addfile(member,io.BytesIO(data))
  raw=output.getvalue();item.update(content_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
  self.assertEqual(verify_plan(self.archive(plan),spec)['artifact_digests'][item['filename']],item['sha256'])
 def test_registration_artifacts_are_replaced_by_trusted_rebuild(self):
  from chattask.package_validation import PREPARE_VALIDATOR
  nodes=[n for n in ast.parse(PREPARE_VALIDATOR).body if isinstance(n,ast.FunctionDef) and n.name=='rebuilt_registration_artifacts']
  self.assertEqual(len(nodes),1)
  namespace={'base64':base64,'hashlib':hashlib}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'<trusted-validator-function>','exec'),namespace)
  with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
   d=Path(root);(d/'demo-0.0.1.whl').write_bytes(b'independently rebuilt wheel');(d/'demo-0.0.1.tar.gz').write_bytes(b'independently rebuilt sdist')
   values=namespace['rebuilt_registration_artifacts'](d)
   self.assertEqual(len(values),2)
   for value in values:self.assertEqual(base64.b64decode(value['content_base64']),(d/value['filename']).read_bytes())
