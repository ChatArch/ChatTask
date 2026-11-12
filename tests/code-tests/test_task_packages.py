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


def test_preparation_is_frozen_before_the_execution_callback_continues(tmp_path):
    class Preparer(SyntheticNativeExecutor):
        async def execute_durable(self,task,run,checkpoint,save_checkpoint,progress):
            prepare=task.get('_prepare_definition')
            assert callable(prepare),'Agent preparation callback is not wired'
            prepared=await prepare(definition(task['goal']))
            persisted=svc.store.get('durable_runs',run['id'])
            assert persisted['package_revision']==prepared['revision']
            assert svc.package('owner',task['id'])['preparation']=='agent_generated'
            return await super().execute_durable(task,run,checkpoint,save_checkpoint,progress)
    svc=DurableTaskService(TaskStore(tmp_path/'state'),Preparer())
    task=create(svc)
    result=asyncio.run(svc.worker_once())
    assert result['status']=='completed',result.get('error')
    assert result['package_snapshot']['preparation']=='agent_generated'


def test_native_executor_receives_private_preparation_callback_and_definition():
    from chattask.codex_tasks import CodexTaskExecutor,CodexRuntime
    executor=CodexTaskExecutor(None,CodexRuntime('https://model.example/v1','synthetic-key',{'python':'trusted-image'}))
    captured={}
    async def execute(invocation,progress,cancelled):captured.update(invocation);return {'ok':False}
    executor.execute=execute
    async def prepare(files):return files
    async def checkpoint(*args):pass
    async def progress(*args):pass
    task={'id':'a'*32,'goal':'Task','image_profile':'python','_prepare_definition':prepare,
          '_definition_files':definition()}
    run={'id':'b'*32,'instructions':'Task','number':1,'session_id':None}
    asyncio.run(executor.execute_durable(task,run,None,checkpoint,progress))
    assert captured['_durable'].get('prepare_definition') is prepare
    assert captured['_durable'].get('definition_files')==definition()


