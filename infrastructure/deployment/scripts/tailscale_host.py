#!/usr/bin/env python3
"""Install/join/check the existing K3s host through SSM; no cloud topology changes."""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TAILSCALE_VERSION = "1.104.1"
KEYRING_SHA256 = "3e03dacf222698c60b8e2f990b809ca1b3e104de127767864284e6c228f1fb39"
PACKAGE_SHA256 = "509753a4ff223efc70234e7f539afc51f743b3af043d9e55313960bd7327700f"
TAILNET = "neptuneops.com"
DNS_SUFFIX = "taild4c940.ts.net"


def command(*args: str) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def status() -> dict:
    result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("tailscaled status is unavailable; run install first") from None


def public_status(value: dict) -> dict:
    # No peer inventory, key material, preferences or state-file contents in SSM output.
    return {
        "state": value.get("BackendState"),
        "hostname": (value.get("Self") or {}).get("HostName"),
        "ips": value.get("TailscaleIPs") or [],
        "tailnet": (value.get("CurrentTailnet") or {}).get("Name"),
        "dns_suffix": (value.get("CurrentTailnet") or {}).get("MagicDNSSuffix"),
    }


def install():
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise RuntimeError("This pinned installer supports Ubuntu 24.04 only")
    if platform.machine() != "x86_64":
        raise RuntimeError("This installer is pinned for amd64")
    key_url = "https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg"
    with urllib.request.urlopen(key_url, timeout=30) as response:
        keyring = response.read()
    if hashlib.sha256(keyring).hexdigest() != KEYRING_SHA256:
        raise RuntimeError("Official Tailscale signing key changed; review before updating the pin")
    key_path = Path("/usr/share/keyrings/tailscale-archive-keyring.gpg")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(keyring)
    key_path.chmod(0o644)
    Path("/etc/apt/sources.list.d/tailscale.list").write_text(
        "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] "
        "https://pkgs.tailscale.com/stable/ubuntu noble main\n"
    )
    command(
        "apt-get",
        "update",
        "-o",
        "Dir::Etc::sourcelist=sources.list.d/tailscale.list",
        "-o",
        "Dir::Etc::sourceparts=-",
        "-o",
        "APT::Get::List-Cleanup=0",
    )
    metadata = command("apt-cache", "show", f"tailscale={TAILSCALE_VERSION}")
    if f"SHA256: {PACKAGE_SHA256}" not in metadata:
        raise RuntimeError("Pinned package checksum is absent from the signed APT metadata")
    command("apt-get", "install", "-y", f"tailscale={TAILSCALE_VERSION}")
    command("apt-mark", "hold", "tailscale")
    command("systemctl", "enable", "--now", "tailscaled")
    command("systemctl", "is-active", "--quiet", "tailscaled")
    print(json.dumps({"installed_version": TAILSCALE_VERSION, "service": "enabled and active"}))


def assert_tailnet(value: dict, expected: str):
    if value.get("BackendState") == "Running":
        actual = (value.get("CurrentTailnet") or {}).get("Name")
        if actual != expected:
            raise RuntimeError(f"Device belongs to tailnet {actual!r}, expected {expected!r}")


def join(args):
    before = status()
    assert_tailnet(before, args.tailnet)
    flags = [
        "tailscale",
        "up",
        f"--hostname={args.hostname}",
        "--accept-dns=false",
        "--accept-routes=false",
        "--ssh=false",
        "--advertise-routes=",
        "--advertise-exit-node=false",
        "--exit-node=",
        "--shields-up=true",
        "--timeout=30s",
    ]
    key = None
    if args.secret_id:
        fetched = subprocess.run(
            [
                "aws",
                "--region",
                args.region,
                "--no-cli-pager",
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                args.secret_id,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
        )
        if fetched.returncode:
            raise RuntimeError(
                "Cannot read Tailscale auth secret; verify exact-ARN host IAM access"
            )
        try:
            payload = json.loads(json.loads(fetched.stdout)["SecretString"])
            key = payload["TS_AUTH_KEY"]
            if not isinstance(key, str) or not key.startswith("tskey-auth-") or "\n" in key:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Expected a Secrets Manager JSON object containing TS_AUTH_KEY"
            ) from None
        # Tailscale opens stdin as a file. Only the fixed file path appears in argv/SSM.
        flags.append("--auth-key=file:/dev/stdin")
    result = subprocess.run(flags, input=key, capture_output=True, text=True, timeout=45)
    current = status()
    assert_tailnet(current, args.tailnet)
    print(json.dumps(public_status(current)))
    if current.get("BackendState") == "Running":
        print("TAILSCALE_CONNECTED")
        return
    if not args.secret_id:
        url = current.get("AuthURL")
        if not url:
            match = re.search(
                r"https://login\.tailscale\.com/a/[A-Za-z0-9]+", result.stdout + result.stderr
            )
            url = match.group(0) if match else None
        if url and re.fullmatch(r"https://login\.tailscale\.com/a/[A-Za-z0-9]+", url):
            print(f"LOGIN_REQUIRED {url}")
            print(f"Approve this device in {args.tailnet}, then run status and verify.")
            return  # Login pending is reported explicitly; it is not a connected result.
    if current.get("BackendState") == "NeedsMachineAuth":
        print("DEVICE_APPROVAL_REQUIRED: approve this device in the Tailscale admin console.")
        return
    # Never echo command output from an authentication attempt; providers can echo credentials.
    raise RuntimeError("Tailscale did not connect; inspect its device approval/login state")


