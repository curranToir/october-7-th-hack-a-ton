"""One-shot issue publication and read-only reconciliation of uncertain writes."""

import os
import re

import httpx

from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure


class GitHubIssues:
    def __init__(self, client=None):
        self.token = os.getenv("MEETING_GITHUB_TOKEN", "")
        self.repository = os.getenv(
            "MEETING_GITHUB_REPOSITORY", "curranToir/october-7-th-hack-a-ton"
        )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ValueError("Expected a GitHub owner/repository")
        self.client = client or httpx.AsyncClient(timeout=25, follow_redirects=False)

    @property
    def configured(self):
        return bool(self.token)

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def publish(self, task):
        if not self.configured or task["repository"] != self.repository:
            raise ProviderFailure(
                "GitHub credentials or the approved repository need configuration"
            )
        try:
            result = await self.client.post(
                f"https://api.github.com/repos/{task['repository']}/issues",
                headers=self.headers(),
                json={"title": task["title"], "body": task["body"]},
            )
        except httpx.HTTPError:
            raise OutcomeUnknown(
                "GitHub may have created this issue. Reconcile before retrying."
            ) from None
        if result.status_code >= 500:
            raise OutcomeUnknown("GitHub returned an uncertain result. Reconcile before retrying.")
        if result.status_code != 201:
            raise ProviderFailure(f"GitHub rejected the issue (HTTP {result.status_code})")
        try:
            number = result.json()["number"]
            if not isinstance(number, int) or number < 1:
                raise ValueError()
            return f"https://github.com/{task['repository']}/issues/{number}"
        except (ValueError, KeyError):
            raise OutcomeUnknown(
                "GitHub accepted the request but returned an invalid receipt"
            ) from None

    async def reconcile(self, task):
        if not self.configured:
            raise ProviderFailure("Configure GitHub credentials first")
        marker = f"<!-- toir-meeting-task:{task['id']} -->"
        try:
            for page in range(1, 11):
                result = await self.client.get(
                    f"https://api.github.com/repos/{task['repository']}/issues",
                    headers=self.headers(),
                    params={
                        "state": "all",
                        "sort": "created",
                        "direction": "desc",
                        "per_page": 100,
                        "page": page,
                        "since": task["decided_at"],
                    },
                )
                result.raise_for_status()
                issues = result.json()
                for issue in issues:
                    if "pull_request" not in issue and marker in (issue.get("body") or ""):
                        return f"https://github.com/{task['repository']}/issues/{issue['number']}"
                if len(issues) < 100:
                    break
        except (httpx.HTTPError, ValueError, KeyError):
            raise ProviderFailure("GitHub reconciliation failed; no issue was created") from None
        return None

    async def close(self):
        await self.client.aclose()
