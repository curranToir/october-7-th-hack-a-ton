"""Recall transport and signature verification; no provider secrets reach the browser."""

import base64
import hmac
import json
import os
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from apps.orchestrator.meetings.models import Segment


class ProviderFailure(Exception):
    pass


class RetryLater(ProviderFailure):
    def __init__(self, delay=30):
        self.delay = delay
        super().__init__("The meeting provider is busy; joining will retry shortly")


class OutcomeUnknown(ProviderFailure):
    pass


def verify_webhook(body: bytes, headers, secret: str) -> str:
    identifier = headers.get("webhook-id") or headers.get("svix-id", "")
    timestamp = headers.get("webhook-timestamp") or headers.get("svix-timestamp", "")
    signatures = headers.get("webhook-signature") or headers.get("svix-signature", "")
    try:
        if not identifier or len(identifier) > 256 or not secret.startswith("whsec_"):
            raise ValueError()
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError()
        key = base64.b64decode(secret[6:], validate=True)
        if len(key) < 16:
            raise ValueError()
        expected = base64.b64encode(
            hmac.digest(
                key,
                f"{identifier}.{timestamp}.".encode() + body,
                "sha256",
            )
        ).decode()
        if not any(hmac.compare_digest(s, f"v1,{expected}") for s in signatures.split()):
            raise ValueError()
        return identifier
    except (ValueError, TypeError):
        raise PermissionError("Invalid or expired meeting webhook signature") from None


def transcript_segments(payload: list[dict]) -> list[Segment]:
    segments = []
    for index, item in enumerate(payload):
        words = item.get("words", [])
        text = " ".join(w["text"] for w in words).strip()
        if not text:
            continue
        start = words[0].get("start_timestamp", {}).get("relative", 0)
        participant = item.get("participant") or {}
        segments.append(
            Segment(
                id=f"utterance-{index}",
                speaker=participant.get("name") or "Unknown speaker",
                start=start,
                text=text,
            )
        )
    return segments


class RecallClient:
    def __init__(self, client=None):
        self.key = os.getenv("RECALL_API_KEY", "")
        self.region = os.getenv("RECALL_REGION", "us-west-2")
        if self.region not in {"us-west-2", "us-east-1", "eu-central-1", "ap-northeast-1"}:
            raise ValueError("Unsupported Recall region")
        self.base = f"https://{self.region}.recall.ai/api/v1"
        self.webhook_base = os.getenv("MEETING_WEBHOOK_BASE_URL", "").rstrip("/")
        self.workspace_secret = os.getenv("RECALL_WORKSPACE_VERIFICATION_SECRET", "")
        self.dashboard_secret = os.getenv("RECALL_SVIX_WEBHOOK_SECRET", "") or self.workspace_secret
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)

    @property
    def missing(self):
        missing = []
        if not self.key:
            missing.append("RECALL_API_KEY")
        if not self.workspace_secret.startswith("whsec_"):
            missing.append("RECALL_WORKSPACE_VERIFICATION_SECRET")
        p = urlsplit(self.webhook_base)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.path
            or p.query
            or p.fragment
        ):
            missing.append("MEETING_WEBHOOK_BASE_URL (public HTTPS origin)")
        return missing

    async def request(self, method, path, **kwargs):
        try:
            result = await self.client.request(
                method,
                self.base + path,
                headers={"Authorization": self.key, "Accept": "application/json"},
                **kwargs,
            )
        except httpx.HTTPError:
            if method == "POST":
                raise OutcomeUnknown(
                    "Bot request outcome is unknown. Check Recall before scheduling another bot."
                ) from None
            raise ProviderFailure("Recall is temporarily unreachable") from None
        if result.status_code in {429, 507}:
            raise RetryLater(30 if result.status_code == 507 else 60)
        if result.status_code >= 500 and method == "POST":
            raise OutcomeUnknown("Recall outcome is unknown; check the provider dashboard")
        if not result.is_success:
            raise ProviderFailure(f"Recall request failed (HTTP {result.status_code})")
        try:
            payload = result.json()
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except ValueError:
            if method == "POST":
                raise OutcomeUnknown(
                    "Recall returned an invalid bot receipt; check the dashboard"
                ) from None
            raise ProviderFailure("Recall returned an invalid response") from None

    async def join(self, meeting):
        if self.missing:
            raise ProviderFailure("Configure the Recall connection before joining Zoom")
        return await self.request(
            "POST",
            "/bot/",
            json={
                "meeting_url": meeting["meeting_url"],
                "join_at": meeting["join_at"],
                "bot_name": "Toir · Customer notes",
                "metadata": {"toir_meeting_id": meeting["id"], "owner": meeting["owner_email"]},
                "chat": {
                    "on_bot_join": {
                        "send_to": "everyone",
                        "message": "Toir is recording and transcribing this call. "
                        "A person will review product issues before sharing them with engineering.",
                    }
                },
                "recording_config": {
                    "transcript": {
                        "provider": {
                            "recallai_streaming": {
                                "mode": "prioritize_low_latency",
                                "language_code": "en",
                            }
                        },
                        "diarization": {"use_separate_streams_when_available": True},
                    },
                    "realtime_endpoints": [
                        {
                            "type": "webhook",
                            "events": ["transcript.data"],
                            "url": self.webhook_base + "/api/meetings/webhooks/recall/realtime",
                        }
                    ],
                },
            },
        )

    async def transcript(self, transcript_id):
        identifier = str(UUID(transcript_id))
        artifact = await self.request("GET", f"/transcript/{identifier}/")
        url = artifact.get("data", {}).get("download_url", "")
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or not host.endswith((".amazonaws.com", ".recall.ai"))
        ):
            raise ProviderFailure("Recall returned an unsupported transcript download host")
        # Presigned download URL only; never send the Recall authorization header here.
        try:
            async with self.client.stream("GET", url, headers={"Authorization": ""}) as result:
                result.raise_for_status()
                body = bytearray()
                async for chunk in result.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 4_000_000:
                        raise ProviderFailure("Transcript exceeds the 4 MB download limit")
            payload = json.loads(body)
            return transcript_segments(payload)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderFailure("Unable to download a valid completed transcript") from None

    async def completed_transcript(self, bot_id):
        artifact = await self.request("GET", f"/bot/{UUID(bot_id)}/")
        recordings = artifact.get("recordings", [])
        if len(recordings) > 1:
            raise ProviderFailure(
                "This call has multiple recordings; import a combined transcript for analysis"
            )
        if not recordings:
            return None
        transcript = recordings[0].get("media_shortcuts", {}).get("transcript") or {}
        if not transcript.get("data", {}).get("download_url") or not transcript.get("id"):
            return None
        return await self.transcript(transcript["id"])

    async def close(self):
        await self.client.aclose()
