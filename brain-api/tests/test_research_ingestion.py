import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from brain.auth import bearer
from brain.initial_grants import apply_initial_read_grants
from brain.registry import DATASETS, ENG, LEAD
from brain.research_api import get_research_service, ingestion_failure, router
from brain.research_cognee import remember_document
from brain.research_contract import IngestionError, ResearchRequest, prepare
from brain.research_ingestion import ResearchIngestion
from brain.research_ledger import ResearchLedger
from fastapi import Depends, FastAPI

KEY = "a" * 64


def payload(user=ENG):
    return {
        "as_user": user,
        "run_id": "research-run-1",
        "report": {
            "ingestion_id": KEY,
            "leads": [
                {
                    "company": "Example Co",
                    "domain": "example.com",
                    "contacts": [
                        {
                            "name": "A Person",
                            "evidence": [
                                {
                                    "source_id": "source-1",
                                    "quote": "Appointed in September",
                                }
                            ],
                        }
                    ],
                    "workflow": {"status": "research_only", "execution": "not_requested"},
                }
            ],
            "sources": [
                {
                    "id": "source-1",
                    "url": "https://example.com/announcement",
                    "text": "Appointed in September. Original source text.",
                    "metadata": {"published_at": "2026-09-12"},
                }
            ],
        },
    }


async def access(user):
    return {
        "readable": [name for name, spec in DATASETS.items() if spec.owner == user]
        + ["toir-firm", "toir-pipeline"]
    }


def service(tmp_path, callback=None, access_callback=access):
    calls = []

    async def remember(doc):
        calls.append(doc)
        return {"pipeline_run_id": "pipeline-1"}

    result = ResearchIngestion(
        ResearchLedger(tmp_path / "receipts.sqlite3"),
        callback or remember,
        access_callback,
        asyncio.Lock(),
    )
    return result, calls


def make_app(ingestion):
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(bearer)])
    app.add_exception_handler(IngestionError, ingestion_failure)
    app.dependency_overrides[get_research_service] = lambda: ingestion
    return app


def test_authenticated_contract_and_successful_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_API_TOKEN", "fixture-token")

    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=make_app(ingestion)), base_url="http://test"
            ) as client:
                for path in ("/capabilities", f"/remember/research/{KEY}"):
                    assert (await client.get(path)).status_code == 401
                assert (await client.post("/remember/research", json=payload())).status_code == 401
                client.headers["Authorization"] = "Bearer fixture-token"
                capabilities = await client.get("/capabilities")
                assert capabilities.json() == {
                    "research_idempotency": True,
                    "research_writers": [LEAD, ENG],
                }
                response = await client.post(
                    "/remember/research", json=payload(), headers={"Idempotency-Key": KEY}
                )
                assert response.status_code == 200
                assert response.json() == {
                    "dataset": "toir-pipeline",
                    "ingestion_id": KEY,
                    "documents": 1,
                }
                replay = await client.post(
                    "/remember/research", json=payload(), headers={"Idempotency-Key": KEY}
                )
                assert replay.content == response.content
                assert len(calls) == 1
                status = await client.get(f"/remember/research/{KEY}")
                assert status.json()["status"] == "completed"
                assert "payload" not in status.text and "Example Co" not in status.text
        finally:
            await ingestion.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("user", [ENG, LEAD])
def test_both_sales_users_can_ingest_without_changing_dataset(tmp_path, user):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        try:
            result = await ingestion.remember(ResearchRequest(**payload(user)), KEY)
            assert result["dataset"] == "toir-pipeline"
            assert "client:toir" in calls[0]["node_set"]
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_changed_request_conflicts_but_object_key_order_does_not(tmp_path):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        try:
            original = payload()
            await ingestion.remember(ResearchRequest(**original), KEY)
            reordered = json.loads(json.dumps(original, sort_keys=True))
            await ingestion.remember(ResearchRequest(**reordered), KEY)
            for change in ("company", "run_id", "as_user"):
                body = copy.deepcopy(original)
                if change == "company":
                    body["report"]["leads"][0]["company"] = "Different company"
                else:
                    body[change] = LEAD if change == "as_user" else "different-run"
                with pytest.raises(IngestionError) as error:
                    await ingestion.remember(ResearchRequest(**body), KEY)
                assert error.value.status == 409
            assert len(calls) == 1
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_completed_receipt_replays_after_process_restart(tmp_path):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        first = await ingestion.remember(ResearchRequest(**payload()), KEY)
        await ingestion.close()
        restarted, later_calls = service(tmp_path)
        await restarted.open()
        try:
            assert await restarted.remember(ResearchRequest(**payload()), KEY) == first
            assert len(calls) == 1 and later_calls == []
        finally:
            await restarted.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("started", [True, False])
