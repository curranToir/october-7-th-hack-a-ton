"""Runtime deployment boundary tests: credentials, persistence and old release recovery."""

import io
import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest
import remote_apply
from releases import ROOT
from runtime_ops import cloudformation_yaml, protected_changes, stack_update


def test_application_boundaries_and_secret_scope():
    resources = json.loads(
        (ROOT / "infrastructure/deployment/kubernetes/foundation.json").read_text()
    )
    deployments = {
        r["metadata"]["name"]: r for r in resources["items"] if r["kind"] == "Deployment"
    }
    assert set(deployments) == {"web", "api", "orchestrator", "research", "contacts"}
    for name, deployment in deployments.items():
        pod = deployment["spec"]["template"]["spec"]
        assert not pod["automountServiceAccountToken"]
        assert deployment["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 0
        if name in {"web", "api"}:
            assert "envFrom" not in pod["containers"][0]
    orch = deployments["orchestrator"]["spec"]["template"]["spec"]
    research = deployments["research"]["spec"]["template"]["spec"]
    assert any(v.get("persistentVolumeClaim") for v in orch["volumes"])
    assert not any(v.get("persistentVolumeClaim") for v in research["volumes"])
    assert research["containers"][0]["envFrom"] == [{"secretRef": {"name": "research-runtime"}}]
    template = cloudformation_yaml(
        (ROOT / "infrastructure/deployment/cloudformation/stack.yaml").read_text()
    )
    policies = template["Resources"]["HostRole"]["Properties"]["Policies"]
    grants = [
        statement
        for policy in policies
        for statement in policy["PolicyDocument"]["Statement"]
        if statement["Action"] == "secretsmanager:GetSecretValue"
    ]
    assert grants[0]["Resource"][:2] == [{"Ref": "RespanSecret"}, {"Ref": "ScalekitSecret"}]
    assert grants[0]["Resource"][2:] == [
        {"Fn::If": ["HasDatabaseSecret", {"Ref": "DatabaseSecretArn"}, {"Ref": "AWS::NoValue"}]},
        {"Fn::If": ["HasBrainApiSecret", {"Ref": "BrainApiSecretArn"}, {"Ref": "AWS::NoValue"}]},
    ]
    assert "DatabaseSecret" not in template["Resources"]
    assert "BrainApiSecret" not in template["Resources"]
    for name in ("RespanSecret", "ScalekitSecret", "ExaSecret"):
        assert "SecretString" not in template["Resources"][name]["Properties"]
        assert template["Resources"][name]["DeletionPolicy"] == "Retain"


def test_missing_secret_is_setup_state_but_access_denied_is_failure(monkeypatch):
    monkeypatch.setattr(remote_apply, "runtime_config", lambda: {})
    answer = subprocess.CompletedProcess([], 1, "", "ResourceNotFoundException")
    monkeypatch.setattr(remote_apply.subprocess, "run", lambda *a, **k: answer)
    assert remote_apply.read_secret("arn:secret", {"RESPAN_API_KEY"}) == {}
    answer.stderr = "AccessDeniedException sensitive-value"
    with pytest.raises(RuntimeError, match="Cannot read runtime secret") as error:
        remote_apply.read_secret("arn:secret", {"RESPAN_API_KEY"})
    assert "sensitive-value" not in str(error.value)


def test_sync_uses_stdin_not_argv_and_separates_pod_credentials(monkeypatch):
    monkeypatch.setattr(
        remote_apply,
        "runtime_config",
        lambda: {
            "respan_secret": "respan",
            "scalekit_secret": "scalekit",
        },
    )
    monkeypatch.setattr(
        remote_apply,
        "read_secret",
        lambda name, _: (
            {
                "RESPAN_API_KEY": "runtime-token",
            }
            if name == "respan"
            else {"SCALEKIT_CLIENT_SECRET": "connector-token"}
        ),
    )
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(remote_apply.subprocess, "run", run)
    remote_apply.sync_secrets()
    args, kwargs = calls[0]
    assert "--server-side" in args
    assert "runtime-token" not in str(args)
    resources = json.loads(kwargs["input"])["items"]
    secrets = {r["metadata"]["name"]: r["data"] for r in resources if r["kind"] == "Secret"}
    assert set(secrets["orchestrator-runtime"]) == {"RESPAN_API_KEY", "SCALEKIT_CLIENT_SECRET"}
    assert secrets["contacts-runtime"] == secrets["research-runtime"]
    assert set(secrets["research-runtime"]) == {"RESPAN_API_KEY", "SCALEKIT_CLIENT_SECRET"}


def test_old_release_removes_research_but_keeps_persistent_volume(tmp_path, monkeypatch):
    (tmp_path / "manifests.json").write_text(
        json.dumps(
            {
                "items": [
                    {"kind": "Deployment", "metadata": {"name": name}}
                    for name in ("web", "api", "orchestrator")
                ]
            }
        )
    )
    calls = []
    monkeypatch.setattr(remote_apply, "execute", lambda *args: calls.append(args))
    monkeypatch.setattr(remote_apply, "probe_ingress", lambda: None)
    monkeypatch.setattr(
        remote_apply, "sync_secrets", lambda: pytest.fail("Old releases need no secrets")
    )
    remote_apply.apply_release(tmp_path)
    deletes = [call for call in calls if "delete" in call]
    assert len(deletes) == 6
    assert not any("pvc" in call for call in calls)
    assert not any("deployment/research" in call for call in calls)


def databases(path: Path):
    path.mkdir(exist_ok=True)
    db = sqlite3.connect(path / "runs.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO schema_version VALUES(1)")
    db.execute("CREATE TABLE findings(value TEXT)")
    db.execute("INSERT INTO findings VALUES('durable evidence')")
    db.commit()
    with sqlite3.connect(path / "checkpoints.sqlite") as checkpoints:
        checkpoints.execute("CREATE TABLE checkpoint(id TEXT)")
    return db


def test_online_snapshot_includes_uncheckpointed_wal(tmp_path):
    source, destination = tmp_path / "live", tmp_path / "snapshot"
    destination.mkdir()
    live = databases(source)
    assert (source / "runs.sqlite-wal").exists()
    hashes = remote_apply.snapshot_databases(source, destination)
    with sqlite3.connect(destination / "runs.sqlite") as snapshot:
        assert snapshot.execute("SELECT value FROM findings").fetchone()[0] == "durable evidence"
    assert set(hashes) == set(remote_apply.DATABASES)
    live.close()


def test_restore_rejects_tampered_bundle_before_touching_live_database(tmp_path, monkeypatch):
    source, output = tmp_path / "live", tmp_path / "output"
    output.mkdir()
    live = databases(source)
    live.close()
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    backup_id = "20261007T000000Z-" + "1" * 12
    manifest = {
        "version": 1,
        "schema_version": 1,
        "backup_id": backup_id,
        "checksums": {name: "0" * 64 for name in remote_apply.DATABASES},
    }
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in remote_apply.DATABASES:
            tar.add(source / name, arcname=name)
        data = json.dumps(manifest).encode()
        member = tarfile.TarInfo("backup.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError, match="Backup checksum mismatch"):
        remote_apply.unpack_backup(archive, output, backup_id)


def test_update_refuses_host_replacement(tmp_path):
    template = ROOT / "infrastructure/deployment/cloudformation/stack.yaml"

    class FakeAws:
        stack = "test"

        def outputs(self):
            return {"InstanceId": "i-test"}

        def call(self, *args):
            assert "execute-change-set" not in args

        def json(self, *args):
            if "get-template" in args:
                return {"TemplateBody": template.read_text()}
            if "describe-secret" in args:
                return {
                    "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:"
                    + args[-1]
                    + "-abc123"
                }
            if "describe-instances" in args:
                return {"Reservations": [{"Instances": [{"ImageId": "ami-pinned"}]}]}
            return {
                "Changes": [
                    {
                        "ResourceChange": {
                            "LogicalResourceId": "Host",
                            "Action": "Modify",
                            "Replacement": "True",
                        }
                    }
                ]
            }

    with pytest.raises(RuntimeError, match="protected resources: Host"):
        stack_update(FakeAws(), template)


@pytest.mark.parametrize("modified", [False, True])
def test_dependency_only_bucket_policy_change_requires_identical_policy(modified):
    import copy

    template = ROOT / "infrastructure/deployment/cloudformation/stack.yaml"
    current = cloudformation_yaml(template.read_text())
    candidate = copy.deepcopy(current)
    if modified:
        candidate["Resources"]["ArtifactPolicy"]["Properties"]["PolicyDocument"]["Statement"][0][
            "Effect"
        ] = "Allow"
    changes = [
        {
            "ResourceChange": {
                "LogicalResourceId": "ArtifactPolicy",
                "Action": "Modify",
                "Replacement": "False",
            }
        }
    ]
    assert protected_changes(current, candidate, changes) == (
        ["ArtifactPolicy"] if modified else []
    )


def test_identical_bucket_policy_cannot_be_removed_or_replaced():
    template = ROOT / "infrastructure/deployment/cloudformation/stack.yaml"
    current = cloudformation_yaml(template.read_text())
    for action, replacement in (("Remove", "False"), ("Modify", "Conditional"), ("Modify", "True")):
        changes = [
            {
                "ResourceChange": {
                    "LogicalResourceId": "ArtifactPolicy",
                    "Action": action,
                    "Replacement": replacement,
                }
            }
        ]
        assert protected_changes(current, current, changes) == ["ArtifactPolicy"]


@pytest.mark.parametrize(
    "research,path,html",
    [
        (True, "/research", "<title>Research · Toir</title>"),
        (False, "/", "<h1>No agents configured</h1>"),
    ],
)
def test_ingress_probes_the_release_specific_page(monkeypatch, research, path, html):
    urls = []

    def response(*args, capture=False):
        url = args[-1]
        urls.append(url)
        if url.endswith("/api/health") or url.endswith("/api/ready"):
            return '{"status":"ok","service":"api"}'
        assert url == f"http://127.0.0.1{path}"
        return html

    monkeypatch.setattr(remote_apply, "execute", response)
    remote_apply.probe_ingress(research=research)
    assert urls[0] == f"http://127.0.0.1{path}"
    assert len(urls) == 3


def test_research_ingress_rejects_generic_client_loading_html(monkeypatch):
    ticks = iter([0, 1, 61])
    monkeypatch.setattr(remote_apply.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote_apply.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        remote_apply, "execute", lambda *args, **kwargs: "<title>Company Brain</title>"
    )
    with pytest.raises(RuntimeError, match="Ingress checks did not pass"):
        remote_apply.probe_ingress(research=True)


@pytest.mark.parametrize("volume_kind", ["local", "hostPath"])
def test_data_directory_supports_k3s_local_pv_and_older_hostpath(monkeypatch, volume_kind):
    path = "/var/lib/rancher/k3s/storage/pvc-6869fab5_company-brain_coordinator-data"
    responses = iter(
        [
            {"spec": {"volumeName": "pvc-6869fab5"}},
            {"spec": {"storageClassName": "local-path", volume_kind: {"path": path}}},
        ]
    )
    monkeypatch.setattr(remote_apply, "kubernetes_json", lambda *args: next(responses))
    assert remote_apply.data_directory() == Path(path).resolve()


@pytest.mark.parametrize(
    "path", ["/etc", "/var/lib/rancher/k3s/storage", "/var/lib/rancher/k3s/storage/../../../etc"]
)
def test_data_directory_rejects_pv_outside_dedicated_local_storage(monkeypatch, path):
    responses = iter(
        [
            {"spec": {"volumeName": "pvc-6869fab5"}},
            {"spec": {"local": {"path": path}}},
        ]
    )
    monkeypatch.setattr(remote_apply, "kubernetes_json", lambda *args: next(responses))
    with pytest.raises(RuntimeError, match="Unexpected coordinator persistent volume path"):
        remote_apply.data_directory()


def retained_foundation(
    tmp_path, monkeypatch, *, actual_image="foundation:known", old_writer=False
):
    release_id = "a" * 12 + "-" + "b" * 12
    folder = tmp_path / "releases" / release_id
    folder.mkdir(parents=True)
    (tmp_path / "state.json").write_text(json.dumps({"current": release_id}))
    spec = {"containers": [{"name": "orchestrator", "image": "foundation:known"}], "volumes": []}
    manifest = {
        "items": [
            {"kind": "Deployment", "metadata": {"name": name}, "spec": {"template": {"spec": spec}}}
            for name in ("web", "api", "orchestrator")
        ]
    }
    (folder / "manifests.json").write_text(json.dumps(manifest))
    deployed = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "orchestrator", "image": actual_image}],
                    "volumes": [],
                }
            }
        }
    }
    pods = {
        "items": (
            [
                {
                    "status": {"phase": "Running"},
                    "spec": {
                        "volumes": [
                            {
                                "persistentVolumeClaim": {"claimName": "coordinator-data"},
                            }
                        ]
                    },
                }
            ]
            if old_writer
            else []
        )
    }
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    monkeypatch.setattr(
        remote_apply, "kubernetes_json", lambda *args: pods if "pods" in args else deployed
    )
    return deployed


