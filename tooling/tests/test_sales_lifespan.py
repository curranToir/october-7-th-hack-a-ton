"""Exercise production composition without any provider calls or production state."""

import asyncio

from fastapi.testclient import TestClient

from apps.orchestrator.main import app
from tooling.tests.test_sales_workflow import CURRAN, Harness


def test_coordinator_composes_sales_services_and_drains(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESPAN_API_KEY", raising=False)
    monkeypatch.setenv("TOIR_DATA_DIR", str(tmp_path))
    (tmp_path / "maintenance.json").write_text('{"enabled": true}')
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/v1/sales/workspace").status_code == 401
        status = client.get("/v1/maintenance").json()
        assert status == {
            "enabled": True,
            "active_run_id": None,
            "storage_backend": "sqlite",
            "active_jobs": [],
            "active_contact_task_id": None,
            "active_crm_operations": 0,
        }
        sales = app.state.sales_service
        assert sales.planner.store is sales.store
        assert sales.executor.store is sales.store
        assert len(sales.tasks) == 4
    assert all(task.done() for task in sales.tasks)
    assert sales.auth.client.is_closed
    assert sales.contacts.client.is_closed
    assert sales.brain.client.is_closed


def test_chat_dispatch_rechecks_maintenance_after_waiting_for_storage(tmp_path):
    async def scenario():
        harness = await Harness.open(tmp_path)
        try:
            session = await harness.service.chat.create_session(CURRAN)
            message = await harness.service.chat.send(
                CURRAN,
                session.id,
                "Research Example",
                "maintenance-race",
            )
            async with harness.store.transaction():
                dispatch = asyncio.create_task(harness.service._chat_tick())
                await asyncio.sleep(0)
                harness.service.research.maintenance = True
            await dispatch
            assert harness.models.calls == []
            async with harness.store.transaction() as tx:
                assert (await tx.get("job", message.job_id))["status"] == "queued"
            harness.service.research.maintenance = False
            await harness.service._chat_tick()
            assert len(harness.models.calls) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())