def verify(args):
    value = status()
    assert_tailnet(value, args.tailnet)
    if value.get("BackendState") != "Running":
        raise RuntimeError("Tailscale is not connected")
    print(json.dumps(public_status(value)))
    command("systemctl", "is-enabled", "--quiet", "tailscaled")
    command("systemctl", "is-active", "--quiet", "tailscaled")
    if command("sysctl", "-n", "net.ipv4.ip_forward") != "1":
        raise RuntimeError("IPv4 forwarding is disabled; repair K3s forwarding before pod testing")
    if not args.peer or not args.port:
        raise ValueError("verify requires --peer (a Tailscale IPv4 address) and --port")
    peer = ipaddress.IPv4Address(args.peer)
    if peer not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise ValueError("Use the confirmed Spark Tailscale IPv4 address")
    route = command("ip", "route", "get", str(peer))
    if "dev tailscale0" not in route:
        raise RuntimeError("Spark route does not use tailscale0")
    print(route)
    # TCP reachability is distinct from database credentials/TLS or application authorization.
    program = f"""import json,socket,time
started=time.monotonic()
try:
 with socket.create_connection(({str(peer)!r},{args.port}),timeout=8):
  pass
 result={{'tcp_connected':True}}
except OSError as error:
 result={{'tcp_connected':False,'error_class':type(error).__name__,'errno':error.errno}}
result['elapsed_ms']=round((time.monotonic()-started)*1000)
print(json.dumps(result))
"""
    outcomes = []
    for scope, run in (
        ("host", lambda: command("python3", "-c", program)),
        ("coordinator_pod", lambda: pod_python(program)),
    ):
        try:
            result = json.loads(run())
            # Only allow our probe's bounded diagnostic fields into SSM output.
            outcome = {
                k: result.get(k)
                for k in ("tcp_connected", "error_class", "errno", "elapsed_ms")
                if k in result
            }
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:
            outcome = {"tcp_connected": False, "probe_error_class": type(error).__name__}
        outcome.update({"scope": scope, "peer": str(peer), "port": args.port})
        outcomes.append(outcome)
        print(json.dumps(outcome), flush=True)
    if not all(outcome.get("tcp_connected") is True for outcome in outcomes):
        raise RuntimeError("TCP verification failed; host and pod outcomes are reported above")
    print("POD_TAILNET_TCP_VERIFIED")


def dns_manifest(suffix: str) -> dict:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net", suffix):
        raise ValueError("Use the exact tailnet MagicDNS suffix ending in .ts.net")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "coredns-custom", "namespace": "kube-system"},
        "data": {
            "tailnet.server": f"{suffix}:53 {{\n  errors\n  cache 30\n"
            "  forward . 100.100.100.100\n}\n"
        },
    }


