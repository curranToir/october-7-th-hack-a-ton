"""Meeting runtime isolation and staged provider-secret setup."""

import base64
import io
import json
import subprocess
from types import SimpleNamespace

import boto3
import pytest
import remote_apply
import runtime_ops
from releases import ROOT, SERVICES


def test_meeting_worker_is_private_stateless_and_respan_only():
    items = json.loads((ROOT / "infrastructure/deployment/kubernetes/foundation.json").read_text())[
        "items"
    ]
    deployments = {x["metadata"]["name"]: x for x in items if x["kind"] == "Deployment"}
    assert set(SERVICES) == set(remote_apply.SERVICES) == set(deployments)
    pod = deployments["meetings"]["spec"]["template"]["spec"]
    assert not any(v.get("persistentVolumeClaim") for v in pod["volumes"])
    worker = pod["containers"][0]
    assert worker["envFrom"] == [{"secretRef": {"name": "meetings-runtime"}}]
    assert worker["securityContext"]["readOnlyRootFilesystem"] is True
    for name in ("startupProbe", "livenessProbe", "readinessProbe"):
        assert worker[name]["httpGet"]["path"] in {"/health", "/ready"}
    coordinator = deployments["orchestrator"]["spec"]["template"]["spec"]["containers"][0]
    assert {"secretRef": {"name": "meetings-provider", "optional": True}} in coordinator["envFrom"]
    policy = next(x for x in items if x["metadata"]["name"] == "meetings-ingress")
    assert policy["spec"]["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "orchestrator"}}}
    ]
    ingress = next(x for x in items if x["kind"] == "Ingress")
    assert "meetings" not in json.dumps(ingress)


@pytest.mark.parametrize("configured", [False, True])
def test_meeting_provider_secret_stays_out_of_workers_and_runtime_args(monkeypatch, configured):
    config = {"respan_secret": "respan", "scalekit_secret": "scalekit"}
    if configured:
        config["meetings_provider_secret"] = "meeting-provider"
    monkeypatch.setattr(remote_apply, "runtime_config", lambda: config)
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "sqlite")
    values = {
        "respan": {"RESPAN_API_KEY": "respan-test-value"},
        "scalekit": {"SCALEKIT_CLIENT_SECRET": "scalekit-test-value"},
        "meeting-provider": {
            "RECALL_API_KEY": "recall-test-value",
            "RECALL_WORKSPACE_VERIFICATION_SECRET": "whsec_test-value",
            "MEETING_GITHUB_TOKEN": "github-test-value",
        },
    }
    monkeypatch.setattr(remote_apply, "read_secret", lambda name, _: values[name])
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(remote_apply.subprocess, "run", run)
    remote_apply.sync_secrets()
    args, kwargs = calls[0]
    assert "test-value" not in str(args)
    secrets = {
        item["metadata"]["name"]: {k: base64.b64decode(v).decode() for k, v in item["data"].items()}
        for item in json.loads(kwargs["input"])["items"]
        if item["kind"] == "Secret"
    }
    assert secrets["meetings-runtime"] == values["respan"]
    assert secrets["meetings-provider"] == (values["meeting-provider"] if configured else {})
    for name in (
        "orchestrator-runtime",
        "research-runtime",
        "contacts-runtime",
        "meetings-runtime",
    ):
        assert not (values["meeting-provider"].keys() & secrets[name].keys())


def secret_client(monkeypatch, previous):
    saved = []
    client = SimpleNamespace(
        get_secret_value=lambda **_: {"SecretString": json.dumps(previous)},
        put_secret_value=lambda **kwargs: saved.append(kwargs),
        exceptions=SimpleNamespace(
            ResourceNotFoundException=type("MissingSecret", (Exception,), {}),
            InvalidRequestException=type("EmptySecret", (Exception,), {}),
        ),
    )
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    return saved


def test_hidden_meeting_secret_prompt_preserves_prior_connection(monkeypatch, capsys):
    saved = secret_client(monkeypatch, {"RECALL_API_KEY": "recall-test-value"})
    monkeypatch.setattr(
        runtime_ops.getpass,
        "getpass",
        lambda prompt: "github-test-value" if prompt.startswith("MEETING_GITHUB_TOKEN") else "",
    )
    runtime_ops.set_secret(
        SimpleNamespace(region="us-east-1", stack="test"), "meetings-provider", False
    )
    assert json.loads(saved[0]["SecretString"]) == {
        "RECALL_API_KEY": "recall-test-value",
        "MEETING_GITHUB_TOKEN": "github-test-value",
    }
    assert "test-value" not in capsys.readouterr().out


def test_stdin_meeting_secret_replaces_with_explicit_supported_subset(monkeypatch):
    saved = secret_client(monkeypatch, {"RECALL_API_KEY": "previous-value"})
    monkeypatch.setattr(
        runtime_ops.sys, "stdin", io.StringIO('{"MEETING_GITHUB_TOKEN":"new-value"}')
    )
    runtime_ops.set_secret(
        SimpleNamespace(region="us-east-1", stack="test"), "meetings-provider", True
    )
    assert json.loads(saved[0]["SecretString"]) == {"MEETING_GITHUB_TOKEN": "new-value"}


@pytest.mark.parametrize("payload", [{}, {"UNEXPECTED": "credential"}, {"RECALL_API_KEY": ""}])
def test_meeting_secret_rejects_invalid_input_without_sending_it(monkeypatch, payload):
    saved = secret_client(monkeypatch, {})
    monkeypatch.setattr(runtime_ops.sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(ValueError, match="supported fields"):
        runtime_ops.set_secret(
            SimpleNamespace(region="us-east-1", stack="test"), "meetings-provider", True
        )
    assert saved == []
