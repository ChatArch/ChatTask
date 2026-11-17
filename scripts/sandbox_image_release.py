"""Prepare OCI image release metadata without loading any credential owner."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile


def image_plan(root, *, registry, namespace, image, source_commit, workflow_run_id):
    if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?", registry):
        raise ValueError("Registry must be a credential-free host[:port]")
    component = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    if not re.fullmatch(component + r"(?:/" + component + r")*", namespace):
        raise ValueError("Explicit lowercase namespace required")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", image):
        raise ValueError("Select a named image directory")
    if not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("Full source commit required")
    if not re.fullmatch(r"[1-9][0-9]*", str(workflow_run_id)):
        raise ValueError("Workflow run identity required")
    root = Path(root).resolve()
    context = root / "packages/chatsandbox/images" / image
    if not context.is_dir() or context.is_symlink() or context.resolve().parent != root / "packages/chatsandbox/images":
        raise ValueError("Image must be a checked-in image directory")
    for filename in ["Dockerfile", "requirements.lock", "workload.py", ".dockerignore"]:
        path = context / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("Complete, regular image sources required")
    repository = f"{registry}/{namespace}/chatsandbox-{image}"
    return {"schema_version": 1, "image": image, "registry": registry, "repository": repository,
            "tag": repository + ":sha-" + source_commit[:12], "source_commit": source_commit,
            "workflow_run_id": str(workflow_run_id),
            "platform": "linux/amd64", "context": context.relative_to(root).as_posix()}


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def inspect_archive(path):
    """Read only bounded metadata; never extract an image's filesystem."""
    with tarfile.open(path, "r:*") as archive:
        def read(name, *, digest=None):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("Expected bounded regular OCI metadata")
            value = archive.extractfile(member).read()
            if digest is not None and "sha256:" + hashlib.sha256(value).hexdigest() != digest:
                raise ValueError("OCI metadata digest mismatch")
            return json.loads(value)

        def blob(descriptor):
            digest = descriptor.get("digest", "")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise ValueError("Expected SHA256 OCI descriptor")
            return read("blobs/sha256/" + digest.split(":")[1], digest=digest)

        if read("oci-layout").get("imageLayoutVersion") != "1.0.0":
            raise ValueError("Expected OCI image archive")
        index = read("index.json")
        # Exactly one linux/amd64 image, no attestation or extra platform index.
        for _ in range(3):
            descriptors = index.get("manifests", [])
            if len(descriptors) != 1:
                raise ValueError("Expected one image in the OCI archive")
            descriptor = descriptors[0]
            manifest = blob(descriptor)
            if "config" in manifest:
                break
            index = manifest
        else:
            raise ValueError("Nested image index is not supported")
        config = blob(manifest["config"])
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("Expected linux/amd64 image")
        if not isinstance(manifest.get("layers"), list) or not manifest["layers"]:
            raise ValueError("Expected an image with layers")
        # Ensure every referenced blob exists. Layer contents remain in the tar.
        for layer in manifest["layers"]:
            digest = layer.get("digest", "")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise ValueError("Expected SHA256 layer descriptor")
            member = archive.getmember("blobs/sha256/" + digest.split(":")[1])
            if not member.isfile() or member.size != layer["size"]:
                raise ValueError("Missing or mismatched image layer")
        return {"manifest_digest": descriptor["digest"], "config_digest": manifest["config"]["digest"],
                "platform": "linux/amd64", "layer_count": len(manifest["layers"])}


def release_receipt(plan, archive, *, copied_digest=None):
    result = {**plan, **inspect_archive(archive), "archive_file": Path(archive).name,
              "archive_sha256": checksum(archive), "archive_size_bytes": Path(archive).stat().st_size,
              "published": copied_digest is not None}
    if copied_digest is not None and copied_digest.strip() != result["manifest_digest"]:
        raise ValueError("Registry copy changed the OCI manifest digest")
    result["image_ref"] = plan["repository"] + "@" + result["manifest_digest"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    plan.add_argument("--registry", default="ghcr.io")
    plan.add_argument("--namespace", required=True)
    plan.add_argument("--image", default="python-sympy")
    plan.add_argument("--source-commit", required=True)
    plan.add_argument("--workflow-run-id", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--github-output", type=Path)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--plan", type=Path, required=True)
    receipt.add_argument("--archive", type=Path, required=True)
    receipt.add_argument("--copied-digest", type=Path)
    receipt.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = image_plan(args.root, registry=args.registry, namespace=args.namespace,
                            image=args.image, source_commit=args.source_commit, workflow_run_id=args.workflow_run_id)
        if args.github_output:
            with args.github_output.open("a") as output:
                for key in ["image", "registry", "repository", "tag", "context"]:
                    output.write(f"{key}={result[key]}\n")
    else:
        result = release_receipt(json.loads(args.plan.read_text()), args.archive,
                                 copied_digest=args.copied_digest.read_text() if args.copied_digest else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
