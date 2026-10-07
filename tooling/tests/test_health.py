import asyncio

import httpx
import pytest

from apps.api.main import app as api
from apps.orchestrator.main import app as orchestrator


@pytest.mark.parametrize("app,path,service", [
    (api, "/api/health", "api"),
    (api, "/api/ready", "api"),
    (orchestrator, "/health", "orchestrator"),
    (orchestrator, "/ready", "orchestrator"),
])
def test_probe_contract(app, path, service):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "service": service}

    asyncio.run(request())


@pytest.mark.parametrize("app,path", [(api, "/api/tasks"), (orchestrator, "/tasks")])
def test_scaffold_has_no_task_execution(app, path):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            assert (await client.post(path, json={})).status_code == 404

    asyncio.run(request())
