"""Runs as root through SSM. No third-party Python packages are required."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/company-brain")
SERVICES = ("web", "api", "orchestrator", "research", "contacts")
DATABASES = ("runs.sqlite", "checkpoints.sqlite")
KUBECTL = ["k3s", "kubectl", "--request-timeout=30s"]
FILES = {"images.tar", "manifests.json", "release.json", "checksums.json"}


class PostgresBackupRequired(RuntimeError):
    """An explicit operator backup is required; recovery must not bypass this gate."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute(*args: str, capture: bool = False) -> str:
    result = subprocess.run(list(args), check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def validate_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{12}-[0-9a-f]{12}", value):
        raise ValueError("Invalid release ID")
    return value


def unpack(archive: Path, destination: Path, expected: str):
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(archive) != expected:
        raise ValueError("Release archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if {m.name for m in members} != FILES or len(members) != len(FILES):
            raise ValueError("Unexpected release contents")
        for member in members:
            if not member.isfile():
                raise ValueError("Only regular files are allowed in a release")
            with tar.extractfile(member) as source:
                with (destination / member.name).open("wb") as target:
                    shutil.copyfileobj(source, target)
    checksums = json.loads((destination / "checksums.json").read_text())
    if set(checksums) != FILES - {"checksums.json"}:
        raise ValueError("Incomplete release checksum manifest")
    for name, digest in checksums.items():
        if sha256(destination / name) != digest:
            raise ValueError(f"Checksum mismatch: {name}")


def probe_ingress(research: bool = False):
    # curl gets a real HTTP response through the same ingress used by SSM forwarding.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            path = "/research" if research else "/"
            page = execute(
                "curl", "-fsS", "--max-time", "5", f"http://127.0.0.1{path}", capture=True
            )
            expected = ("Research", "Toir") if research else ("No agents configured",)
            if not all(marker in page for marker in expected):
                raise ValueError("Unexpected web release response")
            for endpoint in ("health", "ready"):
                result = execute(
                    "curl",
                    "-fsS",
                    "--max-time",
                    "5",
                    f"http://127.0.0.1/api/{endpoint}",
                    capture=True,
                )
                if json.loads(result).get("status") != "ok":
                    raise ValueError("Unexpected API probe response")
            return
        except (subprocess.CalledProcessError, ValueError):
            time.sleep(3)
    raise RuntimeError("Ingress checks did not pass")


def diagnostics():
    # Do not print logs, environment values, or pod descriptions containing credentials.
    # Kubernetes status/reason messages suffice to locate the failed workload.
    for name in SERVICES:
        result = subprocess.run(
            [
                *KUBECTL,
                "-n",
                "company-brain",
                "get",
                "deployment",
                name,
                "-o",
                "jsonpath={.metadata.name}{' ready='}{.status.readyReplicas}{'\\n'}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(result.stdout, end="", flush=True)
    result = subprocess.run(
        [*KUBECTL, "-n", "company-brain", "get", "pods", "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        for pod in json.loads(result.stdout).get("items", []):
            states = [
                c.get("state", {}) for c in pod.get("status", {}).get("containerStatuses", [])
            ]
            # Messages can echo provider responses; print reason and exit code only.
            safe = [
                {
                    k: {f: v.get(f) for f in ("reason", "exitCode") if f in v}
                    for k, v in state.items()
                }
                for state in states
            ]
            print(json.dumps({"pod": pod["metadata"]["name"], "states": safe}), flush=True)


def deployment_names(folder: Path) -> tuple[str, ...]:
    manifest = json.loads((folder / "manifests.json").read_text())
    names = tuple(r["metadata"]["name"] for r in manifest["items"] if r["kind"] == "Deployment")
    if not names or not set(names) <= set(SERVICES):
        raise ValueError("Unexpected application deployments")
    return names


def apply_release(folder: Path):
    services = deployment_names(folder)
    if configured_backend() == "postgres":
        manifest = json.loads((folder / "manifests.json").read_text())
        coordinator = next(
            r
            for r in manifest["items"]
            if r["kind"] == "Deployment" and r["metadata"]["name"] == "orchestrator"
        )
        if (
            coordinator["spec"]["template"]["metadata"]
            .get("annotations", {})
            .get("company-brain/postgres-backup")
            != "v1"
        ):
            raise PostgresBackupRequired(
                "Refusing an old release without PostgreSQL backup support; "
                "data backend remains Postgres"
            )
    # Import before changing any workloads; Never prevents accidental registry pulls.
    execute("k3s", "ctr", "-n", "k8s.io", "images", "import", str(folder / "images.tar"))
    if "research" in services:
        sync_secrets()
    execute(*KUBECTL, "apply", "-f", str(folder / "manifests.json"))
    # Remove only known workers omitted by an older release; preserve all durable state.
    for worker in ("research", "contacts"):
        if worker in services:
            continue
        for kind, name in (
            ("deployment", worker),
            ("service", worker),
            ("networkpolicy", worker + "-ingress"),
        ):
            execute(*KUBECTL, "-n", "company-brain", "delete", kind, name, "--ignore-not-found")
    for service in services:
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "rollout",
            "status",
            f"deployment/{service}",
            "--timeout=210s",
        )
    if "research" in services:
        probe_ingress(research=True)
    else:
        probe_ingress()


def resume_after_apply(folder: Path):
    if "research" in deployment_names(folder):
        maintenance(False)
    else:
        # An older foundation cannot serve the maintenance API; retain databases unchanged.
        directory = data_directory()
        if directory:
            (directory / "maintenance.json").unlink(missing_ok=True)


def atomic_json(path: Path, content: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(content, indent=2) + "\n")
    os.replace(temp, path)


def deploy(release_id: str, bucket: str, expected: str):
    validate_id(release_id)
    if release_id.split("-")[1] != expected[:12]:
        raise ValueError("Release ID does not match artifact hash")
    releases = STATE / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    # Bound temporary disk use: archive + extracted image archive.
    if shutil.disk_usage(STATE).free < 3 * 1024**3:
        raise RuntimeError("Less than 3 GiB free; clean old releases/images before deploying")
    with tempfile.TemporaryDirectory(prefix="incoming-", dir=STATE) as temp:
        incoming = Path(temp)
        archive = incoming / "release.tar.gz"
        execute(
            "aws",
            "s3",
            "cp",
            f"s3://{bucket}/releases/{release_id}.tar.gz",
            str(archive),
            "--only-show-errors",
        )
        extracted = incoming / "unpacked"
        extracted.mkdir()
        unpack(archive, extracted, expected)
        final = releases / release_id
        if final.exists():
            # Reuse only when the cached copy still matches this checked bundle.
            for filename in FILES:
                if sha256(final / filename) != sha256(extracted / filename):
                    raise ValueError(f"Cached release was modified: {release_id}")
        else:
            shutil.move(extracted, final)
        protected = prepare_backup(bucket)
        try:
            apply_release(final)
            if protected:
                resume_after_apply(final)
        except Exception:
            diagnostics()
            print("Release failed. Last successful release is recorded; run rollback.", flush=True)
            raise
        state_file = STATE / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        if state.get("current") != release_id:
            atomic_json(state_file, {"current": release_id, "previous": state.get("current")})
        retain_successful_releases()
        print(f"RELEASE_OK {release_id}", flush=True)


def retain_successful_releases():
    state = json.loads((STATE / "state.json").read_text())
    keep = {state.get("current"), state.get("previous")}
    for directory in (STATE / "releases").iterdir():
        if directory.is_dir() and directory.name not in keep:
            shutil.rmtree(directory)


def rollback(previous: bool = False):
    state_file = STATE / "state.json"
    if not state_file.exists():
        raise RuntimeError("No successful release is recorded")
    state = json.loads(state_file.read_text())
    # Always restore the recorded successful release after an interrupted/failed deploy.
    # --previous explicitly switches back one successful release.
    target = state.get("previous") if previous else state.get("current")
    if not target:
        raise RuntimeError("No previous successful release is available")
    folder = STATE / "releases" / validate_id(target)
    checksums = json.loads((folder / "checksums.json").read_text())
    if set(checksums) != FILES - {"checksums.json"}:
        raise ValueError("Incomplete cached checksum manifest")
    for filename, expected in checksums.items():
        if sha256(folder / filename) != expected:
            raise ValueError(f"Cached release checksum mismatch: {filename}")
    protected = prepare_backup(runtime_config().get("bucket"), recovery=True)
    try:
        apply_release(folder)
        if protected:
            resume_after_apply(folder)
    except Exception:
        diagnostics()
        raise
    if target != state.get("current"):
        atomic_json(state_file, {"current": target, "previous": state.get("current")})
    print(f"ROLLBACK_OK {target}", flush=True)


def runtime_config() -> dict:
    path = STATE / "runtime-config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def aws_command(*args: str) -> list[str]:
    return ["aws", "--region", runtime_config().get("region", "us-east-1"), "--no-cli-pager", *args]


def read_secret(secret_id: str, permitted: set[str]) -> dict[str, str]:
    result = subprocess.run(
        aws_command(
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--output",
            "json",
        ),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        # Empty CloudFormation secrets are expected before provider setup.
        if "ResourceNotFoundException" in result.stderr or (
            "InvalidRequestException" in result.stderr and "AWSCURRENT" in result.stderr
        ):
            return {}
        raise RuntimeError("Cannot read runtime secret; inspect IAM and secret version metadata")
    try:
        values = json.loads(json.loads(result.stdout)["SecretString"])
        if not isinstance(values, dict) or not set(values) <= permitted:
            raise ValueError()
        if any(not isinstance(v, str) or not v.strip() for v in values.values()):
            raise ValueError()
        return values
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Invalid runtime secret JSON; no secret contents were logged") from None


def sync_secrets(restart: bool = False):
    config = runtime_config()
    if not config.get("respan_secret") or not config.get("scalekit_secret"):
        raise RuntimeError("Run stack-update before deploying the research runtime")
    respan = read_secret(config["respan_secret"], {"RESPAN_API_KEY"})
    scalekit = read_secret(
        config["scalekit_secret"],
        {
            "SCALEKIT_ENVIRONMENT_URL",
            "SCALEKIT_CLIENT_ID",
            "SCALEKIT_CLIENT_SECRET",
            "SCALEKIT_CONNECTION_NAME",
            "SCALEKIT_ACCOUNT_ID",
        },
    )
    database = (
        read_secret(config["database_secret"], {"DATABASE_URL"})
        if config.get("database_secret") and configured_backend() == "postgres"
        else {}
    )
    if configured_backend() == "postgres" and not database.get("DATABASE_URL"):
        raise PostgresBackupRequired(
            "PostgreSQL is selected but its credential is missing; runtime secrets were not changed"
        )
    brain = (
        read_secret(config["brain_api_secret"], {"BRAIN_API_URL", "BRAIN_API_TOKEN"})
        if config.get("brain_api_secret")
        else {}
    )
    # Worker account/connection settings select Exa only. CRM/auth credentials and
    # shared HubSpot settings remain coordinator-owned; no DB/Brain secrets reach workers.
    coordinator_scalekit = {
        k: v
        for k, v in scalekit.items()
        if k
        not in {
            "SCALEKIT_ACCOUNT_ID",
            "SCALEKIT_CONNECTION_NAME",
        }
    }
    protected = False
    if restart:
        protected = drain()
    try:
        # Namespace can be absent on a fresh server, before its first release.
        ns = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "company-brain"}}
        secrets = [ns]
        for name, values in (
            ("orchestrator-runtime", {**respan, **coordinator_scalekit, **database, **brain}),
            ("research-runtime", {**respan, **scalekit}),
            ("contacts-runtime", {**respan, **scalekit}),
        ):
            secrets.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": name, "namespace": "company-brain"},
                    "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()},
                }
            )
        # Server-side apply avoids the plaintext last-applied annotation. No payload in argv/logs.
        result = subprocess.run(
            [
                *KUBECTL,
                "apply",
                "--server-side",
                "--force-conflicts",
                "--field-manager=toir-secrets",
                "-f",
                "-",
            ],
            input=json.dumps({"apiVersion": "v1", "kind": "List", "items": secrets}),
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("Kubernetes secret synchronization failed; values were not logged")
        print(
            "Runtime secrets synchronized. "
            + ("Scalekit setup is incomplete." if not scalekit else "Scalekit values are present."),
            flush=True,
        )
        if restart:
            for service in ("orchestrator", "research", "contacts"):
                if not kubernetes_json("-n", "company-brain", "get", "deployment", service):
                    continue
                execute(
                    *KUBECTL, "-n", "company-brain", "rollout", "restart", f"deployment/{service}"
                )
                execute(
                    *KUBECTL,
                    "-n",
                    "company-brain",
                    "rollout",
                    "status",
                    f"deployment/{service}",
                    "--timeout=210s",
                )
            if protected:
                maintenance(False)
    except Exception:
        if protected:
            print("Maintenance remains enabled; repair the rollout before maintenance --resume.")
        raise


def kubernetes_json(*args: str) -> dict | None:
    result = execute(*KUBECTL, *args, "--ignore-not-found", "-o", "json", capture=True)
    return json.loads(result) if result.strip() else None


def data_directory() -> Path | None:
    claim = kubernetes_json("-n", "company-brain", "get", "pvc", "coordinator-data")
    if not claim:
        return None
    volume = claim.get("spec", {}).get("volumeName")
    if not volume:
        return None
    pv = kubernetes_json("get", "pv", volume)
    spec = (pv or {}).get("spec", {})
    # Current K3s local-path-provisioner emits a Local PV; older versions used HostPath.
    # Both refer to the same node-local filesystem, with no CSI/network-volume support.
    raw = spec.get("local", {}).get("path") or spec.get("hostPath", {}).get("path")
    if not raw:
        raise RuntimeError("Backup supports the coordinator's single-node local-path volume only")
    path = Path(raw).resolve()
    storage_root = Path("/var/lib/rancher/k3s/storage").resolve()
    if path == storage_root or not path.is_relative_to(storage_root):
        raise RuntimeError("Unexpected coordinator persistent volume path")
    return path


def coordinator_writes_database() -> bool:
    deployed = kubernetes_json("-n", "company-brain", "get", "deployment", "orchestrator")
    pod_spec = (deployed or {}).get("spec", {}).get("template", {}).get("spec", {})

    def mounts_database(spec: dict) -> bool:
        return any(
            volume.get("persistentVolumeClaim", {}).get("claimName") == "coordinator-data"
            for volume in spec.get("volumes", [])
        )

    if mounts_database(pod_spec):
        return True
    # A retained PVC alone is not a running database writer. Establish the applied
    # foundation image matches our recorded release before bypassing maintenance.
    state_file = STATE / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    release = state.get("current")
    if release:
        folder = STATE / "releases" / validate_id(release)
        manifest_file = folder / "manifests.json"
        if manifest_file.exists() and "research" not in deployment_names(folder):
            resources = json.loads(manifest_file.read_text())["items"]
            expected = next(
                (
                    r
                    for r in resources
                    if r["kind"] == "Deployment" and r["metadata"]["name"] == "orchestrator"
                ),
                None,
            )
            expected_spec = (expected or {}).get("spec", {}).get("template", {}).get("spec", {})
            expected_images = [c["image"] for c in expected_spec.get("containers", [])]
            actual_images = [c["image"] for c in pod_spec.get("containers", [])]
            if expected_images and actual_images == expected_images:
                pods = kubernetes_json("-n", "company-brain", "get", "pods") or {}
                if any(
                    mounts_database(pod.get("spec", {}))
                    and pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                    for pod in pods.get("items", [])
                ):
                    raise RuntimeError(
                        "An old research pod still holds the database; retry after exit"
                    )
                return False
    raise RuntimeError(
        "Cannot establish the active coordinator database ownership; repair its release"
    )


def maintenance(enabled: bool | None = None) -> dict:
    script = (
        "import json, urllib.request; "
        f"data={json.dumps({'enabled': enabled})!r}.encode() if {enabled is not None!r} else None; "
        "request=urllib.request.Request('http://127.0.0.1:8000/v1/maintenance',data=data,"
        "headers={'Content-Type':'application/json'}); "
        "print(urllib.request.urlopen(request,timeout=10).read().decode())"
    )
    raw = execute(
        *KUBECTL,
        "-n",
        "company-brain",
        "exec",
        "deployment/orchestrator",
        "--",
        "python",
        "-c",
        script,
        capture=True,
    )
    return json.loads(raw)


def drain() -> bool:
    if not data_directory() or not coordinator_writes_database():
        return False  # A known foundation has no research database writer.
    maintenance(True)
    deadline = time.monotonic() + 660
    while True:
        status = maintenance()
        if not status.get("enabled"):
            raise RuntimeError("Coordinator did not enter maintenance")
        if not any(
            status.get(key)
            for key in (
                "active_run_id",
                "active_contact_task_id",
                "active_crm_operations",
                "active_jobs",
            )
        ):
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Research, contacts or CRM execution did not drain; maintenance remains enabled"
            )
        time.sleep(5)


def configured_backend() -> str:
    path = STATE / "storage.json"
    backend = json.loads(path.read_text()).get("backend") if path.exists() else "sqlite"
    if backend not in {"sqlite", "postgres"}:
        raise RuntimeError("Invalid persisted storage selection")
    return backend


def live_backend() -> str:
    """Inspect the live process, so pre-cutover secret rotation cannot misidentify it."""
    script = "import os; print('postgres' if os.environ.get('DATABASE_URL') else 'sqlite')"
    backend = execute(
        *KUBECTL,
        "-n",
        "company-brain",
        "exec",
        "deployment/orchestrator",
        "--",
        "python",
        "-c",
        script,
        capture=True,
    ).strip()
    if backend not in {"sqlite", "postgres"}:
        raise RuntimeError("Cannot verify the live storage backend; refusing backup/restore")
    return backend


def require_sqlite_backend():
    if live_backend() != "sqlite":
        raise PostgresBackupRequired("Postgres is active; SQLite restoration is disabled")


def validate_backup_id(value: str) -> str:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", value):
        raise ValueError("Invalid backup ID")
    return value


def snapshot_databases(source: Path, destination: Path) -> dict:
    for name in DATABASES:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing regular database: {name}")
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as src:
            with sqlite3.connect(destination / name) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"SQLite integrity check failed: {name}")
    with sqlite3.connect(destination / "runs.sqlite") as db:
        schema = db.execute("SELECT version FROM schema_version").fetchone()
        if not schema or schema[0] != 1:
            raise RuntimeError("Unsupported application schema; backup was not published")
    return {name: sha256(destination / name) for name in DATABASES}


def backup(bucket: str, *, leave_maintenance: bool = False) -> str | None:
    source = data_directory()
    if source is None:
        print("No research persistent volume exists yet; pre-deployment backup skipped.")
        return None
    writer = coordinator_writes_database()
    if writer and live_backend() == "postgres":
        return backup_postgres(bucket, leave_maintenance=leave_maintenance)
    if configured_backend() == "postgres":
        raise PostgresBackupRequired("Persisted backend is Postgres; refusing stale SQLite backup")
    already_paused = maintenance().get("enabled", False) if writer else False
    protected = drain() if writer else False
    if not writer:
        print("Backing up retained research databases while the foundation has no database writer.")
    try:
        return publish_sqlite_backup(source, bucket)
    finally:
        if protected and not leave_maintenance and not already_paused:
            maintenance(False)


def publish_sqlite_backup(source: Path, bucket: str) -> str:
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="backup-", dir=STATE) as tmp:
        folder = Path(tmp)
        hashes = snapshot_databases(source, folder)
        state_file = STATE / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        atomic_json(
            folder / "backup.json",
            {
                "version": 1,
                "storage_backend": "sqlite",
                "schema_version": 1,
                "backup_id": backup_id,
                "created_at": datetime.now(UTC).isoformat(),
                "source_release": state.get("current"),
                "checksums": hashes,
            },
        )
        archive = folder / "backup.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in (*DATABASES, "backup.json"):
                tar.add(folder / name, arcname=name)
        execute(
            *aws_command(
                "s3",
                "cp",
                str(archive),
                f"s3://{bucket}/backups/{backup_id}.tar.gz",
                "--sse",
                "AES256",
                "--only-show-errors",
            )
        )
        atomic_json(
            STATE / "latest-backup.json",
            {
                "backup_id": backup_id,
                "sha256": sha256(archive),
                "bucket": bucket,
            },
        )
    print(f"BACKUP_OK {backup_id}", flush=True)
    return backup_id


