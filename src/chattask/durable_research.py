"""Trusted adapter between a completed durable Codex round and ResearchPublisher."""
from __future__ import annotations

import io
import json
import re
import tarfile
from urllib.parse import urlsplit


def source_records(value):
    """Bound caller-verified excerpts; they are evidence, never executable options."""
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError('research requires 1–12 caller-verified source records')
    clean = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {'id', 'url', 'title', 'content', 'excerpt', 'retrieved_at', 'accessed_at', 'read_scope', 'read_boundary'}:
            raise ValueError('invalid research source fields')
        source_id = item.get('id')
        if not isinstance(source_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,79}', source_id):
            raise ValueError('canonical research source id required')
        url = item.get('url')
        if not isinstance(url, str) or len(url) > 500:
            raise ValueError('invalid research source URL')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('invalid research source URL')
        title = item.get('title')
        body = item.get('content', item.get('excerpt'))
        if not isinstance(title, str) or not title.strip() or len(title) > 180 or not isinstance(body, str) or not body.strip():
            raise ValueError('research source title and excerpt required')
        for field, limit in (('title', 180), ('content', 4000), ('excerpt', 4000),
                             ('retrieved_at', 40), ('accessed_at', 40), ('read_scope', 160), ('read_boundary', 160)):
            field_value = item.get(field)
            if field_value is not None and (not isinstance(field_value, str) or len(field_value) > limit):
                raise ValueError('invalid research source text')
        clean.append(dict(item))
    if len(json.dumps(clean, ensure_ascii=False).encode()) > 40000:
        raise ValueError('research sources exceed budget')
    return clean


def research_files(result_archive: bytes):
    """Read only flat UTF-8 research files from the bounded result artifact."""
    if not isinstance(result_archive, bytes) or len(result_archive) > 20 * 1024 * 1024:
        raise ValueError('missing or oversized result archive')
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(result_archive), mode='r:gz') as archive:
            for member in archive:
                name = member.name
                if not name.startswith('research/'):
                    continue
                short = name.removeprefix('research/')
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}\.(?:tex|bib|md|json)', short) or not member.isfile() or member.issym() or member.islnk() or short in files:
                    raise ValueError('unsafe research result member')
                if member.size > 1024 * 1024 or len(files) >= 20:
                    raise ValueError('research result budget exceeded')
                body = archive.extractfile(member).read(1024 * 1024 + 1)
                if len(body) != member.size:
                    raise ValueError('research result member truncated')
                files[short] = body.decode('utf-8')
    except (tarfile.TarError, UnicodeError, OSError) as exc:
        raise ValueError('invalid research result archive') from exc
    if 'main.tex' not in files:
        raise ValueError('research/main.tex missing from result')
    return files
