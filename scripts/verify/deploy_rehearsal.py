#!/usr/bin/env python3
"""The deploy rehearsal (phase 001 steps S19 and S19b, ADR-058): every claim of scripts/deploy.py shown
on a throwaway compose project, never the live stack.

    make deploy-rehearsal [KEEP=1]            the deploy chain on the release-shaped stack
    make deploy-rehearsal CUTOVER=1 [KEEP=1]  the one-time cutover from today's stack shape, and back

Under a new directory in $HOME (Docker Desktop shares only the home directory) it builds a scratch git
repository holding this checkout's code and the repository's own documents (contracts/,
tests/access/expectations.yaml), a releases root and a secrets directory made by the real tools:
`scripts/tls.sh init` for the CA and Trino's certificate, `make password TO_FILE=1` for every service
principal. The canary's readers are people, whose passwords principal-secret.py prints once and never
files, so the rehearsal writes theirs itself, line and file, by that script's own in-place write under
its lock: live access-verify reads people's passwords from principals/<id>.password (a gap for S22).
Compose project `k2-rehearsal`, its own networks and volumes, small limits, no published port but
Trino's HTTPS on a free loopback port: Postgres, MinIO and Lakekeeper (on the catalog's network only),
Trino from the release's compiled etc over the pointer, Prometheus reading a rule file through its
pointer, the tools one-shot and a stand-in. Releases are cut at scratch tags and deployed through
`make` as the runbooks do; each deploy's preflight runs S20's release-time suite on throwaway lakes
(scripts/lib/throwaway-lake.sh), and every verify runs the drift check in tools and S20's live suite.

The chain checks: a first deploy that fails verification (exit 3), then succeeds; the canary published
as phase 002's wrapper will (access_verify.py stage) and release-verify with S20's two entry points and
S13's identity.sh; a planted view the drift check names; a rules-only release that restarts nothing; a
stand-in's new image recreating only it; a forced mismatch restoring R-1, images included, exit 1;
`make rollback`, also with Trino stopped; a release dropping a masked column, a reader polling every 50 ms throughout; a SIGKILL
after each step, then a rerun; two deploys at once; the backup-set refusal (review M4) on a real table.
The cutover run starts from TODAY's shape (Trino on HTTP from the checkout's etc, Lakekeeper on the
shared network with a host port, a stand-in capture), follows docs/runbooks/first-cutover-to-releases.md,
measures each service's downtime, proves the capture untouched, then rolls the whole cutover back.
Exit 0 when every check passed. Everything is removed at the end unless KEEP=1.
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

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


k2tools = _load("k2tools", REPO / "docker/tools/k2tools.py")
ps = _load("principal_secret", REPO / "scripts/principal-secret.py")

PROJECT = "k2-rehearsal"
BASE = Path.home() / f"s19-rehearsal-{os.getpid()}"
CLONE, ROOT, SECRETS, WORK = BASE / "repo", BASE / "releases", BASE / "secrets", BASE / "work"
CANARY = "contracts/governance/access-canary"
EVENTS, PRODUCT = f"{CANARY}/events.odcs.yaml", f"{CANARY}/product.odps.yaml"
MODEL_SQL, EXPECT = f"{CANARY}/models/events.sql", "tests/access/expectations.yaml"
VIEW = "governance.access_canary.events"
RUN_RECORD = "governance_build.run_records.run_record"
RESULTS: list[tuple[str, bool, str]] = []
CODE = ("platform/k2gen/k2gen", "platform/k2gen/pyproject.toml", "platform/k2gen/uv.lock", "platform/runs",
        "contracts", "dbt", "docker/tools", "scripts/deploy.py", "scripts/principal-secret.py",
        "scripts/tls.sh", "scripts/check-env.py", "scripts/backup-lake.sh", "scripts/lib", "scripts/access_verify.py", "scripts/verify", "tests/access", "mk")

COMPOSE = """\
# The rehearsal's compose file, release-shaped (scripts/verify/deploy_rehearsal.py): what S22 makes live.
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
    networks: [k2-net, k2-catalog]
    volumes: ["pg:/var/lib/postgresql/data"]
    healthcheck: {test: ["CMD", "pg_isready", "-U", "lk"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: ["server", "/data"]
    environment: {MINIO_ROOT_USER: "${MINIO_ROOT_USER}", MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"}
    networks: [k2-net, k2-catalog]
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
  lakekeeper:  # on the catalog's network only, no host port (ADR-059 part 7)
    image: quay.io/lakekeeper/catalog:v0.13.9
    command: ["serve"]
    environment: *lk
    networks: [k2-catalog]
    depends_on: {lakekeeper-migrate: {condition: service_completed_successfully}}
    healthcheck: {test: ["CMD", "/home/nonroot/lakekeeper", "healthcheck"], interval: 2s, retries: 30}
    restart: unless-stopped
    <<: *small
  trino:  # S13's needs: the key FILE, password.db's directory, three env names, 8443 on loopback
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
    healthcheck:  # the image's own probes HTTP 8080 when etc is not /etc/trino
      test: ["CMD", "bash", "-c", "i=$$(curl -fsk https://localhost:8443/v1/info) && [[ $$i == *'\\"starting\\":false'* ]]"]
      interval: 3s
      retries: 60
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '2.0', memory: 4G}}}
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
  capture-binance:  # a stand-in capture: deploy and the cutover must never restart it
    image: alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
    command: ["sleep", "infinity"]
    networks: [k2-net]
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


