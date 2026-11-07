"""Generated Python caches cannot prevent business-workspace checkpoints."""
import subprocess
import sys
import tarfile
from pathlib import Path


def run_capture(tmp_path, *names):
    from chattask.durable_archive import CAPTURE_SCRIPT
    root = tmp_path / 'workspace'
    for name in names:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('fixture')
    script = CAPTURE_SCRIPT.replace('/workspace', str(root))
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10)
    return result, root / 'checkpoint.tar.gz'


def test_generated_cache_is_pruned_before_checkpoint(tmp_path):
    result, path = run_capture(tmp_path, 'work/citation_index.py', 'work/__pycache__/citation_index.cpython-312.pyc',
                               'work/.pytest_cache/state.json', 'codex-home/sessions/rollout.jsonl')
    assert result.returncode == 0, result.stderr
    with tarfile.open(path, mode='r:gz') as archive:
        assert archive.getnames() == ['codex-home/sessions/rollout.jsonl', 'work/citation_index.py']


def test_secret_path_remains_rejected(tmp_path):
    result, path = run_capture(tmp_path, 'work/code.py', 'work/auth.json', 'codex-home/sessions/rollout.jsonl')
    assert result.returncode != 0
    assert not path.exists()
