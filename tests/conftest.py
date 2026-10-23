"""Offline tests must never resolve the operator's configuration or token home."""
from pathlib import Path
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_home(tmp_path, monkeypatch):
    home=tmp_path/'home';arch=tmp_path/'chatarch';scratch=tmp_path/'scratch'
    for directory in (home,arch,scratch):
        directory.mkdir(mode=0o700)
    for key in list(os.environ):
        if key.startswith(('PYPI_','CHATPYPI_','CHATTASK_','OPENAI_','ANTHROPIC_','GITHUB_','GH_','CRS_')) or key.lower().endswith('proxy'):
            monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('CHATARCH_HOME',str(arch))
    monkeypatch.setenv('TMPDIR',str(scratch))
    monkeypatch.setenv('CHATTASK_TEST_SCRATCH',str(scratch))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(home/'gitconfig'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM','1')
