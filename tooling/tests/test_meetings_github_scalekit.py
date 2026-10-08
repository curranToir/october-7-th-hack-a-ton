"""Exercise connected-account readiness and approval-only GitHub issue transport."""

import asyncio
import json

import httpx
import pytest

from apps.orchestrator.meetings.github import GitHubIssues
from apps.orchestrator.meetings.models import TaskDecision
from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure
from apps.orchestrator.storage.ports import Conflict
from tooling.tests.test_meetings_workflow import ACTOR, demo_task, meeting_stack


def settings(monkeypatch):
    monkeypatch.delenv("MEETING_GITHUB_TOKEN", raising=False)
    for name, value in {
        "MEETING_GITHUB_REPOSITORY": "example/product",
        "MEETING_GITHUB_CONNECTION_NAME": "github-connect",
        "MEETING_GITHUB_ACCOUNT_ID": "curran@toirinc.com",
        "SCALEKIT_ENVIRONMENT_URL": "https://scalekit.example",
        "SCALEKIT_CLIENT_ID": "fixture-client",
        "SCALEKIT_CLIENT_SECRET": "fixture-secret",
    }.items():
        monkeypatch.setenv(name, value)


def task():
    return {
        "id": "task-1",
        "repository": "example/product",
        "title": "Reviewed title",
        "body": "Reviewed body\n<!-- toir-meeting-task:task-1 -->",
        "decided_at": "2026-10-07T12:00:00Z",
    }


class Provider:
    def __init__(self):
        self.requests = []
        self.tools = {"github_issue_create", "github_issues_list", "github_repo_get"}
        self.status = "ACTIVE"
        self.create_response = httpx.Response(200, json={"data": {"number": 42}})
        self.create_failure = None
        self.repo = {
            "full_name": "example/product",
            "has_issues": True,
            "archived": False,
            "permissions": {"admin": True, "push": True, "pull": True},
        }
        self.issues = {
            "items": [
                {"number": 9, "body": task()["body"], "pull_request": {}},
                {"number": 42, "body": task()["body"]},
            ]
        }

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "scalekit.example"
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200, json={"access_token": "fixture-server-token", "expires_in": 300}
            )
        assert request.headers["authorization"] == "Bearer fixture-server-token"
        if request.url.path == "/api/v1/connected_accounts":
            assert request.url.params["identifier"] == ACTOR["email"]
            assert request.url.params["connector"] == "github-connect"
            return httpx.Response(
                200,
                json={
                    "connected_accounts": [
                        {
                            "connector": "github-connect",
                            "identifier": ACTOR["email"],
                            "status": self.status,
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/tools/scoped":
            assert request.url.params["filter.connection_names"] == "github-connect"
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "tool": {
                                "definition": {
                                    "name": name,
                                    "input_schema": {"required": ["owner", "repo", "title"]},
                                }
                            }
                        }
                        for name in self.tools
                    ]
                },
            )
        payload = json.loads(request.content)
        assert request.url.path == "/api/v1/execute_tool"
        assert payload["identifier"] == ACTOR["email"]
        assert payload["connector"] == "github-connect"
        if payload["tool_name"] == "github_repo_get":
            return httpx.Response(200, json={"data": self.repo})
        if payload["tool_name"] == "github_issues_list":
            return httpx.Response(200, json={"data": self.issues})
        assert payload["tool_name"] == "github_issue_create"
        if self.create_failure:
            raise self.create_failure
        return self.create_response

    def executions(self):
        return [
            json.loads(r.content) for r in self.requests if r.url.path == "/api/v1/execute_tool"
        ]


