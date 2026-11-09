"""The trusted native adapter must ship in the installed wheel."""
from pathlib import Path
import subprocess
import sys
import zipfile


def test_dev_extra_declares_no_isolation_build_requirements():
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    from packaging.requirements import Requirement
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root/'pyproject.toml').read_text())
    required = {Requirement(value).name.lower() for value in config['build-system']['requires']}
    installed = {Requirement(value).name.lower() for value in config['project']['optional-dependencies']['dev']}
    assert required <= installed


def test_wheel_includes_trusted_overleaf_adapter(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-1200:]
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        assert "chattask/overleaf_native.mjs" in archive.namelist()
        assert b"@@CHATTASK_RESEARCH_NATIVE_V1@@" in archive.read("chattask/overleaf_native.mjs")
