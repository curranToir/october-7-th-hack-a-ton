"""Provider adapters are exercised with bounded mock HTTP and no real API credentials."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from apps.orchestrator.meetings.github import GitHubIssues
from apps.orchestrator.meetings.models import MeetingCreate
from apps.orchestrator.meetings.providers import (
    OutcomeUnknown,
    ProviderFailure,
    RecallClient,
    RetryLater,
)
from tooling.tests.test_meetings_workflow import SECRET


@pytest.mark.parametrize(
    "url",
    [
        "https://zoom.us.evil.example/j/123",
        "http://zoom.us/j/123",
        "https://user:secret@zoom.us/j/123",
        "https://zoom.us:8443/j/123",
        "https://zoom.us/j/123#fragment",
        "https://zoom.us/profile",
    ],
)
def test_zoom_intake_rejects_non_invitation_urls(url):
    with pytest.raises(ValidationError):
        MeetingCreate(title="Customer call", meeting_url=url, consent_confirmed=True)


def test_zoom_intake_requires_explicit_consent_and_timezone():
    valid = {"title": "Customer call", "meeting_url": "https://zoom.us/j/123"}
    for extra in (
        {},
        {"consent_confirmed": False},
        {
            "consent_confirmed": True,
            "join_at": "2026-10-07T12:00:00",
        },
    ):
        with pytest.raises(ValidationError):
            MeetingCreate(**valid, **extra)


def configure_recall(monkeypatch):
    monkeypatch.setenv("RECALL_API_KEY", "test-only-recall-token")
    monkeypatch.setenv("RECALL_REGION", "us-west-2")
    monkeypatch.setenv("RECALL_WORKSPACE_VERIFICATION_SECRET", SECRET)
    monkeypatch.setenv("MEETING_WEBHOOK_BASE_URL", "https://meeting-webhook.example")


def test_recall_join_identifies_owner_announces_recording_and_requests_transcript_webhook(
    monkeypatch,
):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(201, json={"id": "bot-1"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            assert not recall.missing
            assert await recall.join(
                {
                    "id": "meeting-1",
                    "meeting_url": "https://zoom.us/j/123?pwd=invite",
                    "join_at": "2026-10-07T12:00:00Z",
                    "owner_email": "curran@toirinc.com",
                }
            ) == {"id": "bot-1"}
            assert len(requests) == 1
            request = requests[0]
            assert str(request.url) == "https://us-west-2.recall.ai/api/v1/bot/"
            assert request.headers["authorization"] == "test-only-recall-token"
            body = json.loads(request.content)
            assert body["metadata"] == {
                "toir_meeting_id": "meeting-1",
                "owner": "curran@toirinc.com",
            }
            assert "recording and transcribing" in body["chat"]["on_bot_join"]["message"]
            config = body["recording_config"]
            assert config["transcript"]["provider"]["recallai_streaming"]["language_code"] == "en"
            assert config["realtime_endpoints"] == [
                {
                    "type": "webhook",
                    "events": ["transcript.data"],
                    "url": "https://meeting-webhook.example/api/meetings/webhooks/recall/realtime",
                }
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,exception",
    [(429, RetryLater), (507, RetryLater), (503, OutcomeUnknown), (401, ProviderFailure)],
)
def test_recall_post_classifies_safe_retry_and_uncertain_creation(monkeypatch, status, exception):
    configure_recall(monkeypatch)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json={"detail": "provider-only details"}),
            )
        ) as http:
            recall = RecallClient(http)
            with pytest.raises(exception) as failure:
                await recall.request("POST", "/bot/", json={})
            assert "provider-only details" not in str(failure.value)
            assert "test-only-recall-token" not in str(failure.value)

    asyncio.run(scenario())


def test_recall_download_does_not_forward_api_secret_to_signed_artifact_host(monkeypatch):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            if request.url.host == "us-west-2.recall.ai":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "download_url": "https://fixture.s3.amazonaws.com/transcript?signature=test",
                        }
                    },
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "participant": {"name": "Customer"},
                        "words": [
                            {"text": "CSV exports are empty", "start_timestamp": {"relative": 12.5}}
                        ],
                    }
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            segments = await recall.transcript("bd928c95-25e9-49b0-aa51-96921e6425a8")
            assert len(segments) == 1
            assert segments[0].speaker == "Customer" and segments[0].start == 12.5
            assert segments[0].text == "CSV exports are empty"
            assert requests[0].headers["authorization"] == "test-only-recall-token"
            assert not requests[1].headers.get("authorization")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/private",
        "https://evil.example/data",
        "https://recall.ai.evil.example/data",
    ],
)
def test_recall_transcript_rejects_untrusted_download_host(monkeypatch, url):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json={"data": {"download_url": url}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            with pytest.raises(ProviderFailure, match="unsupported"):
                await recall.transcript("bd928c95-25e9-49b0-aa51-96921e6425a8")
            assert len(requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response,exception",
    [
        (httpx.Response(503, json={"message": "unavailable"}), OutcomeUnknown),
        (httpx.Response(201, json={}), OutcomeUnknown),
        (httpx.Response(201, text="not-json"), OutcomeUnknown),
        (httpx.Response(403, json={"message": "rejected"}), ProviderFailure),
    ],
)
def test_github_distinguishes_uncertain_dispatch_from_confirmed_rejection(
    monkeypatch, response, exception
):
    monkeypatch.setenv("MEETING_GITHUB_TOKEN", "fixture-github-token")
    monkeypatch.setenv("MEETING_GITHUB_REPOSITORY", "example/product")

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            github = GitHubIssues(http)
            with pytest.raises(exception):
                await github.publish(
                    {
                        "repository": "example/product",
                        "title": "Reviewed title",
                        "body": "Reviewed customer evidence.",
                    }
                )
            assert len(requests) == 1  # Adapter never silently resends uncertain requests.
            assert json.loads(requests[0].content) == {
                "title": "Reviewed title",
                "body": "Reviewed customer evidence.",
            }

    asyncio.run(scenario())


def test_github_transport_timeout_is_unknown_and_reconciliation_is_read_only(monkeypatch):
    monkeypatch.setenv("MEETING_GITHUB_TOKEN", "fixture-github-token")
    monkeypatch.setenv("MEETING_GITHUB_REPOSITORY", "example/product")

    async def scenario():
        requests = []
        task = {
            "id": "task-1",
            "repository": "example/product",
            "title": "Reviewed title",
            "body": "Reviewed body",
            "decided_at": "2026-10-07T12:00:00Z",
        }

        def provider(request):
            requests.append(request)
            if request.method == "POST":
                raise httpx.ReadTimeout("Connection closed after sending body")
            return httpx.Response(
                200,
                json=[
                    {"number": 9, "body": "<!-- toir-meeting-task:task-1 -->", "pull_request": {}},
                    {"number": 10, "body": "<!-- toir-meeting-task:task-1 -->"},
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            github = GitHubIssues(http)
            with pytest.raises(OutcomeUnknown):
                await github.publish(task)
            assert await github.reconcile(task) == "https://github.com/example/product/issues/10"
            assert [request.method for request in requests] == ["POST", "GET"]
            assert requests[1].url.params["since"] == task["decided_at"]

    asyncio.run(scenario())


def test_subject_research_adapter_submits_exact_mentioned_identity_and_exposes_missing_task():
    from apps.orchestrator.meetings.subject_client import SubjectResearchClient

    async def scenario():
        requests = []
        task_id = "bd928c95-25e9-49b0-aa51-96921e6425a8"
        subject = {
            "kind": "person",
            "name": "Alex Example",
            "context": "A professional contact",
            "evidence": {"segment_id": "segment-1", "quote": "Please research Alex Example."},
        }

        def worker(request):
            requests.append(request)
            if request.url.path == "/v1/capabilities":
                return httpx.Response(
                    200, json={"configured": True, "capabilities": ["subject_research"]}
                )
            if request.method == "POST":
                return httpx.Response(202, json={"status": "running", "task_id": task_id})
            return httpx.Response(404)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(worker),
            base_url="http://research",
        ) as http:
            client = SubjectResearchClient(http)
            assert await client.configured()
            response = await client.submit(
                {
                    **subject,
                    "id": task_id,
                    "task_id": task_id,
                    "internal_owner": "not part of research prompt",
                }
            )
            assert response["status"] == "running"
            assert requests[1].url.path == "/v1/subjects"
            assert json.loads(requests[1].content) == {
                "version": 1,
                "task_id": task_id,
                "subject": subject,
            }
            assert await client.get(task_id) is None
            assert requests[-1].url.path == f"/v1/subjects/{task_id}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response", [httpx.Response(201, text="not-json"), httpx.Response(201, json=[])]
)
def test_malformed_successful_bot_receipt_is_unknown_and_never_retried(monkeypatch, response):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            with pytest.raises(OutcomeUnknown, match="invalid bot receipt"):
                await recall.request("POST", "/bot/", json={})
            assert len(requests) == 1

    asyncio.run(scenario())


def test_completed_transcript_recovery_fetches_existing_bot_and_artifact_only(monkeypatch):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []
        bot_id = "bd928c95-25e9-49b0-aa51-96921e6425a8"
        transcript_id = "2f3b6986-1e6b-4b8d-bc2f-e002751d27ba"
        download = "https://fixture.s3.amazonaws.com/transcript?signature=test"

        def provider(request):
            requests.append(request)
            if request.url.path == f"/api/v1/bot/{bot_id}/":
                return httpx.Response(
                    200,
                    json={
                        "recordings": [
                            {
                                "media_shortcuts": {
                                    "transcript": {
                                        "id": transcript_id,
                                        "data": {"download_url": download},
                                    },
                                }
                            }
                        ]
                    },
                )
            if request.url.path == f"/api/v1/transcript/{transcript_id}/":
                return httpx.Response(200, json={"data": {"download_url": download}})
            assert str(request.url) == download
            return httpx.Response(
                200,
                json=[
                    {
                        "participant": {"name": "Customer"},
                        "words": [
                            {
                                "text": "The export is empty.",
                                "start_timestamp": {"relative": 8},
                            }
                        ],
                    }
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            recovered = await recall.completed_transcript(bot_id)
            assert len(recovered) == 1 and recovered[0].text == "The export is empty."
            assert [request.method for request in requests] == ["GET", "GET", "GET"]
            assert not requests[-1].headers.get("authorization")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "recordings",
    [
        [],
        [{"media_shortcuts": {}}],
        [
            {
                "media_shortcuts": {
                    "transcript": {"id": "2f3b6986-1e6b-4b8d-bc2f-e002751d27ba", "data": {}},
                }
            }
        ],
    ],
)
def test_completed_transcript_recovery_returns_not_ready_without_creating_a_bot(
    monkeypatch, recordings
):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json={"recordings": recordings})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            assert await recall.completed_transcript("bd928c95-25e9-49b0-aa51-96921e6425a8") is None
            assert len(requests) == 1 and requests[0].method == "GET"

    asyncio.run(scenario())


def test_completed_transcript_recovery_never_silently_discards_multiple_recordings(monkeypatch):
    configure_recall(monkeypatch)

    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json={"recordings": [{}, {}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            recall = RecallClient(http)
            with pytest.raises(ProviderFailure, match="multiple recordings"):
                await recall.completed_transcript("bd928c95-25e9-49b0-aa51-96921e6425a8")
            assert len(requests) == 1 and requests[0].method == "GET"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "receipt",
    [
        {},
        {"task_id": "different", "status": "running"},
        {"task_id": "expected", "status": "invented"},
    ],
)
def test_subject_adapter_rejects_invalid_or_mismatched_task_receipts(receipt):
    from apps.orchestrator.agents.research import AgentFailure
    from apps.orchestrator.meetings.subject_client import SubjectResearchClient

    with pytest.raises(AgentFailure, match="invalid task receipt"):
        SubjectResearchClient.task_result(receipt, "expected")


def test_subject_submit_missing_capability_is_explicitly_blocked():
    from apps.orchestrator.coordinator import NotConfigured
    from apps.orchestrator.meetings.subject_client import SubjectResearchClient

    async def scenario():
        async with httpx.AsyncClient(
            base_url="http://research",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(404, json={"error": "Not found"}),
            ),
        ) as http:
            subject = SubjectResearchClient(http)
            with pytest.raises(NotConfigured, match="Deploy"):
                await subject.submit(
                    {
                        "id": "bd928c95-25e9-49b0-aa51-96921e6425a8",
                        "name": "Linear",
                        "kind": "company",
                        "context": "Mentioned product",
                        "evidence": {"segment_id": "1", "quote": "We are evaluating Linear."},
                    }
                )

    asyncio.run(scenario())