def prepare_backup(bucket: str | None, recovery: bool = False) -> bool:
    # Older foundation deployments and tests have no runtime configuration yet.
    if not runtime_config() or not bucket:
        return False
    if recovery:
        # Rollback is the escape hatch for an unhealthy coordinator. Preserve its PVC unchanged.
        try:
            return backup(bucket, leave_maintenance=True) is not None
        except PostgresBackupRequired:
            if configured_backend() == "postgres":
                return recovery_postgres_backup(bucket) is not None
            raise
        except (subprocess.CalledProcessError, RuntimeError):
            if configured_backend() == "postgres":
                return recovery_postgres_backup(bucket) is not None
            print("Coordinator unavailable for backup; rollback preserves its volume unchanged.")
            return True
    return backup(bucket, leave_maintenance=True) is not None


def unpack_backup(archive: Path, destination: Path, backup_id: str):
    allowed = {*DATABASES, "backup.json"}
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) != len(allowed) or {m.name for m in members} != allowed:
            raise ValueError("Unexpected backup contents")
        for member in members:
            if not member.isfile():
                raise ValueError("Only regular files are allowed in a backup")
            with tar.extractfile(member) as src, (destination / member.name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    manifest = json.loads((destination / "backup.json").read_text())
    if manifest.get("storage_backend", "sqlite") != "sqlite":
        raise ValueError("This restore command accepts only verified SQLite backups")
    if (
        manifest.get("version") != 1
        or manifest.get("schema_version") != 1
        or manifest.get("backup_id") != backup_id
        or set(manifest.get("checksums", {})) != set(DATABASES)
    ):
        raise ValueError("Unsupported backup manifest/schema")
    for name, digest in manifest["checksums"].items():
        if sha256(destination / name) != digest:
            raise ValueError(f"Backup checksum mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix="verify-", dir=STATE) as verify:
        snapshot_databases(destination, Path(verify))


def restore(bucket: str, backup_id: str):
    validate_backup_id(backup_id)
    target = data_directory()
    if target is None or not coordinator_writes_database():
        raise RuntimeError("Deploy the research runtime before restoring its persistent volume")
    if live_backend() == "postgres":
        return restore_postgres(bucket, backup_id)
    require_sqlite_backend()
    with tempfile.TemporaryDirectory(prefix="restore-", dir=STATE) as tmp:
        folder = Path(tmp)
        archive = folder / "backup.tar.gz"
        execute(
            *aws_command(
                "s3",
                "cp",
                f"s3://{bucket}/backups/{backup_id}.tar.gz",
                str(archive),
                "--only-show-errors",
            )
        )
        unpack_backup(archive, folder, backup_id)
        backup(bucket, leave_maintenance=True)  # Preserve all post-backup writes before restoring.
        execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=0")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "app.kubernetes.io/name=orchestrator",
            "--timeout=90s",
        )
        # On failure leave the writer stopped. Both replacements complete before it starts again.
        for name in DATABASES:
            staged = target / (name + ".restoring")
            shutil.copyfile(folder / name, staged)
            os.chown(staged, 1000, 1000)
            os.chmod(staged, 0o600)
            os.replace(staged, target / name)
            for suffix in ("-wal", "-shm"):
                (target / (name + suffix)).unlink(missing_ok=True)
        execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=1")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "rollout",
            "status",
            "deployment/orchestrator",
            "--timeout=210s",
        )
        maintenance(False)
        print(f"RESTORE_OK {backup_id}", flush=True)


