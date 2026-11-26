#!/usr/bin/env python3
"""The deploy rehearsal (phase 001 step S19, ADR-058): every claim of scripts/deploy.py shown on a
throwaway compose project, never the live stack.

    make deploy-rehearsal [KEEP=1]

It builds, under a new directory in $HOME (Docker Desktop shares only the home directory), a scratch
git repository holding this checkout's deploy code and the k2gen good fixture set, a releases root
and a secrets directory with fixture secrets made here (a local CA, Trino's certificate, bcrypt
lines). Compose project `k2-rehearsal`, its own networks and volumes, small limits, no published
port but Trino's, on a free loopback port: a throwaway Postgres, MinIO and Lakekeeper, Trino started
from the release's compiled etc, Spark for warehouse k2's DDL, Prometheus reading a rule file through
its pointer, the tools one-shot and a stand-in service. It then deploys releases cut from that
repository at scratch tags, through `make` exactly as the runbooks do, and checks:

  first deploy; a rules-only release recreates nothing and its policy reaches a reader (timed); a
  stand-in's new image recreates only it; a forced verification mismatch restores the previous
  release, images included, exit 1; `make rollback`; a release that drops a masked column never shows
  its value to the reader polling every 50 ms through the deploy; a SIGKILL after each step, then a
  rerun, converges; two deploys at once, one refused; Prometheus's rules follow the pointer.

Exit 0 when every check passed. Everything is removed at the end unless KEEP=1.
scripts/lib/throwaway-lake.sh (step S12) was not on the phase branch when this was written, so the
throwaway lake is the few compose services below.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bcrypt
from k2gen import model
from k2gen.emit.lake import create_view

REPO = Path(__file__).resolve().parents[2]
GOOD = REPO / "platform/k2gen/tests/fixtures/v2/good"
_spec = importlib.util.spec_from_file_location("k2tools", REPO / "docker/tools/k2tools.py")
assert _spec and _spec.loader
k2tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k2tools)

PROJECT = "k2-rehearsal"
BASE = Path.home() / f"s19-rehearsal-{os.getpid()}"
CLONE, ROOT, SECRETS = BASE / "repo", BASE / "releases", BASE / "secrets"
SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
PRODUCT = "contracts/sensor-net/station-readings/product.odps.yaml"
READER = "contracts/ops-insight/daily-load/product.odps.yaml"  # reads silver_readings: acknowledges each major
MODEL_SQL = "contracts/sensor-net/station-readings/models/silver_readings.sql"
UNIT = "contracts/sensor-net/station-readings/unit_tests.yml"
EXPECT = "tests/access/expectations.yaml"
VIEW = "sensor_net.station_readings.silver_readings"
SERVICE_PRINCIPALS = ("svc.platform", "svc.runs", "svc.catalog", "svc.api", "pub_station_readings", "dbt_station_readings",
                      "pub_daily_load", "dbt_daily_load", "svc.load-dashboard")
PEOPLE = ("p.analyst-full", "p.analyst-delayed")
RESULTS: list[tuple[str, bool, str]] = []

# Copied from this checkout into the scratch repository: what `make release` and `make deploy` run.
CODE = ("platform/k2gen/k2gen", "platform/k2gen/pyproject.toml", "platform/k2gen/uv.lock", "platform/runs",
        "contracts/schema", "dbt/macros", "docker/tools", "scripts/deploy.py", "scripts/principal-secret.py",
        "mk/release.mk", "mk/access.mk")
# An emitter phase 001 has not filled yet (S13 emits the authenticator), so the rehearsal gives Trino
# its password authenticator the way the secrets table says S13 will.
IDENTITY = '''"""Rehearsal stand-in for step S13's emitter: the password authenticator over ${K2_SECRETS}/trino/password.db."""
from k2gen.model import Model, SECRET_FILES


def emit(model: Model) -> dict[str, str]:
    db = next(f.mount for f in SECRET_FILES if f.path == "trino/password.db")
    return {"compiled/trino/etc/password-authenticator.properties":
            f"password-authenticator.name=file\\nfile.password-file={db}\\nfile.refresh-period=5s\\n"}
