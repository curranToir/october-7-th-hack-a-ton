#!/usr/bin/env python3
"""Serve only Recall callbacks through a separate Tailscale Funnel on HTTPS 8443."""

import argparse
import base64
import copy
import hashlib
import http.client
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HOSTNAME = "toir-hackathon.taild4c940.ts.net"
TAILNET = "neptuneops.com"
PORT = 8787
FUNNEL_PORT = 8443
TARGET = f"http://127.0.0.1:{PORT}"
SERVICE = "toir-meeting-webhook.service"
INSTALL = Path("/opt/toir-meeting-webhook/proxy.py")
UNIT = Path("/etc/systemd/system") / SERVICE
PATHS = frozenset(
    f"/api/meetings/webhooks/recall/{channel}" for channel in ("dashboard", "realtime")
)
MAX_BODY = 1_000_000
SIGNATURE_HEADERS = tuple(
    f"{prefix}-{field}"
    for prefix in ("webhook", "svix")
    for field in ("id", "timestamp", "signature")
)


class CallbackHandler(BaseHTTPRequestHandler):
    """Exact paths only. Never forward browser cookies, identity or proxy headers."""

    protocol_version = "HTTP/1.1"
    server_version = "ToirCallback"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, *_):
        pass  # No request headers, signatures, body or URL query in logs.

    def reply(self, status, body=b"", headers=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Toir-Webhook-Proxy", "v1")
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        if self.path not in PATHS:
            return self.reply(404)
        if self.headers.get("Transfer-Encoding"):
            return self.reply(411)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            return self.reply(411)
        if len(lengths[0]) > 7:
            return self.reply(413)
        length = int(lengths[0])
        if length > MAX_BODY:
            return self.reply(413)
        if self.headers.get("Content-Encoding", "identity").lower() != "identity":
            return self.reply(415)
        headers = {"Content-Type": "application/json"}
        for name in SIGNATURE_HEADERS:
            values = self.headers.get_all(name, [])
            if len(values) > 1:
                return self.reply(400)
            if values:
                headers[name] = values[0]
        upstream = None
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                return self.reply(400)
            upstream = http.client.HTTPConnection(*self.server.upstream, timeout=20)
            upstream.request("POST", self.path, body=body, headers=headers)
            response = upstream.getresponse()
            result = response.read(65537)
            if len(result) > 65536:
                return self.reply(502)
            extra = {}
            retry = response.getheader("Retry-After", "")
            if retry.isascii() and retry.isdigit() and len(retry) <= 5:
                extra["Retry-After"] = retry
            return self.reply(response.status, result, extra)
        except (OSError, http.client.HTTPException):
            return self.reply(503, headers={"Retry-After": "30"})
        finally:
            if upstream:
                upstream.close()

    def do_GET(self):
        self.reply(404)

    do_HEAD = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET
    do_TRACE = do_GET
    do_CONNECT = do_GET


class CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, upstream=("127.0.0.1", 80)):
        self.upstream = upstream
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, CallbackHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Deliberately omit request context and tracebacks from the public listener.


def command(*args):
    result = subprocess.run(args, check=True, text=True, capture_output=True, timeout=35)
    return result.stdout.strip()


def node_status():
    return json.loads(command("tailscale", "status", "--json"))


def config():
    return json.loads(command("tailscale", "serve", "status", "--json"))


def validate_node(node):
    if (
        node.get("BackendState") != "Running"
        or node.get("CurrentTailnet", {}).get("Name") != TAILNET
        or node.get("Self", {}).get("DNSName", "").rstrip(".") != HOSTNAME
    ):
        raise RuntimeError("Expected the existing Toir node in neptuneops.com")
    capabilities = node.get("Self", {}).get("CapMap") or {}
    ports = []
    for key in capabilities:
        if key.startswith("https://tailscale.com/cap/funnel-ports?"):
            ports.extend(parse_qs(urlsplit(key).query).get("ports", []))
    if (
        "funnel" not in capabilities
        or "https" not in capabilities
        or not any(str(FUNNEL_PORT) in group.split(",") for group in ports)
    ):
        raise RuntimeError("Existing node policy must permit Funnel 8443; no policy was changed")


def endpoint_key():
    return f"{HOSTNAME}:{FUNNEL_PORT}"


def validate_endpoint(value):
    port = str(FUNNEL_PORT)
    tcp = value.get("TCP", {}).get(port)
    web = value.get("Web", {}).get(endpoint_key())
    if tcp not in (None, {"HTTPS": True}) or web not in (
        None,
        {"Handlers": {"/": {"Proxy": TARGET}}},
    ):
        raise RuntimeError("HTTPS 8443 is already used by another service; nothing was changed")
    if (tcp is None) != (web is None):
        raise RuntimeError("HTTPS 8443 has an unrecognized partial configuration")


