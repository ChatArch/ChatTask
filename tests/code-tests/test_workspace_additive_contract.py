"""Workspace units are an opt-in additive contract, not generic Codex tasks."""
from chattask.settings import TaskSettings
from chattask.workflow import TaskStore


def test_workspace_profile_contract_is_explicit_and_optional():
    fields = TaskSettings.__dataclass_fields__
    assert {'workspace_key', 'analysis_profile'} <= fields.keys()
    settings = TaskSettings(endpoint='http://127.0.0.1:12345', api_key='synthetic')
    assert settings.workspace_key == ''
    assert settings.analysis_profile == ''


def test_analysis_records_use_explicit_isolated_collections(tmp_path):
    store = TaskStore(tmp_path / 'state')
    store.put('analysis_tasks', 'a' * 32, {'id': 'a' * 32, 'status': 'draft'})
    store.put('analysis_runs', 'b' * 32, {'id': 'b' * 32, 'task_id': 'a' * 32})
    assert len(store.list('analysis_tasks')) == 1
    assert len(store.list('analysis_runs')) == 1
    assert store.list('runs') == []
