"""Helpers shared by the platform spikes (b), (c), (e) of N0-4. Stdlib only; runs on the host.

Every container these spikes start is named `spike-n04plat-*` and labelled `k2.spike=n04plat`,
so `docker ps -a --filter label=k2.spike=n04plat` lists anything a killed run left behind.
Credentials are read from the compose env file into this process only: they are passed to
containers by variable name (`-e NAME`), never on a command line, and every line this module
prints or returns goes through `redact()` first.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NET = "k2-market-data-platform_k2-net"
LABEL = "k2.spike=n04plat"
TRINO_IMAGE = "trinodb/trino:484"
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"  # ships mc, as docker/lake/init-lake.sh uses
# The image the live Spark container runs (the brief: the same tag as k2-spark-iceberg); the
# compose file's tag when that container is absent.
SPARK_IMAGE = (subprocess.run(["docker", "inspect", "k2-spark-iceberg", "--format", "{{.Config.Image}}"],
                              capture_output=True, text=True).stdout.strip() or "k2-spark-iceberg:3.5.9_1.8.1")
AIRFLOW_IMAGE = "k2-airflow:3.3.2"
ALPINE_IMAGE = "alpine:3.20"
LAKEKEEPER_IMAGE = "quay.io/lakekeeper/catalog:v0.13.9"
POSTGRES_IMAGE = "postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"  # the Postgres pin in docker-compose.yml
LAKEKEEPER = "http://127.0.0.1:18181"  # published on the host, read here with GETs only

_SECRETS: list[str] = []
# A SIGTERM (a CI timeout, `kill`) unwinds through each spike's `finally`, so its cleanup runs.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "<redacted>")
    return text


def secret(value: str) -> str:
    """Register a value so that redact() hides it; returns it unchanged."""
    if value and value not in _SECRETS:
        _SECRETS.append(value)
    return value


def log(*parts: object) -> None:
    print(redact(" ".join(str(p) for p in parts)), flush=True)


def run(*args: str, check: bool = True, input: str | None = None) -> str:
    p = subprocess.run(list(args), input=input, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(redact(f"{args[:3]} exit {p.returncode}: {p.stderr.strip()[-800:]}"))
    return p.stdout


def docker(*args: str, check: bool = True, input: str | None = None) -> str:
    return run("docker", *args, check=check, input=input)


def wait_memory(min_gib: float = 6.0, max_wait_s: int = 1800) -> None:
    """The workers' rule: a heavy start needs 6 GiB available on the host; wait, then give up."""
    t0 = time.monotonic()
    while True:
        with open("/proc/meminfo") as f:
            kib = next(int(line.split()[1]) for line in f if line.startswith("MemAvailable"))
        if kib / 1048576 >= min_gib:
            return
        if time.monotonic() - t0 > max_wait_s:
            log(f"NOT RUN: MemAvailable {kib / 1048576:.1f} GiB stayed under {min_gib} GiB for {max_wait_s} s")
            sys.exit(3)
        log(f"waiting: MemAvailable {kib / 1048576:.1f} GiB < {min_gib} GiB")
        time.sleep(30)