def postgres_command(
    operation: str, *, pod: str = "deployment/orchestrator", credential: bool = False
) -> list[str]:
    command = [
        *KUBECTL,
        "-n",
        "company-brain",
        "exec",
        "-i",
        pod,
        "--",
        "python",
        "/opt/toir/postgres_admin.py",
        operation,
    ]
    if credential:
        command.append("--credential-stdin")
    return command


def postgres_secret() -> dict:
    arn = runtime_config().get("database_secret")
    values = read_secret(arn, {"DATABASE_URL"}) if arn else {}
    if not values.get("DATABASE_URL"):
        raise PostgresBackupRequired(
            "Database secret is absent; PostgreSQL operation was not attempted"
        )
    return values


def unpack_postgres_backup(archive: Path, destination: Path, backup_id: str | None = None) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) != 2 or {m.name for m in members} != {"database.dump", "backup.json"}:
            raise ValueError("Unexpected PostgreSQL backup contents")
        for member in members:
            if not member.isfile():
                raise ValueError("PostgreSQL backup contains a nonregular file")
            with tar.extractfile(member) as src, (destination / member.name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    manifest = json.loads((destination / "backup.json").read_text())
    if manifest.get("storage_backend") != "postgres" or manifest.get("postgres_major") != 17:
        raise ValueError("Expected a PostgreSQL17 backup")
    if backup_id and (manifest.get("backup_id") != backup_id or manifest.get("version") != 2):
        raise ValueError("PostgreSQL backup identity mismatch")
    if manifest.get("checksums") != {"database.dump": sha256(destination / "database.dump")}:
        raise ValueError("PostgreSQL dump checksum mismatch")
    if not isinstance(manifest.get("tables"), list):
        raise ValueError("PostgreSQL table inventory is missing")
    return manifest


def backup_postgres(
    bucket: str,
    *,
    leave_maintenance: bool = False,
    staged: bool = False,
    operator: str | None = None,
) -> str:
    already_paused = maintenance().get("enabled", False) if not operator else True
    protected = drain() if not operator else False
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    try:
        with tempfile.TemporaryDirectory(prefix="postgres-backup-", dir=STATE) as tmp:
            folder = Path(tmp)
            archive = folder / "backup.tar.gz"
            credentials = json.dumps(postgres_secret()).encode() + b"\n" if staged else None
            with archive.open("wb") as output:
                result = subprocess.run(
                    postgres_command(
                        "snapshot", credential=staged, pod=operator or "deployment/orchestrator"
                    ),
                    input=credentials,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=720,
                )
            if result.returncode:
                raise PostgresBackupRequired(
                    "Whole-database PostgreSQL snapshot failed; no backup was published"
                )
            manifest = unpack_postgres_backup(archive, folder)
            state_file = STATE / "state.json"
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            manifest.update(
                {
                    "version": 2,
                    "backup_id": backup_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_release": state.get("current"),
                }
            )
            atomic_json(folder / "backup.json", manifest)
            with tarfile.open(archive, "w:gz") as tar:
                for name in ("database.dump", "backup.json"):
                    tar.add(folder / name, arcname=name)
            execute(
                *aws_command(
                    "s3",
                    "cp",
                    str(archive),
                    f"s3://{bucket}/backups/{backup_id}.tar.gz",
                    "--sse",
                    "AES256",
                    "--only-show-errors",
                )
            )
            atomic_json(
                STATE / "latest-backup.json",
                {
                    "backup_id": backup_id,
                    "storage_backend": "postgres",
                    "sha256": sha256(archive),
                    "bucket": bucket,
                },
            )
        print(f"POSTGRES_BACKUP_OK {backup_id}", flush=True)
        return backup_id
    except PostgresBackupRequired:
        raise
    except Exception:
        raise PostgresBackupRequired(
            "PostgreSQL backup failed; deployment must not proceed"
        ) from None
    finally:
        if protected and not leave_maintenance and not already_paused:
            maintenance(False)


def recovery_postgres_backup(bucket: str) -> str:
    """A broken application must not prevent a fresh DB backup before app rollback."""
    try:
        state = json.loads((STATE / "state.json").read_text())
        folder = STATE / "releases" / validate_id(state["current"])
        checksums = json.loads((folder / "checksums.json").read_text())
        if sha256(folder / "manifests.json") != checksums["manifests.json"]:
            raise ValueError("Cached recovery manifest checksum mismatch")
        resources = json.loads((folder / "manifests.json").read_text())["items"]
        coordinator = next(
            r
            for r in resources
            if r["kind"] == "Deployment" and r["metadata"]["name"] == "orchestrator"
        )
        template = coordinator["spec"]["template"]
        if template["metadata"].get("annotations", {}).get("company-brain/postgres-backup") != "v1":
            raise ValueError("Last successful image lacks PostgreSQL backup support")
        image = template["spec"]["containers"][0]["image"]
        with database_operator(image=image) as operator:
            return backup_postgres(bucket, leave_maintenance=True, staged=True, operator=operator)
    except Exception:
        raise PostgresBackupRequired(
            "Isolated PostgreSQL recovery backup failed; rollout remains blocked"
        ) from None


@contextmanager
def database_operator(*, source_readonly: bool = False, image: str | None = None):
    """Stop every coordinator writer; the temporary pod has no application server."""
    deployment = kubernetes_json("-n", "company-brain", "get", "deployment", "orchestrator")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    restore_container = {
        key: value
        for key, value in container.items()
        if key in {"image", "imagePullPolicy", "env", "envFrom", "resources", "securityContext"}
    }
    restore_container.update(
        {
            "name": "restore",
            "command": ["python", "-c", "import time; time.sleep(1800)"],
            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
        }
    )
    if image:
        restore_container["image"] = image
    restore_pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "postgres-admin",
            "namespace": "company-brain",
            "labels": {"app.kubernetes.io/name": "orchestrator"},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "serviceAccountName": "application",
            "containers": [restore_container],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}}],
            "securityContext": pod_spec.get("securityContext", {}),
        },
    }
    execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=0")
    execute(
        *KUBECTL,
        "-n",
        "company-brain",
        "delete",
        "pod/postgres-admin",
        "--ignore-not-found",
        "--wait=true",
    )
    execute(
        *KUBECTL,
        "-n",
        "company-brain",
        "wait",
        "--for=delete",
        "pod",
        "-l",
        "app.kubernetes.io/name=orchestrator",
        "--timeout=90s",
    )
    if source_readonly:
        restore_pod["spec"]["volumes"].append(
            {
                "name": "source",
                "persistentVolumeClaim": {"claimName": "coordinator-data", "readOnly": True},
            }
        )
        restore_container["volumeMounts"].append(
            {"name": "source", "mountPath": "/data", "readOnly": True}
        )
    try:
        result = subprocess.run(
            [*KUBECTL, "apply", "-f", "-"],
            input=json.dumps(restore_pod),
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("Cannot create isolated PostgreSQL restore pod")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "wait",
            "--for=condition=Ready",
            "pod/postgres-admin",
            "--timeout=120s",
        )
        yield "pod/postgres-admin"
    finally:
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "delete",
            "pod/postgres-admin",
            "--ignore-not-found",
            "--wait=true",
        )