def test_restart_with_prepared_or_inflight_document(tmp_path, started):
    async def scenario():
        ingestion, _ = service(tmp_path)
        await ingestion.open()
        body = ResearchRequest(**payload())
        request_hash, raw, docs = prepare(body, KEY)
        ingestion.ledger.reserve(KEY, request_hash, raw, docs)
        if started:
            ingestion.ledger.start_document(KEY, 0)
        await ingestion.close()
        restarted, calls = service(tmp_path)
        await restarted.open()
        try:
            if started:
                with pytest.raises(IngestionError) as error:
                    await restarted.remember(body, KEY)
                assert error.value.code == "research_ingestion_uncertain"
                assert calls == []
            else:
                assert (await restarted.remember(body, KEY))["documents"] == 1
                assert len(calls) == 1
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_crash_after_all_document_receipts_committed_can_finish_without_ingestion(tmp_path):
    async def scenario():
        ingestion, _ = service(tmp_path)
        await ingestion.open()
        body = ResearchRequest(**payload())
        request_hash, raw, docs = prepare(body, KEY)
        ingestion.ledger.reserve(KEY, request_hash, raw, docs)
        ingestion.ledger.start_document(KEY, 0)
        ingestion.ledger.finish_document(KEY, 0, {"pipeline_run_id": "completed-before-crash"})
        await ingestion.close()
        restarted, calls = service(tmp_path)
        await restarted.open()
        try:
            assert (await restarted.remember(body, KEY))["documents"] == 1
            assert calls == []
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_provider_failure_and_cancellation_cannot_create_ack_or_repeat_calls(tmp_path):
    async def scenario():
        for kind in ("error", "cancel"):
            calls = []

            async def fail(doc):
                calls.append(doc)
                if kind == "cancel":
                    raise asyncio.CancelledError()
                raise RuntimeError("provider-secret-body")

            ingestion, _ = service(tmp_path / kind, fail)
            await ingestion.open()
            try:
                expected = asyncio.CancelledError if kind == "cancel" else IngestionError
                with pytest.raises(expected):
                    await ingestion.remember(ResearchRequest(**payload()), KEY)
                for _ in range(2):
                    with pytest.raises(IngestionError) as error:
                        await ingestion.remember(ResearchRequest(**payload()), KEY)
                    assert error.value.status == 503
                    assert "provider-secret" not in str(error.value)
                assert len(calls) == 1
                assert (await ingestion.status(KEY))["status"] == "uncertain"
            finally:
                await ingestion.close()

    asyncio.run(scenario())


def test_concurrent_duplicate_requests_share_one_durable_result(tmp_path):
    async def scenario():
        entered, release, calls = asyncio.Event(), asyncio.Event(), []

        async def slow(doc):
            calls.append(doc)
            entered.set()
            await release.wait()
            return {}

        ingestion, _ = service(tmp_path, slow)
        await ingestion.open()
        try:
            first = asyncio.create_task(ingestion.remember(ResearchRequest(**payload()), KEY))
            await entered.wait()
            second = asyncio.create_task(ingestion.remember(ResearchRequest(**payload()), KEY))
            release.set()
            assert await first == await second
            assert len(calls) == 1
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_nested_citations_and_full_source_metadata_are_preserved():
    body = ResearchRequest(**payload())
    _, raw, docs = prepare(body, KEY)
    content = json.loads(docs[0]["text"].split("\n\n", 1)[1])
    assert content["lead"] == body.report["leads"][0]
    assert content["cited_sources"] == body.report["sources"]
    assert json.loads(raw)["report"] == body.report


