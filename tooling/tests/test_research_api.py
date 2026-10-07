"""Public API and internal coordinator share stable validation and lifecycle semantics."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from apps.api.main import app as api_app
from apps.api.research_client import ResearchClient
from apps.api.research_routes import get_client
from apps.orchestrator.main import app as coordinator_app
from apps.orchestrator.routes import get_coordinator
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_research_lifecycle import FakeAgent, coordinator

REQUEST = {"request": "Find US companies with recently appointed technology leaders"}


@asynccontextmanager
async def route_stack(directory, public, configured=True, agent=None):
    repository = await SQLiteRunRepository.open(directory / "runs.sqlite")
    service = coordinator(repository, directory, configured=configured, agent=agent)
    coordinator_app.dependency_overrides[get_coordinator] = lambda: service
    bridge = ResearchClient()
    await bridge.client.aclose()
    bridge.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=coordinator_app), base_url="http://coordinator",
    )
    api_app.dependency_overrides[get_client] = lambda: bridge
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app if public else coordinator_app),
            base_url="http://test",
        ) as client:
            yield client, service, "/api" if public else "/v1"
    finally:
        await service.shutdown()
        await bridge.close()
        await repository.close()
        api_app.dependency_overrides.pop(get_client, None)
        coordinator_app.dependency_overrides.pop(get_coordinator, None)


@pytest.mark.parametrize("public", [False, True])
def test_create_list_detail_replay_cancel_and_retry_contract(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as (client, service, prefix):
            key = {"Idempotency-Key": str(uuid4())}
            created = await client.post(f"{prefix}/research-runs", json=REQUEST, headers=key)
            assert created.status_code == 202
            run = created.json()
            assert run["status"] in {"queued", "running"}
            assert run["brief"]["geography"] == "US"
            assert run["parent_id"] is None
            assert run["report"]["leads"] == []
            run_id = run["id"]
            replay = await client.post(f"{prefix}/research-runs", json=REQUEST, headers=key)
            assert replay.status_code == 202
            assert replay.json()["id"] == run_id

            listed = await client.get(f"{prefix}/research-runs")
            assert listed.status_code == 200
            assert [entry["id"] for entry in listed.json()] == [run_id]
            detail = await client.get(f"{prefix}/research-runs/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["run"]["id"] == run_id
            assert detail.json()["events"][0]["message"] == "Research request saved"

            blocked_retry = await client.post(
                f"{prefix}/research-runs/{run_id}/retries",
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert blocked_retry.status_code == 409
            competing = await client.post(
                f"{prefix}/research-runs", json=REQUEST,
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert competing.status_code == 409

            cancelled = await client.post(f"{prefix}/research-runs/{run_id}/cancellation")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            again = await client.post(f"{prefix}/research-runs/{run_id}/cancellation")
            assert again.json()["status"] == "cancelled"
            retry_key = {"Idempotency-Key": str(uuid4())}
            retried = await client.post(
                f"{prefix}/research-runs/{run_id}/retries", headers=retry_key,
            )
            assert retried.status_code == 202
            assert retried.json()["parent_id"] == run_id
            assert retried.json()["id"] != run_id
            assert retried.json()["brief"] == run["brief"]
            assert len(await service.repository.list()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_required_idempotency_key_validation_does_not_create_run(tmp_path, public, key):
    async def scenario():
        async with route_stack(tmp_path, public) as (client, service, prefix):
            response = await client.post(
                f"{prefix}/research-runs", json=REQUEST,
                headers={"Idempotency-Key": key} if key else {},
            )
            assert response.status_code == 422
            assert await service.repository.list() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
def test_invalid_brief_rejects_before_dispatch(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as (client, service, prefix):
            for body in [
                {"request": "short"},
                REQUEST | {"employee_min": 1000, "employee_max": 20},
                REQUEST | {"target_count": 500},
                REQUEST | {"api_key": "must-not-be-accepted"},
            ]:
                response = await client.post(
                    f"{prefix}/research-runs", json=body,
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == 422
            assert await service.repository.list() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("suffix,method", [
    ("", "GET"), ("/cancellation", "POST"), ("/retries", "POST"),
])
def test_unknown_and_invalid_run_ids_have_explicit_errors(tmp_path, public, suffix, method):
    async def scenario():
        async with route_stack(tmp_path, public) as (client, _, prefix):
            for run_id, expected in [(str(uuid4()), 404), ("not-a-uuid", 422)]:
                response = await client.request(
                    method, f"{prefix}/research-runs/{run_id}{suffix}",
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
def test_same_key_changed_brief_returns_conflict(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as (client, _, prefix):
            headers = {"Idempotency-Key": str(uuid4())}
            assert (await client.post(
                f"{prefix}/research-runs", json=REQUEST, headers=headers,
            )).status_code == 202
            conflict = await client.post(
                f"{prefix}/research-runs", json=REQUEST | {"target_count": 2}, headers=headers,
            )
            assert conflict.status_code == 409

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
def test_missing_configuration_is_explained_without_creating_work(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public, configured=False) as (client, service, prefix):
            route = "/api/research-capabilities" if public else "/v1/capabilities"
            capabilities = await client.get(route)
            assert capabilities.status_code == 200
            assert capabilities.json()["configured"] is False
            assert capabilities.json()["missing"] == ["RESPAN_API_KEY"]
            result = await client.post(
                f"{prefix}/research-runs", json=REQUEST,
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert result.status_code == 503
            assert "configured Respan and Scalekit" in result.json()["detail"]
            assert await service.repository.list() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("coordinator_configured", [False, True])
def test_worker_missing_credentials_are_visible_to_the_browser(
    tmp_path, public, coordinator_configured,
):
    worker_missing = [
        "SCALEKIT_ENVIRONMENT_URL", "SCALEKIT_CLIENT_ID", "SCALEKIT_CLIENT_SECRET",
    ]
    if not coordinator_configured:
        worker_missing.append("RESPAN_API_KEY")

    class UnconfiguredWorker(FakeAgent):
        async def capabilities(self):
            # Exact /v1/capabilities field names from the Bun research service.
            return {
                "version": 1, "agent": "research", "configured": False,
                "missing_credentials": worker_missing, "capabilities": ["company_research"],
            }

    async def scenario():
        async with route_stack(
            tmp_path, public, configured=coordinator_configured, agent=UnconfiguredWorker(),
        ) as (client, service, prefix):
            path = "/api/research-capabilities" if public else "/v1/capabilities"
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json()["configured"] is False
            assert response.json()["missing"] == sorted(set(worker_missing))
            assert response.json()["agent"] == "research"
            result = await client.post(
                f"{prefix}/research-runs", json=REQUEST,
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert result.status_code == 503
            assert await service.repository.list() == []

    asyncio.run(scenario())


def test_maintenance_is_internal_and_public_requests_cannot_change_it(tmp_path):
    async def scenario():
        async with route_stack(tmp_path, True) as (client, service, _):
            for path in ["/api/maintenance", "/v1/maintenance", "/api/research-runs/maintenance"]:
                response = await client.post(path, json={"enabled": True})
                assert response.status_code in {404, 405}
            assert (await service.maintenance_status())["enabled"] is False

    asyncio.run(scenario())


def test_internal_maintenance_route_persists_and_reports_state(tmp_path):
    async def scenario():
        async with route_stack(tmp_path, False) as (client, service, _):
            response = await client.post("/v1/maintenance", json={"enabled": True})
            assert response.status_code == 200
            assert response.json() == {"enabled": True, "active_run_id": None}
            assert (await client.get("/v1/maintenance")).json() == response.json()
            assert service.maintenance is True

    asyncio.run(scenario())