def test_scalekit_readiness_only_reads_and_publication_uses_exact_reviewed_payload(monkeypatch):
    settings(monkeypatch)

    async def scenario():
        provider = Provider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            github = GitHubIssues(client)
            assert not github.configured
            assert await github.ready()
            assert github.configured
            assert [p["tool_name"] for p in provider.executions()] == ["github_repo_get"]
            assert await github.publish(task()) == "https://github.com/example/product/issues/42"
            assert provider.executions()[-1] == {
                "tool_name": "github_issue_create",
                "connector": "github-connect",
                "identifier": ACTOR["email"],
                "params": {
                    "owner": "example",
                    "repo": "product",
                    "title": task()["title"],
                    "body": task()["body"],
                },
            }
            assert await github.reconcile(task()) == "https://github.com/example/product/issues/42"
            assert [p["tool_name"] for p in provider.executions()].count("github_issue_create") == 1
            assert provider.executions()[-1]["params"]["since"] == task()["decided_at"]
            assert provider.executions()[-1]["tool_name"] == "github_issues_list"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["pending", "missing-tool", "wrong-repo", "disabled-issues"])
def test_unready_connection_cannot_publish(monkeypatch, failure):
    settings(monkeypatch)

    async def scenario():
        provider = Provider()
        if failure == "pending":
            provider.status = "PENDING_AUTH"
        elif failure == "missing-tool":
            provider.tools.remove("github_issue_create")
        elif failure == "wrong-repo":
            provider.repo["full_name"] = "example/other"
        else:
            provider.repo["has_issues"] = False
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            github = GitHubIssues(client)
            assert not await github.ready()
            with pytest.raises(ProviderFailure):
                await github.publish(task())
            assert "github_issue_create" not in [p["tool_name"] for p in provider.executions()]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "server", "invalid", "app-error"])
def test_uncertain_scalekit_create_is_never_retried_or_reclassified_as_safe(monkeypatch, failure):
    settings(monkeypatch)

    async def scenario():
        provider = Provider()
        if failure == "timeout":
            provider.create_failure = httpx.ReadTimeout("private-provider-details")
        elif failure == "server":
            provider.create_response = httpx.Response(
                500, json={"message": "private-provider-details"}
            )
        elif failure == "invalid":
            provider.create_response = httpx.Response(200, json={"data": {}})
        else:
            provider.create_response = httpx.Response(
                200, json={"data": {"error": "private-provider-details"}}
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            github = GitHubIssues(client)
            with pytest.raises(OutcomeUnknown) as error:
                await github.publish(task())
            assert "private-provider-details" not in str(error.value)
            assert [p["tool_name"] for p in provider.executions()].count("github_issue_create") == 1

    asyncio.run(scenario())


def test_direct_token_takes_precedence_and_destination_cannot_change(monkeypatch):
    settings(monkeypatch)
    monkeypatch.setenv("MEETING_GITHUB_TOKEN", "fixture-direct-token")

    async def scenario():
        calls = []

        def provider(request):
            calls.append(request)
            assert request.url.host == "api.github.com"
            return httpx.Response(201, json={"number": 42})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            github = GitHubIssues(client)
            assert await github.ready()
            assert not calls
            with pytest.raises(ProviderFailure):
                await github.publish({**task(), "repository": "example/other"})
            assert not calls
            assert await github.publish(task()) == "https://github.com/example/product/issues/42"
            assert len(calls) == 1

    asyncio.run(scenario())


def test_approval_refreshes_github_readiness_outside_record_transaction(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            draft = await demo_task(stack)
            readiness = False

            async def ready():
                # Taking the same store lock proves provider reads are outside the decision lock.
                assert await stack.store.get("task", draft["id"])
                return readiness

            stack.github.ready = ready
            with pytest.raises(Conflict, match="Connect GitHub"):
                await stack.service.tasks.decide(
                    draft["id"],
                    TaskDecision(version=1, decision="approve"),
                    ACTOR,
                )
            assert not await stack.store.list("decision")
            readiness = True
            approved = await stack.service.tasks.decide(
                draft["id"],
                TaskDecision(version=1, decision="approve"),
                ACTOR,
            )
            assert approved["status"] == "approved" and not stack.github.calls

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
