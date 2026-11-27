#!/usr/bin/env python3
"""Compile, deploy, verify and roll back K2 releases (ADR-058; A1 of docs/development/phase-001-interfaces.md).

    make release TAG=<annotated tag> [IMAGES=<images.json>]   compile the tag into a sealed release
    make deploy RELEASE=<release id or tag> [PLAN=1] [ALLOW_STATEFUL=<service>,...] [ALLOW_RUNNING=1]
    make release-verify                                        ask every part which release it runs
    make rollback                                              back to the previous good release
    make release-status                                        pointers, the good release, the log
    make release-diff RELEASE=<id> [FROM=<id>]                 k2gen diff, from the good release

Procedures: docs/runbooks/deploy-a-release.md and docs/runbooks/roll-back-a-release.md.

A deploy runs these steps in order, each one idempotent and recorded as one line of
<releases root>/deploy-log.jsonl (contracts/schema/deploy-record.schema.json), and stops at the
first failure:

 1 preflight     the release is sealed and every file matches its manifest; the release it moves from
                 is known (the newest good one in the log); the secret files it needs exist (only
                 their sha256 goes into the log); disk and memory floors; no dropped backup entry
                 over data storage still holds; no stateful service changes unless ALLOW_STATEFUL
                 names it; no other deploy holds the lock (taken before anything); then, on a
                 throwaway lake from the release, S20's access rows and the tested-data marker (a
                 sealed release's pass is kept by its id)
 2 images        every image of the manifest present by its digest (registry images pulled)
 3 plan          k2gen's needs from the live release (and any attempt since it) to this one, printed;
                 PLAN=1 stops here
   backup        before a one-shot that migrates a database (a service named <database>-migrate), that
                 database dumped by scripts/backup-lake.sh --dump under ${K2_BACKUP_DIR}/deploy/<release>/,
                 its path and sha256 in the record: a rollback never runs a migration backwards
                 (docs/runbooks/roll-back-a-release.md section 5); a dump that fails refuses the deploy
   drain         when Trino restarts or a DAG's shape changes (drain_need), the affected DAGs that run
                 are paused (their names written to <releases root>/deploy-paused.json first) and their
                 running tasks waited for, up to DRAIN_WAIT_S; still running then refuses the deploy and
                 unpauses them, unless ALLOW_RUNNING=1 (recorded as step allow-running). No scheduler
                 running, or no DAG in it: recorded, and the deploy goes on. Deploy unpauses what it
                 paused once one release runs again: done, rolled back, or a failed ddl; on exit 3 they
                 stay paused, and a rerun or `make rollback` that reaches done unpauses them
 4 ddl           the one-shots the plan names (lake-init reads the target's storage plan), the storage plan's missing namespaces, and each table of
                 needs.ddl compared with the catalog: missing is queued, equal is satisfied, different
                 is refused (never an alter or a drop); Spark's tables created now if Spark runs
 5 views-narrow  before any rule can change, each reader view whose contract columns or SQL change loses
                 every column the new contract drops (k2gen.emit.lake.view_update, against the tag), in
                 the target's shape if the old rules admit it, else the old release's shape; each
                 release's statements come from its own k2gen, in its tools image. A rollback that finds
                 Trino not answering swaps and recreates first, then syncs the views (converge)
 6 canary        a fresh password for this release's canary k2_release_<setSha256[:12]>, its line
                 written into trino/password.db in place under the file's lock
 7 swap          every pointer moved to the release from inside the tools container (spike c);
                 Prometheus, when it mounts the root, told to reread its rules (SIGHUP)
 8 recreate      only the services the plan names, one at a time with --no-deps, each waited healthy
 9 rules-loaded  the release's canary runs SELECT 1 and the previous one is refused: its rules are
                 Trino's; then one more refresh period, for the groups' own timer
   ddl-late      the queued tables whose engine had to start or load the rules first (Trino's always)
   views-widen   each view up to the new contract's columns the tag holds
   verify        every pointer (in the VM and in each container that mounts the root), the files under
                 it, each image digest and health check, every default-profile service running, the
                 drift check in tools, the live access suite (S20) and the catalog fence's probes
                 (scripts/verify/catalog-fence.sh); `make release-verify` adds S13's identity.sh
10 done          the other canaries' lines removed; the release is the good one

A failure at step 5 or later rolls back on its own: the same steps toward the previous release from
this one (recorded as rollback-<step>), then exit 1; exit 3 if that fails too. Exit 2 is a refusal
before anything changed. K2_DEPLOY_FAULT, comma-separated, is how the rehearsal breaks a deploy on
purpose: `verify-mismatch` fails step 9, `kill:<step>` SIGKILLs this process right after that step's
work and before its record.

Run through uv in platform/k2gen (mk/release.mk): it imports k2gen (the compiler's own needs and view
rule) and bcrypt, both pinned there, and reuses scripts/principal-secret.py's in-place, locked writes.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
import yaml
from k2gen import diff as k2diff
from k2gen import model
from k2gen import release as k2release
from k2gen.emit.trino_etc import CATALOG_URI, S3_ENDPOINT
from k2gen.model import Release, View

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


k2tools = _load("k2tools", HERE.parent / "docker" / "tools" / "k2tools.py")  # the Trino client, and its catalog types
ps = _load("principal_secret", HERE / "principal-secret.py")  # locked(), read(), write(): in place, never renamed
drift_lib = _load("catalog_drift", HERE / "verify" / "catalog_drift.py")  # its tokens(): one SQL comparison

POINTERS = ("trino", "airflow", "spark", "prometheus", "k2-api")  # A1
REFRESH_S = 5  # Trino rereads rules, groups and the password file this often (compiled etc, S11 and S13)
RULES_WAIT_S = 4 * REFRESH_S  # how long a swap may take to reach Trino: 0.1 to 4.7 s measured (spike c)
HEALTH_WAIT_S = 300  # a recreated service's start, Trino's included
API_TRIES, API_WAIT_S = 5, 2.0  # k2-api reads its pointer at every request; the tries cover a slow first answer
DISK_FLOOR_GIB = 2.0  # free at the releases root; a release is a few MiB, a pulled image is not
MEM_FLOOR_GIB = 2.0  # MemAvailable on the host: a recreate starts a container before the old one's memory returns
DONE = ("done", "rollback-done")
UNCHANGING = ("preflight", "images", "plan", "backup", "drain", "allow-running")  # steps that move no release
# The drain (ADR-058). The ADR names no bound; its revisit trigger is a deploy over 10 minutes, so the
# drain may take half of that.
DRAIN_WAIT_S = 300
DRAIN_POLL_S = 10  # a poll starts one Airflow CLI (a few seconds of CPU) per drained DAG, two with a run
DRAIN_STATE = "deploy-paused.json"  # at the releases root: the DAGs deploy paused, written before the first pause
SCHEDULER = "airflow-scheduler"  # the compose service deploy asks Airflow through
BUSY = ("queued", "running", "restarting")  # a task instance's states a pause does not stop
PASSWORD_DB = "trino/password.db"
CA = "tls/ca.pem"
LAKE_INIT = f"{model.PLATFORM_DIR}/docker/lake/init-lake.sh"  # lake-init runs the deployed release's own (review R6)
CANARY_SQL = "SELECT 1"  # the canary's one statement (S12's rules)
# Spark's `lake` catalog, as k2gen's Trino catalogs and docker/lake/spark_conf.py name it; the keys come
# from the container's AWS_* environment, S3FileIO's default chain (A4), never from this file.
SPARK_LAKE = {
    "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
    "spark.sql.catalog.lake.type": "rest",
    "spark.sql.catalog.lake.uri": CATALOG_URI,
    "spark.sql.catalog.lake.warehouse": model.LAKE_WAREHOUSE,
    "spark.sql.catalog.lake.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
    "spark.sql.catalog.lake.s3.endpoint": S3_ENDPOINT,
    "spark.sql.catalog.lake.s3.path-style-access": "true",
}
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_ROLLBACK_FAILED = 0, 1, 2, 3


class Refused(Exception):
    """Nothing has changed: exit 2."""


class Failed(Exception):
    """Something may have changed: roll back when the step is views-narrow or later."""


@dataclass
class Ctx:
    root: Path  # the releases root on the host
    secrets: Path
    home: Path  # the compose project directory: the checkout, with its .env
    project: str
    trino: str  # https://<name>:<port>, by name: Trino picks its certificate by the name asked for
    operator: str
    faults: set[str]
    tmp: Path
    allow_stateful: set[str] = field(default_factory=set)
    allow_running: bool = False  # ALLOW_RUNNING=1: past the drain's bound with tasks still running
    say: Any = print


def ctx_from_env(tmp: Path) -> Ctx:
    env = os.environ
    root = Path(env.get("K2_RELEASES") or Path.home() / "k2-releases").absolute()
    home = Path(env.get("K2_HOME") or HERE.parent).absolute()
    # Unset: the project `make up` gives the checkout, its directory's name as compose spells it
    # (ponytail: lowercased, [a-z0-9_-] kept). Set but empty: refused in _main (review R1 M1).
    project = env.get("K2_COMPOSE_PROJECT", re.sub(r"[^a-z0-9_-]", "", home.name.lower()))
    return Ctx(root=root, secrets=Path(env.get(model.SECRETS_ENV) or Path.home() / "k2-secrets").absolute(),
               home=home, project=project,
               trino=f"https://{env.get('K2_TRINO_HOST', 'localhost')}:{env.get('K2_TRINO_PORT', '8443')}",
               operator=getpass.getuser(), faults={f for f in env.get("K2_DEPLOY_FAULT", "").split(",") if f}, tmp=tmp,
               allow_running=env.get("ALLOW_RUNNING") == "1")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run(*argv: str, check: bool = True, env: dict[str, str] | None = None, input: str | None = None) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(list(argv), capture_output=True, text=True, env=env, input=input)
    if check and done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-8:])
        raise Failed(f"{' '.join(argv[:6])}{' ...' if len(argv) > 6 else ''}: exit {done.returncode}: {tail}")
    return done


# --- the lock and the log ----------------------------------------------------------------


@contextmanager
def lock(root: Path) -> Iterator[None]:
    """One deploy or compile per releases root: an exclusive flock, refused at once when held.
    The kernel drops it with the process, so a killed deploy never leaves it behind."""
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / ".deploy.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(fd, 64, 0).decode(errors="replace").strip()
            raise Refused(f"another deploy holds {root / '.deploy.lock'} (pid {holder or 'unknown'}); nothing done") from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield
    finally:
        os.close(fd)


def secret_sums(ctx: Ctx, services: set[str]) -> dict[str, dict[str, Any]]:
    """Each secret file the release's services mount, and the canary passwords, by presence, mode and
    size: no byte of a secret, nor a digest of one, goes into the log."""
    found: dict[str, dict[str, Any]] = {}
    for f in model.SECRET_FILES:
        if not (set(f.services) & services or f.path.startswith("canary/")):
            continue
        pattern = re.sub(r"<[a-z]+>", "*", f.path)
        for p in sorted(ctx.secrets.glob(pattern)):
            if p.is_file():
                st = p.stat()
                found[p.relative_to(ctx.secrets).as_posix()] = {"mode": f"{st.st_mode & 0o777:04o}", "size": st.st_size}
    return found


def record(ctx: Ctx, target: Release, frm: str | None, step: str, outcome: str, services: set[str],
           reason: str | None = None, **extra: Any) -> None:
    line = {"at": now(), "release": target.id, "from": frm, "step": step, "outcome": outcome,
            "operator": ctx.operator, "secrets": secret_sums(ctx, services), **extra}
    if reason:
        line["reason"] = reason[:2000]
    if errors := model.schema_errors("deploy-record", line):
        raise Failed(f"a deploy record fails its schema: {errors}")
    with open(ctx.root / model.DEPLOY_LOG, "a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def history(root: Path) -> list[dict[str, Any]]:
    path = root / model.DEPLOY_LOG
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def after_good(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records after the newest good release's done."""
    last = max((i for i, r in enumerate(records) if r["step"] in DONE and r["outcome"] == "ok"), default=-1)
    return records[last + 1:]


