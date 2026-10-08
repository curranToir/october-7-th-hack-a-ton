"""One-shot issue publication and read-only reconciliation of uncertain writes."""

import os
import re

import httpx

from apps.orchestrator.meetings.github_scalekit import ScalekitGitHub
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
        self.scalekit = ScalekitGitHub(self.repository, self.client)

    @property
    def configured(self):
        return bool(self.token) or self.scalekit.ready_value

    @property
    def readiness_error(self):
        return None if self.token else self.scalekit.error

    async def ready(self):
        return bool(self.token) or await self.scalekit.ready()

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def publish(self, task):
        if task["repository"] != self.repository or not await self.ready():
            raise ProviderFailure(
                "GitHub credentials or the approved repository need configuration"
            )
        if not self.token:
            owner, repo = self.repository.split("/")
            receipt = await self.scalekit.execute(
                "github_issue_create",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": task["title"],
                    "body": task["body"],
                },
            )
            return self.issue_url(receipt, task["repository"])
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
            return self.issue_url(result.json(), task["repository"])
        except ValueError:
            raise OutcomeUnknown(
                "GitHub accepted the request but returned an invalid receipt"
            ) from None

    @staticmethod
    def issue_url(receipt, repository):
        number = receipt.get("number") if isinstance(receipt, dict) else None
        if type(number) is not int or number < 1:
            raise OutcomeUnknown("GitHub accepted the request but returned an invalid receipt")
        return f"https://github.com/{repository}/issues/{number}"

    async def reconcile(self, task):
        if task["repository"] != self.repository or not await self.ready():
            raise ProviderFailure("Configure GitHub credentials first")
        marker = f"<!-- toir-meeting-task:{task['id']} -->"
        try:
            for page in range(1, 11):
                params = {
                    "state": "all",
                    "sort": "created",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                    "since": task["decided_at"],
                }
                if self.token:
                    result = await self.client.get(
                        f"https://api.github.com/repos/{task['repository']}/issues",
                        headers=self.headers(),
                        params=params,
                    )
                    result.raise_for_status()
                    issues = result.json()
                else:
                    owner, repo = self.repository.split("/")
                    issues = await self.scalekit.execute(
                        "github_issues_list",
                        {
                            "owner": owner,
                            "repo": repo,
                            **params,
                        },
                    )
                    if isinstance(issues, dict):
                        # Scalekit's protobuf Struct wraps GitHub array responses.
                        lists = [value for value in issues.values() if isinstance(value, list)]
                        issues = lists[0] if len(lists) == 1 else None
                if not isinstance(issues, list) or any(not isinstance(i, dict) for i in issues):
                    raise ValueError()
                for issue in issues:
                    if "pull_request" not in issue and marker in (issue.get("body") or ""):
                        return self.issue_url(issue, task["repository"])
                if len(issues) < 100:
                    break
        except (httpx.HTTPError, ValueError, KeyError, OutcomeUnknown):
            raise ProviderFailure(
                "GitHub reconciliation failed; publication remains uncertain"
            ) from None
        return None

    async def close(self):
        await self.client.aclose()
