"""Build an immutable amd64 release from a frozen snapshot, never on the EC2 host."""

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT = ROOT / ".deployment"
SERVICES = ("web", "api", "orchestrator", "research")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(destination: Path) -> tuple[str, str]:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode().split("\0")
    digest = hashlib.sha256()
    for relative in sorted(set(filter(None, paths))):
        source = ROOT / relative
        if source.is_symlink():
            raise RuntimeError(f"Release source must not contain symlinks: {relative}")
        if not source.is_file():
            continue  # A tracked file can be deleted in the working tree.
        content = source.read_bytes()
        digest.update(relative.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return revision, digest.hexdigest()


def render(template: Path, images: dict[str, str], revision: str, source_hash: str) -> str:
    text = template.read_text()
    for name, image in images.items():
        text = text.replace(f"__{name.upper()}_IMAGE__", image)
    if "_IMAGE__" in text:
        raise ValueError("Unresolved image placeholder")
    manifest = json.loads(text)
    for resource in manifest["items"]:
        if resource["kind"] == "Deployment":
            resource["spec"]["template"]["metadata"]["annotations"] = {
                "company-brain/source-revision": revision,
                "company-brain/source-hash": source_hash,
            }
    return json.dumps(manifest, indent=2) + "\n"


def build() -> Path:
    output = DEPLOYMENT / "releases"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="company-brain-build-") as temp:
        work = Path(temp)
        source = work / "source"
        source.mkdir()
        revision, source_hash = snapshot(source)
        tag = f"{revision[:12]}-{source_hash[:12]}"
        images = {name: f"company-brain.local/{name}:{tag}" for name in SERVICES}
        for name, image in images.items():
            subprocess.run(
                ["docker", "buildx", "build", "--platform", "linux/amd64", "--load",
                 "--provenance=false", "--tag", image, "--file",
                 str(source / f"infrastructure/docker/{name}.Dockerfile"), str(source)],
                check=True,
            )
        bundle = work / "bundle"
        bundle.mkdir()
        subprocess.run(
            ["docker", "save", "--output", str(bundle / "images.tar"), *images.values()],
            check=True,
        )
        (bundle / "manifests.json").write_text(render(
            source / "infrastructure/deployment/kubernetes/foundation.json",
            images, revision, source_hash,
        ))
        (bundle / "release.json").write_text(json.dumps({
            "source_revision": revision, "source_hash": source_hash,
            "images": images, "platform": "linux/amd64",
        }, indent=2) + "\n")
        checksums = {p.name: sha256(p) for p in sorted(bundle.iterdir())}
        (bundle / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n")
        archive = work / "release.tar.gz"
        with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
            for path in sorted(bundle.iterdir()):
                tar.add(path, arcname=path.name)
        artifact_hash = sha256(archive)
        release_id = f"{revision[:12]}-{artifact_hash[:12]}"
        result = output / f"{release_id}.tar.gz"
        shutil.move(archive, result)
        result.with_suffix(".sha256").write_text(artifact_hash + "\n")
        (DEPLOYMENT / "latest-build.json").write_text(json.dumps({
            "release_id": release_id, "archive": str(result), "sha256": artifact_hash,
        }, indent=2) + "\n")
        print(f"Built {result}\nSHA256 {artifact_hash}", flush=True)
        return result
