"""Verify Recall's raw signed inbox, deduplication and final transcript authority."""

import asyncio
import base64
import hmac
import json
import time

import pytest

from apps.orchestrator.meetings.models import MeetingCreate
from apps.orchestrator.meetings.providers import verify_webhook
from apps.orchestrator.storage.ports import Conflict
from tooling.tests.test_meetings_workflow import ACTOR, SECRET, meeting_stack


def signed(body, *, identifier="event-1", prefix="webhook", timestamp=None, secret=SECRET):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.digest(
        base64.b64decode(secret[6:]),
        f"{identifier}.{timestamp}.".encode() + body,
        "sha256",
    )
    return {
        f"{prefix}-id": identifier,
        f"{prefix}-timestamp": timestamp,
        f"{prefix}-signature": "v1," + base64.b64encode(digest).decode(),
    }


def event_body(event, data=None):
    return json.dumps(
        {"event": event, "data": {"bot": {"id": "fixture-bot"}, **(data or {})}}
    ).encode()


@pytest.mark.parametrize("prefix", ["webhook", "svix"])
def test_both_signature_header_families_verify_the_exact_body(prefix):
    body = b'{ "event": "transcript.data", "data": {"text": "Hello"} }\n'
    headers = signed(body, prefix=prefix)
    assert verify_webhook(body, headers, SECRET) == "event-1"
    with pytest.raises(PermissionError):
        verify_webhook(json.dumps(json.loads(body)).encode(), headers, SECRET)
    with pytest.raises(PermissionError):
        verify_webhook(body + b" ", headers, SECRET)
    # Svix can include both the previous and the rotated signing key.
    headers[f"{prefix}-signature"] = "v1,old-signature " + headers[f"{prefix}-signature"]
    assert verify_webhook(body, headers, SECRET) == "event-1"


@pytest.mark.parametrize(
    "mutation", ["stale", "future", "unknown-version", "bad-secret", "missing"]
)
def test_invalid_or_expired_signatures_are_rejected(mutation):
    body = event_body("bot.done")
    timestamp = int(time.time()) + {"stale": -600, "future": 600}.get(mutation, 0)
    headers = signed(body, timestamp=timestamp)
    secret = SECRET
    if mutation == "unknown-version":
        headers["webhook-signature"] = headers["webhook-signature"].replace("v1,", "v2,")
    elif mutation == "bad-secret":
        secret = "whsec_" + base64.b64encode(b"other-signing-secret-value").decode()
    elif mutation == "missing":
        del headers["webhook-id"]
    with pytest.raises(PermissionError):
        verify_webhook(body, headers, secret)


