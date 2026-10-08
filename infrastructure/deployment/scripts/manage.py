#!/usr/bin/env python3
"""Operator CLI: python3 infrastructure/deployment/scripts/manage.py --help."""

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from aws_ops import Aws
from releases import DEPLOYMENT, ROOT, build, sha256
from runtime_ops import SECRET_FIELDS, set_secret, stack_update

HERE = Path(__file__).resolve().parent
BOOTSTRAP_CHECK = """set -eu
if ! cloud-init status --wait; then
  tail -n 100 /var/log/company-brain-bootstrap.log
  exit 1
fi
test -f /var/lib/company-brain/bootstrap-complete
k3s kubectl wait --for=condition=Ready node --all --timeout=300s
for attempt in $(seq 1 60); do
  if k3s kubectl -n kube-system get deployment traefik >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
k3s kubectl -n kube-system rollout status deployment/traefik --timeout=300s
k3s kubectl get nodes -o wide
"""


INGRESS_WAIT = """ingress_ready=0
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1/api/ready >/dev/null 2>&1; then
    ingress_ready=1
    break
  fi
  sleep 2
done
if [ "$ingress_ready" -ne 1 ]; then
  echo 'Ingress did not become ready after 60 attempts' >&2
  exit 1
fi
"""


def provision(aws: Aws):
    try:
        outputs = aws.outputs()
        print("Stack already exists; preserving its infrastructure. Waiting for readiness.")
    except subprocess.CalledProcessError as error:
        if "does not exist" not in (error.stderr or ""):
            raise
        aws.call(
            "cloudformation",
            "deploy",
            "--template-file",
            str(HERE.parent / "cloudformation/stack.yaml"),
            "--stack-name",
            aws.stack,
            "--capabilities",
            "CAPABILITY_IAM",
            "--tags",
            "Project=company-brain-hackathon",
            "--no-fail-on-empty-changeset",
            capture=False,
        )
        outputs = aws.outputs()
    aws.wait_online(outputs["InstanceId"])
    aws.run_ssm(outputs["InstanceId"], BOOTSTRAP_CHECK, timeout=1500)
    DEPLOYMENT.mkdir(exist_ok=True)
    (DEPLOYMENT / "infrastructure.json").write_text(json.dumps(outputs, indent=2) + "\n")
    print(json.dumps(outputs, indent=2))


def remote_script(arguments: list[str]) -> str:
    content = (HERE / "remote_apply.py").read_bytes()
    encoded = base64.b64encode(content).decode()
    runner = f"/var/lib/company-brain/runner-{hashlib.sha256(content).hexdigest()[:16]}.py"
    # Every argument is quoted as a shell token; never interpolate raw user input.
    return "\n".join(
        [
            "set -eu",
            "umask 077",
            "mkdir -p /var/lib/company-brain",
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(runner)}",
            shlex.join(["python3", "-u", runner, *arguments]),
        ]
    )


def runtime_arguments(aws: Aws, outputs: dict) -> list[str]:
    args = ["--region", aws.region, "--bucket", outputs["ArtifactBucket"]]
    for output, flag in (
        ("RespanSecretArn", "--respan-secret"),
        ("ScalekitSecretArn", "--scalekit-secret"),
        ("DatabaseSecretArn", "--database-secret"),
        ("BrainApiSecretArn", "--brain-api-secret"),
        ("MeetingsProviderSecretArn", "--meetings-provider-secret"),
    ):
        if outputs.get(output):
            args.extend([flag, outputs[output]])
    return args


def deploy(aws: Aws, archive: Path | None):
    if archive is None:
        record = DEPLOYMENT / "latest-build.json"
        if not record.exists():
            raise RuntimeError("No build found; run the build command first")
        archive = Path(json.loads(record.read_text())["archive"])
    archive = archive.resolve()
    release_id = archive.name.removesuffix(".tar.gz")
    digest = sha256(archive)
    if not re.fullmatch(r"[0-9a-f]{12}-[0-9a-f]{12}", release_id):
        raise ValueError("Expected a release archive produced by the build command")
    if not release_id.endswith(digest[:12]):
        raise ValueError("Archive hash does not match its release ID")
    outputs = aws.outputs()
    bucket = outputs["ArtifactBucket"]
    instance = outputs["InstanceId"]
    aws.wait_online(instance)
    aws.call(
        "s3",
        "cp",
        str(archive),
        f"s3://{bucket}/releases/{release_id}.tar.gz",
        "--only-show-errors",
        capture=False,
    )
    aws.run_ssm(
        instance,
        remote_script(
            [
                "deploy",
                "--release-id",
                release_id,
                "--sha256",
                digest,
                *runtime_arguments(aws, outputs),
            ]
        ),
        timeout=2400,
    )
    print(f"Deployed {release_id}. Run the tunnel command to open the app.")


