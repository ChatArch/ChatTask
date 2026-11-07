"""Synthetic independent principals for the durable API; no real accounts."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, HTTPException

from chattask import TaskSettings, create_app
from chattask.agent_tasks import AgentTaskService
from chattask.durable import DurableTaskService
from chattask.workflow import TaskStore, WorkflowService


class FakeAuth:
    router = APIRouter()

    def _check_host(self, request):
        pass

    async def current_user(self, request):
        actor = request.headers.get("X-Test-Principal")
        if not actor:
            raise HTTPException(401)
        return SimpleNamespace(user_id=actor)

    async def csrf_user(self, request):
        if request.headers.get("X-CSRF-Token") != "synthetic-csrf":
            raise HTTPException(403)
        return await self.current_user(request)


class FakeSandbox:
    session = None

    async def sweep(self):
        await asyncio.sleep(100)

    async def shutdown(self):
        pass


class FakeExecutor:
    images = {"python": "synthetic-image"}


def test_web_uses_authenticated_actor_and_member_role(tmp_path):
    store = TaskStore(tmp_path / "state")
    durable = DurableTaskService(store, FakeExecutor())
    app = create_app(TaskSettings("http://127.0.0.1:12345", "synthetic", origin="http://testserver",
                                  username="operator", password="synthetic", home=str(tmp_path)),
                     service=FakeSandbox(), workflow=WorkflowService(store),
                     agent_tasks=AgentTaskService(store, FakeExecutor()),
                     durable_tasks=durable, auth_adapter=FakeAuth())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            alice = {"X-Test-Principal": "alice", "X-CSRF-Token": "synthetic-csrf"}
            bob = {"X-Test-Principal": "bob", "X-CSRF-Token": "synthetic-csrf"}
            eve = {"X-Test-Principal": "eve", "X-CSRF-Token": "synthetic-csrf"}
            body = {"request_id": "create-1", "instructions": "build demo", "image_profile": "python", "owner": "eve"}
            assert (await client.post("/api/durable/tasks", headers=alice, json=body)).status_code == 422
            body.pop("owner")
            assert (await client.post("/api/durable/tasks", headers={"X-Test-Principal": "alice"}, json=body)).status_code == 403
            created = await client.post("/api/durable/tasks", headers=alice, json=body)
            assert created.status_code == 200, created.text
            task_id = created.json()["id"]
            assert created.json()["owner"] == "alice"
            assert (await client.get(f"/api/durable/tasks/{task_id}", headers=eve)).status_code == 404
            assert (await client.get(f"/api/durable/tasks/{task_id}/runs", headers=eve)).status_code == 404
            grant = {"request_id": "grant-1", "principal": "bob", "role": "viewer"}
            assert (await client.post(f"/api/durable/tasks/{task_id}/members", headers=alice, json=grant)).status_code == 200
            assert (await client.get(f"/api/durable/tasks/{task_id}", headers=bob)).status_code == 200
            assert (await client.post(f"/api/durable/tasks/{task_id}/pause", headers=bob,
                                      json={"request_id": "pause-1"})).status_code == 404
            assert (await client.post(f"/api/durable/tasks/{task_id}/pause", headers=alice,
                                      json={"request_id": "pause-1"})).status_code == 200
    asyncio.run(exercise())


def test_optional_chatlogin_principals_preserve_operator_and_acl(tmp_path):
    users = [
        {"username": "collab", "password": "synthetic-pass-1", "principal_id": "collaborator", "display_name": "Collaborator"},
        {"username": "outsider", "password": "synthetic-pass-2", "principal_id": "outsider", "display_name": "Outsider"},
    ]
    settings = TaskSettings("http://127.0.0.1:12345", "synthetic", origin="http://testserver",
                            username="operator", password="synthetic-pass-0", home=str(tmp_path),
                            login_users_json=json.dumps(users))
    store = TaskStore(tmp_path / "state")
    app = create_app(settings, service=FakeSandbox(), workflow=WorkflowService(store),
                     agent_tasks=AgentTaskService(store, FakeExecutor()),
                     durable_tasks=DurableTaskService(store, FakeExecutor()))

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as owner, \
                   httpx.AsyncClient(transport=transport, base_url="http://testserver") as collaborator, \
                   httpx.AsyncClient(transport=transport, base_url="http://testserver") as outsider:
            async def login(client, username, password):
                response = await client.post("/auth/login", headers={"Origin": "http://testserver"},
                                             json={"username": username, "password": password, "next": "/"})
                assert response.status_code == 200, response.text
                return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}

            owner_headers = await login(owner, "operator", "synthetic-pass-0")
            collaborator_headers = await login(collaborator, "collab", "synthetic-pass-1")
            await login(outsider, "outsider", "synthetic-pass-2")
            created = await owner.post("/api/durable/tasks", headers=owner_headers,
                                       json={"request_id": "one", "instructions": "Build demo", "image_profile": "python"})
            assert created.status_code == 200, created.text
            task_id = created.json()["id"]
            assert created.json()["owner"] == "site-operator"
            assert (await collaborator.get(f"/api/durable/tasks/{task_id}")).status_code == 404
            granted = await owner.post(f"/api/durable/tasks/{task_id}/members", headers=owner_headers,
                                       json={"request_id": "grant", "principal": "collaborator", "role": "editor"})
            assert granted.status_code == 200, granted.text
            assert (await collaborator.get(f"/api/durable/tasks/{task_id}")).status_code == 200
            assert (await outsider.get(f"/api/durable/tasks/{task_id}")).status_code == 404
            assert (await collaborator.post(f"/api/durable/tasks/{task_id}/pause", headers=collaborator_headers,
                                            json={"request_id": "pause"})).status_code == 200

    asyncio.run(exercise())
    with pytest.raises(ValueError, match="duplicate or invalid login principal"):
        TaskSettings("http://127.0.0.1:12345", "synthetic", username="operator", password="synthetic-pass-0",
                     login_users_json=json.dumps([{**users[0], "principal_id": "site-operator"}]))


def test_optional_research_and_login_config_are_typed_and_bounded():
    with pytest.raises(ValueError, match='configured together'):
        TaskSettings('http://127.0.0.1:12345', 'synthetic', research_container='synthetic-container')
    with pytest.raises(ValueError, match='HTTPS origin'):
        TaskSettings('http://127.0.0.1:12345', 'synthetic', research_container='synthetic-container',
                     research_origin='http://overleaf.example')
    with pytest.raises(ValueError, match='duplicate or invalid login principal'):
        TaskSettings('http://127.0.0.1:12345', 'synthetic', username='operator', password='synthetic-pass-0',
                     login_users_json=json.dumps([{'username': 'collab', 'password': 'synthetic-pass-1',
                                                   'principal_id': 'site-operator', 'display_name': 'Collab'}]))
