"""Task-package lifecycle contracts; all execution fakes are test-only."""
import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
from chattask.durable import DurableTaskService, DurableConflict
from chattask.workflow import TaskStore


def service(tmp_path):
    return DurableTaskService(TaskStore(tmp_path / 'state'), image_profiles={'python':'trusted-image'})


def create(svc, **extra):
    return svc.create('owner', {'request_id':'one','instructions':'Produce an evidence-based report',
                                'image_profile':'python', **extra})


def package(svc, task):
    method = getattr(svc, 'package', None)
    assert callable(method), 'Task package lifecycle is not implemented'
    return method('owner', task['id'])


def definition(purpose='Review two sources'):
    toml = ('schema_version = "1.4"\n[metadata.chatarch]\npurpose = '+json.dumps(purpose)+'\n'
            'deliverables = ["report.md"]\n[[metadata.chatarch.acceptance]]\nid = "AC1"\n'
            'description = "Conclusions cite their sources"\nmethod = "human"\nrequired = true\n')
    return {'task.toml':toml, 'instruction.md':'Write a report with observable evidence.'}


def test_create_initializes_real_portable_definition_before_execution(tmp_path):
    svc=service(tmp_path);task=create(svc);p=package(svc,task)
    assert p['purpose']=='Produce an evidence-based report'
    assert p['preparation']=='pending'
    assert {'task.toml','instruction.md'} <= set(p['files'])
    run=svc.runs('owner',task['id'])[0]
    assert run['package_revision']==p['revision']
    data=svc.store.artifact(task['id'],'task-definition.zip')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read('instruction.md').decode()==p['files']['instruction.md']


def test_structured_import_has_business_fields_and_frozen_revision(tmp_path):
    svc=service(tmp_path);task=create(svc,definition=definition());p=package(svc,task)
    assert p['purpose']=='Review two sources'
    assert p['deliverables']==['report.md']
    assert p['acceptance'][0]['id']=='AC1'
    assert p['preparation']=='authored'
    assert p['revision']==svc.runs('owner',task['id'])[0]['package_revision']


def test_invalid_definition_is_rejected_before_creating_task(tmp_path):
    svc=service(tmp_path)
    with pytest.raises(ValueError):create(svc,definition={**definition(),'../escape.py':'bad'})
    assert svc.list('owner')==[]


def test_package_access_and_export_do_not_leak_private_runtime(tmp_path):
    svc=service(tmp_path);task=create(svc);package(svc,task)
    with pytest.raises(KeyError):svc.package('stranger',task['id'])
    svc.store.put_artifact(task['id'],'checkpoint.tar.gz',b'private native state')
    exported=svc.export_package('owner',task['id'])
    with zipfile.ZipFile(io.BytesIO(exported)) as z:
        names=z.namelist()
        assert 'task.toml' in names and 'instruction.md' in names
        assert not any('checkpoint' in n or 'auth.json' in n for n in names)
        assert b'private native state' not in b''.join(z.read(n) for n in names)


def test_edit_uses_cas_and_does_not_rewrite_previous_revision(tmp_path):
    svc=service(tmp_path);task=create(svc);before=package(svc,task)
    after=svc.revise_package('owner',task['id'],{'request_id':'edit','expected_revision':before['revision'],
                                               'definition':definition()})
    assert after['revision']!=before['revision']
    assert svc.package('owner',task['id'],revision=before['revision'])['files']==before['files']
    with pytest.raises(DurableConflict):
        svc.revise_package('owner',task['id'],{'request_id':'stale','expected_revision':before['revision'],
                                             'definition':definition('Changed again')})


def test_legacy_task_view_is_read_only_and_has_no_retroactive_score(tmp_path):
    svc=service(tmp_path);task=create(svc)
    raw=svc.store.get('durable_tasks',task['id']);raw.pop('package',None)
    svc.store.put('durable_tasks',task['id'],raw)
    file=svc.store.root/'durable_tasks'/(task['id']+'.json');before=file.read_bytes()
    p=package(svc,task)
    assert p['legacy'] is True and p.get('evaluation') is None
    assert file.read_bytes()==before


def test_no_client_score_submission_or_forged_reviewer(tmp_path):
    svc=service(tmp_path);task=create(svc);package(svc,task)
    with pytest.raises(ValueError):
        svc.review_package('owner',task['id'],{'request_id':'fake','decision':'accepted','reward':1})
    with pytest.raises(KeyError):
        svc.review_package('stranger',task['id'],{'request_id':'fake','decision':'accepted','notes':'ok',
                                                'expected_revision':package(svc,task)['revision']})


def test_existing_instruction_character_budget_is_preserved(tmp_path):
    svc=service(tmp_path)
    task=svc.create('owner',{'request_id':'long','instructions':'中'*16000,'image_profile':'python'})
    assert package(svc,task)['files']['instruction.md']=='中'*16000


class SyntheticNativeExecutor:
    images={'python':'trusted-image'}

    async def execute_durable(self,task,run,checkpoint,save_checkpoint,progress):
        import tarfile
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for name,data in [('work/report.md',b'report'),('codex-home/sessions/rollout-session-1.jsonl',b'turn')]:
                entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,io.BytesIO(data))
        await save_checkpoint(stream.getvalue(),'session-1')
        return {'ok':True,'native_turn_completed':True,'final':'done',
                'artifacts':[{'name':'result.md','data':b'report'}],
                'cleanup':{'container_absent':True},'credential_closure':{'closed':True}}


def test_each_round_freezes_its_own_definition_and_archives_real_outputs(tmp_path):
    svc=DurableTaskService(TaskStore(tmp_path/'state'),SyntheticNativeExecutor())
    task=create(svc,definition=definition());first_revision=package(svc,task)['revision']
    first=asyncio.run(svc.worker_once())
    assert first['data_archive']['status']=='ready'
    data=svc.store.artifact(first['id'],'task-data.zip')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read('artifacts/result.md')==b'report'
        assert not any('checkpoint' in n for n in archive.namelist())
    second_definition=svc.revise_package('owner',task['id'],{'request_id':'revise','expected_revision':first_revision,
                                                          'definition':definition('Expand the report')})
    svc.resume('owner',task['id'],{'request_id':'next','instructions':'Continue the report'})
    second=asyncio.run(svc.worker_once())
    assert second['package_revision']==second_definition['revision']
    assert svc.runs('owner',task['id'])[0]['package_revision']==first_revision
    assert first_revision!=second['package_revision']


def test_unavailable_verifier_never_generates_a_passing_reward(tmp_path):
    svc=DurableTaskService(TaskStore(tmp_path/'state'),SyntheticNativeExecutor())
    task=create(svc,definition=definition());run=asyncio.run(svc.worker_once())
    method=getattr(svc,'evaluate_package',None)
    assert callable(method),'Independent evaluation API is not implemented'
    evaluation=asyncio.run(method('owner',task['id'],run['id'],{'request_id':'check'}))
    assert evaluation['status']=='unavailable'
    assert evaluation.get('reward') is None
    again=asyncio.run(method('owner',task['id'],run['id'],{'request_id':'check'}))
    assert again['id']==evaluation['id']