def load_root_credentials() -> None:
    """MINIO_ROOT_USER/PASSWORD from the compose env file into os.environ, never printed.

    The file is K2_ENV_FILE, else the main checkout's .env (a worktree has none of its own).
    """
    path = os.environ.get("K2_ENV_FILE")
    if not path:
        common = run("git", "-C", str(HERE), "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        path = str(Path(common).parent / ".env")
    wanted = {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"}
    with open(path) as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and key in wanted:
                os.environ[key] = secret(value.strip().strip("'\""))
    missing = wanted - set(os.environ)
    if missing:
        raise SystemExit(f"{path} has no {sorted(missing)}")


def remove_containers(*names: str) -> None:
    for n in names:
        docker("rm", "-f", "-v", n, check=False)


def mc(script: str, extra_env: tuple[str, ...] = (), stdin: str | None = None) -> str:
    """Run `sh -c script` in a throwaway MinIO container with alias `root` from MC_HOST_root.

    mc reads an alias from MC_HOST_<alias> in its environment, as scripts/drop.sh uses it, so the
    root key is never on mc's argv inside the container (`mc alias set` would put it there)."""
    os.environ["MC_HOST_root"] = secret("http://%s:%s@minio:9000" % (
        urllib.parse.quote(os.environ["MINIO_ROOT_USER"], safe=""),
        urllib.parse.quote(os.environ["MINIO_ROOT_PASSWORD"], safe="")))
    env: list[str] = []
    for name in ("MC_HOST_root", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", *extra_env):
        env += ["-e", name]
    return docker("run", "--rm", "-i", "--name", f"spike-n04plat-mc-{os.getpid()}", "--label", LABEL,
                  "--network", NET, "--memory", "128m", *env, "--entrypoint", "sh", MINIO_IMAGE,
                  "-c", script, input=stdin)


# ---- Trino ---------------------------------------------------------------------------------------

def write_trino_etc(etc: Path, catalogs: dict[str, str], extra_files: dict[str, str] | None = None,
                    config_extra: str = "", heap: str = "768M") -> None:
    """A throwaway etc/: the live jvm.config with a smaller heap, the live log.properties
    (it keeps the S3 access key out of the start log), and the catalogs given."""
    (etc / "catalog").mkdir(parents=True, exist_ok=True)
    live = REPO / "docker/trino/etc"
    jvm = [f"-Xms{heap}" if ln.startswith("-Xms") else f"-Xmx{heap}" if ln.startswith("-Xmx") else ln
           for ln in (live / "jvm.config").read_text().splitlines()]
    files = {
        "jvm.config": "\n".join(jvm) + "\n",
        "log.properties": (live / "log.properties").read_text(),
        "node.properties": "node.environment=spike\nnode.data-dir=/data/trino\n",
        "config.properties": "coordinator=true\nnode-scheduler.include-coordinator=true\n"
                             "http-server.http.port=8080\ndiscovery.uri=http://localhost:8080\n" + config_extra,
        **{f"catalog/{k}.properties": v for k, v in catalogs.items()},
        **(extra_files or {}),
    }
    for rel, text in files.items():
        (etc / rel).write_text(text)
    for p in [etc, *etc.rglob("*")]:
        p.chmod(0o777 if p.is_dir() else 0o666)  # the image runs as uid 1000


def iceberg_catalog(warehouse: str, uri: str = "http://lakekeeper:8181/catalog") -> str:
    """The generated domain catalog's properties (docker/trino/etc/catalog/<domain>.properties)."""
    return f"""connector.name=iceberg
iceberg.catalog.type=rest
iceberg.rest-catalog.uri={uri}
iceberg.rest-catalog.warehouse={warehouse}
iceberg.rest-catalog.vended-credentials-enabled=false
iceberg.rest-catalog.server-assigned-table-location-enabled=true
iceberg.register-table-procedure.enabled=false
iceberg.add-files-procedure.enabled=false
fs.s3.enabled=true
s3.endpoint=http://minio:9000
s3.region=local-01
s3.path-style-access=true
s3.aws-access-key=${{ENV:MINIO_ROOT_USER}}
s3.aws-secret-key=${{ENV:MINIO_ROOT_PASSWORD}}
"""


class Trino:
    """A throwaway Trino 484 on a published 127.0.0.1 port, queried over its REST API."""

    def __init__(self, name: str, etc: Path, network: str, mounts: tuple[str, ...] = (),
                 env_names: tuple[str, ...] = (), memory: str = "1536m", etc_ro: bool = True):
        self.name = name
        remove_containers(name)
        args = ["run", "-d", "--name", name, "--label", LABEL, "--memory", memory, "--cpus", "1.5",
                "--network", network, "-p", "127.0.0.1::8080",
                "-v", f"{etc}:/etc/trino" + (":ro" if etc_ro else "")]
        for m in mounts:
            args += ["-v", m]
        for n in env_names:
            args += ["-e", n]
        docker(*args, TRINO_IMAGE)
        self.port = int(docker("port", name, "8080/tcp").strip().splitlines()[0].rsplit(":", 1)[1])
        t0 = time.monotonic()
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/info", timeout=5) as r:
                    if not json.load(r).get("starting", True):
                        break
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            if time.monotonic() - t0 > 240:
                raise RuntimeError(f"{name} did not start: " + redact(docker("logs", "--tail", "30", name, check=False)))
            time.sleep(2)
        self.start_s = time.monotonic() - t0

    def q(self, user: str, sql: str, session: str | None = None) -> tuple[list, str | None]:
        """(rows, None) on success, (rows, "ErrorName: message") on failure."""
        headers = {"X-Trino-User": user}
        if session:
            headers["X-Trino-Session"] = session
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/statement", data=sql.encode(),
                                     headers=headers)
        rows: list = []
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = json.load(r)
        deadline = time.monotonic() + 300
        while True:
            rows += doc.get("data", [])
            if "error" in doc:
                e = doc["error"]
                return rows, redact(f"{e.get('errorName')}: {e.get('message')}")
            nxt = doc.get("nextUri")
            if not nxt:
                return rows, None
            if time.monotonic() > deadline:
                raise TimeoutError(sql)
            with urllib.request.urlopen(nxt, timeout=60) as r:
                doc = json.load(r)

    def ok(self, user: str, sql: str, session: str | None = None) -> list:
        rows, err = self.q(user, sql, session)
        if err:
            raise RuntimeError(f"{user}: {sql[:120]}: {err}")
        return rows
