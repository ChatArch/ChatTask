"""Host controlled, isolated TeX build for source read back from the original service.

The original service supplies only its immutable image identity and public TeX
toolchain bind. Manuscript bytes enter a new container with no original app data.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import tarfile

from .research import ResearchUnavailable, _check_files, ID, MAX_PDF


_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OUTPUT_LIMIT = 32 * 1024 * 1024


class IsolatedResearchCompiler:
    """A callable test seam around a trusted local Docker client and private root."""

    def __init__(self, docker_client, state_root, original_container):
        self.docker = docker_client
        self.root = Path(state_root)
        self.original_container = original_container

    def prepare(self):
        """Resolve immutable image and public toolchain before native source writes."""
        try:
            original = self.docker.containers.get(self.original_container)
            original.reload() if hasattr(original, 'reload') else None
            image_id = original.attrs['Image']
            if not isinstance(image_id, str) or not _IMAGE.fullmatch(image_id):
                raise ValueError('original image is not immutable')
            image = self.docker.images.get(image_id)
            if image.id != image_id:
                raise ValueError('original image identity changed')
            mounts = [item for item in original.attrs['Mounts']
                      if item.get('Destination') == '/usr/local/texlive' and item.get('Type') == 'bind']
            if len(mounts) != 1:
                raise ValueError('original public TeX toolchain bind unavailable')
            tex_root = Path(mounts[0]['Source'])
            if not tex_root.is_absolute() or tex_root.is_symlink() or not tex_root.is_dir():
                raise ValueError('public TeX toolchain path invalid')
            engines = list(tex_root.glob('*/bin/*/xelatex'))
            if len(engines) != 1 or not engines[0].is_file() or not engines[0].resolve().is_relative_to(tex_root.resolve()):
                raise ValueError('unique installed xelatex unavailable')
            relative = engines[0].relative_to(tex_root)
            engine = '/usr/local/texlive/' + relative.as_posix()
            return {'image_id': image_id, 'tex_root': str(tex_root), 'engine': engine}
        except Exception:
            raise ResearchUnavailable('isolated compiler preflight unavailable; inspect original image and public TeX bind') from None

    def compile(self, task_id, run_id, files, plan):
        if not isinstance(task_id, str) or not ID.fullmatch(task_id) or not isinstance(run_id, str) or not ID.fullmatch(run_id):
            raise ResearchUnavailable('invalid isolated compiler Run identity')
        checked = _check_files(files)
        if plan != self.prepare():
            raise ResearchUnavailable('isolated compiler image or toolchain changed')
        labels = {'chattask.research.task': task_id, 'chattask.research.run': run_id}
        try:
            if self.docker.containers.list(all=True, filters={'label': [f'{k}={v}' for k, v in labels.items()]}):
                raise ResearchUnavailable('prior isolated compiler container needs operator reconciliation')
        except ResearchUnavailable:
            raise
        except Exception:
            raise ResearchUnavailable('isolated compiler inventory unavailable') from None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise ResearchUnavailable('isolated compiler private root invalid')
        uid, gid = os.getuid(), os.getgid()
        if uid == 0 or gid == 0:
            raise ResearchUnavailable('isolated compiler requires non-root host identity')
        scratch = self.root / (task_id + '-' + run_id + '-' + secrets.token_hex(12))
        scratch.mkdir(mode=0o700)
        source = scratch / 'input'
        source.mkdir(mode=0o700)
        export = scratch / 'export'
        export.mkdir(mode=0o700)
        for name, content in checked.items():
            target = source / name
            target.write_bytes(content.encode('utf-8'))
            target.chmod(0o400)
        source.chmod(0o500)
        nonce = secrets.token_hex(16)
        labels['chattask.research.nonce'] = nonce
        # Fixed shell program: no source text, request data, or host path is interpolated.
        command = [plan['engine'], '-no-shell-escape', '-interaction=nonstopmode',
                   '-halt-on-error', '-output-directory=/output', '/input/main.tex']
        line = ' '.join(shlex.quote(arg) for arg in command)
        # tmpfs disappears when the container exits. Export only the final PDF
        # to this Run's private bind before exit, never application data.
        script = ('cd /output && ' + line + ' >/output/pass1.log 2>&1 && ' + line
                  + ' >/output/pass2.log 2>&1 && cp -- /output/main.pdf /export/main.pdf')
        container = None
        removed = False
        try:
            container = self.docker.containers.create(
                image=plan['image_id'],
                entrypoint=['/usr/bin/env', '-i', 'HOME=/output',
                            'TEXMFVAR=/output/.texmf-var', 'TEXMFCONFIG=/output/.texmf-config',
                            'PATH=' + str(Path(plan['engine']).parent) + ':/usr/bin:/bin',
                            '/usr/bin/timeout', '--signal=TERM', '240', '/bin/sh', '-c'],
                command=[script],
                detach=True, auto_remove=False, user=f'{uid}:{gid}', working_dir='/output',
                environment={},
                volumes={str(source): {'bind':'/input','mode':'ro'},
                         plan['tex_root']: {'bind':'/usr/local/texlive','mode':'ro'},
                         str(export): {'bind':'/export','mode':'rw'}},
                tmpfs={'/output':f'rw,nosuid,nodev,noexec,size={_OUTPUT_LIMIT},uid={uid},gid={gid}'},
                network_disabled=True, read_only=True, privileged=False, cap_drop=['ALL'],
                security_opt=['no-new-privileges:true'], mem_limit=512 * 1024 * 1024,
                nano_cpus=1_000_000_000, pids_limit=64, labels=labels, stdin_open=False, tty=False)
            if not isinstance(container.id, str) or not re.fullmatch(r'[0-9a-f]{64}', container.id):
                raise ResearchUnavailable('isolated compiler container identity unavailable')
            container.start()
            status = container.wait(timeout=300)
            if status.get('StatusCode') != 0:
                raise ResearchUnavailable('isolated xelatex failed; inspect manuscript and compiler log')
            stream, _ = container.get_archive('/export/main.pdf')
            body = bytearray()
            for chunk in stream:
                if len(chunk) > MAX_PDF + 1024 * 1024 - len(body):
                    raise ResearchUnavailable('isolated PDF exceeds bound')
                body.extend(chunk)
            with tarfile.open(fileobj=io.BytesIO(body), mode='r:') as archive:
                members = archive.getmembers()
                if len(members) != 1 or not members[0].isfile() or members[0].name != 'main.pdf' or members[0].size > MAX_PDF:
                    raise ResearchUnavailable('isolated PDF archive invalid')
                pdf = archive.extractfile(members[0]).read(MAX_PDF + 1)
            if not pdf.startswith(b'%PDF-') or not 20 <= len(pdf) <= MAX_PDF:
                raise ResearchUnavailable('isolated PDF invalid or oversized')
            return pdf, {'image_id': plan['image_id'], 'engine': plan['engine'],
                         'container_id': container.id, 'nonce': nonce,
                         'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'passes': 2,
                         'isolation': 'no-network-read-only-rootfs-task-mounts'}
        except ResearchUnavailable:
            raise
        except Exception:
            raise ResearchUnavailable('isolated compiler outcome uncertain; operator must inspect Run-owned container') from None
        finally:
            if container is not None:
                try:
                    container.remove(force=False)
                    removed = True
                except Exception:
                    removed = False
            else:
                removed = True
            if removed:
                source.chmod(0o700)
                shutil.rmtree(scratch)
            else:
                # Keep exact identity and source for operator reconciliation.
                raise ResearchUnavailable('isolated compiler cleanup uncertain; operator must inspect Run-owned container')
