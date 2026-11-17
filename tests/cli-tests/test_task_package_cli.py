"""Real CLI/API lifecycle using private temporary data; no model or remote calls."""
import json
from pathlib import Path
from click.testing import CliRunner
from chattask.cli import main
from chattask.durable import DurableTaskService
from chattask.workflow import TaskStore


def test_package_init_and_validate_do_real_local_work_without_credentials(tmp_path):
    runner=CliRunner();directory=tmp_path/'definition'
    created=runner.invoke(main,['task','package','init',str(directory),'Write a sourced report',
                                '--image','python:3.12'])
    assert created.exit_code==0,created.output
    assert (directory/'task.toml').is_file()
    assert (directory/'instruction.md').read_text()=='Write a sourced report'
    checked=runner.invoke(main,['task','package','validate',str(directory)])
    assert checked.exit_code==0,checked.output
    assert json.loads(checked.output)['official_schema_checked'] is False
    again=runner.invoke(main,['task','package','init',str(directory),'different','--image','python:3.12'])
    assert again.exit_code!=0
    assert (directory/'instruction.md').read_text()=='Write a sourced report'


def test_cli_import_show_and_export_use_the_same_domain_api(tmp_path,monkeypatch):
    runner=CliRunner();directory=tmp_path/'definition'
    assert runner.invoke(main,['task','package','init',str(directory),'Write a report',
                               '--image','python:3.12']).exit_code==0
    service=DurableTaskService(TaskStore(tmp_path/'state'),image_profiles={'python':'python:3.12'})
    monkeypatch.setattr('chattask.cli._durable_service',lambda profile:service)
    created=runner.invoke(main,['task','create','--definition-dir',str(directory),
                                '--image-profile','python','--request-id','create','--owner','owner'])
    assert created.exit_code==0,created.output
    task=json.loads(created.output)
    shown=runner.invoke(main,['task','package','show',task['id'],'--owner','owner'])
    assert shown.exit_code==0,shown.output
    assert json.loads(shown.output)['purpose']=='Write a report'
    exported=tmp_path/'task.zip'
    result=runner.invoke(main,['task','package','export',task['id'],'--owner','owner','--output',str(exported)])
    assert result.exit_code==0,result.output
    assert exported.read_bytes().startswith(b'PK')
