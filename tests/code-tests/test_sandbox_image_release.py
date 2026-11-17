"""Image handoff integrity and controlled build selection, without Docker."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sandbox_image_release", ROOT / "scripts/sandbox_image_release.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


def plan(**changes):
    values = dict(registry="ghcr.io", namespace="chatarch", image="python-sympy",
                  source_commit="a" * 40, workflow_run_id="12345")
    values.update(changes)
    return images.image_plan(ROOT, **values)


def archive_fixture(path, *, architecture="amd64", tamper_config=False, multiple_images=False):
    config = json.dumps({"os": "linux", "architecture": architecture}).encode()
    layer = b"synthetic-layer-not-executed"
    digest = lambda data: "sha256:" + hashlib.sha256(data).hexdigest()
    manifest = json.dumps({"schemaVersion": 2, "config": {"digest": digest(config), "size": len(config)},
                           "layers": [{"digest": digest(layer), "size": len(layer)}]}).encode()
    descriptor = {"digest": digest(manifest), "size": len(manifest)}
    index = json.dumps({"schemaVersion": 2, "manifests": [descriptor] * (2 if multiple_images else 1)}).encode()
    entries = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": index,
               "blobs/sha256/" + digest(manifest)[7:]: manifest,
               "blobs/sha256/" + digest(config)[7:]: b"{}" if tamper_config else config,
               "blobs/sha256/" + digest(layer)[7:]: layer}
    with tarfile.open(path, "w") as archive:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return digest(manifest)


def test_plan_binds_build_to_reviewed_directory_and_full_source_identity():
    result = plan()
    assert result["repository"] == "ghcr.io/chatarch/chatsandbox-python-sympy"
    assert result["tag"].endswith(":sha-" + "a" * 12)
    assert result["context"] == "packages/chatsandbox/images/python-sympy"
    assert result["platform"] == "linux/amd64"
    assert result["source_commit"] == "a" * 40 and result["workflow_run_id"] == "12345"


@pytest.mark.parametrize("values", [
    {"image": "../other"}, {"image": "/etc"}, {"image": "$(command)"},
    {"image": "missing-image"}, {"registry": "https://ghcr.io"},
    {"registry": "user:secret@registry.test"}, {"namespace": "A/B"},
    {"namespace": "owner\ninjected=value"}, {"source_commit": "a" * 12},
    {"workflow_run_id": "not-a-run"},
])
def test_invalid_build_selection_is_rejected(values):
    with pytest.raises(ValueError):
        plan(**values)


def test_archive_receipt_requires_same_manifest_at_registry(tmp_path):
    path = tmp_path / "image.oci.tar"
    digest = archive_fixture(path)
    result = images.release_receipt(plan(), path, copied_digest=digest + "\n")
    assert result["published"] is True
    assert result["image_ref"] == "ghcr.io/chatarch/chatsandbox-python-sympy@" + digest
    assert result["manifest_digest"] == digest
    assert result["archive_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["archive_size_bytes"] == path.stat().st_size
    assert images.release_receipt(plan(), path)["published"] is False
    with pytest.raises(ValueError, match="changed"):
        images.release_receipt(plan(), path, copied_digest="sha256:" + "f" * 64)


@pytest.mark.parametrize("values", [{"architecture": "arm64"}, {"tamper_config": True}, {"multiple_images": True}])
def test_wrong_architecture_or_ambiguous_tampered_archive_rejected(tmp_path, values):
    path = tmp_path / "image.oci.tar"
    archive_fixture(path, **values)
    with pytest.raises(ValueError):
        images.inspect_archive(path)
