"""Run the published SymPy image through an existing approved Sandbox profile."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from chatsandbox import create_backend_from_profile


async def run(profile, image, output):
    if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("Use the operator-approved immutable image reference")
    root = create_backend_from_profile(profile)
    backend = root.for_image(image)
    sandbox = connection = None
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "profile": profile, "image": image}
    try:
        sandbox, connection, evidence = await backend.create(
            ttl_seconds=300, metadata={"purpose": "published-sympy-example"}
        )
        report.update(sandbox_id=sandbox.id, runtime=evidence)
        await sandbox.files.write_file("/input.json", json.dumps({"coefficients": [1, 0, -1]}), mode=600)
        result = await sandbox.commands.run([
            "python", "/opt/chatsandbox-examples/workload.py", "--input", "/input.json", "--output", "/result.json",
        ])
        printed = json.loads("".join(item.text for item in result.logs.stdout))
        downloaded = json.loads(await sandbox.files.read_file("/result.json"))
        expected = {"engine": "sympy", "engine_version": "1.14.0", "polynomial": "x**2 - 1",
                    "factorized": "(x - 1)*(x + 1)", "derivative": "2*x"}
        report["result"] = downloaded
        report["extra_dependency_verified"] = printed == downloaded == expected
    except Exception as error:
        # Avoid exception strings containing transport headers or capabilities.
        report["error_type"] = type(error).__name__
    finally:
        if sandbox is not None:
            try:
                await backend.dispose(sandbox, connection)
                report["absence_verified"] = await asyncio.to_thread(backend.verify_absent, sandbox.id)
                await sandbox.close()
            except Exception as error:
                report["cleanup_error_type"] = type(error).__name__
        await backend.close()
        await root.close()
        report["passed"] = (report.get("extra_dependency_verified") is True and report.get("absence_verified") is True
                            and not any(key in report for key in ["error_type", "cleanup_error_type"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.image, args.output))
