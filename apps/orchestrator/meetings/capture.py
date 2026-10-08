"""Durable meeting intake and authenticated, deduplicated webhook inbox."""

import hashlib
import json
import os
import time

from apps.orchestrator.meetings.demo import demo_transcript
from apps.orchestrator.meetings.models import AnalysisRequest
from apps.orchestrator.meetings.providers import transcript_segments, verify_webhook
from apps.orchestrator.models.research import now
from apps.orchestrator.storage.ports import Conflict


class MeetingCapture:
    def __init__(self, store, recall):
        self.store, self.recall = store, recall
        self.owner = os.getenv("MEETING_OWNER_EMAIL", "curran@toirinc.com")

    async def create(self, source, body, key, actor):
        data = body.model_dump(mode="json") if body else {}
        fingerprint = hashlib.sha256(
            json.dumps([source, data], sort_keys=True).encode()
        ).hexdigest()
        async with self.store.transaction() as tx:
            existing = await tx.get("meeting", key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("This request key was already used for a different call")
                return existing
            if source == "zoom" and self.recall.missing:
                raise Conflict("Connect Recall and a public webhook URL before joining Zoom")
            request = demo_transcript() if source == "demo" else body
            meeting = {
                "id": key,
                "fingerprint": fingerprint,
                "title": request.title,
                "source": source,
                "owner_email": self.owner,
                "created_by": actor["email"],
                "created_at": now(),
                "status": "scheduled" if source == "zoom" else "analysis_queued",
                "transcript": []
                if source == "zoom"
                else [s.model_dump() for s in request.segments],
                "research": [],
                "notes": None,
                "error": None,
                "bot_id": None,
                "consent_confirmed": source == "demo" or body.consent_confirmed,
                "join_attempts": 0,
                "next_attempt": 0,
            }
            if source == "zoom":
                meeting.update(
                    meeting_url=body.meeting_url,
                    join_at=body.join_at.isoformat() if body.join_at else now(),
                )
            await tx.put("meeting", key, meeting)
            return meeting

    async def webhook(self, body, headers, channel):
        secret = (
            self.recall.workspace_secret if channel == "realtime" else self.recall.dashboard_secret
        )
        identifier = verify_webhook(body, headers, secret)
        try:
            payload = json.loads(body)
            event = payload["event"]
            bot = payload["data"]["bot"]["id"]
            if not isinstance(event, str) or not isinstance(bot, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("Invalid Recall webhook payload") from None
        if channel == "realtime" and event != "transcript.data":
            raise ValueError("Unexpected realtime event")
        digest = hashlib.sha256(body).hexdigest()
        async with self.store.transaction() as tx:
            existing = await tx.get("event", identifier)
            if existing:
                if existing["digest"] != digest:
                    raise Conflict("A webhook ID was reused with a different payload")
                return
            await tx.put(
                "event",
                identifier,
                {
                    "id": identifier,
                    "digest": digest,
                    "payload": payload,
                    "status": "queued",
                    "attempts": 0,
                    "next_attempt": 0,
                },
            )

    async def process_event(self, item):
        payload = item["payload"]
        event, data = payload["event"], payload["data"]
        async with self.store.transaction() as tx:
            meetings = await tx.list("meeting")
        meeting = next((m for m in meetings if m.get("bot_id") == data["bot"]["id"]), None)
        if not meeting:
            metadata_id = data["bot"].get("metadata", {}).get("toir_meeting_id")
            meeting = next(
                (
                    m
                    for m in meetings
                    if m["id"] == metadata_id and m["source"] == "zoom" and not m.get("bot_id")
                ),
                None,
            )
        if not meeting:
            return  # An unrelated bot in the same Recall workspace.
        segments = None
        if event == "transcript.done" and not meeting.get("final_transcript"):
            segments = await self.recall.transcript(data["transcript"]["id"])
            AnalysisRequest(title=meeting["title"], segments=segments)
        async with self.store.transaction() as tx:
            current = await tx.get("meeting", meeting["id"])
            current["bot_id"] = data["bot"]["id"]
            if event == "transcript.data" and not current.get("final_transcript"):
                parts = transcript_segments([data["data"]])
                if parts:
                    segment = parts[0].model_dump()
                    segment["id"] = hashlib.sha256(
                        json.dumps(segment, sort_keys=True).encode()
                    ).hexdigest()
                    if not any(s["id"] == segment["id"] for s in current["transcript"]):
                        if (
                            len(current["transcript"]) >= 2000
                            or sum(len(s["text"]) for s in current["transcript"])
                            + len(segment["text"])
                            > 120000
                        ):
                            raise ValueError("Transcript exceeds the analysis limit")
                        current["transcript"].append(segment)
                        current["transcript"].sort(key=lambda s: s["start"])
            elif segments is not None and not current.get("final_transcript"):
                current.update(
                    transcript=[s.model_dump() for s in segments],
                    final_transcript=True,
                    status="analysis_queued",
                    error=None,
                )
            elif event in {"bot.fatal", "transcript.failed", "recording.failed"}:
                if not current.get("notes"):
                    current.update(
                        status="capture_failed",
                        error="Zoom capture failed. "
                        "Check the Recall dashboard; saved transcript segments are retained.",
                    )
            elif event in {
                "bot.joining_call",
                "bot.in_waiting_room",
                "bot.in_call_not_recording",
                "bot.in_call_recording",
                "bot.call_ended",
                "bot.done",
            } and current["status"] in {
                "scheduled",
                "joining",
                "joining_call",
                "in_waiting_room",
                "in_call_not_recording",
                "in_call_recording",
                "call_ended",
                "done",
                "join_unknown",
            }:
                stamp = data.get("data", {}).get("updated_at", "")
                if stamp >= current.get("bot_updated_at", ""):
                    current.update(status=event[4:], bot_updated_at=stamp)
            await tx.put("meeting", current["id"], current)

    async def event_tick(self):
        events = await self.store.list("event")
        for item in events:
            if item["status"] != "queued" or item["next_attempt"] > time.time():
                continue
            try:
                await self.process_event(item)
                item["status"] = "done"
            except Exception:
                item["attempts"] += 1
                item["next_attempt"] = time.time() + min(300, 2 ** item["attempts"])
                if item["attempts"] >= 8:
                    item["status"] = "failed"
                    # Keep a visible failure instead of silently dropping a final transcript.
                    bot = item["payload"]["data"]["bot"]["id"]
                    async with self.store.transaction() as tx:
                        for meeting in await tx.list("meeting"):
                            if meeting.get("bot_id") == bot and not meeting.get("notes"):
                                meeting.update(
                                    status="capture_failed",
                                    error="A capture event failed. "
                                    "Retry capture processing from this meeting.",
                                )
                                await tx.put("meeting", meeting["id"], meeting)
            async with self.store.transaction() as tx:
                await tx.put("event", item["id"], item)