def free_port() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


PORT = free_port()
DOTENV: dict[str, str] = {}


def env() -> dict[str, str]:
    return {**os.environ, **DOTENV, "K2_RELEASES": str(ROOT), "K2_SECRETS": str(SECRETS), "K2_COMPOSE_PROJECT": PROJECT,
            "K2_TRINO_PORT": PORT, "K2_HOME": str(CLONE), "UV_NO_PROGRESS": "1"}


def make(*args: str, fault: str = "", check: bool = True) -> subprocess.CompletedProcess[str]:
    e = {k: v for k, v in env().items() if k != "K2_DEPLOY_FAULT"} | ({"K2_DEPLOY_FAULT": fault} if fault else {})
    t0 = time.monotonic()
    done = sh("make", "-s", "-C", str(CLONE), *args, env=e, check=False)
    with open(BASE / "make.log", "a") as f:
        f.write(f"\n$ make {' '.join(args)}{f'  (K2_DEPLOY_FAULT={fault})' if fault else ''}\n"
                f"exit {done.returncode} in {time.monotonic() - t0:.1f} s\n{done.stdout}{done.stderr}")
    if check and done.returncode:
        raise RuntimeError(f"make {' '.join(args)}: exit {done.returncode}\n{done.stderr[-4000:]}")
    return done


def code(done: subprocess.CompletedProcess[str]) -> int | None:
    """deploy.py's own exit code, from its last line: make reports any failure as its own 2."""
    m = re.findall(r"deploy: exit (\d):", done.stderr)
    return int(m[-1]) if m else None


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)


def compose(*args: str, file: Path | None = None, check_: bool = True) -> subprocess.CompletedProcess[str]:
    return sh("docker", "compose", "-p", PROJECT, "--project-directory", str(CLONE), "-f", str(file or CLONE / "docker-compose.yml"),
              *args, env=env(), check=check_)


def q(user: str, statement: str) -> dict[str, Any]:
    return k2tools.sql(f"https://localhost:{PORT}", user, (SECRETS / model.password_file(user)).read_text().strip(),
                       str(SECRETS / "tls/ca.pem"), statement)


def edit(path: str, old: str, new: str, count: int = -1) -> None:
    p = CLONE / path
    text = p.read_text()
    assert old in text, f"{path}: {old!r} not found"
    p.write_text(text.replace(old, new, count))


def cut(tag: str, message: str) -> str:
    """Commit the scratch tree, tag it, compile it with `make release`: the release id."""
    sh("git", "-C", str(CLONE), "add", "-A")
    sh("git", "-C", str(CLONE), "commit", "-q", "--allow-empty", "-m", message)
    sh("git", "-C", str(CLONE), "tag", "-a", tag, "-m", message)
    return make("release", f"TAG={tag}").stdout.strip().splitlines()[-1]


