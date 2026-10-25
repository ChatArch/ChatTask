"""Scoped publication readback and pinned offline dependency verification."""
import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from chattask import package_publication as pub


def archive(entries):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as stream:
        for name,data,kind in entries:
            info=tarfile.TarInfo(name)
            if kind=='link':
                info.type=tarfile.SYMTYPE;info.linkname='elsewhere'
                stream.addfile(info)
            else:
                info.size=len(data);stream.addfile(info,io.BytesIO(data))
    return buffer.getvalue()


class PublicationEgressTests(unittest.TestCase):
    def test_scoped_release_and_placeholder_have_fixed_routes_and_limits(self):
        calls=[]
        def tool(route,*,limit=500000,timeout=60):
            calls.append((route,limit,timeout));return {'info':{'version':'0.0.2'}}
        with patch.object(pub,'_guest_tool',side_effect=tool):
            pub._release_json('new')
            pub._placeholder_json('new')
            pub._placeholder_json('existing')
        self.assertEqual(calls,[('/pypi/api/release',1000000,12),('/pypi/api/placeholder',1000000,12)])

    def test_artifact_uses_official_digest_route_and_safe_basename(self):
        data=b'official wheel'
        digest=hashlib.sha256(data).hexdigest()
        item={'filename':'example-0.0.2-py3-none-any.whl','sha256':digest,'url':'https://files.pythonhosted.org/packages/example.whl'}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            with patch.object(pub,'_guest_bytes',return_value=data) as fetch:
                result=pub._fetch_artifact(item,Path(root))
            self.assertEqual(result.read_bytes(),data)
            fetch.assert_called_once_with(digest)
            with self.assertRaises(ValueError):
                pub._fetch_artifact({**item,'filename':'../escape.whl'},Path(root))
            with patch.object(pub,'_guest_bytes',return_value=b'changed'):
                with self.assertRaisesRegex(ValueError,'bytes mismatch'):
                    pub._fetch_artifact(item,Path(root))

    def test_binary_transport_uses_scoped_token_and_digest_only(self):
        digest='a'*64
        with patch.dict(os.environ,{'CHATTASK_TOOL_BASE_URL':'https://gateway.example.test/sessions/'+'b'*32,
                                     'CHATTASK_TOOL_ACCESS_TOKEN':'task-token'}),patch.object(pub,'_read_only',return_value=b'wheel') as read:
            self.assertEqual(pub._guest_bytes(digest),b'wheel')
        request,limit,phase=read.call_args.args
        self.assertEqual(request.full_url,'https://gateway.example.test/sessions/'+'b'*32+'/pypi/api/artifacts/'+digest)
        self.assertEqual(request.get_header('Authorization'),'Bearer task-token')
        self.assertEqual(limit,10*1024*1024)
        self.assertEqual(read.call_args.kwargs['timeout'],12)
        with self.assertRaises(ValueError):
            pub._guest_bytes('https://files.pythonhosted.org/elsewhere')

    def test_postprocessor_stages_exact_archive_digest_in_guest_context(self):
        import asyncio,json
        from types import SimpleNamespace
        blob=archive([('deps/wheelhouse/dependency-1.0-py3-none-any.whl',b'wheel','file')])
        writes={}
        class Files:
            async def write_file(self,path,data,**kwargs):writes[path]=data
            async def read_file(self,path):
                if path.endswith('trusted-publication.json'):
                    return json.dumps({'plan_sha256':'a'*64,'repository':'owner/repo','version':'0.0.2',
                        'dependency_source':pub._dependency_provenance(hashlib.sha256(blob).hexdigest())})
                raise FileNotFoundError(path)
        class Runtime:
            def redact(self,value):return value
        async def command(*args,**kwargs):return None
        policy={'repository':'owner/repo','version':'0.0.2'}
        task={'capability':{'mode':'publish','spec':{},'plan':policy,'plan_sha256':'a'*64},'_input_archive':b'approved'}
        async def run():
            with patch('chattask.package_plan.verify_plan',return_value={'plan_sha256':'a'*64,'policy':policy}):
                await pub.publish_postprocess(SimpleNamespace(files=Files()),command,task,{},lambda *args:None,Runtime(),public_dependencies=blob)
        asyncio.run(run())
        self.assertEqual(writes['/workspace/public-deps.tar.gz'],blob)
        context=json.loads(writes['/workspace/publication-policy.json'])
        self.assertEqual(context['public_dependencies_sha256'],hashlib.sha256(blob).hexdigest())

    def test_guest_main_reads_only_fixed_scoped_routes_and_installs_fetched_wheel(self):
        import json
        real_path=Path
        spec={'cli':'example'}
        policy={'mode':'existing','project':'example','version':'0.0.2','repository':'owner/repo'}
        plan=json.dumps({'spec':spec}).encode()
        wheel=b'official wheel';sdist=b'official sdist'
        wheel_name='example-0.0.2-py3-none-any.whl';sdist_name='example-0.0.2.tar.gz'
        files=[{'filename':wheel_name,'sha256':hashlib.sha256(wheel).hexdigest(),'url':'https://files.pythonhosted.org/packages/wheel'},
               {'filename':sdist_name,'sha256':hashlib.sha256(sdist).hexdigest(),'url':'https://files.pythonhosted.org/packages/sdist'}]
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            root=real_path(root)
            (root/'input').mkdir();(root/'result').mkdir()
            (root/'publication-policy.json').write_text(json.dumps({'policy':policy,'spec':spec,'plan_sha256':hashlib.sha256(plan).hexdigest()}))
            with tarfile.open(root/'input'/'previous-result.tar.gz','w:gz') as stream:
                info=tarfile.TarInfo('package-plan.json');info.size=len(plan);stream.addfile(info,io.BytesIO(plan))
            routes=[];commands=[]
            def path(value):
                value=str(value)
                return root/value.removeprefix('/workspace/') if value.startswith('/workspace/') else real_path(value)
            def tool(route,**kwargs):
                routes.append(route)
                return {} if route=='/github/api/status' else {'info':{'version':'0.0.1' if route=='/pypi/api/placeholder' else '0.0.2'},'urls':[]}
            def run(argv,*args,**kwargs):
                commands.append(argv);return 'f'*64
            with patch.object(pub,'Path',side_effect=path),patch.object(pub,'_guest_tool',side_effect=tool),\
                 patch.object(pub,'verify_publication_receipts',return_value={'repository':'owner/repo','version':'0.0.2','tag':'v0.0.2','merged_sha':'a'*40,'files':files}),\
                 patch.object(pub,'_guest_bytes',side_effect=lambda digest:{files[0]['sha256']:wheel,files[1]['sha256']:sdist}[digest]),\
                 patch.object(pub,'_bounded_get',side_effect=AssertionError('direct public guest GET')),patch.object(pub,'_run',side_effect=run):
                pub.guest_main()
            self.assertEqual(routes,['/github/api/status','/pypi/api/release'])
            self.assertEqual((root/'published'/wheel_name).read_bytes(),wheel)
            self.assertEqual((root/'published'/sdist_name).read_bytes(),sdist)
            install=next(argv for argv in commands if 'install' in argv)
            self.assertEqual(install[-1],str(root/'published'/wheel_name))
            self.assertEqual(json.loads((root/'result'/'trusted-publication.json').read_text())['dependency_source'],{'source':'public-index','sha256':None})
            deps=archive([('deps/installed.py',b'ignored','file'),('deps/wheelhouse/dependency-1.0-py3-none-any.whl',b'wheel','file')])
            deps_digest=hashlib.sha256(deps).hexdigest()
            (root/'public-deps.tar.gz').write_bytes(deps)
            policy['mode']='new';policy['artifact_digests']={}
            (root/'publication-policy.json').write_text(json.dumps({'policy':policy,'spec':spec,'plan_sha256':hashlib.sha256(plan).hexdigest(),
                'public_dependencies_sha256':deps_digest}))
            routes.clear();commands.clear()
            with patch.object(pub,'Path',side_effect=path),patch.object(pub,'_guest_tool',side_effect=tool),\
                 patch.object(pub,'verify_publication_receipts',return_value={'repository':'owner/repo','version':'0.0.2','tag':'v0.0.2','merged_sha':'a'*40,'files':files}),\
                 patch.object(pub,'_guest_bytes',side_effect=lambda digest:{files[0]['sha256']:wheel,files[1]['sha256']:sdist}[digest]),\
                 patch.object(pub,'_bounded_get',side_effect=AssertionError('direct public guest GET')),patch.object(pub,'_run',side_effect=run):
                pub.guest_main()
            self.assertEqual(routes,['/github/api/status','/pypi/api/release','/pypi/api/placeholder'])
            install=next(argv for argv in commands if 'install' in argv)
            self.assertEqual(install[-4:],['--no-index','--find-links',str(root/'verification-deps'),str(root/'published'/wheel_name)])
            self.assertFalse((root/'verification-deps'/'installed.py').exists())
            self.assertEqual(json.loads((root/'result'/'trusted-publication.json').read_text())['dependency_source'],
                {'source':'pinned-wheelhouse','sha256':deps_digest})

    def test_wheelhouse_digest_and_structure_fail_closed(self):
        wheel=('deps/wheelhouse/dependency-1.0-py3-none-any.whl',b'wheel','file')
        cases=[
            ([wheel], '0'*64, 'digest'),
            ([('deps/../escape.whl',b'x','file'),wheel],None,'path'),
            ([('deps/wheelhouse/evil.whl',b'','link'),wheel],None,'member'),
            ([wheel,wheel],None,'duplicate'),
            ([('deps/installed.py',b'x','file')],None,'wheelhouse'),
        ]
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            for index,(entries,wrong,expected) in enumerate(cases):
                blob=archive(entries)
                with self.subTest(index=index),self.assertRaisesRegex(ValueError,expected):
                    pub._stage_wheelhouse(blob,wrong or hashlib.sha256(blob).hexdigest(),Path(root)/str(index))

    def test_offline_install_uses_verified_target_wheel_and_provenance(self):
        wheel=('deps/wheelhouse/dependency-1.0-py3-none-any.whl',b'wheel','file')
        blob=archive([('deps/installed.py',b'not extracted','file'),wheel])
        digest=hashlib.sha256(blob).hexdigest()
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            folder=Path(root)/'verification-deps'
            source=pub._stage_wheelhouse(blob,digest,folder)
            self.assertEqual(source,{'source':'pinned-wheelhouse','sha256':digest})
            self.assertFalse((folder/'installed.py').exists())
            self.assertTrue((folder/wheel[0].split('/')[-1]).exists())
            target=Path(root)/'published'/'example-0.0.2-py3-none-any.whl'
            argv=pub._install_argv('/workspace/publication-venv',target,folder)
            self.assertEqual(argv[-4:],['--no-index','--find-links',str(folder),str(target)])
            self.assertIn('--no-index',argv)
            self.assertNotIn('--index-url',argv)
            self.assertEqual(argv[-1],str(target))
            self.assertEqual(pub._dependency_provenance(None),{'source':'public-index','sha256':None})

    def test_service_default_and_web_reuses_workflow_archive(self):
        from chattask.package_service import PackageService
        from chattask.workflow import TaskStore
        class Executor:
            def __init__(self):self.hooks={}
            def register_postprocessor(self,mode,fn,seconds):self.hooks[mode]=fn
        class Tasks:
            executor=Executor()
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            PackageService(TaskStore(root),Tasks())
        self.assertEqual(Tasks.executor.hooks['publish'].keywords['public_dependencies'],None)
        from types import SimpleNamespace
        from chattask.web import create_app
        from chattask.settings import TaskSettings
        settings=TaskSettings(endpoint='http://127.0.0.1:1234',api_key='test',origin='http://testserver',username='operator',password='test-password',home=root)
        flow=SimpleNamespace(store=TaskStore(root),executor=SimpleNamespace(dependency_archive=b'pinned'))
        with patch('chattask.package_service.PackageService') as service,patch('chattask.web.load_guest_dependencies',side_effect=AssertionError('duplicate load')):
            create_app(settings,service=object(),workflow=flow,agent_tasks=SimpleNamespace(executor=Executor()))
        self.assertEqual(service.call_args.kwargs['public_dependencies'],b'pinned')


if __name__=='__main__':unittest.main()
