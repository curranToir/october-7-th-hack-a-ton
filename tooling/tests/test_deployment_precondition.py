"""A guarded rollout cannot overwrite an intervening operator's release."""

import fcntl
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import manage
import pytest
import remote_apply

EXPECTED = "2b0d15355c55-fa3fc4ddf60d"
OTHER = "a32aea998053-7e85693a34ed"


@pytest.mark.parametrize("current", [EXPECTED, OTHER, None])
def test_expected_release_compares_exact_persisted_identity(tmp_path, monkeypatch, current):
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    if current:
        (tmp_path / "state.json").write_text(json.dumps({"current": current}))
    if current == EXPECTED:
        remote_apply.require_expected_release(EXPECTED)
    else:
        with pytest.raises(RuntimeError, match="Deployment precondition failed"):
            remote_apply.require_expected_release(EXPECTED)
    remote_apply.require_expected_release(None)  # Existing unguarded callers retain behavior.


def test_cli_checks_precondition_inside_lock_before_runtime_changes(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text(json.dumps({"current": OTHER}))
    config = state / "runtime-config.json"
    config.write_text('{"region":"us-east-1"}')
    runner = tmp_path / "runner.py"
    source = (
        Path(remote_apply.__file__)
        .read_text()
        .replace(
            'STATE = Path("/var/lib/company-brain")',
            f"STATE = Path({str(state)!r})",
            1,
        )
    )
    runner.write_text(source)
    command = [
        sys.executable,
        str(runner),
        "deploy",
        "--expected-release",
        EXPECTED,
        "--region",
        "eu-west-1",
    ]
    with (state / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = subprocess.run(command, capture_output=True, text=True)
        assert blocked.returncode != 0
        assert "BlockingIOError" in blocked.stderr
        assert "Deployment precondition failed" not in blocked.stderr
    mismatch = subprocess.run(command, capture_output=True, text=True)
    assert mismatch.returncode != 0
    assert "Deployment precondition failed" in mismatch.stderr
    assert config.read_text() == '{"region":"us-east-1"}'
    assert json.loads((state / "state.json").read_text())["current"] == OTHER
    assert not (state / "releases").exists()  # No download, drain, backup or image import ran.


def test_manage_forwards_optional_precondition_to_locked_runner(tmp_path, monkeypatch):
    payload = b"checked local release"
    digest = hashlib.sha256(payload).hexdigest()
    archive = tmp_path / ("a" * 12 + "-" + digest[:12] + ".tar.gz")
    archive.write_bytes(payload)

    class FakeAws:
        region = "us-east-1"

        def outputs(self):
            return {"ArtifactBucket": "bucket", "InstanceId": "instance"}

        def wait_online(self, instance):
            assert instance == "instance"

        def call(self, *args, **kwargs):
            assert args[:2] == ("s3", "cp")

        def run_ssm(self, instance, script, timeout):
            self.arguments = script

    aws = FakeAws()
    monkeypatch.setattr(manage, "remote_script", lambda arguments: arguments)
    manage.deploy(aws, archive, EXPECTED)
    assert aws.arguments[-2:] == ["--expected-release", EXPECTED]
    manage.deploy(aws, archive)
    assert "--expected-release" not in aws.arguments


def test_invalid_expected_release_rejected_before_cloud_access():
    with pytest.raises(ValueError, match="complete release ID"):
        manage.deploy(None, None, "latest")
