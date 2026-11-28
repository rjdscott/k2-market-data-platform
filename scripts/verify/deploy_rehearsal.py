#!/usr/bin/env python3
"""The deploy rehearsal (phase 001 steps S19 and S19b, ADR-058): every claim of scripts/deploy.py shown
on a throwaway compose project, never the live stack.

    make deploy-rehearsal [KEEP=1]            the deploy chain on the release-shaped stack
    make deploy-rehearsal CUTOVER=1 [KEEP=1]  the one-time cutover from today's stack shape, and back
    make deploy-rehearsal FAST=1 [KEEP=1]     sections 1 to 3, kills after swap and recreate only: merge points

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
after each step, then a rerun; a release adding a product R1 was cut without (quality-results), first
with a forced mismatch (exit 1, its namespaces LEFT, a plant beside them still DRIFT), then for real
(FAST too); two deploys at once; the backup-set refusal (review M4) on a real table; then, on a fresh lake, the
first deploy of phase 004 (section 10, FAST too): LIVE_TAG's code and documents deployed by its own deploy.py, then
this checkout's release over it (the holds table, new connector kinds and products) with a scheduler stand-in.
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
FAST = os.environ.get("FAST") == "1" and os.environ.get("CUTOVER") != "1"  # merge points; the full chain before a tag
FAST_SKIPS = ("4 (one image), 5 (forced mismatch, rollback with Trino stopped), 6 (masked column dropped), "
              "7's kills after 12 of its 14 steps and inside the automatic rollback, 8 (two deploys at once), 9 (M4)")
BASE = Path.home() / f"s19-rehearsal-{os.getpid()}"
CLONE, ROOT, SECRETS, WORK = BASE / "repo", BASE / "releases", BASE / "secrets", BASE / "work"
CANARY = "contracts/governance/access-canary"
EVENTS, PRODUCT = f"{CANARY}/events.odcs.yaml", f"{CANARY}/product.odps.yaml"
MODEL_SQL, EXPECT = f"{CANARY}/models/events.sql", "tests/access/expectations.yaml"
VIEW = "governance.access_canary.events"
# A dbt-only product nothing reads: R1 is cut without it and section 7b brings it back, so one release
# of the chain ADDS a product (its builder and publisher unknown to the live rules until the swap).
ADDED, ADDED_VIEW = "contracts/governance/quality-results", "governance.quality_results.test_results"
ADDED_PRINCIPALS = ("dbt_quality_results", "pub_quality_results")
ADDED_NAMESPACES = ("governance.quality_results", "governance.quality_results__base")
RESULTS: list[tuple[str, bool, str]] = []
CODE = ("platform/k2gen/k2gen", "platform/k2gen/pyproject.toml", "platform/k2gen/uv.lock", "platform/runs",
        "contracts", "dbt", "docker/tools", "scripts/deploy.py", "scripts/principal-secret.py",
        "scripts/tls.sh", "scripts/check-env.py", "scripts/backup-lake.sh", "scripts/lib", "scripts/access_verify.py", "scripts/verify", "tests/access", "mk",
        "docker/lake", "scripts/engine-keys.py", ".env.example", "docker/spark", "services/k2-api")  # lake-init's script and policies; make engine-keys and the placeholders it replaces; Spark's image; k2-api's, which a lake builds when a row runs via REST

COMPOSE = """\
# The rehearsal's compose file, release-shaped (scripts/verify/deploy_rehearsal.py): what S22 makes live.
networks:
  k2-net: {}
  k2-catalog: {}
  k2-egress: {}  # fetch's alone (no service here joins it): deploy creates it, and the fetch probe rows dial from it
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
    image: ghcr.io/rjdscott/minio@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2
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
      K2_TRINO_S3_ACCESS_KEY: "${K2_TRINO_S3_ACCESS_KEY:?make engine-keys}"  # its own MinIO user, made by lake_setup
      K2_TRINO_S3_SECRET_KEY: "${K2_TRINO_S3_SECRET_KEY:?make engine-keys}"
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
  spark-iceberg:  # warehouse k2's writer: deploy's ddl step runs spark-sql in it, as on the live stack
    build: {context: ., dockerfile: docker/spark/Dockerfile}  # make release builds it from the tag, as live (layer cache: no COPY)
    image: SPARK_IMAGE
    command: ["sleep", "infinity"]  # no History Server here: the drivers deploy starts by docker exec are the point
    environment:
      K2_SPARK_S3_ACCESS_KEY: "${K2_SPARK_S3_ACCESS_KEY:?make engine-keys}"
      K2_SPARK_S3_SECRET_KEY: "${K2_SPARK_S3_SECRET_KEY:?make engine-keys}"
      AWS_ACCESS_KEY_ID: "${K2_SPARK_S3_ACCESS_KEY:?make engine-keys}"  # deploy's spark-sql takes S3FileIO's default chain
      AWS_SECRET_ACCESS_KEY: "${K2_SPARK_S3_SECRET_KEY:?make engine-keys}"
      AWS_REGION: us-east-1
    networks: [k2-net, k2-catalog]
    volumes: [*releases-ro]
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '1.0', memory: 1536M}}}  # scripts/verify/storage-keys.sh's Spark limits
  capture-binance:  # a stand-in capture: deploy and the cutover must never restart it
    image: alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
    command: ["sleep", "infinity"]
    networks: [k2-net]
    restart: unless-stopped
    deploy: {resources: {limits: {cpus: '0.1', memory: 32M}}}
  redpanda:  # a stand-in broker: a Kafka product's brokers and registry name a service of the stack on
    # spark-iceberg's network (k2gen's connectors.host-outside-stack); nothing here loads from it
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
      rule_files: [/k2/releases/pointers/prometheus/documents/domains/rehearsal/rules/*.yml,
                   /k2/releases/pointers/prometheus/compiled/prometheus/*.yml]  # docker/prometheus/prometheus.yml's glob
"""
ALPINE_A = "alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"  # alpine:3.20, local
ALPINE_B = "alpine:3"  # pinned by make release to its local digest; another image id than A
SPARK = re.search(r"image: *(k2-spark-iceberg:\S+)", (REPO / "docker-compose.yml").read_text())[1]  # type: ignore[index]  # the stack's pin


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
            "K2_TRINO_PORT": PORT, "K2_HOME": str(CLONE), "K2_BACKUP_DIR": str(BASE / "backup"), "UV_NO_PROGRESS": "1"}


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


def code_at(ref: str | None) -> None:
    """The scratch tree's CODE as this checkout has it (None), or as the repository had it at tag `ref`."""
    for rel in CODE:
        dst = CLONE / rel
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
    if ref:
        tar = subprocess.run(["git", "-C", str(REPO), "archive", ref, *CODE], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(CLONE)], input=tar, check=True)
        return
    for rel in CODE:
        src, dst = REPO / rel, CLONE / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".mypy_cache", "target", "logs", "dbt_packages"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def scratch_repo(compose_text: str) -> None:
    code_at(None)
    (CLONE / "Makefile").write_text("include mk/*.mk\n")
    (CLONE / "docker-compose.yml").write_text(compose_text)
    prom_rules(1)
    (CLONE / ".gitignore").write_text(".env*\n!.env.example\n.venv/\n__pycache__/\n")  # .env and engine-keys' backups
    sh("git", "init", "-q", "-b", "main", str(CLONE))
    sh("git", "-C", str(CLONE), "config", "user.name", "Rehearsal Operator")
    sh("git", "-C", str(CLONE), "config", "user.email", "rehearsal@example.org")


def real_secrets(skip: tuple[str, ...] = ()) -> None:
    """The CA and Trino's certificate by scripts/tls.sh; each service principal's password by
    `make password TO_FILE=1`; the canary's people by principal-secret.py's own locked write.
    `skip`: principals of a product the scratch tree does not hold yet (its password comes with it)."""
    sh("bash", str(CLONE / "scripts/tls.sh"), "init", env=env())
    known = ps.known()  # principal-secret.py's own: the declared ones, each dbt product's builder and publisher, the partners
    ids = sorted(set(known[0]) - set(skip))
    for pid in ids:
        password(pid, known[3])
    print(f"rehearsal: secrets by scripts/tls.sh and principal-secret.py for {len(ids)} principals")


def password(pid: str, partners: set[str]) -> None:
    """A service principal's by `make password TO_FILE=1`. A person's or a partner's, which principal-secret.py
    prints once to a terminal, written here by its own locked writes: its password.db line, and for a person
    the file live access-verify reads; a partner's rows are skipped live, so it has no file."""
    if not pid.startswith("p.") and pid not in partners:
        make("password", f"PRINCIPAL={pid}", "TO_FILE=1")
        return
    secret = secrets.token_urlsafe(32)
    hashed = f"$2y${bcrypt.hashpw(secret.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode().removeprefix('$2b$')}"
    with ps.locked(SECRETS / ps.PASSWORD_DB) as db:  # principal-secret.py's slot writer, whole file checked
        users = ps.entries(ps.read(db))
        ps.write_db(db, "".join(f"{u}:{h}\n" for u, h in {**users, pid: hashed}.items()))
    if pid.startswith("p."):
        with ps.locked(SECRETS / model.password_file(pid)) as f:
            ps.write(f, secret)


def dotenv() -> None:
    DOTENV.update({"MINIO_ROOT_USER": "rehearsal", "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(18),
                   "PG_PASSWORD": secrets.token_urlsafe(18), "LAKEKEEPER_KEY": secrets.token_urlsafe(24),
                   "K2_TRINO_SHARED_SECRET": secrets.token_urlsafe(32)})
    (CLONE / ".env").write_text("".join(f"{k}={v}\n" for k, v in DOTENV.items()))
    os.chmod(CLONE / ".env", 0o600)
    # Each engine's own MinIO pair by the operator's command; lake_setup makes their users.
    make("engine-keys")
    ek = _load("engine_keys", CLONE / "scripts/engine-keys.py")
    DOTENV.update({n: v for n, v in ek.values((CLONE / ".env").read_text()).items() if n not in DOTENV})


def lake_setup(release: str, network: str) -> None:
    """lake-init, run as compose runs it: the real docker/lake/init-lake.sh in the MinIO image on the catalog's
    network, the release's storage plan, the root pair and every engine's pair by name. The live layout follows:
    buckets k2-lake and the landing bucket, every warehouse at warehouse/<name> with remote signing off, one
    MinIO user and policy per engine, so Trino signs with its own key."""
    ek = _load("engine_keys", CLONE / "scripts/engine-keys.py")
    names = ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", *(n for e in ek.ENGINES for n in ek.pair_names(e))]
    minio = re.search(r"ghcr\.io/rjdscott/minio@sha256:[0-9a-f]{64}", COMPOSE)[0]  # type: ignore[index]
    done = sh("docker", "run", "--rm", "--network", network, "--memory", "128m", "--cpus", "0.25",
              "-v", f"{CLONE / 'docker/lake'}:/init:ro", "-v", f"{ROOT}:/k2/releases:ro",
              "-e", f"K2_STORAGE_PLAN=/k2/releases/{release}/{model.STORAGE_PLAN}", *(a for n in names for a in ("-e", n)),
              "--entrypoint", "/bin/bash", minio, "/init/init-lake.sh", env=env())
    print(f"rehearsal: lake-init: {done.stdout.strip().splitlines()[-1]}")


def publish(release: str) -> str:
    """The canary published as phase 002's wrapper will: S20's stage, as the builder, the tag commit
    and the publisher, in a tools container on the catalog's network. Stage also creates the platform tables
    (the run record and the holds), which the deploy has already made empty, so they are dropped first."""
    for f in (ROOT / release / "compiled/lake/ddl/trino").glob("*.sql"):  # <warehouse>.<namespace>.<table>.sql
        w, ns, t = f.stem.split(".")
        q("svc.platform", f"DROP TABLE IF EXISTS {w}_build.{ns}.{t}")
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


# R1 was cut with the emitters before the review of S12 made the reader view fail closed (355fa43), so the
# chain crossed from the old view shape to the guarded one. Retired on 2026-11-28: those emitters make no holds
# table, so this checkout's access suite fails 21 rows on such a release, and every live release since
# alpha.3 has the guarded shape. The chain now holds the guarded shape across a rollback and back.


def marker_finding(out: subprocess.CompletedProcess[str], release: str) -> bool:
    """FINDING (2026-11-28): stage builds security master's venue_symbol_held from a one-row placeholder whose
    held_reason is null, so its tag holds no row, nothing can be planted, and the marker's `hidden > 0` fails:
    preflight refuses every release of this checkout's documents (exit 2). Recorded as a FAIL; then the scratch
    tree's marker, which deploy runs from the checkout, accepts an empty tag so the checks after it can run."""
    if "fails the tested-data marker check" not in out.stderr:
        return False
    check(f"release time: the tested-data marker passes, unpatched, for {release}", False,
          " | ".join(x for x in result(release, "marker").get("lines", []) if x.startswith("FAIL"))[:300])
    edit("scripts/verify/tested-data-marker.sh", "hidden > 0 and",
         "(hidden > 0 or trino.ok(pub, f\"SELECT count(*) FROM ({at_tag})\")[0][0] == 0) and", 1)
    return True


def rehearse() -> int:
    scratch_repo(COMPOSE.replace("STANDIN_IMAGE", ALPINE_A).replace("SPARK_IMAGE", SPARK))
    added(False)
    dotenv()
    real_secrets(skip=ADDED_PRINCIPALS)
    t0 = time.monotonic()
    r1 = cut("v0.0.1-rehearsal.1", "R1: the repository's own documents but quality-results")
    check("make release compiles R1 at a scratch tag", (ROOT / r1 / "manifest.json").is_file(), f"{r1} in {time.monotonic() - t0:.0f} s")
    compose("up", "-d", "--wait", "prefect-db", "minio", "lakekeeper", "spark-iceberg", "capture-binance", "redpanda")  # running already, as live
    lake_setup(r1, f"{PROJECT}_k2-catalog")
    capture = started()["capture-binance"]

    # 1. a first deploy that fails, then the first deploy (its preflight runs the release-time suites)
    t0 = time.monotonic()
    out = make("deploy", f"RELEASE={r1}", fault="verify-mismatch", check=False)
    if marker_finding(out, r1):
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
    r2 = cut("v0.0.1-rehearsal.2", "R2: a policy change, rules only")
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
    check("the view's guarded shape after R2, back to R1 by make rollback, and forward: no error, no note, ever",
          guarded and code(back) == 0 and code(again) == 0 and not shape.leaks and not shape.errors and shape.polls > 50,
          f"guarded after R2: {guarded}; {shape.polls} polls, {shape.errors} errors, shapes {shape.shapes}; "
          + "; ".join(x.strip()[:120] for x in (back.stderr + again.stderr).splitlines() if "shape" in x))

    if not FAST:
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
    steps = ("preflight", "images", "plan", "drain", "ddl", "views-narrow", "canary", "swap", "recreate", "rules-loaded",
             "ddl-late", "views-widen", "verify", "done")
    if FAST:  # no R3 to R5, R2 live: two kills, back to R1 and forward, a reader polling
        steps, r3, r5, poller = ("swap", "recreate"), r1, r2, Poller()
        poller.start()
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
    if not FAST:
        make("deploy", f"RELEASE={target}", fault="verify-mismatch,kill:rollback-swap", check=False)
        out = make("rollback", check=False)
        check("a kill inside the automatic rollback, then make rollback, converges", code(out) == 0 and live_release() == other
              and code(make("release-verify", check=False)) == 0)
    poller.stop.set()
    poller.join()
    check("through every kill and rerun the delayed reader never saw a note", not poller.leaks,
          f"{poller.polls} polls, {poller.errors} errors, shapes {poller.shapes}")

    # 7b. a release that adds a product: its builder and publisher are unknown to the live rules until the
    # swap, and its ddl (namespaces) stays behind a rollback, which drops nothing (the live failure of alpha.4-rc.1)
    added(True)
    for pid in ADDED_PRINCIPALS:  # the operator's step before deploying a new product (preflight requires them)
        make("password", f"PRINCIPAL={pid}", "TO_FILE=1")
    r8 = cut("v0.0.1-rehearsal.8", "R8: the quality-results product back")
    was = live_release()
    out = make("deploy", f"RELEASE={r8}", fault="verify-mismatch", check=False)
    check("a release adding a product, verify forced to fail, rolls back with exit 1 (not 3)",
          code(out) == 1 and live_release() == was,
          next((x.strip()[:220] for x in out.stderr.splitlines() if "FAILED" in x and "verify-mismatch" not in x), ""))
    out = make("release-verify", check=False)
    left = [x.strip() for x in out.stderr.splitlines() if x.strip().startswith("LEFT namespace")]
    check("release-verify on the rolled-back stack exits 0, the product's namespaces LEFT, not DRIFT",
          code(out) == 0 and all(any(f"LEFT namespace {n}:" in x for x in left) for n in ADDED_NAMESPACES),
          " | ".join(left)[:300] or next((x.strip()[:220] for x in out.stderr.splitlines() if "DRIFT" in x), ""))
    q("dbt_access_canary", "CREATE TABLE governance_build.access_canary__base.planted (x integer)")
    out = make("release-verify", check=False)
    check("beside them, a table planted in a live namespace is still DRIFT",
          code(out) == 1 and "DRIFT table governance.access_canary__base.planted" in out.stderr)
    q("dbt_access_canary", "DROP TABLE governance_build.access_canary__base.planted")
    out = make("deploy", f"RELEASE={r8}", check=False)
    check("then R8 deploys: its view new in the plan, never read as its publisher before the swap, drift clean",
          code(out) == 0 and f"new in this release: {ADDED_VIEW}" in out.stderr and "catalog drift check clean" in out.stderr,
          next((x.strip()[:220] for x in out.stderr.splitlines() if "FAILED" in x or "new in this release" in x), ""))

    if not FAST:
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
    phase004()
    return summary()


# --- 10. the first deploy of phase 004 -------------------------------------------------------
# What is live (LIVE_TAG, pointers/trino on 2026-11-28): its code and documents, deployed by its own deploy.py.
# Over it, this checkout's code and documents as the operator will deploy them: a second platform table (the
# holds), a Kafka and a public-API connector product, derived products (security master, canonical trades,
# candles) and svc.operator. A fresh lake: the chain above has deployed this checkout's documents already.
LIVE_TAG = "v3.0.0-alpha.5.1"
HELD = "fx-reference.ecb-fx-rates"  # a product of both releases, held as svc.operator across the deploy
SCHED = f"{PROJECT}-airflow-scheduler"
# The scheduler stand-in: the Airflow 3.3.2 CLI calls deploy.py's airflow() makes (dags list, list-runs, pause,
# unpause; never a running task), over the DAGs of the release pointers/airflow names, each created paused as
# the factory's are; its state in /state. It is no scheduler: nothing runs, so a drain never waits.
FAKE_AIRFLOW = '''#!/usr/bin/env python3
import json, pathlib, sys
state = pathlib.Path("/state/dags.json")
s = json.loads(state.read_text()) if state.exists() else {"paused": [], "seen": []}
dags = [e["dagId"] for e in json.loads(pathlib.Path("/rel/pointers/airflow/compiled/airflow/dags.json").read_text())["dags"]]
for d in dags:
    if d not in s["seen"]:
        s["seen"].append(d)
        s["paused"].append(d)
a = sys.argv[1:]
with open("/state/calls.log", "a") as f:
    f.write(" ".join(a) + "\\n")
if a[:2] == ["dags", "list"]:
    print(json.dumps([{"dag_id": d, "is_paused": str(d in s["paused"])} for d in dags]))
elif a[:2] == ["dags", "list-runs"]:
    print("[]")
elif a[:2] in (["dags", "pause"], ["dags", "unpause"]) and len(a) > 2 and a[2] in s["seen"]:
    s["paused"] = sorted(set(s["paused"]) - {a[2]}) + ([a[2]] if a[1] == "pause" else [])
    print(f"Dag: {a[2]}, paused: {a[1] == 'pause'}")
else:
    sys.exit(f"scheduler stand-in: {' '.join(a)}: not a call deploy.py makes")
state.write_text(json.dumps(s))
'''
# scripts/hold.py's own main, its SCHEDULER (the live container's name) pointed at the stand-in: never the live one.
HOLD_PY = ("import sys; sys.path.insert(0, sys.argv[1]); import hold; hold.SCHEDULER = sys.argv[2]; "
           "sys.exit(hold.main(sys.argv[3:]))")


def dag_ids(rid: str) -> dict[str, str]:
    return {e["product"]: e["dagId"] for e in json.loads((ROOT / rid / model.DAGS).read_text())["dags"]}


def namespaces(rid: str) -> set[str]:
    plan = json.loads((ROOT / rid / model.STORAGE_PLAN).read_text())
    return {f"{w['name']}.{ns}" for w in plan["warehouses"] for ns in w["namespaces"]}


def paused() -> set[str]:
    p = BASE / "airflow-state/dags.json"
    return set(json.loads(p.read_text())["paused"]) if p.exists() else set()


def scheduler_up(rid: str) -> None:
    """The stand-in, labelled as the project's airflow-scheduler (deploy finds it so), the releases root at /rel
    so verify's pointer check passes it by, no network."""
    cli, state = BASE / "airflow-cli/airflow", BASE / "airflow-state"
    cli.parent.mkdir()
    state.mkdir()
    cli.write_text(FAKE_AIRFLOW)
    cli.chmod(0o755)
    tools = json.loads((ROOT / rid / "manifest.json").read_text())["images"]["tools"]
    sh("docker", "run", "-d", "--name", SCHED, "--label", f"com.docker.compose.project={PROJECT}",
       "--label", "com.docker.compose.service=airflow-scheduler", "--network", "none", "--memory", "64m", "--cpus", "0.1",
       "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ROOT}:/rel:ro", "-v", f"{state}:/state",
       "-v", f"{cli}:/usr/local/bin/airflow:ro", "--entrypoint", "sleep", tools, "infinity")


def phase004() -> None:
    global ROOT
    compose("--profile", "tools", "down", "-v", "--remove-orphans")  # a fresh lake, as live has none of this chain's
    ROOT = BASE / "releases-004"
    ROOT.mkdir()
    code_at(LIVE_TAG)
    (CLONE / "docker-compose.yml").write_text(COMPOSE.replace("STANDIN_IMAGE", ALPINE_A).replace("SPARK_IMAGE", SPARK))
    base = cut("v0.0.1-rehearsal.10", f"R10: {LIVE_TAG}'s code and documents, what is live")
    # The secrets live has: this checkout's principals are unknown to it (STALE to its principals-status, which
    # its preflight runs), so they go by its own command and come back below by the operator's make password.
    status = make("principals-status", check=False)
    newer = list(dict.fromkeys(re.findall(r"make principal-remove PRINCIPAL=(\S+)", status.stdout + status.stderr)))
    for pid in newer:
        make("principal-remove", f"PRINCIPAL={pid}")
    compose("up", "-d", "--wait", "prefect-db", "minio", "lakekeeper", "spark-iceberg", "capture-binance", "redpanda")
    lake_setup(base, f"{PROJECT}_k2-catalog")
    capture = started()["capture-binance"]
    out = make("deploy", f"RELEASE={base}", check=False)
    check(f"10: R10, {LIVE_TAG} as live has it, deployed by its own deploy.py on a fresh lake", code(out) == 0 and live_release() == base,
          f"exit {code(out)}")
    if code(out) != 0:
        print(out.stderr[-6000:])
        return
    scheduler_up(base)
    try:
        old = dag_ids(base)
        for d in old.values():  # the operator's unpause of each DAG, as live has them
            sh("docker", "exec", SCHED, "airflow", "dags", "unpause", d, "-y")
        code_at(None)
        partners = ps.known()[3]
        for pid in newer:  # the operator's step before deploying new principals (preflight requires them)
            password(pid, partners)
        target = cut("v0.0.1-rehearsal.11", "R11: this checkout's code and documents: the holds, Kafka and public-API "
                     "connector products, derived products, svc.operator")
        new = dag_ids(target)
        added_dags = sorted(set(new.values()) - set(old.values()))
        added_ns = sorted(namespaces(target) - namespaces(base))

        # a. preflight demands svc.operator's password file, the holds' writer the live suite logs in as
        pw = SECRETS / model.password_file("svc.operator")
        pw.rename(pw.with_name("svc.operator.aside"))
        out = make("deploy", f"RELEASE={target}", check=False)
        pw.with_name("svc.operator.aside").rename(pw)
        steps = {r["step"] for r in records() if r["release"] == target}
        check("10a: without svc.operator's password file preflight refuses (exit 2) before any change, naming it and its make line",
              code(out) == 2 and "principals/svc.operator.password (make password PRINCIPAL=svc.operator TO_FILE=1)" in out.stderr
              and steps <= {"preflight"} and live_release() == base,
              next((x.strip()[:220] for x in out.stderr.splitlines() if "svc.operator" in x), ""))

        # b. verify forced to fail: back to R10; the holds table and the new namespaces LEFT, not drift
        out = make("deploy", f"RELEASE={target}", fault="verify-mismatch", check=False)
        if marker_finding(out, target):  # code_at(None) brought this checkout's marker back
            out = make("deploy", f"RELEASE={target}", fault="verify-mismatch", check=False)
        ok = any(r["release"] == target and r["step"] == "preflight" and r["outcome"] == "ok" for r in records())
        check("10b: with every file present preflight passes; verify forced to fail rolls back to R10, exit 1, the drained DAGs unpaused",
              ok and code(out) == 1 and live_release() == base and not paused() & set(old.values()),
              f"exit {code(out)}; " + next((x.strip()[:200] for x in out.stderr.splitlines() if "drain" in x), ""))
        out = make("release-verify", check=False)
        left = [x.strip() for x in out.stderr.splitlines() if x.strip().startswith("LEFT ")]
        want = ["LEFT table governance.run_records.holds:", *(f"LEFT namespace {n}:" for n in added_ns)]
        check("10b: release-verify on R10 exits 0, the holds table and each new namespace a LEFT line, not drift",
              code(out) == 0 and all(any(x.startswith(w) for x in left) for w in want),
              f"{len(left)} LEFT for {len(want)} wanted; missing {[w for w in want if not any(x.startswith(w) for x in left)]}; "
              + next((x.strip()[:220] for x in out.stderr.splitlines() if "DRIFT" in x), ""))

        # e. a kill at ddl-late (R11's rules loaded, its holds table made), then make rollback
        killed = make("deploy", f"RELEASE={target}", fault="kill:ddl-late", check=False)
        back = make("rollback", check=False)
        verified = make("release-verify", check=False)
        check("10e: a kill at ddl-late, then make rollback: exit 0, R10 live and verified, the DAGs deploy paused unpaused",
              "K2_DEPLOY_FAULT=kill:ddl-late" in killed.stderr and code(killed) is None and code(back) == 0
              and live_release() == base and code(verified) == 0 and not paused() & set(old.values()),
              f"rollback {code(back)}, release-verify {code(verified)}, paused {sorted(paused() & set(old.values()))}")

        # c and d. killed at ddl-late again; the operator holds a product meanwhile, by scripts/hold.py as svc.operator
        # under R11's rules; the rerun converges, resume reads the hold and keeps that DAG paused
        killed = make("deploy", f"RELEASE={target}", fault="kill:ddl-late", check=False)
        held = sh("uv", "run", "--no-project", "-q", "--with", "trino==0.340.0", "python3", "-c", HOLD_PY, str(REPO / "scripts"),
                  SCHED, "hold", HELD, "rehearsal: held across a deploy", env=env(), check=False)
        check("10d: scripts/hold.py holds a product as svc.operator while the deploy is down, R11's rules loaded",
              code(killed) is None and held.returncode == 0, (held.stdout + held.stderr).strip()[-220:])
        out = make("deploy", f"RELEASE={target}", check=False)
        check("10c: the same release deployed again: exit 0, the plan names the new DAGs, R11 live and verified",
              code(out) == 0 and live_release() == target and f"never paused or unpaused by deploy: {', '.join(added_dags)}" in out.stderr,
              f"exit {code(out)}; new DAGs {added_dags}")
        check("10c: verify names the new DAGs, created paused, on its idle line",
              f"paused in Airflow and not held, so idle: {', '.join(added_dags)};" in out.stderr,
              next((x.strip()[:220] for x in out.stderr.splitlines() if "idle" in x), ""))
        line = next((x.strip() for x in out.stderr.splitlines() if "kept paused, held:" in x), "")
        check("10d: resume reads the hold under R11 and keeps that DAG paused with its line, unpauses the rest, exit 0",
              code(out) == 0 and old[HELD] in line and paused() & set(old.values()) == {old[HELD]},
              f"{line[:220]}; paused {sorted(paused() & set(old.values()))}")
        out = make("release-verify", check=False)
        check("10c: make release-verify on R11 passes, drift clean", code(out) == 0 and live_release() == target and "catalog drift check clean" in out.stderr,
              next((x.strip()[:220] for x in out.stderr.splitlines() if "DRIFT" in x or "FAILED" in x), ""))

        # f. Prometheus's pointer serves R11's compiled alert rules, and Prometheus loaded them
        served = ROOT / "pointers/prometheus/compiled/prometheus/product-alerts.yml"
        glob = re.findall(r'"(/k2/releases/pointers/prometheus/compiled/[^"]+)"', (REPO / "docker/prometheus/prometheus.yml").read_text())
        groups = prom_group()
        check("10f: the pointer Prometheus globs serves R11's product-alerts.yml, and Prometheus loaded its product groups",
              served.is_file() and served.read_bytes() == (ROOT / target / "compiled/prometheus/product-alerts.yml").read_bytes()
              and any(Path(str(served).replace(str(ROOT), "/k2/releases")).match(g) for g in glob) and "product:" in groups,
              f"glob {glob}; groups {groups[:160]}")
        check("10: the stand-in capture was never restarted by any of it", started()["capture-binance"] == capture)
    finally:
        sh("docker", "rm", "-f", SCHED, check=False)


ADDED_ROWS: list[str] = []  # (the line after them, then its access rows), while the product is out


def added(present: bool) -> None:
    """quality-results out of the scratch tree, its access rows with it, or back as the repository has
    it, the rows where they were (the file has moved since: sections 3 and 6 edit it)."""
    if present:
        shutil.copytree(REPO / ADDED, CLONE / ADDED)
        edit(EXPECT, ADDED_ROWS[0], "".join(ADDED_ROWS[1:]) + ADDED_ROWS[0], 1)
        return
    shutil.rmtree(CLONE / ADDED)
    lines = (CLONE / EXPECT).read_text().splitlines(keepends=True)
    rows = [i for i, x in enumerate(lines) if "quality_results" in x]
    assert rows and rows == list(range(rows[0], rows[-1] + 1)), f"{EXPECT}: the product's rows are not one block"
    ADDED_ROWS[:] = [lines[rows[-1] + 1], *(lines[i] for i in rows)]
    (CLONE / EXPECT).write_text("".join(x for i, x in enumerate(lines) if i not in rows))


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
    if FAST:
        print(f"rehearsal: FAST=1: skipped {FAST_SKIPS}; run the full one before a tag")
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
            sh("docker", "network", "rm", f"{PROJECT}_k2-egress", check=False)  # deploy creates it, so compose down leaves it
            left = sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}").stdout.split()
            print(f"rehearsal: removed {BASE}; containers left: {len(left)}")


if __name__ == "__main__":
    sys.exit(main())