def test_preparation_success_does_not_make_a_timed_out_solver_quiescent(monkeypatch):
    """Synthetic SDK timeout: never capture a workspace while the solver may still write."""
    from types import SimpleNamespace as N
    from chattask.codex_tasks import CodexTaskExecutor,CodexRuntime
    from chattask.settings import TaskSettings
    from chattask.task_packages import initial_package
    captures=[]
    class Commands:
        async def run(self,command,opts):
            if '> /workspace/agent-events.jsonl' in command:raise TimeoutError('synthetic in-flight solver')
            if 'capture_checkpoint.py' in command:captures.append(command)
            return N()
    class Files:
        async def write_file(self,*args,**kwargs):pass
        async def read_file(self,path):
            if path.endswith('preparation-events.jsonl'):
                return '\n'.join(json.dumps(e) for e in [{'type':'thread.started','thread_id':'prep-session'},{'type':'turn.completed'}])
            if path.endswith('prepared-definition.json'):return json.dumps({'files':definition()})
            if path.endswith('agent-events.jsonl'):return json.dumps({'type':'thread.started','thread_id':'live-session'})
            return ''
        async def read_bytes(self,*args):return b'not a finished checkpoint'
    box=N(id='synthetic-box',commands=Commands(),files=Files())
    class Service:
        docker=N(containers=N(list=lambda **kwargs:[]))
        async def create(self,**kwargs):return box,None,{}
        async def dispose(self,*args):pass
        def output(self,result):return {'exit_code':0,'stdout':'','stderr':'','error':None}
    async def exercise():
        parent=N(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:12345','synthetic'),docker=Service.docker)
        executor=CodexTaskExecutor(parent,CodexRuntime('https://model.example/v1','synthetic-key',{'python':'trusted-image'}))
        async def prepare(files):return initial_package('Goal','trusted-image',files)
        async def save(*args):raise AssertionError('in-flight solver cannot be checkpointed')
        async def progress(*args):pass
        monkeypatch.setattr('chattask.codex_tasks.TaskService',lambda *args,**kwargs:Service())
        return await executor.execute({'id':'a'*32,'image_profile':'python','instructions':'Goal',
                                       '_durable':{'checkpoint':None,'session_id':None,'prepare_definition':prepare,
                                                   'definition_files':definition(),'save_checkpoint':save}},progress,lambda:False)
    result=asyncio.run(exercise())
    assert not result['ok']
    assert captures==[], 'A completed preparation turn must not certify the next solver as stopped'


def test_null_preparation_cannot_be_certified_as_agent_generated(tmp_path):
    class NullPreparer(SyntheticNativeExecutor):
        async def execute_durable(self,task,run,*args):
            await task['_prepare_definition'](None)
            return await super().execute_durable(task,run,*args)
    svc=DurableTaskService(TaskStore(tmp_path/'state'),NullPreparer());task=create(svc)
    run=asyncio.run(svc.worker_once())
    assert run['status']=='blocked'
    assert svc.package('owner',task['id'])['preparation']=='pending'


def test_regrading_does_not_relabel_an_old_trajectory_with_the_current_model(tmp_path):
    from types import SimpleNamespace
    class NativeEvents(SyntheticNativeExecutor):
        runtime=SimpleNamespace(model='requested-a')
        async def execute_durable(self,*args):
            result=await super().execute_durable(*args)
            result['requested_model']='requested-a'
            events='\n'.join(json.dumps(e) for e in [{'type':'thread.started','thread_id':'session-1'},
                                                   {'type':'turn.completed'}])
            result['artifacts'].append({'name':'codex.events.jsonl','data':events.encode()})
            return result
    executor=NativeEvents();svc=DurableTaskService(TaskStore(tmp_path/'state'),executor)
    task=create(svc,definition=definition());run=asyncio.run(svc.worker_once())
    executor.runtime.model='requested-b'
    asyncio.run(svc.evaluate_package('owner',task['id'],run['id'],{'request_id':'regrade'}))
    trajectory=json.loads(svc.store.artifact(run['id'],'trajectory.json'))
    assert trajectory['agent']['model_name']=='requested-a'


def test_manual_review_is_included_in_the_downloadable_round_unit(tmp_path):
    svc=DurableTaskService(TaskStore(tmp_path/'state'),SyntheticNativeExecutor())
    task=create(svc,definition=definition());run=asyncio.run(svc.worker_once())
    review=svc.review_package('owner',task['id'],{'request_id':'review','expected_revision':run['package_revision'],
                                                'run_id':run['id'],'decision':'accepted','criteria':{'AC1':True},
                                                'notes':'Reviewed the output against the supplied sources.'})
    with zipfile.ZipFile(io.BytesIO(svc.store.artifact(run['id'],'task-data.zip'))) as archive:
        record=json.loads(archive.read('run.json'))
        assert record.get('review',{}).get('id')==review['id']


def test_evaluation_is_queued_for_the_worker_not_run_in_the_request(tmp_path):
    class Verifier:
        calls=0
        async def evaluate(self,*args,**kwargs):
            self.calls+=1
            return {'status':'completed','reward':{'reward':1.0},'details':{},
                    'criteria':{'AC1':{'passed':True}},
                    'cleanup':{'container_absent':True,'credential_closed':True}}
    svc=DurableTaskService(TaskStore(tmp_path/'state'),SyntheticNativeExecutor())
    files=definition();files['task.toml']=files['task.toml'].replace('method = "human"','method = "script"')
    task=create(svc,definition=files);run=asyncio.run(svc.worker_once())
    checker=Verifier();svc.package_verifier=checker
    queued=asyncio.run(svc.evaluate_package('owner',task['id'],run['id'],{'request_id':'check'}))
    assert queued['status']=='queued','Evaluation must survive Web request/process lifetime'
    assert checker.calls==0
    finished=asyncio.run(svc.worker_once())
    assert finished['status']=='passed' and checker.calls==1
    assert svc.evaluations('owner',task['id'],run['id'])[-1]['status']=='passed'


def test_a_required_failure_cannot_be_averaged_away_by_a_high_total_score(tmp_path):
    class Verifier:
        async def evaluate(self,*args,**kwargs):
            return {'status':'completed','reward':{'reward':100.0},'details':{},
                    'criteria':{'AC1':{'passed':False}},
                    'cleanup':{'container_absent':True,'credential_closed':True}}
    svc=DurableTaskService(TaskStore(tmp_path/'state'),SyntheticNativeExecutor())
    files=definition();files['task.toml']=files['task.toml'].replace('method = "human"','method = "script"')
    task=create(svc,definition=files);run=asyncio.run(svc.worker_once());svc.package_verifier=Verifier()
    asyncio.run(svc.evaluate_package('owner',task['id'],run['id'],{'request_id':'check'}))
    evaluated=asyncio.run(svc.worker_once())
    assert evaluated['status']=='failed'