def test_upgrade_after_foundation_rollback_backs_up_retained_data_without_maintenance(
    tmp_path,
    monkeypatch,
):
    retained_foundation(tmp_path, monkeypatch)
    data = tmp_path / "retained-data"
    live = databases(data)
    live.close()
    monkeypatch.setattr(remote_apply, "data_directory", lambda: data)
    monkeypatch.setattr(
        remote_apply, "maintenance", lambda *args: pytest.fail("Foundation has no maintenance API")
    )
    uploaded = []
    monkeypatch.setattr(remote_apply, "execute", lambda *args: uploaded.append(args))
    backup_id = remote_apply.backup("private-backups", leave_maintenance=True)
    assert remote_apply.validate_backup_id(backup_id) == backup_id
    assert uploaded[0][-4] == f"s3://private-backups/backups/{backup_id}.tar.gz"
    with sqlite3.connect(data / "runs.sqlite") as db:
        assert db.execute("SELECT value FROM findings").fetchone() == ("durable evidence",)


def test_failed_research_rollout_does_not_use_stale_foundation_state_to_skip_drain(
    tmp_path,
    monkeypatch,
):
    deployed = retained_foundation(tmp_path, monkeypatch)
    deployed["spec"]["template"]["spec"]["volumes"] = [
        {
            "persistentVolumeClaim": {"claimName": "coordinator-data"},
        }
    ]
    assert remote_apply.coordinator_writes_database() is True