def verify(aws: Aws, agent_template: bool):
    commands = [
        "set -eu",
        "for attempt in $(seq 1 60); do "
        "if systemctl is-active --quiet k3s && "
        "k3s kubectl --request-timeout=5s get nodes >/dev/null 2>&1; then break; fi; "
        "sleep 5; done",
        "systemctl is-active --quiet k3s",
        "k3s kubectl wait --for=condition=Ready node --all --timeout=300s",
        "k3s kubectl -n kube-system rollout status deployment/traefik --timeout=300s",
        "k3s kubectl -n company-brain rollout status deployment/web --timeout=210s",
        "k3s kubectl -n company-brain rollout status deployment/api --timeout=210s",
        "k3s kubectl -n company-brain rollout status deployment/orchestrator --timeout=210s",
        "if k3s kubectl -n company-brain get deployment research >/dev/null 2>&1; then "
        "k3s kubectl -n company-brain rollout status deployment/research --timeout=210s; fi",
        "if k3s kubectl -n company-brain get deployment contacts >/dev/null 2>&1; then "
        "k3s kubectl -n company-brain rollout status deployment/contacts --timeout=210s; fi",
        "if k3s kubectl -n company-brain get deployment meetings >/dev/null 2>&1; then "
        "k3s kubectl -n company-brain rollout status deployment/meetings --timeout=210s; fi",
        "if k3s kubectl -n company-brain get deployment meetings >/dev/null 2>&1; then "
        "k3s kubectl -n company-brain exec deployment/orchestrator -- python -c "
        + shlex.quote(
            "import urllib.request; print(urllib.request.urlopen("
            "'http://meetings:8000/ready', timeout=5).read().decode())"
        )
        + "; fi",
        INGRESS_WAIT,
        "if k3s kubectl -n company-brain get deployment research >/dev/null 2>&1; then "
        "curl -fsS --max-time 10 http://127.0.0.1/research | grep -o 'Research' | head -n 1; "
        "else curl -fsS --max-time 10 http://127.0.0.1/ | "
        "grep -o 'No agents configured'; fi",
        "curl -fsS --max-time 10 http://127.0.0.1/api/health",
        "curl -fsS --max-time 10 http://127.0.0.1/api/ready",
        "k3s kubectl -n company-brain exec deployment/api -- python -c "
        + shlex.quote(
            "import urllib.request; print(urllib.request.urlopen("
            "'http://orchestrator:8000/ready', timeout=5).read().decode())"
        ),
        'test "$(k3s kubectl auth can-i create deployments '
        '--as=system:serviceaccount:company-brain:application -n company-brain)" = no',
        "k3s kubectl -n company-brain get deployment,pods,services,ingress",
        "k3s kubectl top nodes",
        "k3s kubectl top pods -A",
        "df -h /",
        "cat /var/lib/company-brain/state.json",
    ]
    if agent_template:
        template = (ROOT / "tooling/templates/agent.yaml").read_text()
        encoded = base64.b64encode(template.encode()).decode()
        commands += [
            "agent_manifest=$(mktemp)",
            "trap 'k3s kubectl -n company-brain delete deployment/agent-template-check "
            "service/agent-template-check networkpolicy/agent-template-check-ingress "
            '--ignore-not-found; rm -f "$agent_manifest"\' EXIT',
            f'printf %s {shlex.quote(encoded)} | base64 -d > "$agent_manifest"',
            "agent_image=$(k3s kubectl -n company-brain get deployment orchestrator "
            "-o jsonpath='{.spec.template.spec.containers[0].image}')",
            'sed -i "s|__AGENT_NAME__|agent-template-check|g; '
            's|__AGENT_IMAGE__|$agent_image|g" "$agent_manifest"',
            'k3s kubectl apply -f "$agent_manifest"',
            "k3s kubectl -n company-brain set env deployment/agent-template-check "
            "TOIR_DATA_DIR=/tmp/toir-template",
            "k3s kubectl -n company-brain rollout status deployment/agent-template-check "
            "--timeout=210s",
            "k3s kubectl -n company-brain exec deployment/orchestrator -- python -c "
            + shlex.quote(
                "import urllib.request; print(urllib.request.urlopen("
                "'http://agent-template-check:8000/ready', timeout=5).read().decode())"
            ),
        ]
    aws.run_ssm(aws.outputs()["InstanceId"], "\n".join(commands), timeout=1200)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=os.getenv("AWS_ACCOUNT_ID", "904469541651"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--stack", default=os.getenv("STACK_NAME", "company-brain-hackathon"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("provision", help="Create a new stack, or verify an existing one")
    commands.add_parser("stack-update", help="Add runtime secrets/IAM and backup lifecycle safely")
    secret = commands.add_parser(
        "secret-set", help="Store a secret from hidden prompts or JSON stdin"
    )
    secret.add_argument("--name", choices=tuple(SECRET_FIELDS), required=True)
    secret.add_argument(
        "--stdin", action="store_true", help="Read the complete JSON object from stdin"
    )
    sync = commands.add_parser("secret-sync", help="Synchronize AWS runtime secrets to Kubernetes")
    sync.add_argument(
        "--restart", action="store_true", help="Drain and restart pods to activate values"
    )
    commands.add_parser(
        "backup", help="Drain and upload a consistent active-database backup to private S3"
    )
    commands.add_parser(
        "storage-cutover",
        help="Back up both stores, migrate SQLite history, verify and activate PostgreSQL",
    )
    restore_command = commands.add_parser(
        "restore", help="Preserve current state then restore a backup"
    )
    restore_command.add_argument("--backup-id", required=True)
    maintenance_command = commands.add_parser(
        "maintenance", help="Show or clear admission maintenance"
    )
    maintenance_command.add_argument("--resume", action="store_true")
    commands.add_parser("build", help="Build linux/amd64 images and a checked release archive")
    deployment = commands.add_parser("deploy", help="Deploy a built release through SSM")
    deployment.add_argument("--archive", type=Path)
    rollback = commands.add_parser("rollback", help="Restore the last successful release")
    rollback.add_argument("--previous", action="store_true", help="Switch to its predecessor")
    tunnel = commands.add_parser("tunnel", help="Open the app at http://localhost:8080")
    tunnel.add_argument("--port", type=int, default=8080)
    commands.add_parser("status", help="Show server, pods, usage, and release state")
    verification = commands.add_parser("verify", help="Check the running scaffold")
    verification.add_argument("--agent-template", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        build()
        return
    aws = Aws(args.account, args.region, args.stack)
    aws.verify_account()
    if args.command == "provision":
        provision(aws)
    elif args.command == "stack-update":
        stack_update(aws, HERE.parent / "cloudformation/stack.yaml")
    elif args.command == "secret-set":
        set_secret(aws, args.name, args.stdin)
    elif args.command in {"secret-sync", "backup", "restore", "maintenance", "storage-cutover"}:
        outputs = aws.outputs()
        arguments = [args.command, *runtime_arguments(aws, outputs)]
        if args.command == "secret-sync" and args.restart:
            arguments.append("--restart")
        if args.command == "restore":
            arguments.extend(["--backup-id", args.backup_id])
        if args.command == "maintenance" and args.resume:
            arguments.append("--resume")
        aws.run_ssm(outputs["InstanceId"], remote_script(arguments), timeout=1800)
    elif args.command == "deploy":
        deploy(aws, args.archive)
    elif args.command == "rollback":
        outputs = aws.outputs()
        arguments = ["rollback", *runtime_arguments(aws, outputs)]
        if args.previous:
            arguments.append("--previous")
        aws.run_ssm(outputs["InstanceId"], remote_script(arguments), timeout=2400)
    elif args.command == "tunnel":
        if not 1024 <= args.port <= 65535:
            raise ValueError("Choose a local port between 1024 and 65535")
        print(f"Open http://localhost:{args.port}; leave this command running.", flush=True)
        aws.call(
            "ssm",
            "start-session",
            "--target",
            aws.outputs()["InstanceId"],
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            json.dumps({"portNumber": ["80"], "localPortNumber": [str(args.port)]}),
            capture=False,
        )
    elif args.command == "verify":
        verify(aws, args.agent_template)
    else:
        aws.run_ssm(
            aws.outputs()["InstanceId"],
            "\n".join(
                [
                    "set -eu",
                    "k3s kubectl get nodes -o wide",
                    "k3s kubectl -n company-brain get deployment,pods,services,ingress",
                    "k3s kubectl top nodes",
                    "k3s kubectl top pods -A",
                    "df -h /",
                    "cat /var/lib/company-brain/state.json",
                ]
            ),
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        sys.exit(1)
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