def test_webhook_inbox_persists_one_event_and_rejects_reused_ids_and_forgery(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            body = event_body("bot.done")
            for _ in range(3):
                await stack.service.capture.webhook(body, signed(body), "dashboard")
            assert len(await stack.store.list("event")) == 1
            changed = event_body("bot.fatal")
            with pytest.raises(Conflict, match="different payload"):
                await stack.service.capture.webhook(changed, signed(changed), "dashboard")
            with pytest.raises(PermissionError):
                await stack.service.capture.webhook(changed, signed(body), "dashboard")
            with pytest.raises(ValueError, match="Unexpected realtime"):
                await stack.service.capture.webhook(body, signed(body), "realtime")
            assert len(await stack.store.list("event")) == 1
            await stack.service.capture.event_tick()
            assert (await stack.store.list("event"))[0]["status"] == "done"
            assert not await stack.store.list("meeting")  # Unrelated bots cannot create meetings.

    asyncio.run(scenario())


def test_realtime_is_ordered_and_final_transcript_wins_over_late_events(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://us02web.zoom.us/j/12345?pwd=invite",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "call-1", ACTOR)
            await stack.service.processing.capture_tick()

            async def dispatch(event, identifier, data=None, channel="dashboard"):
                body = event_body(event, data)
                await stack.service.capture.webhook(
                    body, signed(body, identifier=identifier), channel
                )
                await stack.service.capture.event_tick()

            for index, start in enumerate([20, 1, 20]):
                await dispatch(
                    "transcript.data",
                    f"live-{index}",
                    {
                        "data": {
                            "participant": {"name": "Customer"},
                            "words": [
                                {
                                    "text": f"Utterance at {start}",
                                    "start_timestamp": {"relative": start},
                                }
                            ],
                        }
                    },
                    "realtime",
                )
            current = await stack.store.get("meeting", "call-1")
            assert [s["start"] for s in current["transcript"]] == [1, 20]
            await dispatch("bot.done", "done", {"data": {"updated_at": "2026-10-07T20:00:00Z"}})
            await dispatch("transcript.done", "final", {"transcript": {"id": "transcript-1"}})
            final = await stack.store.get("meeting", "call-1")
            assert final["status"] == "analysis_queued" and final["final_transcript"] is True
            assert final["transcript"] == [s.model_dump() for s in stack.recall.final_segments]
            await dispatch(
                "transcript.data",
                "late-live",
                {
                    "data": {
                        "participant": {"name": "Customer"},
                        "words": [
                            {"text": "Late duplicate words", "start_timestamp": {"relative": 99}}
                        ],
                    }
                },
                "realtime",
            )
            await dispatch(
                "bot.done", "late-done", {"data": {"updated_at": "2026-10-07T21:00:00Z"}}
            )
            assert await stack.store.get("meeting", "call-1") == final
            await stack.service.processing.analysis_tick()
            task = (await stack.store.list("task"))[0]
            await dispatch(
                "transcript.done", "replayed-final", {"transcript": {"id": "transcript-1"}}
            )
            await stack.service.processing.analysis_tick()
            assert await stack.store.list("task") == [task]
            assert (await stack.store.get("meeting", "call-1"))["status"] == "ready"

    asyncio.run(scenario())


def test_duplicate_final_event_cannot_restart_analysis_while_agent_is_running(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://zoom.us/j/12345",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "call-1", ACTOR)
            await stack.service.processing.capture_tick()
            body = event_body("transcript.done", {"transcript": {"id": "transcript-1"}})
            await stack.service.capture.webhook(
                body, signed(body, identifier="final-1"), "dashboard"
            )
            await stack.service.capture.event_tick()
            started, release = asyncio.Event(), asyncio.Event()
            original = stack.service.agent.analyze

            async def paused_analysis(request):
                started.set()
                await release.wait()
                return await original(request)

            stack.service.agent.analyze = paused_analysis
            worker = asyncio.create_task(stack.service.processing.analysis_tick())
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                await stack.service.capture.webhook(
                    body,
                    signed(body, identifier="final-2"),
                    "dashboard",
                )
                await stack.service.capture.event_tick()
                assert (await stack.store.get("meeting", "call-1"))["status"] == "analyzing"
            finally:
                release.set()
                await worker
            assert len(await stack.store.list("task")) == 1
            assert (await stack.store.get("meeting", "call-1"))["status"] == "ready"

    asyncio.run(scenario())


def test_lost_join_receipt_cannot_downgrade_webhook_proven_finalized_capture(tmp_path):
    from apps.orchestrator.meetings.providers import OutcomeUnknown

    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://zoom.us/j/12345",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "call-1", ACTOR)
            started, release = asyncio.Event(), asyncio.Event()

            async def lost_receipt(meeting):
                started.set()
                await release.wait()
                raise OutcomeUnknown("Recall accepted the bot but the HTTP receipt was lost")

            stack.recall.join = lost_receipt
            worker = asyncio.create_task(stack.service.processing.capture_tick())
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                body = json.dumps(
                    {
                        "event": "transcript.done",
                        "data": {
                            "bot": {"id": "fixture-bot", "metadata": {"toir_meeting_id": "call-1"}},
                            "transcript": {"id": "transcript-1"},
                        },
                    }
                ).encode()
                await stack.service.capture.webhook(body, signed(body), "dashboard")
                await stack.service.capture.event_tick()
                assert (await stack.store.get("meeting", "call-1"))["status"] == "analysis_queued"
            finally:
                release.set()
                await worker
            current = await stack.store.get("meeting", "call-1")
            assert current["status"] == "analysis_queued"
            assert current["bot_id"] == "fixture-bot" and current["final_transcript"] is True
            assert current["error"] is None
            assert current["transcript"] == [s.model_dump() for s in stack.recall.final_segments]

    asyncio.run(scenario())


def test_unrelated_or_stale_bot_events_cannot_corrupt_lifecycle_status(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://zoom.us/j/12345",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "call-1", ACTOR)
            await stack.service.processing.capture_tick()
            for index, (event, timestamp) in enumerate(
                [
                    ("bot.in_call_recording", "2026-10-07T12:00:00Z"),
                    ("bot.audio_separate_raw.data", "2026-10-07T12:00:30Z"),
                    ("bot.in_waiting_room", "2026-10-07T11:59:00Z"),
                ]
            ):
                body = event_body(event, {"data": {"updated_at": timestamp}})
                await stack.service.capture.webhook(
                    body,
                    signed(body, identifier=f"lifecycle-{index}"),
                    "dashboard",
                )
                await stack.service.capture.event_tick()
                current = await stack.store.get("meeting", "call-1")
                assert current["status"] == "in_call_recording"
                assert current["bot_updated_at"] == "2026-10-07T12:00:00Z"

    asyncio.run(scenario())