def other_endpoints(value):
    value = copy.deepcopy(value)
    for group, key in (
        ("TCP", str(FUNNEL_PORT)),
        ("Web", endpoint_key()),
        ("AllowFunnel", endpoint_key()),
    ):
        if group in value:
            value[group].pop(key, None)
            if not value[group]:
                del value[group]
    return value


def unit_source():
    return f"""[Unit]
Description=Toir exact-path Recall webhook proxy
After=network.target k3s.service tailscaled.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {INSTALL} serve
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
MemoryMax=96M
CPUQuota=25%
TasksMax=32
Restart=on-failure
RestartSec=2
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
"""


def local_ready():
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        return response.status == 404 and response.getheader("X-Toir-Webhook-Proxy") == "v1"
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def install_proxy():
    # An occupied loopback port must already belong to this exact proxy protocol.
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", PORT)) == 0
    if occupied and not local_ready():
        raise RuntimeError("Loopback port 8787 is occupied by another service")
    INSTALL.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    INSTALL.parent.chmod(0o755)  # The SSM wrapper's 077 umask must not block DynamicUser.
    source, unit = Path(__file__).read_bytes(), unit_source()
    changed = not INSTALL.exists() or INSTALL.read_bytes() != source
    unit_changed = not UNIT.exists() or UNIT.read_text() != unit
    INSTALL.write_bytes(source)
    INSTALL.chmod(0o644)
    UNIT.write_text(unit)
    UNIT.chmod(0o644)
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", SERVICE)
    command("systemctl", "restart" if changed or unit_changed else "start", SERVICE)
    for _ in range(25):
        if local_ready():
            return
        time.sleep(0.2)
    raise RuntimeError("Callback proxy did not become ready; Funnel was not enabled")


def setup():
    validate_node(node_status())
    before = config()
    validate_endpoint(before)
    install_proxy()
    command("tailscale", "funnel", "--bg", f"--https={FUNNEL_PORT}", TARGET)
    after = config()
    if other_endpoints(before) != other_endpoints(after):
        raise RuntimeError("Unexpected change outside HTTPS 8443; inspect Tailscale configuration")
    validate_endpoint(after)
    if not after.get("AllowFunnel", {}).get(endpoint_key()):
        raise RuntimeError("Funnel was not enabled; no policy changes were attempted")
    print(
        json.dumps(
            {
                "origin": f"https://{endpoint_key()}",
                "public_paths": sorted(PATHS),
                "private_endpoints_preserved": True,
                "loopback_ready": local_ready(),
            }
        )
    )


def disable():
    before = config()
    validate_endpoint(before)
    if endpoint_key() in before.get("Web", {}):
        command("tailscale", "funnel", f"--https={FUNNEL_PORT}", "off")
    command("systemctl", "disable", "--now", SERVICE)
    if other_endpoints(before) != other_endpoints(config()):
        raise RuntimeError("Unexpected change outside HTTPS 8443")
    print("Meeting Funnel disabled; other Tailscale endpoints preserved.")


def remote_script(operation):
    source = Path(__file__).read_bytes()
    path = (
        "/var/lib/company-brain/webhook-helper-" + hashlib.sha256(source).hexdigest()[:16] + ".py"
    )
    encoded = base64.b64encode(source).decode()
    return "\n".join(
        [
            "set -eu",
            "umask 077",
            "mkdir -p /var/lib/company-brain",
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}",
            shlex.join(["python3", path, "--remote", operation]),
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("setup", "status", "disable", "serve"))
    parser.add_argument("--account", default="904469541651")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="company-brain-hackathon")
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.operation == "serve":
        with CallbackServer(("127.0.0.1", PORT)) as server:
            server.serve_forever()
        return
    if not args.remote:
        from aws_ops import Aws

        aws = Aws(args.account, args.region, args.stack)
        aws.verify_account()
        aws.run_ssm(aws.outputs()["InstanceId"], remote_script(args.operation), timeout=120)
        return
    if os.geteuid() != 0:
        raise RuntimeError("Setup operations must run as root through SSM")
    if args.operation == "setup":
        setup()
    elif args.operation == "disable":
        disable()
    else:
        node = node_status()
        value = config()
        print(
            json.dumps(
                {
                    "dns": node.get("Self", {}).get("DNSName"),
                    "origin": f"https://{endpoint_key()}",
                    "public": bool(value.get("AllowFunnel", {}).get(endpoint_key())),
                    "loopback_ready": local_ready(),
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("Webhook setup failed; command output was suppressed.", file=sys.stderr)
        sys.exit(1)