def bump(old: str, new: str) -> None:
    """The canary's contract version, and its product's and output port's."""
    edit(EVENTS, f"\nversion: {old}\n", f"\nversion: {new}\n", 1)
    edit(PRODUCT, f"\nversion: {old}\n", f"\nversion: {new}\n", 1)
    edit(PRODUCT, f"    version: {old}", f"    version: {new}", 1)


def prom_rules(n: int) -> None:
    d = CLONE / "domains/rehearsal/rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / "release.yml").write_text(f"groups:\n  - name: rehearsal_release_{n}\n    rules:\n"
                                   f"      - record: k2_rehearsal_release\n        expr: vector({n})\n")


# --- the scratch repository, its secrets and its lake -------------------------------------


def scratch_repo(compose_text: str) -> None:
    for rel in CODE:
        src, dst = REPO / rel, CLONE / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".mypy_cache", "target", "logs", "dbt_packages"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (CLONE / "Makefile").write_text("include mk/*.mk\n")
    (CLONE / "docker-compose.yml").write_text(compose_text)
    prom_rules(1)
    (CLONE / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n")
    sh("git", "init", "-q", "-b", "main", str(CLONE))
    sh("git", "-C", str(CLONE), "config", "user.name", "Rehearsal Operator")
    sh("git", "-C", str(CLONE), "config", "user.email", "rehearsal@example.org")


def real_secrets() -> None:
    """The CA and Trino's certificate by scripts/tls.sh; each service principal's password by
    `make password TO_FILE=1`; the canary's people by principal-secret.py's own locked write."""
    sh("bash", str(CLONE / "scripts/tls.sh"), "init", env=env())
    doc = (CLONE / "contracts/access/principals.yaml").read_text()
    ids = re.findall(r"^  - id: (\S+)$", doc, re.M)
    for pid in ids:
        if pid.startswith("p."):
            secret = secrets.token_urlsafe(32)
            hashed = f"$2y${bcrypt.hashpw(secret.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode().removeprefix('$2b$')}"
            with ps.locked(SECRETS / ps.PASSWORD_DB) as db:  # principal-secret.py's slot writer, whole file checked
                users = ps.entries(ps.read(db))
                ps.write_db(db, "".join(f"{u}:{h}\n" for u, h in {**users, pid: hashed}.items()))
            with ps.locked(SECRETS / model.password_file(pid)) as f:
                ps.write(f, secret)
        else:
            make("password", f"PRINCIPAL={pid}", "TO_FILE=1")
    print(f"rehearsal: secrets by scripts/tls.sh and principal-secret.py for {len(ids)} principals")


def dotenv() -> None:
    DOTENV.update({"MINIO_ROOT_USER": "rehearsal", "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(18),
                   "PG_PASSWORD": secrets.token_urlsafe(18), "LAKEKEEPER_KEY": secrets.token_urlsafe(24),
                   "K2_TRINO_SHARED_SECRET": secrets.token_urlsafe(32)})
    (CLONE / ".env").write_text("".join(f"{k}={v}\n" for k, v in DOTENV.items()))
    os.chmod(CLONE / ".env", 0o600)


LAKE_SETUP = '''
import json, os, sys, urllib.request
def call(m, u, b=None):
    r = urllib.request.Request(u, data=None if b is None else json.dumps(b).encode(), method=m, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return x.status
    except urllib.error.HTTPError as e: return e.code
lk = sys.argv[1]
print("bootstrap", call("POST", f"{lk}/management/v1/bootstrap", {"accept-terms-of-use": True}))
for w in sys.argv[2:]:
    body = {"warehouse-name": w, "project-id": "00000000-0000-0000-0000-000000000000",
            "storage-profile": {"type": "s3", "bucket": "lake", "key-prefix": w, "endpoint": "http://minio:9000",
                                "region": "local-01", "path-style-access": True, "flavor": "s3-compat", "sts-enabled": False,
                                "remote-signing-enabled": False},
            "storage-credential": {"type": "s3", "credential-type": "access-key",
                                   "aws-access-key-id": os.environ["MINIO_ROOT_USER"], "aws-secret-access-key": os.environ["MINIO_ROOT_PASSWORD"]}}
    print("warehouse", w, call("POST", f"{lk}/management/v1/warehouse", body))
'''


def lake_setup(release: str, network: str, lakekeeper: str = "http://lakekeeper:8181") -> None:
    """What lake-init does: the bucket, the catalog's bootstrap, every warehouse of the storage plan."""
    c = compose("ps", "-q", "minio").stdout.strip()
    sh("docker", "exec", c, "sh", "-c", 'MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc mb --ignore-existing l/lake')
    plan = json.loads((ROOT / release / model.STORAGE_PLAN).read_text())
    tools = json.loads((ROOT / release / "manifest.json").read_text())["images"]["tools"]
    print(sh("docker", "run", "--rm", "-i", "--network", network, "--memory", "128m", "-e", "MINIO_ROOT_USER",
             "-e", "MINIO_ROOT_PASSWORD", tools, "python3", "-", lakekeeper, *[w["name"] for w in plan["warehouses"]],
             input=LAKE_SETUP, env=env()).stdout.strip().replace("\n", "; "))


def publish(release: str) -> str:
    """The canary published as phase 002's wrapper will: S20's stage, as the builder, the tag commit
    and the publisher, in a tools container on the catalog's network. Stage also creates the run-record
    table, which the deploy has already made empty, so it is dropped first (no run writes it before 002)."""
    q("svc.platform", f"DROP TABLE IF EXISTS {RUN_RECORD}")
    WORK.mkdir(exist_ok=True)
    lake_env = _load("lake_env", REPO / "scripts/lib/lake_env.py")  # the one writer of env.json
    plan = json.loads((ROOT / release / model.STORAGE_PLAN).read_text())
    lake_env.write(WORK / "env.json", release=f"/k2/releases/{release}", trino="https://trino:8443",
                   lakekeeper="http://lakekeeper:8181", secrets="/work/secrets", ca="/work/secrets/tls/ca.pem",
                   passwords={f.stem: f"/work/secrets/principals/{f.name}" for f in (SECRETS / "principals").glob("*.password")},
                   warehouses=[w["name"] for w in plan["warehouses"]])
    tools = json.loads((ROOT / release / "manifest.json").read_text())["images"]["tools"]
    return sh("docker", "run", "--rm", "--network", f"{PROJECT}_k2-catalog", "--user", f"{os.getuid()}:{os.getgid()}",
              "--memory", "256m", "-v", f"{CLONE / 'scripts'}:/opt/k2/scripts:ro", "-v", f"{ROOT}:/k2/releases:ro",
              "-v", f"{SECRETS}:/work/secrets:ro", "-v", f"{WORK / 'env.json'}:/work/env.json:ro", tools,
              "python3", "/opt/k2/scripts/access_verify.py", "stage", "--release", f"/k2/releases/{release}",
              "--env", "/work/env.json").stdout.strip().splitlines()[-1]


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


def records(n: int = 0) -> list[dict[str, Any]]:
    lines = (ROOT / model.DEPLOY_LOG).read_text().splitlines()
    return [json.loads(x) for x in lines[-n:]]


class Poller(threading.Thread):
    """The delayed reader polling the canary's view every 50 ms: what it ever saw of the masked note."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.polls = self.errors = 0
        self.leaks: list[str] = []
        self.shapes: dict[str, int] = {}

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                r = q("p.canary-delayed", f"SELECT * FROM {VIEW}")
                cols = [c[0] for c in r["columns"]]
                self.shapes[",".join(cols)] = self.shapes.get(",".join(cols), 0) + 1
                if "note" in cols:
                    self.leaks += [str(row[cols.index("note")]) for row in r["rows"] if row[cols.index("note")] is not None]
                self.polls += 1
            except (k2tools.TrinoError, k2tools.Unreachable, TimeoutError) as e:
                self.errors += 1
                self.shapes[f"error {str(e)[:70]}"] = self.shapes.get(f"error {str(e)[:70]}", 0) + 1
            time.sleep(0.05)


def prom_group() -> str:
    c = compose("ps", "-q", "prometheus").stdout.strip()
    out = sh("docker", "exec", c, "wget", "-qO-", "http://localhost:9090/api/v1/rules", check=False).stdout
    return ",".join(g["name"] for g in json.loads(out or "{}").get("data", {}).get("groups", [])) if out else ""


def result(release: str, kind: str) -> dict[str, Any]:
    p = ROOT / "verify" / f"{release}.{kind}.json"
    return json.loads(p.read_text()) if p.exists() else {}


# --- the deploy chain ----------------------------------------------------------------------


# The emitters before the review of S12 made the reader view fail closed (ba78445): R1 is cut with them,
# so the chain crosses from the old view shape to the guarded one, and back, with a reader polling.
OLD_SHAPE = {f"platform/k2gen/k2gen/emit/{m}.py": "355fa43" for m in ("lake", "trino_rules", "api")}  # api imports rules


def emitters(old: bool) -> None:
    for path, ref in OLD_SHAPE.items():
        (CLONE / path).write_text(sh("git", "-C", str(REPO), "show", f"{ref}:{path}").stdout if old else (REPO / path).read_text())


def rehearse() -> int:
    scratch_repo(COMPOSE.replace("STANDIN_IMAGE", ALPINE_A))
    emitters(old=True)
    dotenv()
    real_secrets()
    t0 = time.monotonic()
    r1 = cut("v0.0.1-rehearsal.1", "R1: the repository's own documents, the reader view in its old shape")
    check("make release compiles R1 at a scratch tag", (ROOT / r1 / "manifest.json").is_file(), f"{r1} in {time.monotonic() - t0:.0f} s")
    compose("up", "-d", "--wait", "prefect-db", "minio", "lakekeeper", "capture-binance")  # running already, as live
    lake_setup(r1, f"{PROJECT}_k2-catalog")
    capture = started()["capture-binance"]

    # 1. a first deploy that fails, then the first deploy (its preflight runs the release-time suites)
    t0 = time.monotonic()
    out = make("deploy", f"RELEASE={r1}", fault="verify-mismatch", check=False)
    check("a first deploy that fails verification exits 3 (nothing to go back to)", code(out) == 3,
          f"exit {code(out)} in {time.monotonic() - t0:.0f} s")
    access = result(r1, "access-release")
    check("release time: access-verify on a throwaway lake, every row", access.get("exit") == 0,
          f"counts {access.get('counts')}")
    marker = result(r1, "marker")
    check("release time: the tested-data marker check", marker.get("exit") == 0,
          f"{sum(1 for x in marker.get('lines', []) if x.startswith('PASS'))} PASS")
    first = out
    t0 = time.monotonic()
    out = make("deploy", f"RELEASE={r1}", check=False)
    check("then the first deploy converges, exit 0", code(out) == 0, f"{time.monotonic() - t0:.0f} s (release-time suites kept from the first try)")
    if code(out) != 0:
        print(out.stderr[-6000:])
        return 1
    check("the run-record table created by deploy as svc.platform after the rules loaded, then found equal",
          "created governance.run_records.run_record (trino)" in first.stderr
          and "exists and matches governance.run_records.run_record" in out.stderr)
    check("the canary is pending until published: the live suite SKIPs with its reason",
          "SKIP access-verify LIVE=1: not yet published" in out.stderr)

    # 2. the canary published; release-verify with S20's two entry points and S13's identity.sh
    print("rehearsal:", publish(r1))
    t0 = time.monotonic()
    out = make("release-verify", check=False)
    live = result(r1, "access-live")
    check("release-verify: drift clean, access-verify LIVE=1 passed, identity.sh passed",
          code(out) == 0 and "catalog drift check clean" in out.stderr and live.get("exit") == 0 and "identity.sh:" in out.stderr,
          f"{time.monotonic() - t0:.0f} s; live counts {live.get('counts')}; "
          + " ".join(x.strip() for x in out.stderr.splitlines() if "identity.sh" in x or "drift" in x)[:300])
    q("dbt_access_canary", "CREATE TABLE governance_build.access_canary__base.planted (x integer)")  # beside the base
    out = make("release-verify", check=False)
    check("a planted table: release-verify fails and the drift check names it", code(out) == 1 and "planted" in out.stderr,
          next((x.strip()[:200] for x in out.stderr.splitlines() if "planted" in x), ""))
    q("dbt_access_canary", "DROP TABLE governance_build.access_canary__base.planted")
    check("the plant removed: release-verify passes again", code(make("release-verify", check=False)) == 0)

    # 3. a rules-only release: the delay goes from 20 to 60 minutes (a major), its expectation with it
    edit(EVENTS, "delay: {column: event_ts, minutes: 20}", "delay: {column: event_ts, minutes: 60}")
    edit(EXPECT, "minRowAgeMinutes: 20", "minRowAgeMinutes: 60")  # a tightened policy: a major, its rows with it
    edit(EXPECT, 'mustAppear: {where: "id IN (1, 2, 4)", minRows: 3}', 'mustAppear: {where: "id IN (1, 4)", minRows: 2}')  # 2 is 21 min old
    bump("1.0.0", "2.0.0")
    prom_rules(2)
    emitters(old=False)
    r2 = cut("v0.0.1-rehearsal.2", "R2: a policy change, rules only, and the reader view in its guarded shape")
    shape = Poller()
    shape.start()
    before = started()
    seen: list[float] = []
    stop = threading.Event()
    base_rows = len(q("p.canary-delayed", f"SELECT id FROM {VIEW}")["rows"])

    def watch() -> None:
        while not stop.is_set():
            try:
                if len(q("p.canary-delayed", f"SELECT id FROM {VIEW}")["rows"]) < base_rows:
                    seen.append(time.time())
                    return
            except (k2tools.TrinoError, k2tools.Unreachable):
                pass
            time.sleep(0.05)

    w = threading.Thread(target=watch, daemon=True)
    w.start()
    t0 = time.monotonic()
    out = make("deploy", f"RELEASE={r2}", check=False)
    took = time.monotonic() - t0
    stop.set()
    w.join()
    after = started()
    swap_at = next((r["at"] for r in reversed(records(40)) if r["step"] == "swap" and r["release"] == r2), None)
    lag = seen[0] - datetime.fromisoformat(swap_at.replace("Z", "+00:00")).timestamp() if seen and swap_at else None
    check("R2 (rules only) deploys and restarts no container", code(out) == 0 and before == after,
          f"{took:.0f} s with its release-time suites; {'' if before == after else set(before.items()) ^ set(after.items())}")
    m = re.search(r"admitted and \S+ refused (\d+\.\d+) s after the swap", out.stderr)
    check("R2: the new rules applied within the refresh period", bool(m and float(m[1]) <= 6 and lag is not None and lag <= 6),
          f"canary {m[1] if m else '?'} s after the swap; the 60-minute delay reached the polling reader "
          f"{f'{lag:.2f} s' if lag is not None else 'never'} after the swap record")
    check("R2: Prometheus's rules followed the pointer after SIGHUP", "rehearsal_release_2" in prom_group(), prom_group())
    guarded = "EXISTS" in (q("pub_access_canary", "SELECT view_definition FROM governance_build.information_schema.views "
                              "WHERE table_name = 'events'")["rows"] or [[""]])[0][0]
    back = make("rollback", check=False)
    again = make("deploy", f"RELEASE={r2}", check=False)
    shape.stop.set()
    shape.join()
    check("the view's shape crosses old to guarded, back by make rollback, and forward: no error, no note, ever",
          guarded and code(back) == 0 and code(again) == 0 and not shape.leaks and not shape.errors and shape.polls > 50,
          f"guarded after R2: {guarded}; {shape.polls} polls, {shape.errors} errors, shapes {shape.shapes}; "
          + "; ".join(x.strip()[:120] for x in (back.stderr + again.stderr).splitlines() if "shape" in x))

    # 4. the stand-in's image only
    edit("docker-compose.yml", ALPINE_A, ALPINE_B, 1)
    r3 = cut("v0.0.1-rehearsal.3", "R3: the stand-in's image")
    before = started()
    out = make("deploy", f"RELEASE={r3}", check=False)
    after = started()
    moved = sorted(s for s in after if before.get(s) != after[s])
    check("R3 recreates only the stand-in", code(out) == 0 and moved == ["standin"], f"recreated {moved}")
    img3 = after["standin"].split()[1]

    # 5. a forced mismatch: back to R3, images included, exit 1
    edit("docker-compose.yml", ALPINE_B, ALPINE_A, 1)
    r4 = cut("v0.0.1-rehearsal.4", "R4: the stand-in back, deployed with a forced mismatch")
    out = make("deploy", f"RELEASE={r4}", fault="verify-mismatch", check=False)
    failed = [r for r in records(40) if r["outcome"] == "failed" and r["release"] == r4]
    check("a forced mismatch restores R3, its image too, exit 1, the reason recorded",
          code(out) == 1 and live_release() == r3 and started()["standin"].split()[1] == img3
          and bool(failed) and "verify-mismatch" in failed[-1].get("reason", ""), (failed[-1].get("reason", "") if failed else "")[:120])
    out = make("rollback", check=False)
    check("make rollback returns to R2, the stand-in on R2's image", code(out) == 0 and live_release() == r2
          and started()["standin"].split()[1] != img3)
    make("deploy", f"RELEASE={r3}")
    compose("stop", "trino")  # Trino down under the live release (review R1 H2): the rollback swaps first
    out = make("rollback", check=False)
    check("with Trino stopped, make rollback moves the pointers and recreates first, exit 0",
          code(out) == 0 and live_release() == r2 and "Trino does not answer" in out.stderr,
          next((x.strip()[:160] for x in out.stderr.splitlines() if "FAILED" in x), ""))
    make("deploy", f"RELEASE={r3}")

    # 6. the masked column dropped, read every 50 ms throughout
    drop_note()
    r5 = cut("v0.0.1-rehearsal.5", "R5: note dropped from the canary, its mask with it; the stand-in on A")
    poller = Poller()
    poller.start()
    time.sleep(1)
    out = make("deploy", f"RELEASE={r5}", check=False)
    time.sleep(1)
    cols = [c[0] for c in q("p.canary-full", f"SELECT * FROM {VIEW}")["columns"]]
    check("R5 deploys and the view no longer has note", code(out) == 0 and "note" not in cols, f"columns {cols}")
    check("R5: the delayed reader never saw a note", not poller.leaks and poller.polls > 20,
          f"{poller.polls} polls, {poller.errors} errors, shapes {poller.shapes}")

    # 7. a SIGKILL after each step, then a rerun, alternating R3 and R5, the reader still polling
    steps = ("preflight", "images", "plan", "ddl", "views-narrow", "canary", "swap", "recreate", "rules-loaded",
             "ddl-late", "views-widen", "verify", "done")
    target, other, bad = r3, r5, []
    for s in steps:
        killed = make("deploy", f"RELEASE={target}", fault=f"kill:{s}", check=False)
        rerun = make("deploy", f"RELEASE={target}", check=False)
        verified = make("release-verify", check=False)
        if f"K2_DEPLOY_FAULT=kill:{s}" not in killed.stderr or code(killed) is not None or code(rerun) or code(verified) \
                or live_release() != target:
            bad.append(f"{s}: kill {code(killed)}, rerun {code(rerun)}, verify {code(verified)}")
        target, other = other, target
    check(f"a kill after each of the {len(steps)} steps, then a rerun, converges and verifies", not bad, "; ".join(bad))
    make("deploy", f"RELEASE={target}", fault="verify-mismatch,kill:rollback-swap", check=False)
    out = make("rollback", check=False)
    check("a kill inside the automatic rollback, then make rollback, converges", code(out) == 0 and live_release() == other
          and code(make("release-verify", check=False)) == 0)
    poller.stop.set()
    poller.join()
    check("through every kill and rerun the delayed reader never saw a note", not poller.leaks,
          f"{poller.polls} polls, {poller.errors} errors, shapes {poller.shapes}")

    # 8. two deploys at once
    first = subprocess.Popen(["make", "-s", "-C", str(CLONE), "deploy", f"RELEASE={other}"], env=env(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(4)
    second = make("deploy", f"RELEASE={other}", check=False)
    _, err = first.communicate()
    check("two deploys at once: the second refused at the lock (exit 2), the first exits 0",
          code(second) == 2 and "another deploy holds" in second.stderr and code(subprocess.CompletedProcess([], 0, "", err)) == 0)

    # 9. the backup-set refusal (review M4) on a real table: restore, then rebuild while the table holds rows
    edit(EVENTS, "  - property: backup\n    value: rebuild", "  - property: backup\n    value: restore")
    bump("3.0.0", "3.0.1")
    r6 = cut("v0.0.1-rehearsal.6", "R6: the canary's events kept by restore")
    out6 = make("deploy", f"RELEASE={r6}", check=False)
    edit(EVENTS, "  - property: backup\n    value: restore", "  - property: backup\n    value: rebuild")
    bump("3.0.1", "3.0.2")
    r7 = cut("v0.0.1-rehearsal.7", "R7: back to rebuild while the table still holds its rows")
    out7 = make("deploy", f"RELEASE={r7}", check=False)
    check("M4: a release dropping a restore entry while the table holds rows is refused, naming make drop",
          code(out6) == 0 and code(out7) == 2 and "make drop TARGET" in out7.stderr and live_release() == r6,
          next((x.strip()[:220] for x in out7.stderr.splitlines() if "REFUSED" in x), ""))
    check("the stand-in capture was never restarted by any of it", started()["capture-binance"] == capture)
    return summary()


def drop_note() -> None:
    """note out of the canary's contract (its column block), its mask, its model and its rows."""
    text = (CLONE / EVENTS).read_text()
    start = text.index("      - name: note\n")
    end = text.index("      - name: ", start + 10)
    (CLONE / EVENTS).write_text(text[:start] + text[end:])
    edit(EVENTS, "        mask: [note]\n", "")
    edit(MODEL_SQL, "  cast(note as varchar) as note,\n", "")
    edit(EXPECT, "[id, region, note, event_ts]", "[id, region, event_ts]")
    edit(EXPECT, "\n    masked: [note]", "")
    bump("2.0.0", "3.0.0")  # the stand-in has been on A since R4, against R3's B: the kills below recreate it


def summary() -> int:
    print("\n".join(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}" for name, ok, detail in RESULTS))
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


def main() -> int:
    avail = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text())[1]) / 2**20  # type: ignore[index]
    if avail < 10:  # the stack, and a throwaway lake beside it at each new release's preflight
        print(f"rehearsal: {avail:.1f} GiB available, under 10 GiB; not started")
        return 2
    BASE.mkdir(mode=0o700)
    ROOT.mkdir()
    print(f"rehearsal: {BASE}, project {PROJECT}, Trino on 127.0.0.1:{PORT}; {avail:.1f} GiB available", flush=True)
    try:
        if os.environ.get("CUTOVER") == "1":
            cutover = _load("deploy_cutover", Path(__file__).with_name("deploy_cutover.py"))
            return int(cutover.rehearse(sys.modules[__name__]))
        return rehearse()
    finally:
        if os.environ.get("KEEP") == "1":
            print(f"rehearsal: KEEP=1: left {BASE} and project {PROJECT} running")
        else:
            for f in (CLONE / "docker-compose.yml", CLONE / "docker-compose.today.yml"):
                if f.exists():
                    compose("--profile", "tools", "down", "-v", "--remove-orphans", file=f, check_=False)
            for p in (BASE, *BASE.rglob("*")):
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o700)
            shutil.rmtree(BASE, ignore_errors=True)
            left = sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}").stdout.split()
            print(f"rehearsal: removed {BASE}; containers left: {len(left)}")


if __name__ == "__main__":
    sys.exit(main())
