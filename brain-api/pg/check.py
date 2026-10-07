"""Run with sudo python3 pg/check.py after Compose reports both services healthy."""
import json
import subprocess
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dns = json.loads(run("tailscale", "status", "--json"))["Self"]["DNSName"].rstrip(".")
    sockets = run("ss", "-Hltn", "( sport = :5432 or sport = :8101 or sport = :8102 )")
    assert {line.split()[3] for line in sockets.splitlines()} == {
        "127.0.0.1:5432", "100.87.113.122:5432", "127.0.0.1:8101"
    }, sockets
    print(sockets)
    containers = run("docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Ports}}")
    rows = containers.splitlines()
    assert {line.split()[0] for line in rows} == {"oct7-rag-db-1", "oct7-rag-embed-1"}, containers
    assert all("(healthy)" in line for line in rows), containers
    print(containers)
    mounts = json.loads(run("docker", "inspect", "oct7-rag-db-1"))[0]["Mounts"]
    assert any(m.get("Name") == "oct7-rag_pgdata" and m["Destination"] == "/var/lib/postgresql/data" for m in mounts)
    print("Preserved volume: oct7-rag_pgdata")
    # Verify the certificate by DNS name while keeping the admin connection local.
    conn = f"host={dns} hostaddr=127.0.0.1 port=5432 dbname=postgres user=rag sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt"
    print(run("docker", "run", "--rm", "--network", "host", "--env-file", str(root / ".env"),
              "-v", "/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro",
              "pgvector/pgvector:pg17", "sh", "-c", 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql "$1" -c "\\conninfo"', "sh", conn))
    for address in ("127.0.0.1", "100.87.113.122"):
        for database, tls, allowed in (
            ("toir_runs", "verify-full", True),
            ("toir_runs_test", "verify-full", True),
            ("rag", "verify-full", False),
            ("postgres", "verify-full", False),
            ("toir_runs", "disable", False),
        ):
            conn = f"host={dns} hostaddr={address} port=5432 dbname={database} user=toir_runs sslmode={tls} sslrootcert=/etc/ssl/certs/ca-certificates.crt connect_timeout=5"
            result = subprocess.run(
                ["docker", "run", "--rm", "--network", "host", "--env-file", str(root / ".env"),
                 "-v", "/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro",
                 "pgvector/pgvector:pg17", "sh", "-c",
                 'export PGPASSWORD="$TOIR_RUNS_PASSWORD"; exec psql "$1" -Atc "SELECT current_database()"', "sh", conn],
                text=True, capture_output=True, timeout=30,
            )
            if allowed:
                assert result.returncode == 0 and result.stdout.strip() == database, result.stderr
            else:
                assert result.returncode != 0 and "pg_hba.conf rejects connection" in result.stderr, result.stderr
            print(f"toir_runs via {address}, db={database}, sslmode={tls}: {'allowed' if allowed else 'rejected'}")
    models = json.loads(run("curl", "-fsS", "--max-time", "15", "http://127.0.0.1:8101/v1/models"))
    assert any(model["id"] == "nemotron-embed" for model in models["data"]), models
    print("Embedding model: nemotron-embed")
    print(run("sudo", "-u", "curran", "sudo", "-n", "/usr/local/bin/oct7-rag-ctl", "ps"))
    print(f"MagicDNS: {dns}; certificate: Tailscale-issued")


if __name__ == "__main__":
    main()