def standing(records: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(the good releases, oldest first: each deploy or rollback that reached done; every release an
    attempt since the newest good one named, which may have changed the live state)."""
    goods = [r["release"] for r in records if r["step"] in DONE and r["outcome"] == "ok"]
    since: list[str] = []
    for r in after_good(records):
        if r["step"] in UNCHANGING:  # a refusal or PLAN=1 moved nothing
            continue
        for rid in (r["release"], r["from"]):
            if rid and rid not in since:
                since.append(rid)
    return goods, since


def open_release(ctx: Ctx, rid: str) -> Release:
    """A sealed release under the root, every file checked against its manifest (k2gen.release.read)."""
    path = ctx.root / rid
    if not re.fullmatch(r"v[0-9][A-Za-z0-9.-]*-[0-9a-f]{12}", rid) or not path.is_dir():
        raise Refused(f"{rid}: no release of that id under {ctx.root}")
    try:
        rel = k2release.read(path)
    except ValueError as e:
        raise Refused(f"{rid}: {e}") from None
    if writable := [p for p in (path, *path.rglob("*")) if p.stat().st_mode & 0o222]:
        raise Refused(f"{rid}: not sealed: {writable[0]} is writable")
    return rel


# --- compose and docker ------------------------------------------------------------------


def services(rel: Release) -> dict[str, dict[str, Any]]:
    """The release's compose services as compose reads them (anchors and merge keys resolved)."""
    doc = yaml.safe_load((rel.root / model.RELEASE_COMPOSE).read_bytes()) or {}
    return dict(doc.get("services") or {})


def compose(ctx: Ctx, rel: Release, *args: str) -> list[str]:
    """`docker compose` on the release's own compose file, with every image pinned to the manifest's
    digest by an override written here: a rollback restores images as well as configuration."""
    override = ctx.tmp / f"{rel.id}.images.json"
    if not override.exists():
        pinned = {s: {"image": ref} for s, ref in rel.manifest.images.items() if s in services(rel)}
        override.write_text(json.dumps({"services": pinned}, indent=2))
    return ["docker", "compose", "-p", ctx.project, "--project-directory", str(ctx.home),
            "-f", str(rel.root / model.RELEASE_COMPOSE), "-f", str(override), *args]


def rendered(ctx: Ctx, rel: Release) -> dict[str, Any]:
    """The release's compose as compose renders it, interpolated from this environment and the checkout's
    .env, every declared network and volume included (--all-resources), the default set's services."""
    doc: dict[str, Any] = json.loads(run(*compose(ctx, rel, "--all-resources", "config", "--format", "json"),
                                         env=compose_env(ctx)).stdout or "{}")
    return doc


def compose_env(ctx: Ctx) -> dict[str, str]:
    return {**os.environ, "K2_RELEASES": str(ctx.root), model.SECRETS_ENV: str(ctx.secrets), "K2_COMPOSE_PROJECT": ctx.project}


def containers(ctx: Ctx) -> list[dict[str, Any]]:
    """Every container of the project but `compose run` ones, newest first, as docker inspect gives them."""
    ids = run("docker", "ps", "-aq", "--no-trunc", "--filter", f"label=com.docker.compose.project={ctx.project}").stdout.split()
    found = json.loads(run("docker", "inspect", *ids).stdout) if ids else []
    found = [c for c in found if (c["Config"].get("Labels") or {}).get("com.docker.compose.oneoff") != "True"]
    return sorted(found, key=lambda c: c["Created"], reverse=True)


def container(ctx: Ctx, service: str) -> dict[str, Any] | None:
    return next((c for c in containers(ctx) if c["Config"]["Labels"].get("com.docker.compose.service") == service), None)


def image_id(ref: str) -> str | None:
    done = run("docker", "image", "inspect", "--format", "{{.Id}}", ref, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def local_build(ref: str) -> bool:
    return "@sha256:" not in ref


def wait_service(ctx: Ctx, service: str, oneshot: bool) -> float:
    """Seconds until the service's container is healthy (or running, without a health check), or,
    for a one-shot, has exited 0."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < HEALTH_WAIT_S:
        c = container(ctx, service)
        if c:
            st = c["State"]
            if oneshot and st["Status"] == "exited":
                if st["ExitCode"] != 0:
                    raise Failed(f"{service}: the one-shot exited {st['ExitCode']}")
                return time.monotonic() - t0
            health = (st.get("Health") or {}).get("Status")
            if not oneshot and st["Running"] and not st["Restarting"] and health in (None, "healthy"):
                return time.monotonic() - t0
            if not oneshot and st["Status"] == "exited":
                raise Failed(f"{service}: exited {st['ExitCode']} instead of starting")
        time.sleep(1)
    raise Failed(f"{service}: not {'done' if oneshot else 'healthy'} after {HEALTH_WAIT_S} s")


def tools(ctx: Ctx, rel: Release, *argv: str, mounts: tuple[str, ...] = (), entry: str = "k2tools") -> str:
    """One command (k2tools by default) in a fresh tools one-shot of the release, as the host user, on the
    catalog's network."""
    cmd = compose(ctx, rel, "run", "--rm", "--no-deps", "--pull", "never", "--user", f"{os.getuid()}:{os.getgid()}",
                  *(a for m in mounts for a in ("-v", m)), "tools", entry, *argv)
    return run(*cmd, env=compose_env(ctx)).stdout


# --- Trino -------------------------------------------------------------------------------


def password_of(ctx: Ctx, user: str) -> str:
    path = ctx.secrets / (model.canary_password_file(user.removeprefix("k2_release_")) if user.startswith("k2_release_")
                          else model.password_file(user))
    return path.read_text().strip()


def sql(ctx: Ctx, user: str, statement: str) -> dict[str, Any]:
    result: dict[str, Any] = k2tools.sql(ctx.trino, user, password_of(ctx, user), str(ctx.secrets / CA), statement)
    return result


LAST_REFUSAL: dict[str, str] = {}  # user -> why admitted() last said no, for the failure message


def admitted(ctx: Ctx, user: str) -> bool | None:
    """True when the canary runs its statement, False when the rules refuse it, None when it cannot
    log in (its password line not yet read, or gone) or Trino does not answer."""
    try:
        sql(ctx, user, CANARY_SQL)
        return True
    except k2tools.TrinoError as e:
        LAST_REFUSAL[user] = str(e)[:200]
        return None if e.name == "AUTHENTICATION_FAILED" else False
    except (k2tools.Unreachable, FileNotFoundError) as e:
        LAST_REFUSAL[user] = f"{type(e).__name__}: {str(e)[:200]}"
        return None


# --- the plan ----------------------------------------------------------------------------


@dataclass
class Plan:
    target: Release
    sources: list[Release | None]  # the live release first, then any attempt since it
    recreate: list[str]
    oneshots: list[str]
    trino_restart: bool
    ddl: list[str]
    views: list[View]
    skipped: list[str]
    create: list[tuple[str, str, str]] = field(default_factory=list)  # (table, engine, DDL) still to create
    remove: list[str] = field(default_factory=list)  # running, and moved out of the default set by the target


def files_of(rel: Release) -> dict[str, bytes]:
    return {p: (rel.root / p).read_bytes() for p in rel.manifest.files}


def views_of(rel: Release | None) -> dict[str, View]:
    if rel is None or model.VIEWS not in rel.manifest.files:
        return {}
    return {v["relation"]: View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
            for v in json.loads((rel.root / model.VIEWS).read_bytes())["views"]}


def make_plan(ctx: Ctx, target: Release, sources: list[Release | None]) -> Plan:
    """The union of k2gen's needs from each source to the target: compose is idempotent, so a
    service another source already moved is left as it is. Never a stateful service the target
    changes unless --allow-stateful names it: that is refused here, before anything moves."""
    files, svc = files_of(target), services(target)
    recreate: set[str] = set()
    ddl: set[str] = set()
    trino_restart = False
    views: dict[str, View] = {}
    target_views = views_of(target)
    for s in sources:
        n = k2release.needs(s, files, target.manifest.images)
        recreate |= set(n.recreate)
        trino_restart |= n.trino_restart
        ddl |= set(n.ddl)
        held = [h for h in k2release.changed_services((s.root / model.RELEASE_COMPOSE).read_bytes() if s else None,
                                                       files[model.RELEASE_COMPOSE], s.manifest.images if s else {},
                                                       target.manifest.images) if not k2release.recreatable(h)]
        for h in held:
            if h in ctx.allow_stateful:
                recreate.add(h)
            elif s is None:  # a first deploy: the service runs already, from the checkout's compose
                c, want = container(ctx, h), image_id(target.manifest.images.get(h, ""))
                if c and want and c["Image"] != want:
                    raise Refused(f"{h} runs image {c['Image'][:19]}, the release names {target.manifest.images[h]}; "
                                  f"deploy never recreates it unless ALLOW_STATEFUL={h}")
            else:
                raise Refused(f"the release changes {h}, which deploy never recreates (A1); recreate it by hand "
                              f"after review, or deploy with ALLOW_STATEFUL={h}")
        before = views_of(s)
        views |= {r: v for r, v in target_views.items() if r not in before or before[r].columns != v.columns
                  or s.manifest.files.get(model.view_sql_path(r)) != target.manifest.files.get(model.view_sql_path(r))}
    running = {c["Config"]["Labels"].get("com.docker.compose.service") for c in containers(ctx) if c["State"]["Running"]}
    # In a source's default set, behind a profile in the target (k2-api, rolling back to a release where it
    # was a profiled stub): the running container is the source's, which the target never starts, so it
    # is removed at recreate. A service profiled in both that runs was started on purpose (hot-gold):
    # recreated in place, as before.
    defaults = {s for src in sources if src for s, spec in services(src).items() if not spec.get("profiles")}
    order, oneshots, skipped, remove = [], [], [], []
    retired = {model.fence(x.id.split(".")[1]) for x in target.manifest.products if x.status == "retired"}
    for r in sorted(r for r in views if r.split(".")[1] in retired):
        skipped.append(f"view {r}: its product is retired, so its rules are gone and no reader reaches it; "
                       "deploy does not sync it as a publisher the rules no longer name (drop it: make drop)")
        del views[r]
    for name in sorted(recreate):
        spec = svc.get(name)
        if spec is None:
            skipped.append(f"{name}: not in the release's compose")
        elif name == "tools":
            skipped.append("tools: a one-shot this deploy runs itself, from the release")
        elif spec.get("profiles") and name in running and name in defaults:
            remove.append(name)
        elif spec.get("profiles") and name not in running:
            skipped.append(f"{name}: profile {','.join(spec['profiles'])}, not running; its next start uses the release")
        elif name == "lake-init" and LAKE_INIT not in target.manifest.files:
            # Its compose runs the checkout's script, which a newer checkout's lake-init demands ten more names
            # for than that compose passes. Its warehouses exist (each deploy since alpha.3 made them) and it
            # never had scoped users, whose grants a newer release made and an older one never reads.
            skipped.append(f"lake-init: {target.id} carries no {LAKE_INIT} (a release before review R6), and its "
                           "compose would run this checkout's script; not run, its warehouses already exist")
        elif str(spec.get("restart", "no")) == "no":
            oneshots.append(name)
        else:
            order.append(name)
    if trino_restart and "trino" in svc and "trino" not in order:
        order.append("trino")
    # A stateful service the operator allowed first (Trino's catalogs reach Lakekeeper on its new network
    # at a cutover), then the rules' reader, which verify waits on, then the rest.
    order.sort(key=lambda s: (s not in ctx.allow_stateful, s != "trino"))
    return Plan(target, sources, order, oneshots, trino_restart, sorted(ddl),
                [views[r] for r in sorted(views)], skipped, remove=remove)


def show(ctx: Ctx, p: Plan, frm: str | None) -> None:
    say = ctx.say
    say(f"deploy: plan {frm or '(first deploy)'} -> {p.target.id}"
        + (f" (also undoing attempts {', '.join(s.id for s in p.sources[1:] if s)})" if len(p.sources) > 1 else ""))
    say(f"  recreate, one at a time: {', '.join(p.recreate) or 'nothing'}")
    say(f"  one-shots run: {', '.join(p.oneshots) or 'none'}")
    if p.remove:
        say(f"  stopped and removed, out of the release's default set: {', '.join(p.remove)}")
    say(f"  Trino restart: {'yes' if p.trino_restart else 'no'}; DDL: {', '.join(p.ddl) or 'none'}")
    say(f"  views to re-sync: {', '.join(v.relation for v in p.views) or 'none'}")
    for line in p.skipped:
        say(f"  not recreated: {line}")


# --- the drain (ADR-058) -----------------------------------------------------------------


def dags_json(rel: Release) -> bytes | None:
    return (rel.root / model.DAGS).read_bytes() if model.DAGS in rel.manifest.files else None


def drain_need(p: Plan) -> tuple[set[str] | None, str]:
    """(the DAGs to drain, None for every DAG Airflow has; why). Every one when Trino restarts (each
    product DAG's build runs on it), when the DAG code moves (the factory decides every DAG's tasks) or
    from no release (its DAGs are unknown); else each DAG of a source whose task list the target changes
    or drops, as k2gen.release.needs compares them. A DAG new in the target has no run to drain."""
    if "trino" in p.recreate:  # trinoRestart, or a changed Trino service: either restarts it
        return None, "Trino restarts"
    new, some = k2release._dag_tasks(dags_json(p.target)), set()
    for s in p.sources:
        if s is None:
            return None, "no release before this one, so its DAGs are unknown"
        a, b = s.manifest.files, p.target.manifest.files
        if any(f != model.DAGS and model.need_of(f) == "dagShapeChange" and a.get(f) != b.get(f) for f in a.keys() | b.keys()):
            return None, f"the DAG code moved since {s.id}"
        some |= {d for d, t in k2release._dag_tasks(dags_json(s)).items() if new.get(d) != t}
    return some, (f"the task list of {', '.join(sorted(some))} changes or goes" if some
                  else "no Trino restart and no DAG's task list changes")


def airflow(ctx: Ctx, cid: str, *argv: str) -> Any:
    """One Airflow CLI command in the scheduler's container; with `-o json` last, its rows. Every argv
    deploy runs is in drain, running_tasks and resume, read from apache-airflow-core 3.3.2's
    cli_config.py: `dags list -o json`, `dags list-runs <dag> --state running -o json`, `dags
    states-for-dag-run <dag> <run> -o json`, `dags pause <dag>`, `dags unpause <dag>`. Piped, -o json
    prints one line, each value a string ("True", not true: AirflowConsole._normalize_data)."""
    out = run("docker", "exec", cid, "airflow", *argv).stdout
    if argv[-2:] != ("-o", "json"):
        return None
    rows = [x for x in out.splitlines() if x.startswith("[")]
    if not rows:
        raise Failed(f"airflow {' '.join(argv)}: no JSON in its output: {out.strip()[-200:]}")
    return json.loads(rows[-1])


def scheduler(ctx: Ctx) -> str | None:
    c = container(ctx, SCHEDULER)
    return c["Id"] if c and c["State"]["Running"] else None


def paused_by_deploy(ctx: Ctx) -> set[str]:
    path = ctx.root / DRAIN_STATE
    return set(json.loads(path.read_text())["paused"]) if path.exists() else set()


def write_paused(ctx: Ctx, dags: set[str]) -> None:
    """Written whole and fsynced before it replaces the old one: a kill mid-write leaves either."""
    tmp = ctx.root / f".{DRAIN_STATE}.tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps({"paused": sorted(dags), "at": now()}) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ctx.root / DRAIN_STATE)


def running_tasks(ctx: Ctx, cid: str, dags: list[str]) -> list[str]:
    """Each task instance of the DAGs' running runs in a state a pause does not stop. A paused DAG's run
    stays `running` with its later tasks unscheduled, so the tasks are asked, not the runs."""
    out = []
    for d in dags:
        for r in airflow(ctx, cid, "dags", "list-runs", d, "--state", "running", "-o", "json"):
            out += [f"{d}.{t['task_id']} (run {r['run_id']}, {t['state']})"
                    for t in airflow(ctx, cid, "dags", "states-for-dag-run", d, r["run_id"], "-o", "json") if t["state"] in BUSY]
    return out


def resume(ctx: Ctx) -> list[str]:
    """Exactly the DAGs a deploy paused unpaused, then forgotten; never one an operator paused."""
    held = sorted(paused_by_deploy(ctx))
    if not held:
        return []
    cid = scheduler(ctx)
    if cid is None:
        raise Failed(f"{SCHEDULER} is not running, so the DAGs deploy paused stay paused: {', '.join(held)} "
                     f"({ctx.root / DRAIN_STATE}); the next deploy or rollback that reaches done unpauses them")
    for d in held:
        airflow(ctx, cid, "dags", "unpause", d)
    (ctx.root / DRAIN_STATE).unlink()
    return [f"unpaused {', '.join(held)}: the DAGs deploy paused"]


def drain(ctx: Ctx, p: Plan, frm: str | None) -> list[str]:
    """Before the DDL and the swap: the DAGs drain_need names, as Airflow has them, paused where they run
    (the set written first, so a rerun after a kill unpauses them), then their running tasks waited for.
    Still running at DRAIN_WAIT_S: refused, and what deploy paused unpaused; ALLOW_RUNNING=1 goes on."""
    wanted, why = drain_need(p)
    if wanted == set():
        return [f"no drain: {why}"]
    cid = scheduler(ctx)
    if cid is None:
        return [f"no scheduler: {SCHEDULER} is not running, so no task to drain ({why})"]
    listed = {d["dag_id"]: str(d["is_paused"]) == "True" for d in airflow(ctx, cid, "dags", "list", "-o", "json")}
    dags = sorted(d for d in listed if wanted is None or d in wanted)
    if not dags:
        return [f"no DAGs: Airflow has {'none' if not listed else 'none of them'} to drain ({why})"]
    pause = [d for d in dags if not listed[d]]  # an operator's pause is left as it is, and never lifted
    write_paused(ctx, paused_by_deploy(ctx) | set(pause))  # a dead deploy's set kept: its rerun unpauses both
    t0 = time.monotonic()
    try:
        for d in pause:
            airflow(ctx, cid, "dags", "pause", d)
        while (busy := running_tasks(ctx, cid, dags)) and time.monotonic() - t0 < DRAIN_WAIT_S:
            time.sleep(DRAIN_POLL_S)
    except Exception:
        resume(ctx)
        raise
    lines = [f"drained {', '.join(dags)} ({why}) in {time.monotonic() - t0:.0f} s; paused {', '.join(pause) or 'none'}"]
    if busy and not ctx.allow_running:
        lines += resume(ctx)
        raise Refused(f"drain: still running after {DRAIN_WAIT_S} s: {'; '.join(busy)}; {'; '.join(lines)}. Nothing "
                      "changed: deploy again once they finish, or with ALLOW_RUNNING=1 to go on under them")
    if busy:
        record(ctx, p.target, frm, "allow-running", "ok", set(services(p.target)))
        lines.append(f"ALLOW_RUNNING=1: going on with {'; '.join(busy)} still running (recorded as step allow-running)")
    return lines


# --- step 1, preflight -------------------------------------------------------------------


# A bind source under the secrets directory, as compose files spell it: ${K2_SECRETS} or with a default.
SECRET_SOURCE = re.compile(r"\$\{K2_SECRETS(?::?-(?:\$\{[^}]*\}|[^}])*)?\}/([^:]+)")


def secret_binds(rel: Release) -> set[str]:
    """Each path under ${K2_SECRETS} a bind of the release's compose mounts, for its default set and
    tools (deploy runs it), as compose reads the file; a source naming another variable is per run."""
    out = set()
    for name, spec in services(rel).items():
        if spec.get("profiles") and name != "tools":
            continue
        for v in spec.get("volumes") or []:
            m = SECRET_SOURCE.match(str(v.get("source", "") if isinstance(v, dict) else v))
            if m and "$" not in m[1]:
                out.add(m[1])
    return out


def run_principals(rel: Release) -> set[str]:
    """The principals whose password files the one-shot operator mounts at a build: each live
    product's builder and publisher, as its pipeline.json names them (factory.PinnedOneShot), and svc.runs."""
    retired = {x.id for x in rel.manifest.products if x.status == "retired"}
    out = set()
    for path in rel.manifest.files:
        product = path.removeprefix("compiled/products/").removesuffix("/pipeline.json")
        if path == model.pipeline_path(product) and product not in retired:
            if build := json.loads((rel.root / path).read_bytes()).get("build"):
                out |= {build["writer"], build["publisher"], "svc.runs"}
    return out


def make_for(path: str) -> str:
    """The command that writes a secret file, from model.SECRET_FILES' writer."""
    if path.startswith("principals/"):
        return f"make password PRINCIPAL={path.removeprefix('principals/').removesuffix('.password')} TO_FILE=1"
    f = next((f for f in model.SECRET_FILES if re.fullmatch(re.sub(r"<[a-z]+>", "[^/]+", f.path), path)), None)
    m = re.search(r"`(make [^`]+)`", f.writer) if f else None
    return m[1] if m else "docs/runbooks/create-or-rotate-a-principal.md"


def required_secrets(ctx: Ctx, p: Plan) -> list[str]:
    """Every secret file the target's services mount, each directory bind by the files
    model.SECRET_FILES puts in it, the build principals' password files, and those this deploy's
    own SQL logs in with: the missing ones, before anything changes."""
    need, dirs = {CA}, set()
    for b in secret_binds(p.target):
        inside = {f.path for f in model.SECRET_FILES if f.path.startswith(f"{b}/") and "<" not in f.path}
        if inside:  # a directory bind (trino/, api/, alertmanager/): the files read through it
            need |= inside
        elif b.startswith("principals/") or b in {f.path for f in model.SECRET_FILES}:
            need.add(b)
        else:  # a bind model.SECRET_FILES does not describe: it must at least exist
            dirs.add(b)
    need |= {model.password_file(x) for x in run_principals(p.target)}
    if any(model.ddl_path("trino", *t.split(".")) in p.target.manifest.files for t in p.ddl):
        need.add(model.password_file("svc.platform"))
    need |= {model.password_file(v.owner) for v in p.views}
    return sorted([n for n in need if not (ctx.secrets / n).is_file()] + [d for d in dirs if not (ctx.secrets / d).exists()])


def floors(ctx: Ctx) -> None:
    free = shutil.disk_usage(ctx.root).free / 2**30
    if free < DISK_FLOOR_GIB:
        raise Refused(f"{free:.1f} GiB free at {ctx.root}, under the {DISK_FLOOR_GIB} GiB floor")
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        avail = int(re.search(r"MemAvailable:\s+(\d+)", meminfo.read_text())[1]) / 2**20  # type: ignore[index]
        if avail < MEM_FLOOR_GIB:
            raise Refused(f"{avail:.1f} GiB of memory available, under the {MEM_FLOOR_GIB} GiB floor")


def backup_entries(rel: Release | None) -> set[tuple[str, ...]]:
    if rel is None or model.BACKUP_SET not in rel.manifest.files:
        return set()
    out = set()
    for e in json.loads((rel.root / model.BACKUP_SET).read_bytes())["entries"]:
        if e["kind"] == "landing":
            out.add(("landing", e["bucket"], e["prefix"]))
        elif e["kind"] == "table" and e["mode"] == "restore":
            out.add(("table", e["table"]))
    return out


def dropped_storage(ctx: Ctx, old: Release | None, new: Release) -> list[str]:
    """Each restore or landing entry of the old backup set the new one drops while storage still
    holds it (review M4): a table still in the catalog, a landing prefix with an object."""
    held = []
    for entry in sorted(backup_entries(old) - backup_entries(new)):
        if entry[0] == "table":
            if json.loads(tools(ctx, new, "table", CATALOG_URI, *entry[1].split("."))) is not None:
                held.append(f"table {entry[1]}")
        else:
            c = container(ctx, "minio")
            if c is None:
                held.append(f"landing s3://{entry[1]}/{entry[2]} (MinIO not running, so not proven empty)")
                continue
            # Fails closed: only an empty listing, or a bucket that does not exist, proves it empty; any
            # other answer from mc (a refused key, no answer) counts as data still held.
            ls = run("docker", "exec", c["Id"], "sh", "-c",
                     'out=$(MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc ls --recursive "l/$1" 2>&1)\n'
                     'rc=$?\ncase "$rc:$out" in 0:) exit 0 ;; 0:*) echo held ;; *"The specified bucket does not exist"*) exit 0 ;; *) exit 3 ;; esac',
                     "sh", f"{entry[1]}/{entry[2]}", check=False)
            if ls.returncode or ls.stdout.strip():
                held.append(f"landing s3://{entry[1]}/{entry[2]}" + ("" if ls.stdout.strip() else " (MinIO did not list it, so not proven empty)"))
    return held


def secrets_health(ctx: Ctx) -> None:
    """Before anything touches the stack: the .env file's mode (scripts/check-env.py --mode-only) and every
    password.db line well formed and known (`make principals-status`, exit 1 on a malformed or stale one)."""
    env = compose_env(ctx)
    mode = run(sys.executable, str(ctx.home / "scripts/check-env.py"), "--mode-only", "--env-file", str(ctx.home / ".env"),
               env=env, check=False)
    if mode.returncode:
        raise Refused(f"scripts/check-env.py --mode-only: {tail(mode, 4)}")
    status = make_target(ctx, "principals-status", env)
    if status.returncode:
        raise Refused(f"make principals-status exit {status.returncode}, a malformed or stale password.db line: {tail(status, 6)}")


def preflight(ctx: Ctx, p: Plan) -> None:
    floors(ctx)
    if p.sources[0] and (gone := missing_images(p.sources[0])):  # the rollback target (review R1 H3)
        raise Refused(f"the rollback target {p.sources[0].id} names images this host no longer has: "
                      f"{', '.join(f'{s} {r}' for s, r in gone)}; {IMAGE_FIX}")
    secrets_health(ctx)
    if missing := required_secrets(ctx, p):
        raise Refused(f"secret files missing under {ctx.secrets}: "
                      + "; ".join(f"{n} ({make_for(n)})" for n in missing) + "; nothing changed")
    if held := dropped_storage(ctx, p.sources[0], p.target):
        raise Refused(f"the release's backup set drops {'; '.join(held)}, which storage still holds; "
                      "copy and drop it first with `make drop TARGET=...` (docs/runbooks/drop-and-rebuild.md)")
    run(*compose(ctx, p.target, "config", "-q"), env=compose_env(ctx))
    for line in release_suite(ctx, p.target):
        ctx.say(f"  {line}")


def missing_images(rel: Release) -> list[tuple[str, str]]:
    """(service, ref) of each image of the release this host lacks: a local build by its id, a registry
    image by its digest."""
    out = []
    for service, ref in sorted(rel.manifest.images.items()):
        have = image_id(ref)
        if not have or (local_build(ref) and not have.startswith(f"sha256:{ref.rsplit(':', 1)[1]}")):
            out.append((service, ref))
    return out


IMAGE_FIX = ("a registry image comes back with `docker pull <ref>`; a local build cannot be rebuilt to the same id, so "
             "cut its tag again (make release TAG=<its tag>) and deploy that first; never `docker image prune -a` here "
             "(docs/runbooks/roll-back-a-release.md)")


def ensure_images(ctx: Ctx, rel: Release) -> None:
    for service, ref in missing_images(rel):
        if local_build(ref):
            raise Refused(f"{service}: local build {ref} is not on this host; `make release` builds and tags it, "
                          "and an image a kept release names is never pruned")
        ctx.say(f"deploy: pulling {ref}")
        run("docker", "pull", "-q", ref)


# --- step 4, ddl -------------------------------------------------------------------------

_SPARK_TYPES = {"STRING": "string", "INT": "int", "BIGINT": "long", "FLOAT": "float", "DOUBLE": "double",
                "BOOLEAN": "boolean", "BINARY": "binary", "DATE": "date", "TIMESTAMP_NTZ": "timestamp",
                "TIMESTAMP_LTZ": "timestamptz"}
_TRINO_TYPES = {"varchar": "string", "integer": "int", "bigint": "long", "real": "float", "double": "double",
                "boolean": "boolean", "varbinary": "binary", "date": "date", "timestamp(6)": "timestamp",
                "timestamp(6) with time zone": "timestamptz"}


def iceberg_type(t: str, engine: str) -> str:
    """A DDL column type in Iceberg's spelling, as k2tools `table` prints the catalog's."""
    t = t.strip()
    if m := re.fullmatch(r"(?i)decimal\((\d+),\s*(\d+)\)", t):
        return f"decimal({m[1]},{m[2]})"
    if m := re.fullmatch(r"ARRAY<(.+)>|array\((.+)\)", t):
        return f"list<{iceberg_type(m[1] or m[2], engine)}>"
    if m := re.fullmatch(r"MAP<(.+?), (.+)>|map\((.+?), (.+)\)", t):
        return f"map<{iceberg_type(m[1] or m[3], engine)},{iceberg_type(m[2] or m[4], engine)}>"
    table = _SPARK_TYPES if engine == "spark" else _TRINO_TYPES
    if t not in table:
        raise Failed(f"a DDL type deploy cannot compare: {t}")
    return table[t]


def _partition(f: str, engine: str) -> list[str]:
    f = f.strip().strip("'").replace('"', "")
    if m := re.fullmatch(r"(years|months|days|hours|year|month|day|hour)\((\w+)\)", f):
        return [m[1].removesuffix("s"), m[2]]
    if m := re.fullmatch(r"(bucket|truncate)\((\w+), ?(\w+)\)", f):
        n, col = (m[2], m[3]) if engine == "spark" else (m[3], m[2])
        return [f"{m[1]}[{n}]", col]
    return ["identity", f]


def expected_table(text: str, engine: str) -> dict[str, Any]:
    """The columns [name, Iceberg type, required] and partition fields [transform, column] of one
    emitted CREATE TABLE: Spark's dialect (emit/lake.py) or Trino's (platform/runs/run_record.sql)."""
    body = text[text.index("(", text.index("CREATE TABLE")) + 1:]
    columns, depth, rest = [], 0, ""
    for i, ch in enumerate(body):  # the column list ends at the parenthesis that closes it
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            rest, body = body[i + 1:], body[:i]
            break
    for line in body.splitlines():
        line = re.sub(r"\s+COMMENT\s+'(?:[^']|'')*'", "", line).strip().rstrip(",")
        if not line:
            continue
        m = re.fullmatch(r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s+(.+?)(\s+NOT NULL)?', line)
        if not m:
            raise Failed(f"a DDL column line deploy cannot read: {line}")
        columns.append([m[1], iceberg_type(m[2], engine), bool(m[3])])
    fields: list[str] = []
    if m := re.search(r"PARTITIONED BY \((.*)\)\s*$", rest, re.M):
        fields = re.findall(r'\w+\([^)]*\)|"[^"]+"|\w+', m[1])
    elif m := re.search(r"partitioning = ARRAY\[(.*?)\]", rest):
        fields = re.findall(r"'([^']*)'", m[1])
    return {"columns": columns, "partitioning": [_partition(f, engine) for f in fields]}


def table_difference(name: str, want: dict[str, Any], have: dict[str, Any]) -> str | None:
    if want == have:
        return None
    w = {c[0]: c for c in want["columns"]}
    h = {c[0]: c for c in have["columns"]}
    parts = [f"column {c} is in the DDL, not the catalog" for c in w if c not in h]
    parts += [f"column {c} is in the catalog, not the DDL" for c in h if c not in w]
    parts += [f"column {c} is {h[c][1]}{' required' if h[c][2] else ''} in the catalog, "
              f"{w[c][1]}{' required' if w[c][2] else ''} in the DDL" for c in w if c in h and w[c] != h[c]]
    if not parts and [c[0] for c in want["columns"]] != [c[0] for c in have["columns"]]:
        parts.append("the columns are in another order")
    if want["partitioning"] != have["partitioning"]:
        parts.append(f"partitioned by {have['partitioning']} in the catalog, {want['partitioning']} in the DDL")
    return f"{name} exists and differs from the release's DDL: {'; '.join(parts)}"


def compare_ddl(ctx: Ctx, p: Plan) -> list[str]:
    """Every needs.ddl table found equal to its DDL, or missing (then queued in p.create): all are
    compared before any is created, so a refusal creates nothing."""
    out = []
    for name in p.ddl:
        path = next((q for e in ("spark", "trino") if (q := model.ddl_path(e, *name.split("."))) in p.target.manifest.files), None)  # type: ignore[arg-type]
        if path is None:
            raise Failed(f"needs.ddl names {name}, the release has no DDL file for it")
        engine, text = path.split("/")[3], (p.target.root / path).read_text()
        have = json.loads(tools(ctx, p.target, "table", CATALOG_URI, *name.split(".")))
        if have is None:
            p.create.append((name, engine, text))
        elif why := table_difference(name, expected_table(text, engine), have):
            raise Refused(why + "; an existing table is never altered or dropped by deploy (make drop, then deploy)")
        else:
            out.append(f"exists and matches {name}")
    return out


def create_tables(ctx: Ctx, p: Plan, late: bool) -> list[str]:
    """The queued tables whose engine runs the target: Spark's (warehouse k2, through the `lake`
    catalog this conf names) while Spark runs and is not being recreated, else after the recreate;
    Trino's only once the target's rules are loaded, since svc.platform's rights are in them."""
    def ready(engine: str) -> bool:
        if engine == "trino":
            return late
        c = container(ctx, "spark-iceberg")
        return late or ("spark-iceberg" not in p.recreate and bool(c and c["State"]["Running"]))

    now_ = [t for t in p.create if ready(t[1])]
    for _, engine, text in now_:
        if engine == "trino":
            sql(ctx, "svc.platform", text)
    if spark := [text for _, engine, text in now_ if engine == "spark"]:
        c = container(ctx, "spark-iceberg")
        if c is None or not c["State"]["Running"]:
            raise Failed("spark-iceberg is not running, and the release's DDL for warehouse k2 needs Spark")
        conf = [a for k, v in SPARK_LAKE.items() for a in ("--conf", f"{k}={v}")]
        run("docker", "exec", c["Id"], "/opt/spark/bin/spark-sql", "--driver-memory", "512m", *conf, "-e", "\n".join(spark))
    for name, engine, text in now_:
        have = json.loads(tools(ctx, p.target, "table", CATALOG_URI, *name.split(".")))
        if have is None or (why := table_difference(name, expected_table(text, engine), have)):
            raise Failed(f"{name}: after its DDL the catalog shows {'nothing' if have is None else why}")
        p.create.remove((name, engine, text))
    return [f"created {n} ({e})" for n, e, _ in now_] + [f"{n}: created after the recreate, by {e}" for n, e, _ in p.create]


# --- steps 5 and 9, the views ------------------------------------------------------------


def _build_name(v: View) -> str:
    catalog, namespace, table = v.relation.split(".")
    return f"{model.build_catalog(catalog)}.{namespace}.{table}"


def live_view(ctx: Ctx, v: View) -> list[str] | None:
    catalog, namespace, table = _build_name(v).split(".")
    rows = sql(ctx, v.owner, f"SELECT column_name FROM {model.quote_ident(catalog)}.information_schema.columns "
                             f"WHERE table_schema = {model.sql_literal(namespace)} AND table_name = {model.sql_literal(table)} "
                             "ORDER BY ordinal_position")["rows"]
    return [r[0] for r in rows] or None


def tagged(ctx: Ctx, v: View) -> dict[str, str] | None:
    """The columns and types of the base at its `audited` tag, as the view's owner reads them."""
    try:
        result = sql(ctx, v.owner, f"SELECT * FROM {model.quote_relation(v.base)} FOR VERSION AS OF "
                                   f"{model.sql_literal(model.AUDITED_TAG)} LIMIT 0")
    except k2tools.TrinoError as e:
        if re.search(r"(?i)(tag|ref|version|snapshot).*(not found|does not exist)|cannot find", str(e)):
            return None
        raise
    return {name: typ for name, typ in result["columns"]}


def tokens(text: str) -> list[str]:
    """SQL as the drift check compares it (scripts/verify/catalog_drift.tokens): Trino stores a view's
    SQL reformatted, so stored and generated SQL are compared token by token."""
    return list(drift_lib.tokens(text))


def view_body(statement: str) -> str:
    """The query of a CREATE VIEW statement: what Trino stores as the view's definition."""
    return re.split(r"(?i)\bSECURITY\s+DEFINER\s+AS\b", statement, maxsplit=1)[-1]


def stored_view(ctx: Ctx, v: View) -> str | None:
    catalog, namespace, table = _build_name(v).split(".")
    rows = sql(ctx, v.owner, f"SELECT view_definition FROM {model.quote_ident(catalog)}.information_schema.views "
                             f"WHERE table_schema = {model.sql_literal(namespace)} AND table_name = {model.sql_literal(table)}")["rows"]
    return rows[0][0] if rows else None


STATEMENTS = """
import json, sys
from k2gen.emit.lake import NoCommonColumns, view_update
from k2gen.model import View
a = json.loads(sys.argv[1])
try:
    print(json.dumps(view_update(View(**a["view"]), a["old"], a["new"])))
except NoCommonColumns as e:
    print(json.dumps({"none": str(e)}))
"""


def statements(ctx: Ctx, rel: Release, v: View, old: dict[str, str], new: dict[str, str]) -> tuple[str | None, str]:
    """k2gen.emit.lake.view_update's two statements as `rel`'s own k2gen writes them, run in rel's tools
    image: each release's view shape is the one its own rules can read (the guarded view of the review
    of S12 needs the `$refs` grant only its release's rules give)."""
    arg = json.dumps({"view": dataclasses.asdict(v), "old": old, "new": new})
    out = run("docker", "run", "--rm", "--network", "none", "--memory", "256m", rel.manifest.images["tools"],
              "python3", "-c", STATEMENTS, arg).stdout.strip().splitlines()[-1]
    doc = json.loads(out)
    if isinstance(doc, dict):
        raise Failed(doc["none"])
    return doc[0], doc[1]


def sync_views(ctx: Ctx, p: Plan, phase: str) -> list[str]:
    """phase `narrow`: each view down to its contract's columns that it and the tag both hold, so a
    column the target drops leaves the view before the rules that masked it go. phase `widen`, once
    the target's rules are loaded: up to the contract's columns the tag holds. Each statement is
    k2gen.emit.lake.view_update's, made at run time from the tag's schema, run as the view's owner,
    and only where the view's stored SQL differs from it by token."""
    lines = []
    if phase == "narrow" and p.sources == [None]:
        return ["a first deploy: no view has been published under a release yet"]
    source = p.sources[0]
    for v in p.views:
        try:
            lines += _sync_view(ctx, p, phase, source, v)
        except Exception as e:  # which view could not be synced: after a rollback's swap that is exit 3
            raise Failed(f"view {v.relation} not synced ({phase}): {type(e).__name__}: {e}") from e
    return lines


def _sync_view(ctx: Ctx, p: Plan, phase: str, source: Release | None, v: View) -> list[str]:
    lines = []
    live = live_view(ctx, v)
    tag = tagged(ctx, v) if live else None
    if live is None or tag is None:
        return [f"{v.relation}: not published yet; the wrapper creates it at its first tag"]
    old = {c: tag[c] for c in live if c in tag}
    narrow, widen = statements(ctx, p.target, v, old, tag)
    statement = str(narrow if phase == "narrow" else widen)
    stored = stored_view(ctx, v) or ""
    if tokens(view_body(statement)) == tokens(stored):
        pass
    elif phase == "widen":  # the target's rules are loaded: the target's shape
        sql(ctx, v.owner, statement)
        lines.append(f"{v.relation}: {live} -> {[c for c in v.columns if c in tag]} (the target's shape)")
    else:  # the source's rules still hold: the target's shape when they admit it, else the source's
        try:
            sql(ctx, v.owner, statement)
            lines.append(f"{v.relation}: {live} -> {[c for c in v.columns if c in tag and c in live]} (the target's shape)")
        except k2tools.TrinoError as e:
            if e.name != "PERMISSION_DENIED" or source is None:
                raise
            legacy = str(statements(ctx, source, v, old, tag)[0])
            if tokens(view_body(legacy)) != tokens(stored):
                sql(ctx, v.owner, legacy)
            lines.append(f"{v.relation}: {live} -> {[c for c in v.columns if c in tag and c in live]} in the source's shape "
                         f"(its rules refuse the target's: {str(e)[:90]}); the target's shape after its rules load")
    if phase == "widen" and (missing := [c for c in v.columns if c not in tag]):
        lines.append(f"{v.relation}: {', '.join(missing)} failing closed until republished: the contract has "
                     "them, the tested snapshot does not; SKIP reconcile: phase 002 builds the product pipeline")
    return lines


# --- step 6, the canary ------------------------------------------------------------------


def password_db(ctx: Ctx, change: Any) -> list[str]:
    """password.db rewritten to `change(users)` by principal-secret.py's own slot writer under the file's
    lock: fixed 128-byte slots, in place, whole-file validation before and after (its write_db). Its
    refusals (a malformed or unreadable line) are this deploy's failures, never a process exit."""
    with ps.locked(ctx.secrets / PASSWORD_DB) as db:
        old = ps.read(db)
        if bad := ps.problems(old):
            raise Failed(f"{PASSWORD_DB} has lines Trino refuses: " + "; ".join(f"line {n}: {why}" for n, _, why in bad)
                         + " (make principals-status; docs/runbooks/create-or-rotate-a-principal.md)")
        users = ps.entries(old)
        want = change(dict(users))
        try:
            ps.write_db(db, "".join(f"{u}:{h}\n" for u, h in want.items()))
        except SystemExit as e:
            raise Failed(str(e)) from None
    return sorted(set(users) - set(want))


def canary_write(ctx: Ctx, user: str) -> None:
    """A fresh 32-byte secret: its password file (mode 600, never mounted) first, in place under its
    lock, then its bcrypt line in trino/password.db by the slot writer."""
    secret = secrets.token_urlsafe(32)
    with ps.locked(ctx.secrets / model.canary_password_file(user.removeprefix("k2_release_"))) as f:
        ps.write(f, secret)
    hashed = "$2y$" + bcrypt.hashpw(secret.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode().removeprefix("$2b$")
    password_db(ctx, lambda users: {**{u: h for u, h in users.items() if u != user}, user: hashed})


def canary_prune(ctx: Ctx, keep: str) -> list[str]:
    """Every other release canary's line and password file removed; the principals' lines untouched."""
    gone = password_db(ctx, lambda users: {u: h for u, h in users.items() if not (u.startswith("k2_release_") and u != keep)})
    for f in (ctx.secrets / "canary").glob("k2_release_*.password"):
        if f.name != f"{keep}.password":
            with ps.locked(f) as fd:
                ps.write(fd, "")
            f.unlink()
    return gone


def wait_rules(ctx: Ctx, new: str, old: str | None) -> float:
    """Seconds until Trino admits the new canary and refuses the old one: this release's rules are
    the ones loaded."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < RULES_WAIT_S:
        if admitted(ctx, new) and (old is None or old == new or not admitted(ctx, old)):
            return time.monotonic() - t0
        time.sleep(0.25)
    raise Failed(f"Trino did not load the rules of {new} within {RULES_WAIT_S} s (its canary: "
                 f"{LAST_REFUSAL.get(new, 'admitted')}; the previous one: {LAST_REFUSAL.get(old or '', 'admitted') if old else 'none'})")


# --- step 9, verify ----------------------------------------------------------------------


def verify(ctx: Ctx, target: Release, previous_canary: str | None) -> list[str]:
    """Every part answers with the target: the pointers as the VM and each mounting container see them,
    the tree under them, the canary, the access suite and drift check (S20), each image, each health check."""
    if "verify-mismatch" in ctx.faults:
        raise Failed("verify: K2_DEPLOY_FAULT=verify-mismatch, a mismatch forced for the rehearsal")
    lines, wrong = [], []
    seen = json.loads(tools(ctx, target, "pointers"))
    wrong += [f"pointer {p} is {seen.get(p)} in the VM" for p in POINTERS if seen.get(p) != f"../{target.id}"]
    for c in containers(ctx):
        name = c["Config"]["Labels"].get("com.docker.compose.service")
        if c["State"]["Running"] and any(m.get("Destination") == model.RELEASES_MOUNT for m in c.get("Mounts", [])):
            pointer = next((p for p in POINTERS if name == p or name.startswith(f"{p}-")), "trino")  # the one it reads
            ask = run("docker", "exec", c["Id"], "readlink", f"{model.RELEASES_MOUNT}/pointers/{pointer}", check=False)
            if "executable file not found" in ask.stderr:
                lines.append(f"{name}: has no readlink to ask (a distroless image); the VM's answer stands for it")
            elif ask.stdout.strip() != f"../{target.id}":
                wrong.append(f"{name} sees pointer {pointer} as {ask.stdout.strip() or 'nothing'}")
            else:
                lines.append(f"{name}: pointer {pointer} is ../{target.id}")
    try:
        if k2release.read(ctx.root / "pointers" / "trino").id != target.id:
            wrong.append("the host's pointer trino names another release")
    except (ValueError, OSError) as e:
        wrong.append(f"the release under the pointer does not verify: {e}")
    canary = model.canary_user(target.manifest.set_sha256)
    if admitted(ctx, canary) is not True:
        wrong.append(f"Trino does not admit {canary}: this release's rules or its password line are not loaded")
    if previous_canary and previous_canary != canary and admitted(ctx, previous_canary):
        wrong.append(f"Trino still admits {previous_canary}: the previous release's rules are loaded")
    lines.append(f"canary {canary} admitted" + (f", {previous_canary} refused" if previous_canary and previous_canary != canary else ""))
    more, bad = s20_checks(ctx, target)
    lines, wrong = lines + more, wrong + bad
    svc = services(target)
    found = containers(ctx)
    # Nothing found is never a pass (review R1 M1): a project name that matches no container would
    # check nothing below. Every default-profile, long-running service of the release runs.
    expected = {s for s, spec in svc.items() if not spec.get("profiles") and str(spec.get("restart", "no")) != "no"}
    running = {c["Config"]["Labels"].get("com.docker.compose.service") for c in found if c["State"]["Running"]}
    if not found:
        wrong.append(f"no container of compose project {ctx.project!r}: K2_COMPOSE_PROJECT names another project")
    elif missing := sorted(expected - running):
        wrong.append(f"{len(running & expected)} of the release's {len(expected)} long-running services run; not running: {', '.join(missing)}")
    api = svc.get("k2-api")
    if api and not api.get("profiles") and str(api.get("restart", "no")) != "no":  # W3-6: k2-api is a part too
        said, where = api_release(ctx, target)
        if said != target.id:
            wrong.append(f"k2-api serves {said}, not {target.id} ({where})" if said else f"k2-api did not answer: {where}")
        else:
            lines.append(f"k2-api: GET {where} names {said}")
    fence = catalog_fence(ctx)
    if fence.returncode:
        wrong.append(f"catalog fence exit {fence.returncode}: {tail(fence, 6)}")
    else:
        lines.append(tail(fence, 1))
    for c in found:
        name = c["Config"]["Labels"].get("com.docker.compose.service")
        ref = target.manifest.images.get(name)
        if not ref or name not in svc or (svc[name].get("profiles") and not c["State"]["Running"]):
            continue
        # An exited one-shot keeps the image of its last run: a rollback never reruns one (a migration
        # never runs backwards), so after a rollback across airflow-migrate's image it is the newer one.
        done_oneshot = str(svc[name].get("restart", "no")) == "no" and not c["State"]["Running"]
        if c["Image"] != image_id(ref) and not done_oneshot:
            wrong.append(f"{name} runs {c['Image'][:19]}, the release names {ref}")
        health = (c["State"].get("Health") or {}).get("Status")
        if c["State"]["Running"] and health not in (None, "healthy"):
            wrong.append(f"{name} is {health}")
        if not c["State"]["Running"] and (str(svc[name].get("restart", "no")) != "no" or c["State"]["ExitCode"] != 0):
            wrong.append(f"{name} is {c['State']['Status']} (exit {c['State']['ExitCode']})")
    lines.append(f"images and health checked for {sum(1 for c in found if c['Config']['Labels'].get('com.docker.compose.service') in target.manifest.images)} container(s)")
    if wrong:
        raise Failed("verify: " + "; ".join(wrong))
    return lines


def ensure_networks(ctx: Ctx, rel: Release) -> list[str]:
    """Every network the release's compose declares, created when missing, with the labels compose gives
    its own (project, network key, compose's version), so a later `up` adopts it: compose makes a network
    only for a service it starts, and fetch's k2-egress has no other member (the operator `docker run`s
    fetch on it). Never --all-resources on `up`, which would also make the paused hot-gold volumes."""
    out, version = [], ""
    for key, spec in sorted((rendered(ctx, rel).get("networks") or {}).items()):
        name = spec.get("name") or f"{ctx.project}_{key}"
        if spec.get("external") or run("docker", "network", "inspect", "--format", "{{.Name}}", name, check=False).returncode == 0:
            continue
        if set(spec) - {"name", "driver", "ipam"} or spec.get("ipam"):
            raise Failed(f"network {key} declares {sorted(set(spec) - {'name'})}: deploy creates only a plain one; "
                         f"create {name} by hand as compose would")
        version = version or run("docker", "compose", "version", "--short").stdout.strip()
        run("docker", "network", "create", "--driver", spec.get("driver") or "bridge",
            "--label", f"com.docker.compose.project={ctx.project}", "--label", f"com.docker.compose.network={key}",
            "--label", f"com.docker.compose.version={version}", name)
        out.append(f"network {name} created: no service compose starts uses it yet")
    return out


def api_release(ctx: Ctx, rel: Release) -> tuple[str | None, str]:
    """(the release k2-api says it serves, or None; where it asked or why not): GET /v1/release on the
    port the release's rendered compose publishes, a few tries while a recreated k2-api starts."""
    port = (rendered(ctx, rel).get("services", {}).get("k2-api", {}).get("ports") or [{}])[0]
    if not port.get("published"):
        return None, "the release's compose publishes no port for k2-api"
    host = port.get("host_ip") or "127.0.0.1"
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port['published']}/v1/release"
    why = ""
    for i in range(API_TRIES):
        try:
            with urllib.request.urlopen(url, timeout=3) as answer:
                return str(json.load(answer)["release"]), url
        except (OSError, ValueError, KeyError, TypeError) as e:  # no answer, an error status, not the document
            why = f"{url}: {type(e).__name__}: {str(e)[:160]}"
        if i + 1 < API_TRIES:
            time.sleep(API_WAIT_S)
    return None, why


def catalog_fence(ctx: Ctx) -> subprocess.CompletedProcess[str]:
    """The catalog fence's network probes (scripts/verify/catalog-fence.sh, ADR-059 part 7) on the
    deploy's project: exit 0 every probe row passed, 1 one failed, 2 no Docker or no rows."""
    return run("bash", str(ctx.home / "scripts/verify/catalog-fence.sh"), env=compose_env(ctx), check=False)


def verify_dir(ctx: Ctx) -> Path:
    """<releases root>/verify/: the results S20's checks write beside the releases, by release id."""
    d = ctx.root / "verify"
    d.mkdir(exist_ok=True)
    return d


def release_suite(ctx: Ctx, rel: Release) -> list[str]:
    """Release time, before anything moves, on one throwaway lake started from the release (S20, by
    the commands mk/access.mk's `access-verify RELEASE=<dir>` runs): the release's products staged,
    every row of tests/access/expectations.yaml, then the tested-data marker
    (scripts/verify/tested-data-marker.sh). A release is sealed, so a pass is kept by its id under
    verify/ and not run again."""
    d = verify_dir(ctx)
    access, marker = d / f"{rel.id}.access-release.json", d / f"{rel.id}.marker.json"
    passed = [f.name for f in (access, marker) if f.exists() and json.loads(f.read_text()).get("exit") == 0]
    if len(passed) == 2:
        return [f"access-verify and the tested-data marker passed earlier for {rel.id} ({', '.join(passed)})"]
    prefix, env = f"deploy-{rel.id[-12:]}", compose_env(ctx)
    lake = ["bash", str(ctx.home / "scripts/lib/throwaway-lake.sh")]
    work = f"/work/releases/{rel.id}"
    try:
        run(*lake, "up", prefix, str(rel.root), env=env)
        state = Path(run(*lake, "state", prefix, env=env).stdout.strip())
        run(*lake, "py", prefix, "scripts/access_verify.py", "stage", "--release", work, "--env", "/work/env.json", env=env)
        rows = run(*lake, "py", prefix, "scripts/access_verify.py", "run", "--scope", "release", "--release", work,
                   "--env", "/work/env.json", "--out", "/work/access-verify.json", env=env, check=False)
        if (state / "access-verify.json").exists():
            shutil.copy(state / "access-verify.json", access)
        counts = json.loads(access.read_text()).get("counts") if access.exists() else None
        if rows.returncode:
            raise Refused(f"the release fails its access suite at release time (exit {rows.returncode}, {counts}): {tail(rows)}")
        done = run("bash", str(ctx.home / "scripts/verify/tested-data-marker.sh"), prefix, env=env, check=False)
    finally:
        run(*lake, "down", prefix, env=env, check=False)
    marker.write_text(json.dumps({"release": rel.id, "at": now(), "exit": done.returncode,
                                  "lines": done.stdout.strip().splitlines()[-40:],
                                  "stderr": done.stderr.strip().splitlines()[-20:]}, indent=2) + "\n")
    if done.returncode:
        raise Refused(f"the release fails the tested-data marker check (exit {done.returncode}): {tail(done)}")
    return [f"access-verify at release time: {counts}",
            f"tested-data marker: {sum(1 for x in done.stdout.splitlines() if x.startswith('PASS'))} PASS"]


def make_target(ctx: Ctx, target: str, env: dict[str, str], **variables: str) -> subprocess.CompletedProcess[str]:
    """`make <target> VAR=value...` in the checkout: the variables on its command line, and none of the
    calling make's (MAKEFLAGS would carry `make deploy`'s RELEASE=<id> into it and override them)."""
    clean = {k: v for k, v in env.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")}
    return run("make", "-s", "-C", str(ctx.home), target, *(f"{k}={v}" for k, v in variables.items()), env=clean, check=False)


def tail(done: subprocess.CompletedProcess[str], n: int = 6) -> str:
    return " | ".join((done.stdout + done.stderr).strip().splitlines()[-n:])


def drift(ctx: Ctx, target: Release) -> dict[str, Any]:
    """S20's catalog drift check, from inside tools on the catalog's network (the catalog has no host
    port once fenced, ADR-059 part 7): `catalog_drift.py --release <dir> --out <file>`; exit 0 clean,
    1 drift, 2 unreachable. The script comes from this checkout, mounted read-only; k2gen from the
    release's own tools image."""
    script = ctx.home / "scripts" / "verify" / "catalog_drift.py"
    result = f"{model.RELEASES_MOUNT}/verify/{target.id}.drift.json"
    verify_dir(ctx)
    cmd = compose(ctx, target, "run", "--rm", "--no-deps", "--pull", "never", "--user", f"{os.getuid()}:{os.getgid()}",
                  "-v", f"{script}:/opt/k2/verify/catalog_drift.py:ro", "tools", "python3", "/opt/k2/verify/catalog_drift.py",
                  "--release", f"{model.RELEASES_MOUNT}/{target.id}", "--out", result)
    done = run(*cmd, env=compose_env(ctx), check=False)
    found = ctx.root / "verify" / f"{target.id}.drift.json"
    doc: dict[str, Any] = json.loads(found.read_text()) if found.exists() and done.returncode in (0, 1) else {}
    return {"exit": done.returncode, "doc": doc, "text": tail(done, 12)}


def access_live(ctx: Ctx, rel: Release, out: Path) -> subprocess.CompletedProcess[str]:
    """S20's live suite (`access_verify.py run --scope live`, `make access-verify LIVE=1`) in a tools
    one-shot of the release on the stack's networks: Trino by name, and each storage-key row's request
    to minio:9000 from the lake's own network, which has no host port. Each engine's pair goes in by -e
    name from this process's environment, for this run only: never written, never on a command line.
    The passwords the platform holds and the CA, read-only. The result is copied to `out`."""
    names = [f"K2_{f.stem.upper()}_S3_{k}" for f in sorted((ctx.home / "docker/lake/minio/policies").glob("*.json"))
             for k in ("ACCESS_KEY", "SECRET_KEY")]  # one per policy, as throwaway-lake.sh and init-lake.sh read them
    verify_dir(ctx)
    cmd = compose(ctx, rel, "run", "--rm", "--no-deps", "--pull", "never", "--user", f"{os.getuid()}:{os.getgid()}",
                  "-e", "K2_TRINO_HOST=trino", "-e", "K2_TRINO_PORT=8443", "-e", f"{model.SECRETS_ENV}=/run/k2-secrets",
                  *(a for n in names if os.environ.get(n) for a in ("-e", n)),
                  "-v", f"{ctx.home / 'scripts'}:/opt/k2/scripts:ro",
                  "-v", f"{ctx.secrets / 'principals'}:/run/k2-secrets/principals:ro",
                  "-v", f"{ctx.secrets / CA}:/run/k2-secrets/{CA}:ro",
                  "tools", "python3", "/opt/k2/scripts/access_verify.py", "run", "--scope", "live",
                  "--release", f"{model.RELEASES_MOUNT}/{rel.id}", "--out", f"{model.RELEASES_MOUNT}/verify/{rel.id}.access-live.json")
    done = run(*cmd, env=compose_env(ctx), check=False)
    found = ctx.root / "verify" / f"{rel.id}.access-live.json"
    if found.exists() and found.resolve() != out.resolve():
        shutil.copy(found, out)
    return done


def s20_checks(ctx: Ctx, target: Release) -> tuple[list[str], list[str]]:
    """(lines, what is wrong): the drift check, then the access suite on the live system
    (`make access-verify LIVE=1`), its rows for every product the drift check finds published."""
    lines, wrong = [], []
    d = drift(ctx, target)
    pending = [str(x) for x in d["doc"].get("pending", [])]
    if d["exit"] == 0:
        lines.append(f"catalog drift check clean ({len(d['doc'].get('differences', []))} differences, "
                     f"pending {pending or 'none'}, {len(d['doc'].get('skipped', []))} skipped)")
    else:
        wrong.append(f"catalog drift check exit {d['exit']}: {d['text']}")
    if pending:
        lines.append(f"SKIP access-verify LIVE=1: not yet published, so no live row can read it: {', '.join(pending)}; "
                     "every row ran on a throwaway lake at release time (preflight); phase 002 publishes them")
        return lines, wrong
    result = verify_dir(ctx) / f"{target.id}.access-live.json"
    done = access_live(ctx, target, result)
    counts = json.loads(result.read_text()).get("counts") if result.exists() else None
    if done.returncode:
        wrong.append(f"access-verify LIVE=1 exit {done.returncode} ({counts}): {tail(done)}")
    else:
        lines.append(f"access-verify LIVE=1 passed: {counts}")
    return lines, wrong


def identity(ctx: Ctx) -> list[str]:
    """S13's identity rows on the running Trino (scripts/verify/identity.sh, live): exit 0 all pass."""
    c = container(ctx, "trino")
    if c is None:
        raise Failed("no trino container to check identity on")
    network = next(iter(c["NetworkSettings"]["Networks"]))
    env = {**compose_env(ctx), "K2_TRINO_CONTAINER": c["Name"].lstrip("/"), "K2_IDENTITY_NETWORK": network}
    done = run("bash", str(ctx.home / "scripts/verify/identity.sh"), env=env, check=False)
    if done.returncode:
        raise Failed(f"identity.sh exit {done.returncode}: {tail(done, 10)}")
    return [f"identity.sh: {sum(1 for x in done.stdout.splitlines() if x.startswith('PASS'))} PASS, exit 0"]


# --- the sequence ------------------------------------------------------------------------


def step(ctx: Ctx, target: Release, frm: str | None, name: str, svc: set[str], work: Any,
         extra: dict[str, Any] | None = None) -> Any:
    """Run one step: its work, then the fault hook, then its record (with `extra`, which the work may fill)."""
    t0 = time.monotonic()
    try:
        out = work()
    except Exception as e:  # any failure is recorded; the caller decides refusal, failure or rollback
        record(ctx, target, frm, name, "failed", svc, f"{type(e).__name__}: {e}")
        ctx.say(f"deploy: {name} FAILED after {time.monotonic() - t0:.1f} s: {e}")
        raise
    if f"kill:{name}" in ctx.faults:
        ctx.say(f"deploy: K2_DEPLOY_FAULT=kill:{name}: killed after the step's work, before its record")
        os.kill(os.getpid(), signal.SIGKILL)
    record(ctx, target, frm, name, "ok", svc, **(extra or {}))
    for line in out if isinstance(out, list) else []:
        ctx.say(f"  {line}")
    ctx.say(f"deploy: {name} ok ({time.monotonic() - t0:.1f} s)")
    return out


def converge(ctx: Ctx, p: Plan, frm: str | None, prefix: str = "", trino_down: bool = False) -> None:
    """Steps 5 to 10 toward p.target; any failure propagates (the caller rolls back).

    trino_down (a rollback only, review R1 H2): the views cannot be read or changed, so the pointers move
    and the services are recreated first, Trino among them, and views-narrow runs right after the
    recreate, before rules-loaded. With Trino down no reader reads anything. Once it is back on the good
    release's etc and rules, a column only the failed release masked is readable through a view that
    still carries it, from Trino healthy until views-narrow's statement for that view, typically under
    a second (ponytail: no fence for that window; narrowing through Lakekeeper's view endpoint closes
    it). A view that cannot be synced then fails the rollback (exit 3) naming the view, and nothing is
    declared done before every view is synced."""
    t, svc = p.target, set(services(p.target))
    canary = model.canary_user(t.manifest.set_sha256)
    old = model.canary_user(p.sources[0].manifest.set_sha256) if p.sources[0] else None
    if trino_down and "trino" not in p.recreate:
        p.recreate.insert(0, "trino")  # it comes back on the good release's etc and rules
    if not trino_down:  # the source's rules still hold, so a dropped column leaves before they go
        step(ctx, t, frm, prefix + "views-narrow", svc, lambda: sync_views(ctx, p, "narrow"))
    step(ctx, t, frm, prefix + "canary", svc, lambda: canary_write(ctx, canary) or [f"{canary}: line written in place"])
    swapped: list[float] = []

    def swap() -> list[str]:
        seen = tools(ctx, t, "swap", t.id, *POINTERS).strip()
        swapped.append(time.monotonic())
        out = [f"as the VM sees them: {seen}"]
        for c in containers(ctx):  # Prometheus reads its rules through the pointer only when told to
            if (c["Config"]["Labels"].get("com.docker.compose.service") == "prometheus" and c["State"]["Running"]
                    and any(m.get("Destination") == model.RELEASES_MOUNT for m in c.get("Mounts", []))):
                run("docker", "kill", "--signal", "HUP", c["Id"])
                out.append("prometheus: SIGHUP, its rules reread through the pointer")
        return out

    step(ctx, t, frm, prefix + "swap", svc, swap)
    target_svc = services(t)
    for s in p.remove:  # one record per service, its name in the step's: verify then expects none of it
        profiles = [a for x in target_svc[s]["profiles"] for a in ("--profile", x)]  # so compose resolves it
        step(ctx, t, frm, prefix + f"remove-{s}", svc, lambda s=s, profiles=profiles: run(
            *compose(ctx, t, *profiles, "rm", "--stop", "--force", s), env=compose_env(ctx)) and
            [f"{s} stopped and removed: behind profile {','.join(target_svc[s]['profiles'])} in {t.id}"])

    def recreate() -> list[str]:
        out = ensure_networks(ctx, t)  # before verify: the fence's fetch rows dial from k2-egress
        for s in p.recreate:
            run(*compose(ctx, t, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                         *(["--force-recreate"] if s == "trino" else []), s), env=compose_env(ctx))
            out.append(f"{s} recreated, healthy in {wait_service(ctx, s, False):.1f} s")
        return out or ["nothing to recreate"]

    step(ctx, t, frm, prefix + "recreate", svc, recreate)
    if trino_down:
        step(ctx, t, frm, prefix + "views-narrow", svc, lambda: sync_views(ctx, p, "narrow"))

    def rules() -> list[str]:
        wait_rules(ctx, canary, old)
        loaded = time.monotonic()
        time.sleep(REFRESH_S)  # groups reload on a timer of their own (spike c): one more period before views widen
        since = f"{loaded - swapped[0]:.2f} s after the swap" if swapped else "(the swap was an earlier run's)"
        return [f"{canary} admitted{f' and {old} refused' if old and old != canary else ''} {since}"]

    step(ctx, t, frm, prefix + "rules-loaded", svc, rules)
    step(ctx, t, frm, prefix + "ddl-late", svc, lambda: create_tables(ctx, p, late=True) or ["nothing left to create"])
    step(ctx, t, frm, prefix + "views-widen", svc, lambda: sync_views(ctx, p, "widen"))
    step(ctx, t, frm, prefix + "verify", svc, lambda: verify(ctx, t, old))
    step(ctx, t, frm, prefix + "done", svc, lambda: [f"removed canary {g}" for g in canary_prune(ctx, canary)] or ["no other canary"])


def resolve(ctx: Ctx) -> tuple[Release | None, list[str]]:
    """(the live good release, the releases attempted since it)."""
    records = history(ctx.root)
    goods, since = standing(records)
    good = goods[-1] if goods else None
    if not records and (ctx.root / "pointers" / "trino").is_symlink():  # a root whose log was lost
        good = os.readlink(ctx.root / "pointers" / "trino").removeprefix("../")
    # else, with no good release, a first deploy that failed: plan again from nothing (every step converges)
    return (open_release(ctx, good) if good else None), since


def trino_answers(ctx: Ctx) -> bool:
    """Any answer from Trino, a refusal included; only no HTTP answer at all is False."""
    try:
        sql(ctx, "svc.platform", CANARY_SQL)
    except k2tools.Unreachable:
        return False
    except (k2tools.TrinoError, FileNotFoundError):
        pass
    return True


def migration_dumps(ctx: Ctx, p: Plan, record: dict[str, Any]) -> list[str]:
    """Before step 4 runs a one-shot that migrates a database (named <database>-migrate: airflow-migrate,
    lakekeeper-migrate), that database dumped from the project's prefect-db by scripts/backup-lake.sh's
    own dump (--dump) to ${K2_BACKUP_DIR:-~/k2-backup}/deploy/<release>/, its path and sha256 into `record`
    (review R1 H1). Never under the releases root: every service mounts that, and a dump holds a whole database.
    Skipped only when every release the plan moves from pins the one-shot's image: then no migration
    can have changed. A failed dump refuses the deploy: nothing has changed yet."""
    out, dumps = [], {}
    for s in (s for s in p.oneshots if s.endswith("-migrate")):
        db, want = s.removesuffix("-migrate"), p.target.manifest.images.get(s)
        if p.sources[0] and all(src and src.manifest.images.get(s) == want for src in p.sources):
            out.append(f"SKIP dump of database {db}: {s} runs {want}, the image {p.sources[0].id} pinned, so no migration can have changed")
            continue
        c = container(ctx, "prefect-db")
        if c is None:
            raise Refused(f"{s} migrates database {db}, and project {ctx.project} has no prefect-db container to dump it from")
        backups = Path(compose_env(ctx).get("K2_BACKUP_DIR") or Path.home() / "k2-backup")
        path = backups / "deploy" / p.target.id / f"{db}-{now().replace(':', '')}.sql.gz"
        done = run("bash", str(ctx.home / "scripts/backup-lake.sh"), "--dump", c["Id"], db, str(path), env=compose_env(ctx), check=False)
        if done.returncode or not path.is_file():
            raise Refused(f"the dump of database {db} before {s} failed: {tail(done, 4)}")
        dumps[db] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        out.append(f"database {db} dumped before {s}: {path} sha256 {dumps[db]['sha256']}")
    if dumps:
        record["backups"] = dumps
    return out or ["no one-shot migrates a database"]


def ddl(ctx: Ctx, p: Plan) -> list[str]:
    """Step 4: the one-shots, the storage plan's namespaces, then the tables (all additive)."""
    plan = f"{model.RELEASES_MOUNT}/{p.target.id}/{model.STORAGE_PLAN}"
    out = []
    # The one-shots first: lake-init makes the warehouses the namespaces go into. It runs the TARGET's
    # script and policies (K2_LAKE_INIT_RELEASE) and reads its plan (K2_STORAGE_PLAN), not the pointer's,
    # which still names the previous release, or nothing yet.
    for s in p.oneshots:  # a fresh container each time, so an old exit is never read as this run's
        run(*compose(ctx, p.target, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", s),
            env={**compose_env(ctx), "K2_STORAGE_PLAN": plan, "K2_LAKE_INIT_RELEASE": p.target.id})
        out.append(f"one-shot {s} exited 0 in {wait_service(ctx, s, True):.1f} s")
    out += tools(ctx, p.target, "namespaces", CATALOG_URI, plan).splitlines() or ["every namespace exists"]
    return out + compare_ddl(ctx, p) + create_tables(ctx, p, late=False)


def deploy(ctx: Ctx, rid: str, plan_only: bool) -> int:
    target = open_release(ctx, rid)
    good, since = resolve(ctx)
    frm = good.id if good else None
    sources: list[Release | None] = [good] + [open_release(ctx, r) for r in since if r not in (rid, frm)]
    if rid in since and good is not None:  # an attempt of this very release was interrupted
        ctx.say(f"deploy: an earlier attempt at {rid} did not finish; converging from {frm}")
    # Some attempt since the good release went past the one-shots: views, the canary, maybe the swap (a
    # kill after a step's work leaves no record of it, so any later step counts, not only swap).
    past_ddl = any(r["step"] not in (*UNCHANGING, "ddl") for r in after_good(history(ctx.root)))
    svc = set(services(target))
    plans: list[Plan] = []

    def pre() -> None:
        plans.append(make_plan(ctx, target, sources))
        preflight(ctx, plans[0])

    step(ctx, target, frm, "preflight", svc, pre)
    p = plans[0]
    step(ctx, target, frm, "images", svc, lambda: ensure_images(ctx, target))
    step(ctx, target, frm, "plan", svc, lambda: show(ctx, p, frm) or [f"drain: {drain_need(p)[1]}"])
    if plan_only:
        ctx.say("deploy: PLAN=1: stopped before any change")
        return EXIT_OK
    dumped: dict[str, Any] = {}
    step(ctx, target, frm, "backup", svc, lambda: migration_dumps(ctx, p, dumped), extra=dumped)
    step(ctx, target, frm, "drain", svc, lambda: drain(ctx, p, frm))
    try:
        step(ctx, target, frm, "ddl", svc, lambda: ddl(ctx, p))
    except Exception:  # nothing past the one-shots moved: the DAGs run again (a kill or an interrupt: the rerun's job)
        if past_ddl:  # review R8: the pointers may name the target already, so its DAG code would run on the old Trino
            ctx.say("deploy: the DAGs deploy paused stay paused: an earlier attempt went past the one-shots, so the "
                    f"pointers may already name a newer release ({ctx.root / DRAIN_STATE}); a deploy or rollback "
                    "that reaches done unpauses them")
        else:
            say_all(ctx, resume(ctx))
        raise
    try:
        converge(ctx, p, frm)
    except Exception as e:  # any failure from step 5 on rolls back (an interrupt does not: rerun or make rollback)
        ctx.say(f"deploy: {rid} failed after the views began to move: {e}")
        if good is None:  # between two releases the DAGs deploy paused stay paused (DRAIN_STATE)
            ctx.say("deploy: FAILED on a first deploy: there is no previous release to go back to; the pointers name "
                    f"{rid}. Fix the cause and deploy again (docs/runbooks/roll-back-a-release.md)")
            return EXIT_ROLLBACK_FAILED
        return EXIT_FAILED if rollback_to(ctx, good, [target, *(s for s in sources[1:] if s)], rid) else EXIT_ROLLBACK_FAILED
    ctx.say(f"deploy: {rid} is live and verified")
    say_all(ctx, resume(ctx))
    return EXIT_OK


def say_all(ctx: Ctx, lines: list[str]) -> None:
    for line in lines:
        ctx.say(f"  {line}")


def rollback_to(ctx: Ctx, good: Release, sources: list[Release], frm: str) -> bool:
    ctx.say(f"deploy: rolling back to {good.id}")
    # the forward deploy's faults are spent; a rollback step is broken only when named (kill:rollback-<step>)
    ctx.faults = {f for f in ctx.faults if f.startswith("kill:rollback-")}
    try:
        p = make_plan(ctx, good, list(sources))
        show(ctx, p, frm)
        ensure_images(ctx, good)
        down = not trino_answers(ctx)
        if down:
            ctx.say("deploy: Trino does not answer: the pointers move and the services are recreated first, then the views")
        converge(ctx, p, frm, prefix="rollback-", trino_down=down)
    except Exception as e:
        ctx.say(f"deploy: ROLLBACK FAILED: {e}. The platform is between {frm} and {good.id}: page the owner and "
                "follow docs/runbooks/roll-back-a-release.md")
        return False
    ctx.say(f"deploy: rolled back; {good.id} is live and verified")
    say_all(ctx, resume(ctx))  # a drained deploy's DAGs, or a dead one's (DRAIN_STATE)
    return True


def rollback(ctx: Ctx) -> int:
    goods, since = standing(history(ctx.root))
    if not goods:
        raise Refused("no good release in the deploy log to roll back to")
    if since:  # an unfinished attempt: an interrupted rollback is resumed, an interrupted deploy undone
        newest = [r for r in history(ctx.root) if r["step"] not in UNCHANGING][-1]
        target = newest["release"] if newest["step"].startswith("rollback-") else goods[-1]
        sources = since
    else:
        target = next((g for g in reversed(goods) if g != goods[-1]), "")
        sources = [goods[-1]]
        if not target:
            raise Refused(f"{goods[-1]} is the only good release in the deploy log")
    return EXIT_OK if rollback_to(ctx, open_release(ctx, target), [open_release(ctx, s) for s in sources if s != target],
                                  goods[-1]) else EXIT_ROLLBACK_FAILED


def verify_live(ctx: Ctx) -> int:
    good, _ = resolve(ctx)
    if good is None:
        raise Refused("no release is deployed under this root")
    goods, _ = standing(history(ctx.root))
    before = next((g for g in reversed(goods) if g != good.id), None)
    previous = model.canary_user(open_release(ctx, before).manifest.set_sha256) if before else None
    for line in [*verify(ctx, good, previous), *identity(ctx)]:  # identity.sh only here: it writes a probe line
        ctx.say(f"  {line}")
    ctx.say(f"release-verify: {good.id} everywhere")
    return EXIT_OK


def status(ctx: Ctx) -> int:
    goods, since = standing(history(ctx.root))
    say = ctx.say
    say(f"releases root {ctx.root}; good release {goods[-1] if goods else 'none'}"
        + (f"; UNFINISHED attempt since it: {', '.join(since)}" if since else ""))
    for p in POINTERS:
        link = ctx.root / "pointers" / p
        say(f"  pointer {p} -> {os.readlink(link) if link.is_symlink() else 'none'} (as the host sees it)")
    db = ctx.secrets / PASSWORD_DB
    canaries = [x.split(":", 1)[0] for x in db.read_text().splitlines() if x.startswith("k2_release_")] if db.exists() else []
    say(f"  canary lines in {PASSWORD_DB}: {', '.join(canaries) or 'none'}")
    say(f"  DAGs a deploy paused and has not yet unpaused: {', '.join(sorted(paused_by_deploy(ctx))) or 'none'}")
    kept = sorted(p.name for p in ctx.root.iterdir() if p.is_dir() and p.name.startswith("v")) if ctx.root.is_dir() else []
    say(f"  releases kept: {', '.join(kept) or 'none'}")
    if goods:  # a deploy's rollback target, as its preflight checks it (review R1 H3)
        good = open_release(ctx, goods[-1])
        gone = missing_images(good)
        say(f"  rollback images of {good.id}: " + (f"MISSING {', '.join(f'{s} {r}' for s, r in gone)}; {IMAGE_FIX}" if gone
                                                   else f"all {len(good.manifest.images)} on this host"))
    for r in history(ctx.root)[-12:]:
        say(f"  {r['at']} {r['release']} {r['step']} {r['outcome']} (from {r['from']}, by {r['operator']})")
    return EXIT_OK


# --- make release ------------------------------------------------------------------------

LOCAL = re.compile(r"^(k2-airflow|k2-capture|k2-spark-iceberg|tools|fetch|dbt-runner|k2-api)$")


def registry_ref(image: str) -> str:
    """repo@sha256:<digest> for a registry image, pulled first when the host lacks it."""
    if "@sha256:" in image:
        return image
    if "$" in image:
        raise Refused(f"{image}: an image named through a variable cannot be pinned")
    if image_id(image) is None:
        run("docker", "pull", "-q", image)
    repo = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    digests = json.loads(run("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image).stdout) or []
    short = repo.removeprefix("docker.io/").removeprefix("library/")
    match = [d for d in digests if d.split("@")[0].removeprefix("docker.io/").removeprefix("library/") == short]
    if not match:
        raise Refused(f"{image}: no registry digest on this host (a local image under a registry's name?)")
    return match[0]


def held_ref(iid: str, image: str, local: str) -> str:
    """The ref of a running container's image: a local build as <name>:<id12>, tagged here; a registry
    image by the digest the host recorded for its repository."""
    if LOCAL.match(local):
        ref = f"{local}:{iid.removeprefix('sha256:')[:12]}"
        run("docker", "tag", iid, ref)
        return ref
    name = image.split("@")[0]  # the digest off first: a ref may carry a tag and a digest (<repo>:<tag>@sha256:...)
    repo = name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name
    short = repo.removeprefix("docker.io/").removeprefix("library/")
    digests = json.loads(run("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", iid).stdout) or []
    match = [d for d in digests if d.split("@")[0].removeprefix("docker.io/").removeprefix("library/") == short]
    if not match:
        raise Refused(f"{image}: the running image {iid[:19]} has no registry digest for {repo} on this host")
    return match[0]


def build_images(ctx: Ctx, tree: Path, compose_doc: dict[str, Any]) -> dict[str, str]:
    """Every service's image ref: a registry image by digest; a local build built from the tag's tree,
    without touching the tag compose gives it, and tagged <name>:<image id 12>."""
    refs: dict[str, str] = {}
    built: dict[str, str] = {}  # compose image name -> ref
    svcs = compose_doc.get("services") or {}
    running = {c["Config"]["Labels"].get("com.docker.compose.service"): c for c in containers(ctx) if c["State"]["Running"]}
    for name, spec in sorted(svcs.items(), key=lambda kv: "build" not in kv[1]):  # builders first
        image = str(spec.get("image") or "")
        local = image.split(":")[0]
        if not k2release.recreatable(name) and name in running:
            # Deploy never recreates it (A1), so the release names the image it runs, not a new build
            # or tag: a capture keeps running through a cutover, and a change to one is its own operation.
            refs[name] = held_ref(running[name]["Image"], image, local)
            ctx.say(f"release: {name} keeps the image it runs, {refs[name]}: deploy never recreates it")
            if "build" not in spec and not LOCAL.match(local) and (tagged := registry_ref(image)) != refs[name]:
                ctx.say(f"release: WARNING {name}: the tag names {tagged}, not what runs; moving it is its own "
                        f"operation: IMAGES=<file naming it>, then ALLOW_STATEFUL={name}")
            continue
        if "build" in spec:
            if not LOCAL.match(local):
                raise Refused(f"{name}: a local build must be named one of {LOCAL.pattern} (manifest.schema.json)")
            if image not in built:
                b = spec["build"] if isinstance(spec["build"], dict) else {"context": spec["build"]}
                context = tree / b.get("context", ".")
                args = b.get("args") or {}
                args = dict(a.split("=", 1) for a in args) if isinstance(args, list) else args
                # Reproducible: no provenance attestation (it records the build's time and paths), every
                # timestamp in the image at SOURCE_DATE_EPOCH, and the tree's at 0 (compile_release), so one
                # tree builds one image id and a release that changes no image recreates no service.
                # Tagged while built: the containerd image store keeps no untagged build output.
                cmd = ["docker", "build", "-q", "--provenance=false", "--build-arg", "SOURCE_DATE_EPOCH=0",
                       "-t", f"{local}:k2-release-build", "-f", str(context / b.get("dockerfile", "Dockerfile")),
                       *[x for k, v in args.items() for x in ("--build-arg", f"{k}={v}")],
                       *(["--target", b["target"]] if b.get("target") else []), str(context)]
                ctx.say(f"release: building {local} for {name} from the tag")
                run(*cmd)
                iid = (image_id(f"{local}:k2-release-build") or "").removeprefix("sha256:")
                built[image] = f"{local}:{iid[:12]}"
                run("docker", "tag", f"{local}:k2-release-build", built[image])
                run("docker", "rmi", f"{local}:k2-release-build")  # the tag only; the image keeps its id tag
            refs[name] = built[image]
        elif image in built:
            refs[name] = built[image]
        elif LOCAL.match(local):
            raise Refused(f"{name}: image {image} is a local name no service of the tag builds")
        elif image:
            refs[name] = registry_ref(image)
    return refs


def compile_release(ctx: Ctx, tag: str, images: Path | None) -> str:
    if run("git", "-C", str(ctx.home), "cat-file", "-t", f"refs/tags/{tag}", check=False).stdout.strip() != "tag":
        raise Refused(f"{tag}: not an annotated tag in {ctx.home}")
    tree = ctx.tmp / "tree"
    tree.mkdir()
    archive = subprocess.run(["git", "-C", str(ctx.home), "archive", "--format=tar", f"refs/tags/{tag}"], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
    for path in [tree, *tree.rglob("*")]:  # git archive stamps each tag's commit time: one content, one time
        os.utime(path, (0, 0), follow_symlinks=False)
    doc = yaml.safe_load((tree / model.COMPOSE).read_bytes()) or {}
    refs = json.loads(images.read_text()) if images else build_images(ctx, tree, doc)
    if "tools" not in refs:
        raise Refused("the tag's compose has no tools service, which compiles and deploys a release")
    good, _ = resolve(ctx)
    gitdir = Path(run("git", "-C", str(ctx.home), "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    ctx.root.mkdir(parents=True, exist_ok=True)  # created once, here, by the host user: never by docker as root
    # Only the git directory and the root are mounted: Docker Desktop shares the home directory, not /tmp.
    out = run("docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
              "-v", f"{gitdir}:/repo.git:ro", "-v", f"{ctx.root}:{model.RELEASES_MOUNT}",
              "-e", f"K2_IMAGES={json.dumps(refs)}", refs["tools"], "sh", "-c",
              'printf %s "$K2_IMAGES" > /tmp/images.json && k2gen docset --tag "$1" --repo /repo.git --out /tmp/set && '
              'k2gen release --set /tmp/set --previous "$2" --images /tmp/images.json --out /k2/releases',
              "sh", tag, f"{model.RELEASES_MOUNT}/{good.id}" if good else "none", check=False)
    if out.returncode:  # the checks library's findings come first on stderr: name them, not the traceback
        found = [x for x in out.stderr.splitlines() if x.startswith(("error ", "warning "))]
        raise Refused(f"{tag} does not compile: " + ("; ".join(found) or tail(out, 6)))
    for line in out.stderr.strip().splitlines():
        ctx.say(f"  {line}")
    rid = out.stdout.strip().splitlines()[-1]
    # Docker Desktop keeps a chmod made in a container in its own metadata, so the host still sees the
    # tree writable: seal it here as well, the compiler's way, then check it as a deploy will.
    k2release._seal(ctx.root / rid)
    open_release(ctx, rid)  # sealed, and every file as its manifest says, seen from the host
    ctx.say(f"release: {rid} (needs against {good.id if good else 'nothing: a first release'})")
    print(rid)
    return rid


MEANING = {EXIT_OK: "done", EXIT_FAILED: "failed; back on the previous release, or nothing to undo",
           EXIT_REFUSED: "refused before any change", EXIT_ROLLBACK_FAILED: "the rollback failed too: page the owner"}


def main(argv: list[str] | None = None) -> int:
    """The exit code, also printed as the last line: make reports any failed recipe as its own 2."""
    code = _main(argv)
    print(f"deploy: exit {code}: {MEANING[code]}", file=sys.stderr)
    return code


def _main(argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("release")
    r.add_argument("--tag", required=True)
    r.add_argument("--images", type=Path)
    d = sub.add_parser("deploy")
    d.add_argument("release")
    d.add_argument("--plan", action="store_true")
    d.add_argument("--allow-stateful", default="")
    sub.add_parser("verify")
    rb = sub.add_parser("rollback")
    rb.add_argument("--allow-stateful", default="")
    sub.add_parser("status")
    df = sub.add_parser("diff")
    df.add_argument("release")
    df.add_argument("--from", dest="frm", default="")
    al = sub.add_parser("access-live")  # make access-verify LIVE=1
    al.add_argument("release")
    al.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="k2-deploy-") as tmp:
        ctx = ctx_from_env(Path(tmp))
        ctx.say = lambda line: print(line, file=sys.stderr, flush=True)
        try:
            if not ctx.project:
                raise Refused("K2_COMPOSE_PROJECT is set but empty: set it to the project the running containers carry "
                              "(docker inspect k2-trino --format '{{index .Config.Labels \"com.docker.compose.project\"}}'), "
                              "or unset it for this checkout directory's name")
            if args.cmd == "status":
                return status(ctx)
            if args.cmd == "diff":
                frm = args.frm or (resolve(ctx)[0] or open_release(ctx, args.release)).id
                print(k2diff.diff(ctx.root / frm, ctx.root / args.release))
                return EXIT_OK
            if args.cmd == "access-live":  # no lock: a deploy's verify runs it too
                done = access_live(ctx, open_release(ctx, args.release), args.out)
                sys.stdout.write(done.stdout)
                sys.stderr.write(done.stderr)
                return done.returncode
            with lock(ctx.root):
                if args.cmd == "release":
                    compile_release(ctx, args.tag, args.images)
                    return EXIT_OK
                if args.cmd == "verify":
                    return verify_live(ctx)
                if args.cmd in ("deploy", "rollback"):
                    ctx.allow_stateful = {s for s in args.allow_stateful.split(",") if s}
                if args.cmd == "rollback":
                    return rollback(ctx)
                rid = args.release
                if not (ctx.root / rid).is_dir():  # a tag: compile it first, as `make release` would
                    with tempfile.TemporaryDirectory(prefix="k2-release-") as rtmp:
                        rid = compile_release(dataclasses.replace(ctx, tmp=Path(rtmp)), rid, None)
                return deploy(ctx, rid, args.plan)
        except Refused as e:
            ctx.say(f"deploy: REFUSED: {e}")
            return EXIT_REFUSED
        except Failed as e:
            ctx.say(f"deploy: FAILED: {e}")
            return EXIT_FAILED
        except Exception as e:  # before step 5 nothing needs undoing; from it on, deploy() has rolled back
            ctx.say(f"deploy: FAILED: {type(e).__name__}: {e}")
            return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
