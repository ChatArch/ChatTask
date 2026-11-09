"""Strict private checkpoint format for a quiescent Codex round."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import tarfile

MAX_ARCHIVE = 25 * 1024 * 1024
MAX_EXPANDED = 60 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
MAX_MEMBERS = 3000
_SECRET_NAME = re.compile(r"(^|[._-])(auth|credential|credentials|secret|token|password|passwd|cookie|private|cache|env)([._-]|$)", re.I)
_ALLOWED = ("work/", "result/", "codex-home/sessions/")


def validate_checkpoint(data: bytes, *, expected=None, known_tokens=(), session_id=None) -> dict:
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE:
        raise ValueError("checkpoint archive size exceeded")
    digest = hashlib.sha256(data).hexdigest()
    if expected and (expected.get("sha256") != digest or expected.get("size") != len(data)):
        raise ValueError("checkpoint digest mismatch")
    seen, total, session_found = set(), 0, False
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive:
                name = member.name
                if len(seen) >= MAX_MEMBERS or name in seen or name.startswith("/") or "\\" in name:
                    raise ValueError("unsafe checkpoint member")
                parts = name.split("/")
                if any(part in ("", ".", "..") for part in parts) or not name.startswith(_ALLOWED):
                    raise ValueError("checkpoint path outside allowlist")
                if any(_SECRET_NAME.search(part) or part in (".git", ".ssh", "__pycache__") for part in parts):
                    raise ValueError("secret-looking checkpoint path")
                if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                    raise ValueError("checkpoint links and special files forbidden")
                seen.add(name)
                if session_id and name.startswith("codex-home/sessions/") and session_id in name:
                    session_found = True
                if member.isfile():
                    total += member.size
                    if member.size > MAX_FILE or total > MAX_EXPANDED:
                        raise ValueError("checkpoint expanded size exceeded")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("checkpoint member unreadable")
                    body = stream.read(MAX_FILE + 1)
                    if len(body) != member.size:
                        raise ValueError("checkpoint member truncated")
                    for token in known_tokens:
                        if token:
                            raw = token.encode()
                            if raw in body or base64.b64encode(raw) in body:
                                raise ValueError("live credential in checkpoint")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError("invalid checkpoint archive") from exc
    if session_id and not session_found:
        raise ValueError("native session missing from checkpoint")
    return {"sha256": digest, "size": len(data), "members": len(seen), "expanded_size": total}


CAPTURE_SCRIPT = r'''import os, pathlib, re, stat, tarfile
root=pathlib.Path('/workspace')
targets=('work','result','codex-home/sessions')
bad=re.compile(r'(^|[._-])(auth|credential|credentials|secret|token|password|passwd|cookie|private|cache|env)([._-]|$)',re.I)
files=[];total=0
for target in targets:
 base=root/target
 if not base.exists():continue
 if base.is_symlink():raise SystemExit('checkpoint root symlink')
 for directory,dirs,names in os.walk(base,followlinks=False):
  # Generated caches are disposable; do not persist them or let them block a round.
  for name in list(dirs):
   if name in ('__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'):
    dirs.remove(name)
  for name in dirs+names:
   p=pathlib.Path(directory)/name
   rel=p.relative_to(root)
   if any(bad.search(part) or part=='.git' for part in rel.parts):raise SystemExit('secret-looking checkpoint path')
   st=p.lstat()
   if stat.S_ISLNK(st.st_mode) or (not stat.S_ISDIR(st.st_mode) and not stat.S_ISREG(st.st_mode)) or (stat.S_ISREG(st.st_mode) and st.st_nlink>1):raise SystemExit('checkpoint link or special file')
   if stat.S_ISREG(st.st_mode):
    total+=st.st_size
    if st.st_size>12*1024*1024 or total>60*1024*1024 or len(files)>=3000:raise SystemExit('checkpoint budget exceeded')
    files.append(p)
with tarfile.open('/workspace/checkpoint.tar.gz','w:gz') as archive:
 for p in sorted(files):archive.add(p,arcname=str(p.relative_to(root)),recursive=False)
if pathlib.Path('/workspace/checkpoint.tar.gz').stat().st_size>25*1024*1024:raise SystemExit('compressed checkpoint budget exceeded')
'''
