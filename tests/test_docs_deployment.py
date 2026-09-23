"""Regression coverage for preview-safe documentation deployment."""
from pathlib import Path
import os
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "      - name: Build and deploy docs\n        run: |\n"


def test_docs_workflows_share_a_non_cancelling_publish_lock():
    for name in ("deploy.yaml", "preview.yaml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "concurrency:\n  group: docs-pages\n  cancel-in-progress: false" in text


@pytest.mark.parametrize("with_preview", [False, True])
def test_deploy_replaces_production_and_preserves_existing_preview(tmp_path, with_preview):
    workflow = (ROOT / ".github/workflows/deploy.yaml").read_text(encoding="utf-8")
    assert MARKER in workflow, "Deployment must preserve the published preview"
    script = textwrap.dedent(workflow.split(MARKER, 1)[1])
    repo = tmp_path / "repo"
    repo.mkdir()
    remote = tmp_path / "origin.git"
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    git("init", "--bare", str(remote))
    git("init", "-b", "main")
    git("config", "user.name", "Docs Test")
    git("config", "user.email", "docs@example.invalid")
    (repo / "README.md").write_text("test source")
    git("add", "README.md")
    git("commit", "-m", "source")
    git("remote", "add", "origin", str(remote))
    if with_preview:
        git("switch", "-c", "gh-pages")
        (repo / "dev").mkdir()
        (repo / "dev/index.html").write_text("preserved preview")
        (repo / "versions.json").write_text('[{"version": "dev"}]')
        (repo / "obsolete.html").write_text("obsolete production")
        git("add", "dev", "versions.json", "obsolete.html")
        git("commit", "-m", "published docs")
        git("push", "origin", "gh-pages")
        git("switch", "main")
    tools = tmp_path / "bin"
    tools.mkdir()
    commands = {
        "mkdocs": "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\nassert sys.argv[1:] == ['build', '--strict']\nPath('site').mkdir()\nPath('site/index.html').write_text('new production')\n",
        "ghp-import": "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\nassert '--no-jekyll' in sys.argv and '--push' in sys.argv and '--force' in sys.argv\nassert sys.argv[-1] == 'site'\nPath('published.ok').write_text('ok')\n",
    }
    for name, content in commands.items():
        executable = tools / name
        executable.write_text(content)
        executable.chmod(0o755)
    runner = tmp_path / "runner"
    runner.mkdir()
    env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"], RUNNER_TEMP=str(runner), GITHUB_SHA="test-source-sha")
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (repo / "published.ok").exists()
    assert (repo / "site/index.html").read_text() == "new production"
    assert not (repo / "site/obsolete.html").exists()
    if with_preview:
        assert (repo / "site/dev/index.html").read_text() == "preserved preview"
        assert (repo / "site/versions.json").read_text() == '[{"version": "dev"}]'
    else:
        assert not (repo / "site/dev").exists()
    assert not (runner / "docs-published").exists()
