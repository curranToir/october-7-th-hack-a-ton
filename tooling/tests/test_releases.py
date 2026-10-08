import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
import remote_apply
from releases import ROOT, render


def release_archive(tmp_path: Path, extra=None, corrupt=False):
    files = {"images.tar": b"image", "manifests.json": b"{}", "release.json": b"{}"}
    checksums = {
        name: remote_apply.hashlib.sha256(data).hexdigest() for name, data in files.items()
    }
    files["checksums.json"] = json.dumps(checksums).encode()
    if corrupt:
        files["images.tar"] = b"modified"
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
        if extra:
            tar.addfile(extra)
    output = tmp_path / "output"
    output.mkdir()
    return archive, output, remote_apply.sha256(archive)


def test_unpack_checks_every_file(tmp_path):
    archive, output, digest = release_archive(tmp_path)
    remote_apply.unpack(archive, output, digest)
    assert {p.name for p in output.iterdir()} == remote_apply.FILES


def test_unpack_rejects_changed_archive(tmp_path):
    archive, output, _ = release_archive(tmp_path)
    with pytest.raises(ValueError, match="archive checksum"):
        remote_apply.unpack(archive, output, "0" * 64)
    assert not list(output.iterdir())


def test_unpack_rejects_changed_contents(tmp_path):
    archive, output, digest = release_archive(tmp_path, corrupt=True)
    with pytest.raises(ValueError, match="Checksum mismatch: images.tar"):
        remote_apply.unpack(archive, output, digest)


def test_unpack_rejects_path_traversal(tmp_path):
    archive, output, digest = release_archive(tmp_path, extra=tarfile.TarInfo("../escape"))
    with pytest.raises(ValueError, match="Unexpected release contents"):
        remote_apply.unpack(archive, output, digest)
    assert not (tmp_path / "escape").exists()


def test_unpack_rejects_symlinks(tmp_path):
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in remote_apply.FILES:
            member = tarfile.TarInfo(name)
            if name == "images.tar":
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
            tar.addfile(member)
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError, match="regular files"):
        remote_apply.unpack(archive, output, remote_apply.sha256(archive))


def test_failed_apply_does_not_change_recorded_success(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    archive, _, digest = release_archive(build_dir)
    state_dir = tmp_path / "server"
    state_dir.mkdir()
    state_file = state_dir / "state.json"
    state = {"current": "1" * 12 + "-" + "2" * 12, "previous": None}
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(remote_apply, "STATE", state_dir)

    def download(*args):
        assert args[:3] == ("aws", "s3", "cp")
        shutil.copyfile(archive, args[4])

    def fail_apply(folder):
        raise RuntimeError("rollout failed")

    monkeypatch.setattr(remote_apply, "execute", download)
    monkeypatch.setattr(remote_apply, "apply_release", fail_apply)
    monkeypatch.setattr(remote_apply, "diagnostics", lambda: None)
    with pytest.raises(RuntimeError, match="rollout failed"):
        remote_apply.deploy("a" * 12 + "-" + digest[:12], "bucket", digest)
    assert json.loads(state_file.read_text()) == state


@pytest.mark.parametrize("release_id", ["../escape", "", "abc", "0" * 12 + ";whoami"])
def test_release_id_cannot_be_used_as_a_path(release_id):
    with pytest.raises(ValueError):
        remote_apply.validate_id(release_id)


def test_render_deploys_foundation_and_research():
    names = ("web", "api", "orchestrator", "research", "contacts", "meetings")
    images = {name: f"company-brain.local/{name}:test" for name in names}
    rendered = render(
        ROOT / "infrastructure/deployment/kubernetes/foundation.json",
        images,
        "source-sha",
        "source-hash",
    )
    resources = json.loads(rendered)["items"]
    deployments = [r for r in resources if r["kind"] == "Deployment"]
    assert {r["metadata"]["name"] for r in deployments} == set(names)
    for deployment in deployments:
        pod = deployment["spec"]["template"]["spec"]
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 0
        assert pod["automountServiceAccountToken"] is False
        assert pod["containers"][0]["imagePullPolicy"] == "Never"
        assert pod["containers"][0]["image"] == images[deployment["metadata"]["name"]]
    ingress = next(r for r in resources if r["kind"] == "Ingress")
    paths = ingress["spec"]["rules"][0]["http"]["paths"]
    assert {p["backend"]["service"]["name"] for p in paths} == {"web", "api"}


def test_failed_deploy_preserves_last_good_release(tmp_path, monkeypatch):
    state = {"current": "1" * 12 + "-" + "2" * 12, "previous": None}
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    folder = tmp_path / "releases" / state["current"]
    folder.mkdir(parents=True)
    for name in ("images.tar", "manifests.json", "release.json"):
        (folder / name).write_text("test")
    checksums = {p.name: remote_apply.sha256(p) for p in folder.iterdir()}
    (folder / "checksums.json").write_text(json.dumps(checksums))
    called = []
    monkeypatch.setattr(remote_apply, "apply_release", called.append)
    remote_apply.rollback()
    assert called == [folder]
    assert json.loads(state_file.read_text()) == state
