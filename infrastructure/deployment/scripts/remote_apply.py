"""Runs as root through SSM. No third-party Python packages are required."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

STATE = Path("/var/lib/company-brain")
SERVICES = ("web", "api", "orchestrator")
KUBECTL = ["k3s", "kubectl", "--request-timeout=30s"]
FILES = {"images.tar", "manifests.json", "release.json", "checksums.json"}


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


def probe_ingress():
    # curl gets a real HTTP response through the same ingress used by SSM forwarding.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            page = execute("curl", "-fsS", "--max-time", "5", "http://127.0.0.1/", capture=True)
            if "No agents configured" not in page:
                raise ValueError("Unexpected web release response")
            for endpoint in ("health", "ready"):
                result = execute(
                    "curl", "-fsS", "--max-time", "5", f"http://127.0.0.1/api/{endpoint}",
                    capture=True,
                )
                if json.loads(result) != {"status": "ok", "service": "api"}:
                    raise ValueError("Unexpected API probe response")
            return
        except (subprocess.CalledProcessError, ValueError):
            time.sleep(3)
    raise RuntimeError("Ingress checks did not pass")


def diagnostics():
    for args in (
        ["get", "pods", "-o", "wide"],
        ["get", "events", "--sort-by=.lastTimestamp"],
        ["describe", "pods"],
    ):
        subprocess.run([*KUBECTL, "-n", "company-brain", *args], check=False)
    for name in SERVICES:
        subprocess.run(
            [*KUBECTL, "-n", "company-brain", "logs", f"deployment/{name}", "--tail=40"],
            check=False,
        )


def apply_release(folder: Path):
    # Import before changing any workloads; Never prevents accidental registry pulls.
    execute("k3s", "ctr", "-n", "k8s.io", "images", "import", str(folder / "images.tar"))
    execute(*KUBECTL, "apply", "-f", str(folder / "manifests.json"))
    for service in SERVICES:
        execute(*KUBECTL, "-n", "company-brain", "rollout", "status",
                f"deployment/{service}", "--timeout=210s")
    probe_ingress()


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
        execute("aws", "s3", "cp", f"s3://{bucket}/releases/{release_id}.tar.gz", str(archive),
                "--only-show-errors")
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
        try:
            apply_release(final)
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
    try:
        apply_release(folder)
    except Exception:
        diagnostics()
        raise
    if target != state.get("current"):
        atomic_json(state_file, {"current": target, "previous": state.get("current")})
    print(f"ROLLBACK_OK {target}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["deploy", "rollback"])
    parser.add_argument("--release-id")
    parser.add_argument("--bucket")
    parser.add_argument("--sha256")
    parser.add_argument("--previous", action="store_true")
    ARGS = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if ARGS.operation == "deploy":
            deploy(ARGS.release_id, ARGS.bucket, ARGS.sha256)
        else:
            rollback(ARGS.previous)
