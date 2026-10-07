"""Read-only and mocked enrollment checks; no local/cloud network mutations."""

import argparse
import subprocess

import pytest
import tailscale_host


@pytest.mark.parametrize("missing", [None, {}])
def test_initial_needs_login_has_no_self_or_current_tailnet(missing):
    value = {
        "BackendState": "NeedsLogin",
        "Self": missing,
        "CurrentTailnet": missing,
        "TailscaleIPs": None,
    }
    assert tailscale_host.public_status(value) == {
        "state": "NeedsLogin",
        "hostname": None,
        "ips": [],
        "tailnet": None,
        "dns_suffix": None,
    }
    tailscale_host.assert_tailnet(value, "neptuneops.com")


def test_login_prints_approval_url_when_initial_daemon_fields_are_null(monkeypatch, capsys):
    pending = {
        "BackendState": "NeedsLogin",
        "Self": None,
        "CurrentTailnet": None,
        "AuthURL": "https://login.tailscale.com/a/approval123",
    }
    monkeypatch.setattr(tailscale_host, "status", lambda: pending)
    calls = []

    def up(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "timeout waiting for login")

    monkeypatch.setattr(tailscale_host.subprocess, "run", up)
    tailscale_host.join(
        argparse.Namespace(
            tailnet="neptuneops.com", hostname="toir-hackathon", secret_id=None, region="us-east-1"
        )
    )
    output = capsys.readouterr().out
    assert "LOGIN_REQUIRED https://login.tailscale.com/a/approval123" in output
    assert "TAILSCALE_CONNECTED" not in output
    assert "--shields-up=true" in calls[0]


def test_running_state_without_tailnet_does_not_bypass_membership_check():
    with pytest.raises(RuntimeError, match="expected 'neptuneops.com'"):
        tailscale_host.assert_tailnet(
            {"BackendState": "Running", "CurrentTailnet": None}, "neptuneops.com"
        )


@pytest.mark.parametrize("suffix", [".:53 {", "evil.ts.net\n.:53", "*.ts.net", "ts.net"])
def test_dns_zone_cannot_expand_beyond_one_literal_tailnet_suffix(suffix):
    with pytest.raises(ValueError, match="exact tailnet"):
        tailscale_host.dns_manifest(suffix)


def dns_setup(monkeypatch):
    import json

    monkeypatch.setattr(
        tailscale_host,
        "status",
        lambda: {
            "BackendState": "Running",
            "CurrentTailnet": {"Name": "neptuneops.com", "MagicDNSSuffix": "taild4c940.ts.net"},
        },
    )
    answers = iter(
        [
            {"data": {"Corefile": "import /etc/coredns/custom/*.server"}},
            {
                "spec": {
                    "template": {
                        "spec": {
                            "volumes": [
                                {"configMap": {"name": "coredns-custom", "optional": True}},
                            ]
                        }
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(tailscale_host, "command", lambda *args: json.dumps(next(answers)))
    return argparse.Namespace(
        tailnet="neptuneops.com",
        dns_suffix="taild4c940.ts.net",
        peer_name="spark.taild4c940.ts.net",
    )


def test_dns_config_is_applied_only_after_direct_pod_resolver_check(monkeypatch):
    import json

    args = dns_setup(monkeypatch)
    actions = []

    def probe(program):
        compile(program, "<pod-dns-probe>", "exec")
        actions.append("pod probe")
        return '{"dns_ok": true}'

    def apply(argv, **kwargs):
        actions.append("apply")
        manifest = json.loads(kwargs["input"])
        assert manifest["metadata"]["name"] == "coredns-custom"
        assert set(manifest["data"]) == {"tailnet.server"}
        assert manifest["data"]["tailnet.server"].startswith("taild4c940.ts.net:53 {")
        assert "--force-conflicts" not in argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tailscale_host, "pod_python", probe)
    monkeypatch.setattr(tailscale_host.subprocess, "run", apply)
    tailscale_host.configure_dns(args)
    assert actions == ["pod probe", "apply", "pod probe"]


def test_unreachable_magicdns_keeps_cluster_dns_unchanged(monkeypatch):
    args = dns_setup(monkeypatch)

    def probe(_):
        raise subprocess.CalledProcessError(1, "pod DNS probe")

    monkeypatch.setattr(tailscale_host, "pod_python", probe)
    monkeypatch.setattr(
        tailscale_host.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Must not apply before pod DNS works"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        tailscale_host.configure_dns(args)


def test_tcp_verify_reports_both_scopes_when_database_refuses_host_connection(monkeypatch, capsys):
    import json

    monkeypatch.setattr(
        tailscale_host,
        "status",
        lambda: {
            "BackendState": "Running",
            "CurrentTailnet": {"Name": "neptuneops.com"},
        },
    )

    def host(*argv):
        if argv[0] == "sysctl":
            return "1"
        if argv[0] == "ip":
            return "100.87.113.122 dev tailscale0"
        if argv[0] == "python3":
            compile(argv[-1], "<tcp-probe>", "exec")
            return json.dumps(
                {
                    "tcp_connected": False,
                    "error_class": "ConnectionRefusedError",
                    "errno": 111,
                    "elapsed_ms": 100,
                }
            )
        return ""

    pod_calls = []

    def pod(program):
        pod_calls.append(program)
        return json.dumps(
            {
                "tcp_connected": False,
                "error_class": "ConnectionRefusedError",
                "errno": 111,
                "elapsed_ms": 120,
            }
        )

    monkeypatch.setattr(tailscale_host, "command", host)
    monkeypatch.setattr(tailscale_host, "pod_python", pod)
    with pytest.raises(RuntimeError, match="host and pod outcomes"):
        tailscale_host.verify(
            argparse.Namespace(tailnet="neptuneops.com", peer="100.87.113.122", port=5432)
        )
    lines = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    outcomes = [line for line in lines if "scope" in line]
    assert len(pod_calls) == 1
    assert [outcome["scope"] for outcome in outcomes] == ["host", "coordinator_pod"]
    assert all(outcome["errno"] == 111 for outcome in outcomes)
