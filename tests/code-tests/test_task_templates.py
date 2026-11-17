"""Task-template and Python-release adapter contracts; no live publishing."""
import asyncio
import importlib.util
import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from chattask.workflow import TaskStore
from chattask.package_service import PackageService
from chattask.durable import DurableTaskService


def spec():
    return {'mode':'existing','package_name':'example-taskpkg','module':'example_taskpkg','cli':'taskpkg',
            'owner':'example-team','repository':'example-taskpkg','description':'A controlled package task',
            'requirements':'Add a small reusable utility and its tests','version':'0.2.1'}


class Tasks:
    images={'python':'trusted-image'}
    executor=SimpleNamespace(gateway=SimpleNamespace(_token='PRIVATE-CANARY-NOT-FOR-TASK-EXPORT'))
    def __init__(self):self.jobs={}
    async def create_internal(self,owner,body,capability):
        task={'id':uuid.uuid4().hex,'owner':owner,'status':'queued','image_profile':body['image_profile'],
              'request_id':body['request_id'],'artifacts':[]}
        self.jobs[task['id']]=task
        return task
    def get(self,owner,ident):
        task=self.jobs[ident]
        if task['owner']!=owner:raise KeyError('task not found')
        return task


def template_module():
    assert importlib.util.find_spec('chattask.task_templates') is not None,'Task Template catalog is not implemented'
    from chattask import task_templates
    return task_templates


def test_python_release_draft_is_a_task_unit_with_explicit_resource_requests(tmp_path):
    tasks=Tasks();packages=PackageService(TaskStore(tmp_path/'state'),tasks)
    draft=asyncio.run(packages.create('owner',{'request_id':'draft','spec':spec()}))
    assert draft.get('task_package'),'Python release has no first-class Task definition'
    definition=packages.task_definition('owner',draft['id'])
    assert definition['template']['id']=='python-package'
    assert {'model','environment','repository','publisher'} <= {r['kind'] for r in definition['resources']}
    assert definition['parameters']['version']=='0.2.1'
    exported=packages.export_task('owner',draft['id'])
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert {'task.toml','instruction.md','tests/test.sh','environment/skills/python-release/SKILL.md'} <= set(archive.namelist())
        assert b'PRIVATE-CANARY-NOT-FOR-TASK-EXPORT' not in b''.join(archive.read(name) for name in archive.namelist())
    assert tasks.jobs=={},'Creating a task unit must not dispatch or publish'
    with pytest.raises(KeyError):packages.task_definition('outsider',draft['id'])


def test_preflight_links_a_real_stage_and_cannot_skip_confirmation(tmp_path):
    packages=PackageService(TaskStore(tmp_path/'state'),Tasks())
    draft=asyncio.run(packages.create('owner',{'request_id':'draft','spec':spec()}))
    prepared=asyncio.run(packages.prepare('owner',draft['id'],{'request_id':'prepare','revision':1,'image_profile':'python'}))
    assert prepared.get('task_stages'),'No task-owned publication phase history'
    stage=prepared['task_stages'][-1]
    assert stage['kind']=='prepare' and stage['native_task_id']==prepared['prepare_task_id']
    assert stage['definition_revision']==prepared['task_package']['revision']
    with pytest.raises(ValueError,match='preflight'):
        asyncio.run(packages.confirm('owner',draft['id'],{'request_id':'publish','revision':1,
                     'preview_sha256':'not-verified','confirmation':'not-approved'}))


def test_parameter_revisions_do_not_overwrite_old_python_task_definitions(tmp_path):
    packages=PackageService(TaskStore(tmp_path/'state'),Tasks())
    draft=asyncio.run(packages.create('owner',{'request_id':'draft','spec':spec()}))
    getter=getattr(packages,'task_definition',None)
    assert callable(getter),'Versioned Python Task definition API is not implemented'
    before=getter('owner',draft['id'])
    changed={**spec(),'requirements':'A revised utility with explicit error cases'}
    asyncio.run(packages.update('owner',draft['id'],{'request_id':'edit','revision':1,'spec':changed}))
    after=getter('owner',draft['id'])
    assert before['revision']!=after['revision']
    assert getter('owner',draft['id'],revision=before['revision'])['files']==before['files']


def test_python_task_unit_export_contains_frozen_definition_and_stage_history(tmp_path):
    packages=PackageService(TaskStore(tmp_path/'state'),Tasks())
    draft=asyncio.run(packages.create('owner',{'request_id':'draft','spec':spec()}))
    draft=asyncio.run(packages.prepare('owner',draft['id'],{'request_id':'prepare','revision':1,'image_profile':'python'}))
    export=getattr(packages,'export_task_data',None)
    assert callable(export),'Python publication has no reusable task-unit data export'
    with zipfile.ZipFile(io.BytesIO(export('owner',draft['id']))) as archive:
        metadata=json.loads(archive.read('task.json'))
        assert metadata['id']==draft['id'] and metadata['kind']=='python-package'
        assert metadata['task_stages'][0]['native_task_id']==draft['prepare_task_id']
        assert 'task.toml' in archive.namelist()
        assert not any('checkpoint' in name or 'auth.json' in name for name in archive.namelist())
    with pytest.raises(KeyError):export('outsider',draft['id'])


def test_report_template_invalid_image_type_is_a_value_error(tmp_path):
    templates=template_module()
    service=templates.TaskTemplateService(DurableTaskService(TaskStore(tmp_path/'state'),image_profiles={'python':'trusted-image'}))
    with pytest.raises(ValueError,match='image profile'):
        asyncio.run(service.instantiate('owner','research-report',{'request_id':'invalid-image','template_revision':1,
            'image_profile':[],'parameters':{'title':'Topic','requirements':'Explain the supplied facts'}}))


def test_template_records_task_scoped_skill_content_digests():
    templates=template_module()
    from hashlib import sha256
    from chattask.task_packages import validate_files
    definition=templates.python_task_package(spec())
    file='environment/skills/python-release/SKILL.md'
    expected={file:sha256(definition['files'][file].encode()).hexdigest()}
    assert definition.get('skill_digests')==expected,'Reusable task has no explicit effective Skill fingerprint'
    assert validate_files(definition['files'])['skill_digests']==expected


def test_report_template_changes_data_not_the_fixed_verifier_code(tmp_path):
    templates=template_module();store=TaskStore(tmp_path/'state')
    durable=DurableTaskService(store,image_profiles={'python':'trusted-image'})
    catalog=templates.TaskTemplateService(durable)
    assert {'research-report','python-package'} <= {t['id'] for t in catalog.catalog()}
    first=asyncio.run(catalog.instantiate('owner','research-report',{'request_id':'first','template_revision':1,
        'image_profile':'python','parameters':{'title':'First topic','requirements':'Discuss the supplied excerpts'}}))
    second=asyncio.run(catalog.instantiate('owner','research-report',{'request_id':'second','template_revision':1,
        'image_profile':'python','parameters':{'title':'Another topic','requirements':'Explain scope and uncertainty'}}))
    a=durable.package('owner',first['id']);b=durable.package('owner',second['id'])
    assert first['id']!=second['id']
    assert a['files']['tests/checks.py']==b['files']['tests/checks.py']
    assert a['files']['inputs/parameters.json']!=b['files']['inputs/parameters.json']
    assert a['revision']!=b['revision']
    with pytest.raises(ValueError):
        asyncio.run(catalog.instantiate('owner','research-report',{'request_id':'invalid','template_revision':1,
             'image_profile':'python','parameters':{'title':'x','requirements':'y','api_key':'forbidden'}}))
