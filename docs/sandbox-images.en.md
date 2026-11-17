# Build, publish and use Sandbox images

| Goal | Entry point | Required authority |
| --- | --- | --- |
| Run code in an existing environment | Configured ChatSandbox profile and SDK | Caller Key and dedicated Sandbox grant |
| Package dependencies into a new environment | Checked-in Dockerfile → **Sandbox Image** workflow | Reviewed source and workflow permission |
| Admit an environment into the runtime pool | Release receipt and OCI archive | Node image installation and exact caller approval |

An image supplies the operating system, interpreter and dependencies. A Sandbox supplies a bounded runtime instance. Publishing an image does not grant Sandbox access, and a Sandbox Key cannot push to a registry. Small scripts and input files can be uploaded through the SDK without rebuilding an image.

## Default image behavior

`CHATSANDBOX_IMAGE` in a named `ChatSandbox/<profile>` selects the image explicitly. `backend.for_image(exact_reference)` changes the image while preserving the provider, credential and resource policy. The package's compatibility default is not a production allowlist. It never builds, publishes or silently substitutes an image. Deployments should pin `repo@sha256:...` and approve that entire string in the caller's `allowed_images`.

The current executor starts `/bin/sh -c 'sleep 7200'` and uses the execd supplied by OpenSandbox for commands and files. The image therefore needs Linux, `sh`, `sleep` and the workload's tools; this example targets **linux/amd64** and Python. It does not include Codex. Native ChatTask Agent images still need their corresponding Codex executable and tools.

## Start with a dependency-bearing example

[`packages/chatsandbox/images/python-sympy`](https://github.com/ChatArch/ChatTask/tree/main/packages/chatsandbox/images/python-sympy) contains a Python base pinned to an upstream amd64 manifest, public wheel hashes in `requirements.lock`, a narrow build context and `workload.py`. It preinstalls `sympy==1.14.0` and `mpmath==1.3.0`. JSON coefficients `[1,0,-1]` produce factorization `(x - 1)*(x + 1)` and derivative `2*x`; input strings are never evaluated as code.

Pin the complete dependency closure and wheel hashes when changing dependencies. Keep credentials out of images, build arguments and source. Additional scenarios can add `images/<name>/` following the `Dockerfile`, `.dockerignore`, `requirements.lock` and `workload.py --self-test` convention, then merge through normal review and checks.

## Publish without Docker on the client

1. Merge the image source into the default branch.
2. Open the repository's [Sandbox Image workflow](https://github.com/ChatArch/ChatTask/actions/workflows/publish-sandbox-image.yml), choose **Run workflow** on the default branch and select `image=python-sympy`.
3. A GitHub amd64 runner builds the image and runs its self-test with no network, 1 CPU, 512Mi and 128 PIDs. Pull requests only build and test; publication requires a manual run on the default branch.
4. The publish job uses its short-lived `GITHUB_TOKEN` with `packages:write` to push `ghcr.io/<namespace>/chatsandbox-python-sympy:sha-<commit>`. No long-lived registry token is needed. Namespace defaults to the lowercase repository owner; maintainers may explicitly set `SANDBOX_IMAGE_NAMESPACE` when required.
5. Read the immutable `image_ref` in the run summary and download `sandbox-image-python-sympy-<run_id>`. A tag or successful build alone does not prove runtime availability.

The artifact contains `image.oci.tar`, `SHA256SUMS`, `image-release.json`, source and dependency locks, the actual self-test result and the registry manifest. The same build emits an OCI archive, which is published with `skopeo copy --preserve-digests`. Archive metadata, the copy receipt and registry readback must agree on the manifest digest. Metadata separates the source commit, workflow run, platform, image/config digests, archive checksum and `published` state. Do not replace this archive with a Docker save export that may rewrite the manifest. Artifacts expire after seven days; operators should retain approved archives and receipts in their managed runtime storage.

New GHCR packages may initially be private. Public source does not imply anonymous package access. A package administrator can set the intended visibility, or an authorized operator can download the OCI artifact. Registry visibility and the caller's Sandbox grant remain separate boundaries. This workflow does not change visibility or distribute GitHub credentials to workers or SDK consumers.

## Admit the image to the runtime pool

Operators verify successful publication from the repository's default branch, exact source commit, all OCI blob digests, platform and image contents. Workers can then pull from an authorized registry or import the same OCI archive. Verify the imported manifest/config digests and CRI availability rather than an image alias. Archive import supports workers with restricted registry connectivity without connecting external SDK consumers to the private network.

Add only the receipt's exact `image_ref` to the intended caller's `allowed_images`, preserving its actions, quotas, expiry and existing images. Operators own image retention and GC; publishing a replacement does not establish that the previous image has no active users. The Sandbox public API does not accept Dockerfiles, image archives or registry credentials and has no implicit build/push operation.

## Use the published image through the public SDK

Consumers install `ChatSandbox==0.1.0` and select their existing profile. They need neither Docker nor Kubernetes credentials. From a source checkout:

```bash
python packages/chatsandbox/examples/run_published_image.py \
  --profile my-sandbox \
  --image 'ghcr.io/owner/chatsandbox-python-sympy@sha256:<approved-manifest-digest>' \
  --output ./reports/sympy-run.json
```

The example uploads coefficients, invokes the preinstalled program, downloads and checks the SymPy version, factorization and derivative, then calls `dispose()` and verifies positive absence. Control and runtime requests use the profile's same HTTPS origin. The SDK handles sensitive runtime headers without logging them. Unknown creation or cleanup outcomes still require lifecycle reconciliation instead of blind retries.

These repository sources, workflow and example do not add installed CLI commands or change the Python package protocol. See [shared backends](sandbox-backends.en.md) for configuration, authentication and cleanup, and [generic tasks](tasks.en.md) for ChatTask image profiles and model/tool responsibilities.

References: [GitHub registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [Docker exporters](https://docs.docker.com/build/exporters/), [Skopeo digest-preserving copy](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md).