def pod_python(program: str) -> str:
    result = subprocess.run(
        [
            "k3s",
            "kubectl",
            "--request-timeout=15s",
            "-n",
            "company-brain",
            "exec",
            "deployment/orchestrator",
            "--",
            "python",
            "-c",
            program,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return result.stdout.strip()


def configure_dns(args):
    value = status()
    assert_tailnet(value, args.tailnet)
    if value.get("BackendState") != "Running":
        raise RuntimeError("Connect and approve Tailscale before configuring pod DNS")
    actual_suffix = (value.get("CurrentTailnet") or {}).get("MagicDNSSuffix")
    if actual_suffix != args.dns_suffix:
        raise RuntimeError("Requested DNS suffix does not match the connected tailnet")
    manifest = dns_manifest(args.dns_suffix)
    name = (args.peer_name or "").lower().rstrip(".")
    if (
        not name.endswith("." + args.dns_suffix)
        or len(name) > 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in name.split(".")
        )
    ):
        raise ValueError("dns requires --peer-name with the confirmed Spark FQDN in this tailnet")
    core = json.loads(
        command("k3s", "kubectl", "-n", "kube-system", "get", "configmap", "coredns", "-o", "json")
    )
    deployment = json.loads(
        command("k3s", "kubectl", "-n", "kube-system", "get", "deployment", "coredns", "-o", "json")
    )
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    if "import /etc/coredns/custom/*.server" not in core.get("data", {}).get(
        "Corefile", ""
    ) or not any(v.get("configMap", {}).get("name") == "coredns-custom" for v in volumes):
        raise RuntimeError(
            "Installed CoreDNS does not support the expected custom configuration mount"
        )
    # Prove this pod can use MagicDNS directly before changing cluster configuration.
    direct_query = f"""import json,secrets,socket,struct
name={name!r}
query_id=secrets.randbelow(65536)
question=b''.join(bytes([len(label)])+label.encode() for label in name.split('.'))+b'\\0'
packet=struct.pack('!HHHHHH',query_id,256,1,0,0,0)+question+struct.pack('!HH',1,1)
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as resolver:
 resolver.settimeout(8)
 resolver.connect(('100.100.100.100',53))
 resolver.send(packet)
 data=resolver.recv(4096)
ident,flags,questions,answers,_,_=struct.unpack('!HHHHHH',data[:12])
assert ident==query_id and flags&32768 and not flags&15 and answers>0, 'No valid MagicDNS answer'
print(json.dumps({{'magic_dns_reachable':True,'name':name,'answers':answers}}))
"""
    print(pod_python(direct_query))
    subprocess.run(
        ["k3s", "kubectl", "apply", "--server-side", "--field-manager=toir-tailnet-dns", "-f", "-"],
        input=json.dumps(manifest),
        text=True,
        capture_output=True,
        check=True,
    )
    # The projected ConfigMap and CoreDNS reload plugin update without restarting cluster DNS.
    normal_query = (
        "import ipaddress,json,socket; "
        f"ips=sorted({{r[4][0] for r in socket.getaddrinfo({name + '.'!r},None,socket.AF_INET)}}); "
        "assert ips and all(ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10') "
        "for ip in ips), 'Unexpected tailnet DNS address'; print(json.dumps({'name':"
        f"{name!r},'addresses':ips}}))"
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            print(pod_python(normal_query))
            print("POD_MAGIC_DNS_VERIFIED")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(3)
    raise RuntimeError("Tailnet DNS ConfigMap applied but pod resolution did not become ready")


def remote_script(args) -> str:
    source = Path(__file__).read_bytes()
    encoded = base64.b64encode(source).decode()
    path = (
        "/var/lib/company-brain/tailscale-helper-" + hashlib.sha256(source).hexdigest()[:16] + ".py"
    )
    argv = [
        "python3",
        path,
        "--remote",
        "--region",
        args.region,
        "--tailnet",
        args.tailnet,
        "--hostname",
        args.hostname,
        args.operation,
    ]
    if args.secret_id:
        argv.extend(["--secret-id", args.secret_id])
    if args.peer:
        argv.extend(["--peer", args.peer])
    if args.port:
        argv.extend(["--port", str(args.port)])
    if args.peer_name:
        argv.extend(["--peer-name", args.peer_name])
    argv.extend(["--dns-suffix", args.dns_suffix])
    return "\n".join(
        [
            "set -eu",
            "umask 077",
            "mkdir -p /var/lib/company-brain",
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}",
            shlex.join(argv),
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default="904469541651")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="company-brain-hackathon")
    parser.add_argument("--tailnet", default=TAILNET)
    parser.add_argument("--hostname", default="toir-hackathon")
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("operation", choices=["install", "login", "status", "verify", "dns"])
    parser.add_argument("--secret-id", help="Exact AWS secret ARN; never pass the key itself")
    parser.add_argument("--peer", help="Spark Tailscale IPv4 address")
    parser.add_argument("--port", type=int, help="Confirmed Spark service TCP port")
    parser.add_argument("--peer-name", help="Confirmed Spark fully qualified MagicDNS name")
    parser.add_argument("--dns-suffix", default=DNS_SUFFIX)
    args = parser.parse_args()
    if args.secret_id and not re.fullmatch(
        r"arn:aws(?:-us-gov|-cn)?:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+",
        args.secret_id,
    ):
        raise ValueError("--secret-id must be a Secrets Manager ARN; do not supply an auth key")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise ValueError("Invalid TCP port")
    if not args.remote:
        from aws_ops import Aws

        aws = Aws(args.account, args.region, args.stack)
        aws.verify_account()
        aws.run_ssm(aws.outputs()["InstanceId"], remote_script(args), timeout=600)
        return
    if os.geteuid() != 0:
        raise RuntimeError("The remote helper must run as root through SSM")
    if args.operation == "install":
        install()
    elif args.operation == "login":
        join(args)
    elif args.operation == "status":
        current = status()
        assert_tailnet(current, args.tailnet)
        print(json.dumps(public_status(current)))
    elif args.operation == "dns":
        configure_dns(args)
    else:
        verify(args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(
            "Host operation failed or timed out; no command output/credentials were logged.",
            file=sys.stderr,
        )
        sys.exit(1)