@pytest.mark.parametrize("issue", ["empty", "missing_id", "mismatch", "unresolved", "duplicate"])
def test_invalid_reports_fail_before_provider_work(tmp_path, issue):
    async def scenario():
        body = payload()
        if issue == "empty":
            body["report"]["leads"] = []
        elif issue == "missing_id":
            del body["report"]["ingestion_id"]
        elif issue == "mismatch":
            body["report"]["ingestion_id"] = "b" * 64
        elif issue == "unresolved":
            body["report"]["sources"] = []
        else:
            body["report"]["sources"] *= 2
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        try:
            with pytest.raises(IngestionError) as error:
                await ingestion.remember(ResearchRequest(**body), KEY)
            assert error.value.status == 422
            assert calls == []
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_exact_initial_read_grants_and_revoked_writer_access(tmp_path):
    async def scenario():
        grants = []

        async def record(*args):
            grants.append(args)

        await apply_initial_read_grants(record)
        assert grants == [(LEAD, ENG, "toir-firm"), (LEAD, ENG, "toir-pipeline")]
        assert all(not name.startswith(("acme-", "initech-", "globex-")) for _, _, name in grants)

        async def restricted(user):
            return {"readable": ["toir-pipeline"] if user == LEAD else ["globex-eng", "toir-firm"]}

        ingestion, calls = service(tmp_path, access_callback=restricted)
        await ingestion.open()
        try:
            assert (await ingestion.capabilities())["research_writers"] == [LEAD]
            for user in (ENG, "unknown@example.com"):
                with pytest.raises(IngestionError) as error:
                    await ingestion.remember(ResearchRequest(**payload(user)), KEY)
                assert error.value.status == 403
            assert calls == []
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_forget_invalidates_receipts_only_for_owned_pipeline(tmp_path):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        body = ResearchRequest(**payload())
        try:
            await ingestion.remember(body, KEY)
            await ingestion.before_forget(ENG, "globex-eng")
            await ingestion.before_forget(LEAD, "acme-eng")
            assert (await ingestion.remember(body, KEY))["documents"] == 1
            with pytest.raises(PermissionError):
                await ingestion.before_forget(ENG, "toir-pipeline")
            await ingestion.before_forget(LEAD, "toir-pipeline")
            with pytest.raises(IngestionError) as error:
                await ingestion.remember(body, KEY)
            assert error.value.code == "research_ingestion_forgotten"
            assert len(calls) == 1
        finally:
            await ingestion.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,dataset,success",
    [
        ("completed", "toir-pipeline", True),
        ("errored", "toir-pipeline", False),
        ("running", "toir-pipeline", False),
        ("session_stored", "toir-pipeline", False),
        ("completed", "acme-commercial", False),
    ],
)
def test_cognee_adapter_requires_completed_target_dataset(status, dataset, success):
    async def scenario():
        recorded = []
        owner = object()

        async def remember(*args, **kwargs):
            recorded.append((args, kwargs))
            return SimpleNamespace(
                status=status,
                dataset_name=dataset,
                pipeline_run_id="pipeline",
                error="sensitive-provider-error",
            )

        call = remember_document(
            {"text": "source", "node_set": ["client:toir"]},
            remember=remember,
            owner=owner,
            graph_model="graph",
            prompt="prompt",
        )
        if success:
            assert await call == {"pipeline_run_id": "pipeline"}
        else:
            with pytest.raises(RuntimeError, match="research_document_not_completed"):
                await call
        kwargs = recorded[0][1]
        assert kwargs["user"] is owner
        assert kwargs["dataset_name"] == "toir-pipeline"
        assert kwargs["run_in_background"] is False and kwargs["self_improvement"] is False

    asyncio.run(scenario())


def test_second_server_cannot_own_same_ledger(tmp_path):
    first = ResearchLedger(tmp_path / "receipts.sqlite3")
    second = ResearchLedger(tmp_path / "receipts.sqlite3")
    first.open()
    try:
        with pytest.raises(BlockingIOError):
            second.open()
    finally:
        first.close()
        second.close()