'''
S12_REF = os.environ.get("K2_REHEARSAL_RULES_REF", "step/s12-emit-trino-rules")

COMPOSE = """\
# The rehearsal's compose file (scripts/verify/deploy_rehearsal.py): the services a deploy touches.
networks:
  k2-net: {}
  k2-catalog: {}
volumes:
  pg: {}
  s3: {}
x-releases-ro: &releases-ro
  type: bind
  source: ${K2_RELEASES}
  target: /k2/releases
  read_only: true
  bind: {create_host_path: false}
x-small: &small
  deploy: {resources: {limits: {cpus: '0.5', memory: 256M}}}
services:
  prefect-db:
    image: postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b
    environment: {POSTGRES_USER: lk, POSTGRES_PASSWORD: "${PG_PASSWORD}", POSTGRES_DB: lakekeeper}
    networks: [k2-catalog]
    volumes: ["pg:/var/lib/postgresql/data"]
    healthcheck: {test: ["CMD", "pg_isready", "-U", "lk"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: ["server", "/data"]
    environment: {MINIO_ROOT_USER: "${MINIO_ROOT_USER}", MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"}
    networks: [k2-catalog]
    volumes: ["s3:/data"]
    healthcheck: {test: ["CMD", "curl", "-fs", "http://localhost:9000/minio/health/live"], interval: 2s, retries: 30}
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '0.5', memory: 512M}}}
  lakekeeper-migrate:
    image: quay.io/lakekeeper/catalog:v0.13.9
    command: ["migrate"]
    environment: &lk
      LAKEKEEPER__PG_DATABASE_URL_READ: "postgres://lk:${PG_PASSWORD}@prefect-db:5432/lakekeeper"
      LAKEKEEPER__PG_DATABASE_URL_WRITE: "postgres://lk:${PG_PASSWORD}@prefect-db:5432/lakekeeper"
      LAKEKEEPER__PG_ENCRYPTION_KEY: "${LAKEKEEPER_KEY}"
      LAKEKEEPER__BASE_URI: "http://lakekeeper:8181"
    networks: [k2-catalog]
    depends_on: {prefect-db: {condition: service_healthy}}
    restart: "no"
    <<: *small
  lakekeeper:
    image: quay.io/lakekeeper/catalog:v0.13.9
    command: ["serve"]
    environment: *lk
    networks: [k2-catalog]
    depends_on: {lakekeeper-migrate: {condition: service_completed_successfully}}
    healthcheck: {test: ["CMD", "/home/nonroot/lakekeeper", "healthcheck"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  trino:
    image: trinodb/trino:484
    command: ["/usr/lib/trino/bin/launcher", "run", "--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc"]
    environment:
      K2_TRINO_SHARED_SECRET: "${K2_TRINO_SHARED_SECRET}"
      K2_TRINO_S3_ACCESS_KEY: "${MINIO_ROOT_USER}"
      K2_TRINO_S3_SECRET_KEY: "${MINIO_ROOT_PASSWORD}"
    networks: [k2-net, k2-catalog]
    ports: ["127.0.0.1:${K2_TRINO_PORT}:8443"]
    volumes:
      - *releases-ro
      - "${K2_SECRETS}/trino:/run/k2-secrets/trino:ro"
      - "${K2_SECRETS}/tls/trino.pem:/run/k2-secrets/tls/trino.pem:ro"
    healthcheck:
      test: ["CMD", "bash", "-c", "i=$$(curl -fsk https://localhost:8443/v1/info) && [[ $$i == *'\\"starting\\":false'* ]]"]
      interval: 3s
      retries: 60
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '2.0', memory: 4G}}}
  spark-iceberg:
    build: {context: ., dockerfile: rehearsal/spark.Dockerfile}
    image: k2-spark-iceberg:rehearsal
    environment:
      AWS_ACCESS_KEY_ID: "${MINIO_ROOT_USER}"
      AWS_SECRET_ACCESS_KEY: "${MINIO_ROOT_PASSWORD}"
      AWS_REGION: local-01
    networks: [k2-catalog]
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '1.5', memory: 1536M}}}
  prometheus:
    image: prom/prometheus@sha256:5888c188cf09e3f7eebc97369c3b2ce713e844cdbd88ccf36f5047c958aea120
    command: ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus"]
    configs: [{source: prom, target: /etc/prometheus/prometheus.yml}]
    networks: [k2-net]
    volumes: [*releases-ro]
    restart: unless-stopped
    <<: *small
  standin:
    image: STANDIN_IMAGE
    command: ["sleep", "infinity"]
    networks: [k2-net]
    volumes: [*releases-ro]
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '0.1', memory: 32M}}}
  tools:
    profiles: ["tools"]
    build: {context: ., dockerfile: docker/tools/Dockerfile}
    image: tools:local
    networks: [k2-net, k2-catalog]
    volumes: [{<<: *releases-ro, read_only: false}]
    restart: "no"
    deploy: {resources: {limits: {cpus: '0.25', memory: 256M}}}
configs:
  prom:
    content: |
      global: {evaluation_interval: 5s}
      rule_files: [/k2/releases/pointers/prometheus/documents/domains/rehearsal/rules/*.yml]
"""
ALPINE_A = "alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"  # alpine:3.20, local
ALPINE_B = "alpine:3"  # pinned by make release to its local digest; another image id than A


def sh(*argv: str, check: bool = True, env: dict[str, str] | None = None, cwd: Path | None = None,
       input: str | None = None) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(list(argv), capture_output=True, text=True, env=env, cwd=cwd, input=input)
    if check and done.returncode:
        raise RuntimeError(f"{' '.join(argv[:5])}: exit {done.returncode}\n{done.stdout[-2000:]}\n{done.stderr[-3000:]}")
    return done


def env() -> dict[str, str]:
    return {**os.environ, "K2_RELEASES": str(ROOT), "K2_SECRETS": str(SECRETS), "K2_COMPOSE_PROJECT": PROJECT,
            "K2_TRINO_PORT": PORT, "K2_HOME": str(CLONE), "UV_NO_PROGRESS": "1"}


def make(*args: str, fault: str = "", check: bool = True) -> subprocess.CompletedProcess[str]:
    e = {k: v for k, v in env().items() if k != "K2_DEPLOY_FAULT"} | ({"K2_DEPLOY_FAULT": fault} if fault else {})
    done = sh("make", "-s", "-C", str(CLONE), *args, env=e, check=False)
    log = BASE / "make.log"
    with open(log, "a") as f:
        f.write(f"\n$ make {' '.join(args)}{f'  (K2_DEPLOY_FAULT={fault})' if fault else ''}\nexit {done.returncode}\n{done.stdout}{done.stderr}")
    if check and done.returncode:
        raise RuntimeError(f"make {' '.join(args)}: exit {done.returncode}\n{done.stderr[-4000:]}")
    return done


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)


def compose(*args: str, check_: bool = True) -> subprocess.CompletedProcess[str]:
    return sh("docker", "compose", "-p", PROJECT, "--project-directory", str(CLONE), "-f", str(CLONE / "docker-compose.yml"),
              *args, env=env(), check=check_)


# --- the scratch repository, its secrets and its releases --------------------------------


def edit(path: str, old: str, new: str) -> None:
    p = CLONE / path
    text = p.read_text()
    assert old in text, f"{path}: {old!r} not found"
    p.write_text(text.replace(old, new))


def cut(tag: str, message: str) -> str:
    """Commit the scratch tree, tag it, compile it with `make release`: the release id."""
    sh("git", "-C", str(CLONE), "add", "-A")
    sh("git", "-C", str(CLONE), "commit", "-q", "--allow-empty", "-m", message)
    sh("git", "-C", str(CLONE), "tag", "-a", tag, "-m", message)
    out = make("release", f"TAG={tag}")
    return out.stdout.strip().splitlines()[-1]


def scratch_repo() -> None:
    for rel in CODE:
        src, dst = REPO / rel, CLONE / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".mypy_cache"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for p in GOOD.rglob("*"):
        if p.is_file():
            (CLONE / p.relative_to(GOOD)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, CLONE / p.relative_to(GOOD))
    (CLONE / "Makefile").write_text("include mk/*.mk\n")
    (CLONE / "docker-compose.yml").write_text(COMPOSE.replace("STANDIN_IMAGE", ALPINE_A))
    (CLONE / "rehearsal").mkdir()
    (CLONE / "rehearsal/spark.Dockerfile").write_text("FROM k2-spark-iceberg:3.5.9_1.8.1\nCMD [\"sleep\", \"infinity\"]\n")
    prom_rules(1)
    emit = CLONE / "platform/k2gen/k2gen/emit"
    if "NotImplementedError" in (emit / "trino_identity.py").read_text():
        (emit / "trino_identity.py").write_text(IDENTITY)
        print("rehearsal: trino_identity is a stub (S13): the rehearsal's own password authenticator emitter stands in")
    if "NotImplementedError" in (emit / "trino_rules.py").read_text():
        rules = sh("git", "-C", str(REPO), "show", f"{S12_REF}:platform/k2gen/k2gen/emit/trino_rules.py").stdout
        (emit / "trino_rules.py").write_text(rules)
        print(f"rehearsal: trino_rules is a stub here (S12): its emitter taken from {S12_REF}")
    (CLONE / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n")
    sh("git", "init", "-q", "-b", "main", str(CLONE))
    sh("git", "-C", str(CLONE), "config", "user.name", "Rehearsal Operator")
    sh("git", "-C", str(CLONE), "config", "user.email", "rehearsal@example.org")


def prom_rules(n: int) -> None:
    d = CLONE / "domains/rehearsal/rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / "release.yml").write_text(f"groups:\n  - name: rehearsal_release_{n}\n    rules:\n"
                                   f"      - record: k2_rehearsal_release\n        expr: vector({n})\n")


def fixture_secrets() -> dict[str, str]:
    """A local CA and Trino's certificate; a password per principal: bcrypt lines in trino/password.db,
    service passwords in principals/<id>.password, mode 600; people's kept in this run's memory."""
    tls = SECRETS / "tls"
    tls.mkdir(parents=True)
    # keyUsage on both: Python 3.13 verifies strictly and refuses a CA certificate without it.
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=k2-rehearsal-ca",
       "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
       "-keyout", str(tls / "ca.key"), "-out", str(tls / "ca.pem"))
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=trino", "-keyout", str(tls / "trino.key"),
       "-out", str(tls / "trino.csr"))
    (tls / "san.ext").write_text("subjectAltName=DNS:trino,DNS:localhost,IP:127.0.0.1\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                                 "extendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\n")
    sh("openssl", "x509", "-req", "-in", str(tls / "trino.csr"), "-CA", str(tls / "ca.pem"), "-CAkey", str(tls / "ca.key"),
       "-CAcreateserial", "-days", "2", "-extfile", str(tls / "san.ext"), "-out", str(tls / "trino.crt"))
    (tls / "trino.pem").write_text((tls / "trino.key").read_text() + (tls / "trino.crt").read_text())
    for f in ("trino.key", "trino.csr", "trino.crt", "san.ext", "ca.srl"):
        (tls / f).unlink(missing_ok=True)
    words: dict[str, str] = {}
    lines = []
    (SECRETS / "principals").mkdir(mode=0o700)
    for p in (*SERVICE_PRINCIPALS, *PEOPLE):
        words[p] = secrets.token_urlsafe(24)
        lines.append(f"{p}:$2y${bcrypt.hashpw(words[p].encode(), bcrypt.gensalt(10)).decode().removeprefix('$2b$')}\n")
        if p not in PEOPLE:
            (SECRETS / model.password_file(p)).write_text(words[p])
            os.chmod(SECRETS / model.password_file(p), 0o600)
    (SECRETS / "trino").mkdir(mode=0o700)
    (SECRETS / "trino/password.db").write_text("".join(lines))
    os.chmod(SECRETS / "trino/password.db", 0o600)
    return words


def free_port() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


PORT = free_port()
PASSWORDS: dict[str, str] = {}


def q(user: str, statement: str) -> dict[str, Any]:
    return k2tools.sql(f"https://localhost:{PORT}", user, PASSWORDS[user], str(SECRETS / "tls/ca.pem"), statement)


def in_catalog(script: str, tools_image: str) -> str:
    """A Python snippet in the tools image on the catalog network, the MinIO keys in its environment."""
    return sh("docker", "run", "--rm", "-i", "--network", f"{PROJECT}_k2-catalog", "--memory", "128m",
              "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD", tools_image, "python3", "-",
              input=script, env={**os.environ, **DOTENV}).stdout


LAKE_SETUP = '''
import json, os, urllib.request
def call(m, u, b=None):
    r = urllib.request.Request(u, data=None if b is None else json.dumps(b).encode(), method=m, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return x.status
    except urllib.error.HTTPError as e: return e.code
print("bootstrap", call("POST", "http://lakekeeper:8181/management/v1/bootstrap", {"accept-terms-of-use": True}))
for w in ("k2", "sensor_net", "ops_insight"):
    body = {"warehouse-name": w, "project-id": "00000000-0000-0000-0000-000000000000",
            "storage-profile": {"type": "s3", "bucket": "lake", "key-prefix": w, "endpoint": "http://minio:9000",
                                "region": "local-01", "path-style-access": True, "flavor": "s3-compat", "sts-enabled": False,
                                "remote-signing-enabled": False},
            "storage-credential": {"type": "s3", "credential-type": "access-key",
                                   "aws-access-key-id": os.environ["MINIO_ROOT_USER"], "aws-secret-access-key": os.environ["MINIO_ROOT_PASSWORD"]}}
    print("warehouse", w, call("POST", "http://lakekeeper:8181/management/v1/warehouse", body))
'''

TAG_AUDITED = '''
import json, urllib.request
def call(m, u, b=None):
    r = urllib.request.Request(u, data=None if b is None else json.dumps(b).encode(), method=m, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as x:
        raw = x.read(); return json.loads(raw) if raw else {}
cfg = call("GET", "http://lakekeeper:8181/catalog/v1/config?warehouse=sensor_net")
base = "http://lakekeeper:8181/catalog/v1/" + cfg["defaults"]["prefix"]
meta = call("GET", base + "/namespaces/station_readings__base/tables/silver_readings")["metadata"]
sid = meta["current-snapshot-id"]
call("POST", base + "/transactions/commit", {"table-changes": [{"identifier": {"namespace": ["station_readings__base"], "name": "silver_readings"},
     "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": sid}],
     "updates": [{"action": "set-snapshot-ref", "ref-name": "audited", "type": "tag", "snapshot-id": sid}]}]})
