"""Validate a synthetic staging stack, including restarts and disposable restore."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    root = Path(__file__).resolve().parents[1]
    parser_args = argparse.ArgumentParser(description=__doc__)
    parser_args.add_argument("--url", default=os.getenv("DOCOPS_URL", "http://127.0.0.1:18113"))
    base = parser_args.parse_args().url.rstrip("/")
    key = (root / ".secrets/api_key").read_text().strip()

    def engine(*args):
        return subprocess.check_output(["docker", *args], cwd=root, text=True, timeout=300).strip()

    def compose(*args):
        return engine("compose", *args)

    def snapshot():
        with urlopen(Request(base + "/api/snapshot", headers={"X-API-Key": key}), timeout=10) as response:
            return json.load(response)

    def wait_ready():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                with urlopen(base + "/ready", timeout=5) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError):
                pass
            time.sleep(1)
        raise TimeoutError("API did not become ready after restart")

    def inspect(name):
        container_id = compose("ps", "-q", name)
        assert container_id, f"Missing service: {name}"
        return json.loads(engine("inspect", container_id))[0]

    checks = []
    for service in ("api", "worker", "parser", "llm-gateway", "postgres", "redis"):
        data = inspect(service)
        assert data["State"]["Health"]["Status"] == "healthy", service
    checks.append("all six service health checks")
    for service in ("api", "worker", "parser", "llm-gateway"):
        data = inspect(service)
        assert data["Config"]["User"] in {"10001:10001", "gateway"}
        assert data["HostConfig"]["ReadonlyRootfs"]
        assert "ALL" in data["HostConfig"]["CapDrop"]
        assert "no-new-privileges:true" in data["HostConfig"]["SecurityOpt"]
        assert data["HostConfig"]["Memory"] == 1024**3
    checks.append("non-root/read-only/capability/resource boundaries")
    parser = inspect("parser")
    assert parser["HostConfig"]["NetworkMode"] == "none"
    assert all(mount["Destination"] == "/ipc" for mount in parser["Mounts"])
    environment_names = {item.split("=", 1)[0] for item in parser["Config"]["Env"]}
    # The official Python base publishes its build-verification GPG_KEY fingerprint.
    # This public identifier is distinct from application credentials or secret mounts.
    assert not any(
        ("KEY" in name and name != "GPG_KEY") or "PASSWORD" in name or "TOKEN" in name or "DATABASE" in name
        for name in environment_names
    )
    blocked = compose("exec", "-T", "parser", "python", "-c", "import socket; s=socket.socket(); s.settimeout(1); print(s.connect_ex(('1.1.1.1',443)) != 0)")
    assert blocked == "True"
    checks.append("parser has no network or secrets")

    def sql(statement, database="docops", admin=False):
        secret = "postgres_admin_password" if admin else "postgres_app_password"
        user = "docops_admin" if admin else "docops"
        return compose(
            "exec", "-T", "postgres", "sh", "-c",
            f'PGPASSWORD="$(cat /run/secrets/{secret})" exec psql -h 127.0.0.1 -U {user} -d "$1" -v ON_ERROR_STOP=1 -tAc "$2"',
            "sh", database, statement,
        )

    assert sql("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user") == "f"
    checks.append("least-privilege application database role")
    before = snapshot()["total_jobs"]
    assert before > 0, "Run scripts/smoke.py before lifecycle validation"
    compose("restart", "worker")
    compose("restart", "postgres")
    wait_ready()
    assert snapshot()["total_jobs"] == before
    checks.append("worker/database restart preserves durable rows")
    restore = "northstar_restore_" + uuid4().hex[:12]
    dump = "/tmp/" + restore + ".dump"
    try:
        compose("exec", "-T", "postgres", "sh", "-c", 'PGPASSWORD="$(cat /run/secrets/postgres_app_password)" pg_dump -h 127.0.0.1 -U docops -d docops -Fc -f "$1"', "sh", dump)
        sql('CREATE DATABASE "' + restore + '" OWNER docops', admin=True)
        compose("exec", "-T", "postgres", "sh", "-c", 'PGPASSWORD="$(cat /run/secrets/postgres_app_password)" pg_restore -h 127.0.0.1 -U docops --no-owner --exit-on-error -d "$1" "$2"', "sh", restore, dump)
        for table in ("jobs", "audit", "invoice_registry", "ingestion_keys", "outbox"):
            assert sql(f"SELECT count(*) FROM docops.{table}", restore) == sql(f"SELECT count(*) FROM docops.{table}")
        assert sql("SELECT md5(string_agg(encode(source_bytes,'hex'),',' ORDER BY id)) FROM docops.jobs", restore) == sql("SELECT md5(string_agg(encode(source_bytes,'hex'),',' ORDER BY id)) FROM docops.jobs")
        checks.append("synthetic dump/restore retains counts and original document hashes")
    finally:
        # Only this run's randomly named disposable database is eligible for cleanup.
        sql('DROP DATABASE IF EXISTS "' + restore + '"', admin=True)
        compose("exec", "-T", "postgres", "sh", "-c", 'rm -f -- "$1"', "sh", dump)
    print(json.dumps({"result": "passed", "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