def storage_cutover(bucket: str):
    """The sole automatic SQLite→PG switch; retries require identical migrated records."""
    if configured_backend() == "postgres":
        # A verified receipt permits retry after a failed post-import rollout.
        sync_secrets()
        execute(*KUBECTL, "-n", "company-brain", "rollout", "restart", "deployment/orchestrator")
        execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=1")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "rollout",
            "status",
            "deployment/orchestrator",
            "--timeout=210s",
        )
        if live_backend() != "postgres":
            raise RuntimeError("Coordinator did not activate the recorded PostgreSQL cutover")
        backup_postgres(bucket, leave_maintenance=True)
        maintenance(False)
        print("STORAGE_ALREADY_POSTGRES", flush=True)
        return
    source = data_directory()
    if source is None or not coordinator_writes_database():
        raise RuntimeError("Deploy the research runtime before database cutover")
    drain()
    try:
        postgres_backup = backup_postgres(bucket, leave_maintenance=True, staged=True)
        with database_operator(source_readonly=True) as operator:
            sqlite_backup = publish_sqlite_backup(source, bucket)
            result = subprocess.run(
                [
                    *postgres_command("migrate", pod=operator, credential=True),
                    "--source",
                    "/data/runs.sqlite",
                ],
                input=json.dumps(postgres_secret()) + "\n",
                text=True,
                capture_output=True,
                timeout=300,
            )
            if result.returncode:
                raise RuntimeError(
                    "Migration failed; SQLite remains selected and maintenance stays enabled"
                )
            receipt = json.loads(result.stdout)
            if not re.fullmatch(r"[a-f0-9]{64}", receipt.get("sha256", "")):
                raise RuntimeError("Migration did not produce a verified record hash")
            atomic_json(
                STATE / "storage.json",
                {
                    "backend": "postgres",
                    "cutover_at": datetime.now(UTC).isoformat(),
                    "sqlite_backup": sqlite_backup,
                    "postgres_backup": postgres_backup,
                    "migration": receipt,
                },
            )
        sync_secrets()
        execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=1")
        execute(*KUBECTL, "-n", "company-brain", "rollout", "restart", "deployment/orchestrator")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "rollout",
            "status",
            "deployment/orchestrator",
            "--timeout=210s",
        )
        if live_backend() != "postgres":
            raise RuntimeError("New coordinator did not select PostgreSQL")
        backup_postgres(bucket, leave_maintenance=True)
        maintenance(False)
        print(
            json.dumps(
                {
                    "storage_backend": "postgres",
                    "migration": receipt,
                    "sqlite_backup": sqlite_backup,
                    "postgres_backup": postgres_backup,
                }
            ),
            flush=True,
        )
    except Exception:
        if configured_backend() == "sqlite":
            execute(
                *KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=1"
            )
        print(
            "Cutover stopped with maintenance enabled. Stored database selection was preserved.",
            flush=True,
        )
        raise