print(sid)
'''
DOTENV: dict[str, str] = {}


# --- what the checks read --------------------------------------------------------------


def started() -> dict[str, str]:
    out = sh("docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format",
             '{{.Label "com.docker.compose.service"}} {{.ID}}').stdout.split("\n")
    found = {}
    for line in filter(None, out):
        svc, cid = line.split()
        found[svc] = sh("docker", "inspect", "-f", "{{.State.StartedAt}} {{.Image}}", cid).stdout.strip()
    return found


def live_release() -> str:
    return os.readlink(ROOT / "pointers/trino").removeprefix("../")


def last_records(n: int) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (ROOT / model.DEPLOY_LOG).read_text().splitlines()[-n:]]


class Poller(threading.Thread):
    """A reader polling the view every 50 ms: records what each principal saw of operator_note."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.polls = self.errors = 0
        self.leaks: list[str] = []
        self.shapes: dict[str, int] = {}

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                r = q("p.analyst-delayed", f"SELECT * FROM {VIEW}")
                cols = [c[0] for c in r["columns"]]
                self.shapes[",".join(cols)] = self.shapes.get(",".join(cols), 0) + 1
                if "operator_note" in cols:
                    i = cols.index("operator_note")
                    self.leaks += [str(row[i]) for row in r["rows"] if row[i] is not None]
                self.polls += 1
            except (k2tools.TrinoError, k2tools.Unreachable, TimeoutError) as e:
                self.errors += 1
                self.shapes[f"error {str(e)[:70]}"] = self.shapes.get(f"error {str(e)[:70]}", 0) + 1
            time.sleep(0.05)


def prom_group() -> str:
    c = compose("ps", "-q", "prometheus").stdout.strip()
    out = sh("docker", "exec", c, "wget", "-qO-", "http://localhost:9090/api/v1/rules", check=False).stdout
    groups = [g["name"] for g in json.loads(out or "{}").get("data", {}).get("groups", [])] if out else []
    return ",".join(groups)


# --- the rehearsal -----------------------------------------------------------------------


def main() -> int:
    avail = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text())[1]) / 2**20  # type: ignore[index]
    if avail < 6:
        print(f"rehearsal: {avail:.1f} GiB available, under 6 GiB; not started")
        return 2
    BASE.mkdir(mode=0o700)
    ROOT.mkdir()
    print(f"rehearsal: {BASE}, project {PROJECT}, Trino on 127.0.0.1:{PORT}; {avail:.1f} GiB available")
    try:
        return rehearse()
    finally:
        if os.environ.get("KEEP") == "1":
            print(f"rehearsal: KEEP=1: left {BASE} and project {PROJECT} running")
        else:
            compose("--profile", "tools", "down", "-v", "--remove-orphans", check_=False)
            for p in (BASE, *BASE.rglob("*")):
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o700)
            shutil.rmtree(BASE, ignore_errors=True)
            left = sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}").stdout.split()
            print(f"rehearsal: removed {BASE}; containers left: {len(left)}")


def rehearse() -> int:
    scratch_repo()
    PASSWORDS.update(fixture_secrets())
    DOTENV.update({"MINIO_ROOT_USER": "rehearsal", "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(18),
                   "PG_PASSWORD": secrets.token_urlsafe(18), "LAKEKEEPER_KEY": secrets.token_urlsafe(24),
                   "K2_TRINO_SHARED_SECRET": secrets.token_urlsafe(32)})
    (CLONE / ".env").write_text("".join(f"{k}={v}\n" for k, v in DOTENV.items()))
    os.chmod(CLONE / ".env", 0o600)

    t0 = time.monotonic()
    r1 = cut("v0.0.1-rehearsal.1", "R1: the good fixture set")
    check("make release compiles R1 at a scratch tag", (ROOT / r1 / "manifest.json").is_file(), f"{r1} in {time.monotonic() - t0:.0f} s")
    manifest = json.loads((ROOT / r1 / "manifest.json").read_text())
    tools_image = manifest["images"]["tools"]
    check("every image of R1 is a digest or a local build by id", all("@sha256:" in r or re.fullmatch(r"[a-z0-9-]+:[0-9a-f]{12}", r)
                                                                    for r in manifest["images"].values()), json.dumps(manifest["images"]))

    compose("up", "-d", "--wait", "prefect-db", "minio", "lakekeeper")  # the stateful services run already, as on the live stack
    c = compose("ps", "-q", "minio").stdout.strip()
    sh("docker", "exec", c, "sh", "-c", 'MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc mb --ignore-existing l/lake')
    print(in_catalog(LAKE_SETUP, tools_image).strip())

    # 1. the first deploy
    t0 = time.monotonic()
    out = make("deploy", f"RELEASE={r1}", check=False)
    took = time.monotonic() - t0
    check("first deploy of R1 exits 0", out.returncode == 0, f"{took:.0f} s")
    if out.returncode:
        print(out.stderr[-6000:])
        return 1
    check("release-verify after R1", make("release-verify", check=False).returncode == 0)

    # what phase 002's wrapper will do at a first publish: build the base, tag it, create the view
    q("dbt_station_readings", "CREATE TABLE sensor_net_build.station_readings__base.silver_readings "
      "WITH (partitioning = ARRAY['day(read_at)']) AS SELECT * FROM (VALUES "
      "('s-1', current_timestamp - INTERVAL '3' HOUR, CAST(1.5 AS decimal(12,3)), 'north', 'synthetic-note-a'), "
      "('s-2', current_timestamp - INTERVAL '30' MINUTE, CAST(2.5 AS decimal(12,3)), 'north', 'synthetic-note-b'), "
      "('s-3', current_timestamp - INTERVAL '3' HOUR, CAST(3.5 AS decimal(12,3)), 'west', 'synthetic-note-c')) "
      "AS t(station_id, read_at, value, region, operator_note)")
    in_catalog(TAG_AUDITED, tools_image)
    v = json.loads((ROOT / r1 / model.VIEWS).read_text())["views"]
    view = next(model.View(x["relation"], x["base"], tuple(x["columns"]), x["owner"]) for x in v if x["relation"] == VIEW)
    q(view.owner, create_view(view))
    delayed, full = q("p.analyst-delayed", f"SELECT station_id, operator_note FROM {VIEW} ORDER BY 1"), q("p.analyst-full", f"SELECT station_id, operator_note FROM {VIEW} ORDER BY 1")
    check("R1: the delayed reader sees 2 rows, operator_note masked; the full reader 3 rows with it",
          delayed["rows"] == [["s-1", None], ["s-2", None]] and len(full["rows"]) == 3 and full["rows"][0][1] == "synthetic-note-a",
          f"delayed {delayed['rows']}, full {len(full['rows'])} rows")

    # 2. a rules-only release: the delay goes from 20 to 60 minutes
    edit(SILVER, "delay: {column: read_at, minutes: 20}", "delay: {column: read_at, minutes: 60}")
    edit(EXPECT, "minRowAgeMinutes: 20", "minRowAgeMinutes: 60")  # a tightened policy: a major, its expectation raised
    bump("1.0.0", "2.0.0")
    prom_rules(2)
    r2 = cut("v0.0.1-rehearsal.2", "R2: a policy change, rules only")
    before = started()
    plan = make("deploy", f"RELEASE={r2}", "PLAN=1")
    check("R2's plan recreates nothing", "recreate, one at a time: nothing" in plan.stderr and "Trino restart: no" in plan.stderr)
    seen: list[float] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            try:
                if len(q("p.analyst-delayed", f"SELECT station_id FROM {VIEW}")["rows"]) == 1:
                    seen.append(time.time())
                    return
            except (k2tools.TrinoError, k2tools.Unreachable):
                pass
            time.sleep(0.05)

    w = threading.Thread(target=watch, daemon=True)
    w.start()
    out = make("deploy", f"RELEASE={r2}", check=False)
    stop.set()
    w.join()
    swap_at = next((r["at"] for r in last_records(12) if r["step"] == "swap" and r["release"] == r2), None)
    after = started()
    check("R2 deploys, exit 0", out.returncode == 0)
    check("R2 restarted no container (StartedAt unchanged for every one)", before == after,
          "" if before == after else f"{set(before.items()) ^ set(after.items())}")
    m = re.search(r"admitted and \S+ refused (\d+\.\d+) s after the swap", out.stderr)
    # the swap record is written when the swap step ends; the reader's first poll under the new policy after it
    lag = seen[0] - datetime.fromisoformat(swap_at.replace("Z", "+00:00")).timestamp() if seen and swap_at else None
    check("R2: Trino applies the new rules within its refresh period", bool(m and float(m[1]) <= 5 + 1 and lag is not None and lag <= 5 + 1),
          f"canary admitted {m[1] if m else '?'} s after the swap; the 60-minute delay reached the polling reader "
          f"{f'{lag:.2f} s' if lag is not None else 'never'} after the swap record")
    check("R2: Prometheus's rules followed the pointer after SIGHUP", "rehearsal_release_2" in prom_group(), prom_group())

    # 3. a release that changes only the stand-in's image
    edit("docker-compose.yml", ALPINE_A, ALPINE_B)
    r3 = cut("v0.0.1-rehearsal.3", "R3: the stand-in's image")
    before = started()
    out = make("deploy", f"RELEASE={r3}", check=False)
    after = started()
    moved = sorted(s for s in after if before.get(s) != after[s])
    check("R3 deploys, exit 0, and recreates only the stand-in", out.returncode == 0 and moved == ["standin"], f"recreated {moved}")
    img3 = after["standin"].split()[1]

    # 4. a forced verification mismatch: back to R3, images included, exit 1
    edit("docker-compose.yml", ALPINE_B, ALPINE_A)
    r4 = cut("v0.0.1-rehearsal.4", "R4: the stand-in back, deployed with a forced mismatch")
    out = make("deploy", f"RELEASE={r4}", fault="verify-mismatch", check=False)
    now_ = started()
    check("a forced mismatch restores R3 and exits 1", "deploy: exit 1:" in out.stderr and out.returncode and live_release() == r3 and now_["standin"].split()[1] == img3,
          f"exit {out.returncode}, pointer {live_release()[-12:]}, stand-in image {'R3' if now_['standin'].split()[1] == img3 else 'NOT R3'}")
    check("after it, release-verify names R3", make("release-verify", check=False).returncode == 0)

    # 5. make rollback: R3 back to R2
    out = make("rollback", check=False)
    check("make rollback returns to R2, the stand-in on R2's image", out.returncode == 0 and live_release() == r2
          and started()["standin"].split()[1] != img3, f"exit {out.returncode}, pointer {live_release()[-12:]}")
    make("deploy", f"RELEASE={r3}")

    # 6. a release that drops the masked column, read by a reader every 50 ms throughout
    drop_masked_column()  # and the stand-in stays on A since R4, against R3's B: the kill series below recreates too
    r5 = cut("v0.0.1-rehearsal.5", "R5: operator_note dropped from silver_readings, its mask with it")
    poller = Poller()
    poller.start()
    time.sleep(1)
    out = make("deploy", f"RELEASE={r5}", check=False)
    time.sleep(1)
    full = q("p.analyst-full", f"SELECT * FROM {VIEW}")
    check("R5 deploys, exit 0, and the view no longer has operator_note", out.returncode == 0
          and "operator_note" not in [c[0] for c in full["columns"]], f"full reader's columns {[c[0] for c in full['columns']]}")
    check("R5: the delayed reader never saw an operator_note value", not poller.leaks and poller.polls > 20,
          f"{poller.polls} polls, {poller.errors} errors, shapes {poller.shapes}, leaks {poller.leaks[:3]}")

    # 7. a SIGKILL after each step, then a rerun: every one converges (and the reader keeps polling)
    steps = ("preflight", "images", "plan", "ddl", "views-narrow", "canary", "swap", "recreate", "rules-loaded",
             "ddl-late", "views-widen", "verify", "done")
    target, other = r3, r5
    bad = []
    for s in steps:
        killed = make("deploy", f"RELEASE={target}", fault=f"kill:{s}", check=False)
        rerun = make("deploy", f"RELEASE={target}", check=False)
        verified = make("release-verify", check=False)
        if f"K2_DEPLOY_FAULT=kill:{s}" not in killed.stderr or "deploy: exit" in killed.stderr \
                or rerun.returncode or verified.returncode or live_release() != target:
            bad.append(f"{s}: kill exit {killed.returncode}, rerun {rerun.returncode}, verify {verified.returncode}")
        target, other = other, target
    check(f"a kill after each of the {len(steps)} steps, then a rerun, converges and verifies", not bad, "; ".join(bad))
    killed = make("deploy", f"RELEASE={target}", fault="verify-mismatch,kill:rollback-swap", check=False)
    rerun = make("rollback", check=False)
    check("a kill inside the automatic rollback, then make rollback, converges", rerun.returncode == 0 and live_release() == other
          and make("release-verify", check=False).returncode == 0, f"rollback exit {rerun.returncode}, pointer {live_release()[-12:]}")
    poller.stop.set()
    poller.join()
    check("through every kill and rerun the delayed reader never saw an operator_note value", not poller.leaks,
          f"{poller.polls} polls, {poller.errors} errors, shapes {poller.shapes}")

    # 8. two deploys at once: the second is refused at the lock
    first = subprocess.Popen(["make", "-s", "-C", str(CLONE), "deploy", f"RELEASE={target}"], env=env(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(4)
    second = make("deploy", f"RELEASE={target}", check=False)
    _, err = first.communicate()
    check("two deploys at once: the second refused at the lock, exit 2, the first exits 0",
          "deploy: exit 2:" in second.stderr and "another deploy holds" in second.stderr and first.returncode == 0,
          f"second exit {second.returncode}, first exit {first.returncode}")

    print("\n".join(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}" for name, ok, detail in RESULTS))
    print(f"rehearsal: make output in {BASE / 'make.log'}" if os.environ.get("KEEP") == "1" else "")
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


def bump(old: str, new: str) -> None:
    """The silver contract's version, and the product's and its output port's."""
    edit(SILVER, f"\nversion: {old}\n", f"\nversion: {new}\n")
    p = CLONE / PRODUCT
    text = p.read_text()
    text = text.replace(f"\nversion: {old}\n", f"\nversion: {new}\n", 1)
    text = text.replace(f"  - name: silver_readings\n    version: {old}", f"  - name: silver_readings\n    version: {new}")
    p.write_text(text)
    edit(READER, f"  - name: silver_readings\n    version: {old}", f"  - name: silver_readings\n    version: {new}")
    reader = (CLONE / READER).read_text()  # and daily-load's own version, a patch for each acknowledgement
    mine = re.search(r"\nversion: 1\.0\.(\d+)\n", reader)
    assert mine
    (CLONE / READER).write_text(reader.replace(mine[0], f"\nversion: 1.0.{int(mine[1]) + 1}\n", 1))


def drop_masked_column() -> None:
    edit(SILVER, "      - name: operator_note\n        physicalType: varchar\n        description: Free text the made-up operator attached.\n", "")
    edit(SILVER, "        mask: [operator_note]\n", "")
    edit(MODEL_SQL, "  region,\n  operator_note\n", "  region\n")
    edit(UNIT, "value: 1.5, region: north, operator_note: synthetic}", "value: 1.5, region: north}")
    expect = (CLONE / EXPECT).read_text()
    expect = re.sub(r"\[station_id, read_at, value, region, operator_note\]", "[station_id, read_at, value, region]", expect)
    expect = re.sub(r"\n\s+masked: \[operator_note\]", "", expect)
    (CLONE / EXPECT).write_text(expect)
    bump("2.0.0", "3.0.0")


if __name__ == "__main__":
    sys.exit(main())