def test_unknown_coordinator_cannot_bypass_database_drain(tmp_path, monkeypatch):
    retained_foundation(tmp_path, monkeypatch, actual_image="unrecognized:runtime")
    with pytest.raises(RuntimeError, match="Cannot establish"):
        remote_apply.coordinator_writes_database()


def test_terminating_research_pod_prevents_offline_backup(tmp_path, monkeypatch):
    retained_foundation(tmp_path, monkeypatch, old_writer=True)
    with pytest.raises(RuntimeError, match="still holds the database"):
        remote_apply.coordinator_writes_database()


@pytest.mark.parametrize("eventually_ready", [True, False])
def test_post_reboot_ingress_wait_retries_and_has_a_finite_failure(eventually_ready):
    from manage import INGRESS_WAIT

    # Run the actual operator shell retry with a service-LB that is initially unavailable.
    response = '[ "$ingress_attempts" -ge 3 ]' if eventually_ready else "return 7"
    script = (
        "set -eu\ningress_attempts=0\n"
        f"curl() {{ ingress_attempts=$((ingress_attempts + 1)); {response}; }}\n"
        "sleep() { :; }\n" + INGRESS_WAIT + "printf '%s' \"$ingress_attempts\""
    )
    result = subprocess.run(["sh", "-c", script], text=True, capture_output=True, timeout=5)
    if eventually_ready:
        assert result.returncode == 0
        assert result.stdout == "3"
    else:
        assert result.returncode == 1
        assert "60 attempts" in result.stderr


