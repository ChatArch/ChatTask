"""Bounded abstract-only discovery from the official arXiv Atom API."""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_ATOM = "{http://www.w3.org/2005/Atom}"
_ID = re.compile(r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z.-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v[1-9][0-9]*)?\Z", re.I)
_QUERY = re.compile(r"[\w .-]{3,120}\Z", re.UNICODE)
_MAX_FEED = 512 * 1024


def _fetch_arxiv(url: str) -> bytes:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(url, headers={"User-Agent": "ChatTaskResearch/0.1 (abstract-only)", "Accept": "application/atom+xml"})
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=15) as response:
        if response.status != 200 or response.geturl().split("?", 1)[0] != "https://export.arxiv.org/api/query":
            raise ValueError("official arXiv endpoint did not return a direct success")
        return response.read(_MAX_FEED + 1)


def _valid_search(query: str, max_results: int) -> None:
    if not isinstance(query, str) or not _QUERY.fullmatch(query) or query.strip() != query:
        raise ValueError("search query must be 3–120 plain characters")
    if type(max_results) is not int or not 1 <= max_results <= 5:
        raise ValueError("arXiv discovery accepts 1–5 abstracts")


def discover_arxiv(query: str, *, max_results: int = 3, fetch=None) -> list[dict]:
    """Return canonical abstract records, not a claim to have read full papers."""
    _valid_search(query, max_results)
    search = "all:" + " AND all:".join(query.split())
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": search, "start": 0, "max_results": max_results, "sortBy": "relevance", "sortOrder": "descending"})
    try:
        raw = (fetch or _fetch_arxiv)(url)
        if not isinstance(raw, bytes) or len(raw) > _MAX_FEED or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("arXiv feed invalid or oversized")
        feed = ET.fromstring(raw)
    except (ET.ParseError, UnicodeError, OSError, TimeoutError) as exc:
        raise ValueError("official arXiv feed unavailable") from exc
    if feed.tag != _ATOM + "feed":
        raise ValueError("unexpected arXiv feed")
    records = []
    for entry in feed.findall(_ATOM + "entry"):
        original = (entry.findtext(_ATOM + "id") or "").strip()
        parsed = urllib.parse.urlsplit(original)
        if parsed.scheme not in ("http", "https") or parsed.hostname != "arxiv.org" or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith("/abs/"):
            raise ValueError("unexpected arXiv identifier")
        identifier = parsed.path[len("/abs/"):]
        if not _ID.fullmatch(identifier):
            raise ValueError("invalid arXiv identifier")
        title = " ".join((entry.findtext(_ATOM + "title") or "").split())
        abstract = " ".join((entry.findtext(_ATOM + "summary") or "").split())
        date = (entry.findtext(_ATOM + "updated") or "").strip()
        try:
            dt.datetime.fromisoformat(date.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("arXiv update date missing") from exc
        if not title or not abstract or len(title) > 180 or len(abstract) > 4000:
            raise ValueError("arXiv title or abstract missing or oversized")
        source_id = ("arxiv-legacy-" + identifier.encode('ascii').hex()
                     if '/' in identifier else "arxiv-" + identifier)
        records.append({"id": source_id, "url": "https://arxiv.org/abs/" + identifier,
                        "title": title, "content": abstract,
                        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "read_scope": "arXiv Atom abstract only; full paper not retrieved"})
    if not records or len(records) > max_results or len({r["id"] for r in records}) != len(records):
        raise ValueError("arXiv returned no unique abstracts")
    return records