def restore_postgres(bucket: str, backup_id: str):
    """Explicit destructive restore: backup first, stop writer, restore atomically, verify."""
    with tempfile.TemporaryDirectory(prefix="postgres-restore-", dir=STATE) as tmp:
        folder = Path(tmp)
        archive = folder / "backup.tar.gz"
        execute(
            *aws_command(
                "s3",
                "cp",
                f"s3://{bucket}/backups/{backup_id}.tar.gz",
                str(archive),
                "--only-show-errors",
            )
        )
        unpack_postgres_backup(archive, folder, backup_id)
        drain()
        with database_operator() as operator:
            backup_postgres(bucket, leave_maintenance=True, operator=operator)
            with archive.open("rb") as input_file:
                result = subprocess.run(
                    [*postgres_command("restore", pod=operator), "--replace"],
                    stdin=input_file,
                    capture_output=True,
                    timeout=720,
                )
            if result.returncode:
                raise RuntimeError(
                    "PostgreSQL restore/verification failed; coordinator remains stopped"
                )
        execute(*KUBECTL, "-n", "company-brain", "scale", "deployment/orchestrator", "--replicas=1")
        execute(
            *KUBECTL,
            "-n",
            "company-brain",
            "rollout",
            "status",
            "deployment/orchestrator",
            "--timeout=210s",
        )
        maintenance(False)
        print(f"POSTGRES_RESTORE_OK {backup_id}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=[
            "deploy",
            "rollback",
            "secret-sync",
            "backup",
            "restore",
            "maintenance",
            "storage-cutover",
        ],
    )
    parser.add_argument("--release-id")
    parser.add_argument("--bucket")
    parser.add_argument("--sha256")
    parser.add_argument("--previous", action="store_true")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--respan-secret")
    parser.add_argument("--scalekit-secret")
    parser.add_argument("--database-secret")
    parser.add_argument("--brain-api-secret")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--backup-id")
    parser.add_argument("--resume", action="store_true")
    ARGS = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = runtime_config()
        for name in (
            "bucket",
            "region",
            "respan_secret",
            "scalekit_secret",
            "database_secret",
            "brain_api_secret",
        ):
            value = getattr(ARGS, name)
            if value:
                config[name] = value
        atomic_json(STATE / "runtime-config.json", config)
        if ARGS.operation == "deploy":
            deploy(ARGS.release_id, ARGS.bucket, ARGS.sha256)
        elif ARGS.operation == "rollback":
            rollback(ARGS.previous)
        elif ARGS.operation == "secret-sync":
            sync_secrets(ARGS.restart)
        elif ARGS.operation == "backup":
            backup(config["bucket"])
        elif ARGS.operation == "restore":
            restore(config["bucket"], ARGS.backup_id)
        elif ARGS.operation == "storage-cutover":
            storage_cutover(config["bucket"])
        else:
            print(json.dumps(maintenance(False if ARGS.resume else None)))