def test_contacts_has_an_independent_private_worker_and_no_durable_volume():
    resources = json.loads(
        (ROOT / "infrastructure/deployment/kubernetes/foundation.json").read_text()
    )["items"]
    deployment = next(
        r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"] == "contacts"
    )
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["replicas"] == 1
    assert not any(v.get("persistentVolumeClaim") for v in pod["volumes"])
    assert pod["containers"][0]["envFrom"] == [{"secretRef": {"name": "contacts-runtime"}}]
    policy = next(
        r
        for r in resources
        if r["kind"] == "NetworkPolicy" and r["metadata"]["name"] == "contacts-ingress"
    )
    assert policy["spec"]["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "orchestrator"}}}
    ]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_spark_secret_values_reach_only_coordinator(monkeypatch, backend):
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: backend)
    monkeypatch.setattr(
        remote_apply,
        "runtime_config",
        lambda: {
            "respan_secret": "respan",
            "scalekit_secret": "scalekit",
            "database_secret": "database",
            "brain_api_secret": "brain",
        },
    )
    values = {
        "respan": {"RESPAN_API_KEY": "respan-value"},
        "scalekit": {
            "SCALEKIT_CLIENT_SECRET": "scalekit-value",
            "SCALEKIT_CONNECTION_NAME": "exa",
            "SCALEKIT_ACCOUNT_ID": "toir",
        },
        "database": {"DATABASE_URL": "database-value"},
        "brain": {"BRAIN_API_URL": "http://brain", "BRAIN_API_TOKEN": "brain-value"},
    }
    monkeypatch.setattr(remote_apply, "read_secret", lambda name, fields: values[name])
    calls = []
    monkeypatch.setattr(
        remote_apply.subprocess,
        "run",
        lambda args, **kwargs: (
            calls.append(kwargs["input"]) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    remote_apply.sync_secrets()
    secrets = {
        r["metadata"]["name"]: r["data"]
        for r in json.loads(calls[0])["items"]
        if r["kind"] == "Secret"
    }
    assert set(secrets["orchestrator-runtime"]) == {
        "RESPAN_API_KEY",
        "SCALEKIT_CLIENT_SECRET",
        "BRAIN_API_URL",
        "BRAIN_API_TOKEN",
    } | ({"DATABASE_URL"} if backend == "postgres" else set())
    for worker in ("research-runtime", "contacts-runtime"):
        assert not ({"DATABASE_URL", "BRAIN_API_TOKEN", "BRAIN_API_URL"} & secrets[worker].keys())
        assert "SCALEKIT_CONNECTION_NAME" in secrets[worker]


@pytest.mark.parametrize(
    "active", ["active_contact_task_id", "active_crm_operations", "active_jobs"]
)
def test_drain_waits_for_contact_and_crm_work(monkeypatch, active):
    monkeypatch.setattr(remote_apply, "data_directory", lambda: Path("/data"))
    monkeypatch.setattr(remote_apply, "coordinator_writes_database", lambda: True)
    responses = iter([{}, {"enabled": True, active: ["work"]}, {"enabled": True}])
    monkeypatch.setattr(remote_apply, "maintenance", lambda *_: next(responses))
    waits = []
    monkeypatch.setattr(remote_apply.time, "sleep", waits.append)
    assert remote_apply.drain() is True
    assert waits == [5]


@pytest.mark.parametrize("backend", ["postgres", "unknown"])
def test_backup_backend_check_fails_closed_without_exposing_url(monkeypatch, backend):
    monkeypatch.setattr(remote_apply, "execute", lambda *args, **kwargs: backend)
    with pytest.raises(RuntimeError):
        remote_apply.require_sqlite_backend()


def test_postgres_backup_never_copies_retained_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_apply, "data_directory", lambda: tmp_path)
    monkeypatch.setattr(remote_apply, "coordinator_writes_database", lambda: True)
    monkeypatch.setattr(remote_apply, "execute", lambda *args, **kwargs: "postgres")
    monkeypatch.setattr(
        remote_apply,
        "snapshot_databases",
        lambda *_: pytest.fail("Cannot snapshot SQLite as a PG backup"),
    )
    monkeypatch.setattr(remote_apply, "backup_postgres", lambda bucket, **kwargs: "pg-backup")
    assert remote_apply.backup("bucket") == "pg-backup"


def test_code_rollback_cannot_bypass_required_postgres_backup(monkeypatch):
    monkeypatch.setattr(remote_apply, "runtime_config", lambda: {"bucket": "private"})

    def postgres_backup(*args, **kwargs):
        raise remote_apply.PostgresBackupRequired("Postgres requires its owner backup")

    monkeypatch.setattr(remote_apply, "backup", postgres_backup)
    with pytest.raises(remote_apply.PostgresBackupRequired):
        remote_apply.prepare_backup("private", recovery=True)


def test_verified_project_crm_attestation_is_coordinator_only():
    resources = json.loads(
        (ROOT / "infrastructure/deployment/kubernetes/foundation.json").read_text()
    )["items"]
    for deployment in (r for r in resources if r["kind"] == "Deployment"):
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        values = {item["name"]: item["value"] for item in container.get("env", [])}
        if deployment["metadata"]["name"] == "orchestrator":
            # This project's actual HubSpot grants are recorded in the Spark handoff.
            assert values["SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED"] == "true"
        else:
            assert "SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED" not in values
    # A fresh local setup must still attest its own account's permissions.
    assert "SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED=false" in (ROOT / ".env.example").read_text()
