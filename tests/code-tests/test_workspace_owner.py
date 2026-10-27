"""One material-analysis controller owns a shared store, including recovery."""
import asyncio
import os
import subprocess
import sys

import pytest

from chattask.workflow import TaskStore
from chattask.workspace_analysis import WorkspaceAnalysis, AnalysisUnavailable


def draft(request_id):
    return {'actor': 'subject-a', 'request_id': request_id, 'title': 'Review',
            'instructions': 'Read only the supplied material.', 'acceptance': 'State gaps.',
            'source': {'site_id': 'workspace-web', 'proposal_id': 'proposal-1',
                       'catalog_revision': 'a' * 64, 'proposal_revision': 'b' * 64,
                       'project_ids': ['project-1']},
            'context': []}


def confirmation(task, request_id):
    return {'actor': 'subject-a', 'request_id': request_id, 'task_version': 1,
            'source_digest': task['source_digest'], 'confirmed': True}


class HeldDriver:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def analyze(self, task):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return 'Only supplied evidence was analyzed.', 'fixture-model'


def test_competing_controller_cannot_execute_or_interrupt_live_owner(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / 'state')
        first_driver, second_driver = HeldDriver(), HeldDriver()
        first = WorkspaceAnalysis(store, driver=first_driver)
        second = WorkspaceAnalysis(TaskStore(store.root), driver=second_driver)
        try:
            task, _ = first.create_task(draft('draft-1'))
            run, _ = await first.create_run(task['id'], confirmation(task, 'run-1'))
            await asyncio.wait_for(first_driver.started.wait(), 1)
            with pytest.raises(AnalysisUnavailable):
                await second.recover()
            assert store.get('analysis_runs', run['id'])['status'] == 'running'
            other, _ = second.create_task(draft('draft-2'))
            with pytest.raises(AnalysisUnavailable):
                await second.create_run(other['id'], confirmation(other, 'run-2'))
            with pytest.raises(AnalysisUnavailable):
                await second.cancel('subject-a', task['id'], run['id'])
            assert second_driver.calls == 0
            first_driver.release.set()
            await first.wait(run['id'])
            assert store.get('analysis_runs', run['id'])['status'] == 'succeeded'
            await first.shutdown()
            await second.recover()
            second_driver.release.set()
            new_run, _ = await second.create_run(other['id'], confirmation(other, 'run-2'))
            await second.wait(new_run['id'])
            assert second_driver.calls == 1
            assert store.get('analysis_runs', new_run['id'])['status'] == 'succeeded'
        finally:
            await first.shutdown()
            await second.shutdown()
    asyncio.run(scenario())


def test_other_process_cannot_recover_an_owned_store(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / 'state')
        owner = WorkspaceAnalysis(store, driver=HeldDriver())
        await owner.recover()
        script = '''import asyncio, sys
from chattask.workflow import TaskStore
from chattask.workspace_analysis import WorkspaceAnalysis, AnalysisUnavailable
async def main():
    controller = WorkspaceAnalysis(TaskStore(sys.argv[1]))
    try:
        await controller.recover()
    except AnalysisUnavailable:
        print("blocked")
    else:
        await controller.shutdown()
        print("unexpected-owner")
asyncio.run(main())
'''
        try:
            result = subprocess.run([sys.executable, '-c', script, str(store.root)],
                                    capture_output=True, text=True, timeout=15,
                                    env=dict(os.environ))
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == 'blocked'
        finally:
            await owner.shutdown()
    asyncio.run(scenario())


def test_owner_symlink_is_refused_without_touching_target(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / 'state')
        target = tmp_path / 'untouched'
        target.write_text('preserved')
        (store.root / '.analysis-owner.lock').symlink_to(target)
        controller = WorkspaceAnalysis(store, driver=HeldDriver())
        try:
            with pytest.raises(AnalysisUnavailable):
                await controller.recover()
            assert target.read_text() == 'preserved'
        finally:
            await controller.shutdown()
    asyncio.run(scenario())
