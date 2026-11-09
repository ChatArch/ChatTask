"""arXiv abstract discovery is a bounded, authenticated Task creation path."""
import asyncio
from pathlib import Path
import httpx
import pytest

ATOM = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2601.01234v2</id><title>  Durable Research  Agents </title><updated>2026-01-09T00:00:00Z</updated><summary> A verified abstract about bounded recovery. </summary></entry>
<entry><id>http://arxiv.org/abs/2601.05678v1</id><title>Research Workspaces</title><updated>2026-01-10T00:00:00Z</updated><summary> A second bounded abstract with citations. </summary></entry>
</feed>'''


def test_official_arxiv_discovery_binds_abstract_and_doi_free_identity():
    from chattask.research_discovery import discover_arxiv
    seen = []
    def fetch(url):
        seen.append(url)
        return ATOM
    records = discover_arxiv("durable research agents", max_results=2, fetch=fetch)
    assert len(records) == 2
    assert records[0]["id"] == "arxiv-2601.01234v2"
    assert records[0]["url"] == "https://arxiv.org/abs/2601.01234v2"
    assert records[0]["title"] == "Durable Research Agents"
    assert "verified abstract" in records[0]["content"]
    assert records[0]["read_scope"] == "arXiv Atom abstract only; full paper not retrieved"
    assert len(seen) == 1 and seen[0].startswith("https://export.arxiv.org/api/query?")
    assert "max_results=2" in seen[0]


@pytest.mark.parametrize("query,maximum", [("", 2), ("a" * 121, 2), ("abc/../secret", 2), ("ok\nPOST", 2), ("valid query", 0), ("valid query", 9)])
def test_arxiv_rejects_invalid_query_without_network(query, maximum):
    from chattask.research_discovery import discover_arxiv
    calls = []
    with pytest.raises(ValueError):
        discover_arxiv(query, max_results=maximum, fetch=lambda url: calls.append(url))
    assert not calls


@pytest.mark.parametrize("payload", [b"<!DOCTYPE root><foo/>", b"<feed />", b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://127.0.0.1/abs/2601.01234</id></entry></feed>", b"a" * (512 * 1024 + 1)])
def test_arxiv_untrusted_feed_fails_closed(payload):
    from chattask.research_discovery import discover_arxiv
    with pytest.raises(ValueError):
        discover_arxiv("valid query", fetch=lambda _: payload)


@pytest.mark.parametrize('feed,expected_source', [
    (ATOM, 'arxiv-2601.01234v2'),
    (b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/astro-ph/0701001</id><title>Legacy paper</title><updated>2007-01-01T00:00:00Z</updated><summary>Legacy abstract.</summary></entry></feed>''',
     'arxiv-legacy-617374726f2d70682f30373031303031'),
])
def test_authenticated_auto_research_creates_owner_bound_task_without_model_execution(tmp_path, monkeypatch, feed, expected_source):
    from chattask import TaskSettings, create_app
    from chattask.workflow import TaskStore, WorkflowService
    from chattask.agent_tasks import AgentTaskService
    from chattask.durable import DurableTaskService
    from chattask import research_discovery
    monkeypatch.setattr(research_discovery, "_fetch_arxiv", lambda _: feed)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    store = TaskStore(tmp_path / "state")
    class Runner:
        images = {"python": "example.test/fixed:1"}
    class Sandbox:
        session = None
        async def sweep(self):
            await asyncio.sleep(100)
        async def shutdown(self):
            pass
    durable = DurableTaskService(store, image_profiles=Runner.images, research_enabled=True)
    app = create_app(TaskSettings("http://127.0.0.1:1234", "synthetic", origin="http://testserver", username="operator", password="synthetic", home=tmp_path),
                     service=Sandbox(), workflow=WorkflowService(store), agent_tasks=AgentTaskService(store, Runner()), durable_tasks=durable)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            body={"request_id":"auto-1","instructions":"Research bounded recovery","image_profile":"python","query":"durable research agents","max_results":2}
            assert (await c.get('/api/durable/tasks')).status_code==401
            assert (await c.post('/api/durable/research/auto', json=body)).status_code==403
            login=await c.post('/auth/login',headers={"Origin":"http://testserver"},json={"username":"operator","password":"synthetic","next":"/"})
            assert login.status_code==200
            assert (await c.post('/api/durable/research/auto',json=body)).status_code==403
            result=await c.post('/api/durable/research/auto',json=body,headers={"Origin":"http://testserver","X-CSRF-Token":login.json()['csrf_token']})
            assert result.status_code==200, result.text
            task=result.json();assert task['workflow']=='research' and task['owner']=='site-operator' and task['status']=='queued'
            rounds=durable.runs('site-operator',task['id']);assert len(rounds)==1 and len(rounds[0]['source_records'])==len(feed.split(b'<entry>'))-1
            assert rounds[0]['source_records'][0]['id'] == expected_source
            assert rounds[0]['source_records'][0]['read_scope'].endswith('full paper not retrieved')
            monkeypatch.setattr(research_discovery, "_fetch_arxiv", lambda _: (_ for _ in ()).throw(AssertionError("duplicate discovery request")))
            again=await c.post('/api/durable/research/auto',json=body,headers={"Origin":"http://testserver","X-CSRF-Token":login.json()['csrf_token']});assert again.status_code==200 and again.json()['id']==task['id']
            conflict=await c.post('/api/durable/research/auto',json={**body,"query":"different topic"},headers={"Origin":"http://testserver","X-CSRF-Token":login.json()['csrf_token']})
            assert conflict.status_code==409
    asyncio.run(check())