def test_disconnected_http_waiter_leaves_ingestion_running_and_replayable(tmp_path):
    async def scenario():
        entered, release, calls = asyncio.Event(), asyncio.Event(), []

        async def slow(doc):
            calls.append(doc)
            entered.set()
            await release.wait()
            return {"pipeline_run_id": "finished-after-disconnect"}

        ingestion, _ = service(tmp_path, slow)
        await ingestion.open()
        try:
            waiter = asyncio.create_task(ingestion.submit(ResearchRequest(**payload()), KEY))
            await entered.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert len(ingestion.inflight) == 1
            retry = asyncio.create_task(ingestion.submit(ResearchRequest(**payload()), KEY))
            release.set()
            acknowledgment = await retry
            assert acknowledgment["documents"] == 1
            assert len(calls) == 1
            assert await ingestion.submit(ResearchRequest(**payload()), KEY) == acknowledgment
            assert len(calls) == 1
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_partial_multiple_document_ingestion_is_not_acknowledged_or_repeated(tmp_path):
    async def scenario():
        calls = []

        async def write(doc):
            calls.append(doc)
            if len(calls) == 2:
                raise RuntimeError("second document failed after a possible partial write")
            return {"pipeline_run_id": "first-document"}

        body = payload()
        body["report"]["leads"].append({"company": "Second company"})
        ingestion, _ = service(tmp_path, write)
        await ingestion.open()
        try:
            for _ in range(2):
                with pytest.raises(IngestionError):
                    await ingestion.remember(ResearchRequest(**body), KEY)
            assert len(calls) == 2
            status = await ingestion.status(KEY)
            assert status["completed"] == 1 and status["active_document"] == 1
            assert status["status"] == "uncertain"
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_receipt_commit_failure_after_provider_success_is_uncertain(tmp_path, monkeypatch):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()

        def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr(ingestion.ledger, "finish_document", fail)
        try:
            for _ in range(2):
                with pytest.raises(IngestionError):
                    await ingestion.remember(ResearchRequest(**payload()), KEY)
            assert len(calls) == 1
            assert (await ingestion.status(KEY))["status"] == "uncertain"
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_final_ack_commit_failure_can_retry_without_more_provider_work(tmp_path, monkeypatch):
    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        original_finish = ingestion.ledger.finish

        def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr(ingestion.ledger, "finish", fail)
        try:
            with pytest.raises(OSError):
                await ingestion.remember(ResearchRequest(**payload()), KEY)
            monkeypatch.setattr(ingestion.ledger, "finish", original_finish)
            assert (await ingestion.remember(ResearchRequest(**payload()), KEY))["documents"] == 1
            assert len(calls) == 1
        finally:
            await ingestion.close()

    asyncio.run(scenario())


def test_route_validation_conflict_and_error_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_API_TOKEN", "fixture-token")

    async def scenario():
        ingestion, calls = service(tmp_path)
        await ingestion.open()
        app = make_app(ingestion)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer fixture-token"},
            ) as client:
                assert (await client.post("/remember/research", json=payload())).status_code == 422
                assert (
                    await client.post(
                        "/remember/research",
                        json=payload(),
                        headers={"Idempotency-Key": "not-a-key"},
                    )
                ).status_code == 422
                client.headers["Idempotency-Key"] = KEY
                mismatch = payload()
                mismatch["report"]["ingestion_id"] = "b" * 64
                response = await client.post("/remember/research", json=mismatch)
                assert (
                    response.status_code == 422
                    and response.json()["detail"] == "ingestion_id_mismatch"
                )
                assert calls == []
                assert (await client.post("/remember/research", json=payload())).status_code == 200
                changed = payload()
                changed["report"]["leads"][0]["company"] = "Different"
                assert (await client.post("/remember/research", json=changed)).status_code == 409
                unknown = payload("unrelated@example.com")
                assert (await client.post("/remember/research", json=unknown)).status_code == 403
                key2 = "c" * 64
                client.headers["Idempotency-Key"] = key2
                new_body = payload()
                new_body["report"]["ingestion_id"] = key2

                async def fail(doc):
                    raise RuntimeError("vendor-body-secret")

                ingestion.remember_document = fail
                response = await client.post("/remember/research", json=new_body)
                assert response.status_code == 503
                assert response.json() == {"detail": "research_ingestion_uncertain"}
                assert response.headers["Retry-After"] == "60"
                assert "vendor" not in response.text
        finally:
            await ingestion.close()

    asyncio.run(scenario())
