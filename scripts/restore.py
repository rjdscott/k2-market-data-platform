#!/usr/bin/env python3
"""make restore and make restore-drill (plan 004, steps W0-10 and W1-16): the lake back from the newest
complete run of `make backup`, every product held while it comes back. What it restores is generated
from the live release's compiled/lake/backup-set.json, entry by entry, as the backup copied it.

    restore.py run   [--run <stamp>] [stack flags]  # make restore: on the host, the stack's .env loaded
    restore.py drill [stack flags]                  # make restore-drill: the damage below, then run, timed
    restore.py lake  --phase platform|rest --release <dir> --run-dir <dir>   # in the tools image, on k2-catalog
    restore.py holds hold|release --release <dir> --run <stamp> --trino <host:port> --password-file <f> --ca <f>
    restore.py drift --release <dir> --recreated <json list>                 # in the tools image, on k2-catalog

The writers, each named before it writes, none of them MinIO's root pair:
- MinIO: the restore key, K2_RESTORE_S3_ACCESS_KEY and _SECRET_KEY (docker/lake/minio/policies/restore.json:
  get and put on every warehouse prefix and on k2-landing, list there, no delete). No other engine key
  writes every warehouse, and each of those can delete. The key reaches the in-network container by name.
- The catalog: Lakekeeper's REST API, which has no authentication, from the catalog's network only.
- The holds: svc.operator (k2gen.model.HOLDS_WRITER), from the host on localhost:${K2_TRINO_PORT:-8443}, its
  password file ${K2_SECRETS}/principals/svc.operator.password host-held, as scripts/hold.py connects.
  Their reads, and the run records', are svc.runs's, in the in-network container (its password mounted
  read-only, as every dbt-runner one-shot mounts it).
- The catalog database: its dump replayed by psql through `docker exec` into the Postgres container,
  Lakekeeper stopped meanwhile. LAKEKEEPER_ENCRYPTION_KEY is the one the Lakekeeper container already
  has in its environment: the dump's warehouse credentials are encrypted with it, so this restores onto
  the same host's stack only. A restore after the loss of the host (that key, .env, ${K2_SECRETS}) is
  NOT proven by anything here.

`run`, in order, stopping at the first refusal (exit 2) or failure (exit 1):
 1. the run: the newest complete one under ${K2_BACKUP_DIR}/runs (refused: a --run that is not the newest,
    a .partial, one that `make backup-verify`'s check finds incomplete, one whose tables and landings are
    not the live backup set's);
 2. every DAG of the live release's dags.json paused;
 3. the catalog database from the run's dump, only when it is lost (no warehouse row); never over a live one;
 4. `lake --phase platform`: refused unless every `restore` table the catalog holds sits at the location
    the backup holds; then the run records' namespace (the holds and the run record) put back, since the
    holds can be written only once their table is whole (phase-001-interfaces.md, Holds);
 5. a `restore` hold on every product, as svc.operator;
 6. `lake --phase rest`: refused unless every product is held; each `restore` table's files back at their
    own keys (Iceberg metadata holds absolute paths), never overwriting, and the table registered, or
    re-pointed (dropped without purge, registered again) when the catalog's entry is the backup's or older,
    or broken; kept when it is newer than the backup and whole; each landing's objects back; each `rebuild`
    table whose files are gone dropped without purge and recreated empty with its schema, partitioning,
    sort order and properties, left for its rebuild (make rebuild PRODUCT=...); a whole one kept;
 7. the drift check with the run records (catalog_drift's newest_audited, as `catalog_drift.py --runs`):
    each audited tag equal to its newest audited run record, nothing else different but what step 6 left
    for rebuild; else exit 1, every hold kept and every DAG left paused;
 8. the holds released and the DAGs unpaused; the elapsed time printed.
`drill` empties the lake first, with MinIO's root pair from the environment in MinIO's own image (the
damage, not the restore): the catalog database dropped and made empty, and every object under
warehouse/k2/, warehouse/governance/ and k2-landing deleted; the domain warehouses are left, so their
audited tags come back from the dump and are held to the run records. It refuses without
CONFIRM=<the live release id>. The releases root is never restored here (runbook section 3c).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(HERE / "verify"), str(ROOT / "platform" / "runs")]
import backup_lake as bl  # noqa: E402  (complete_runs, check, split, plain: the backup's own reading of a run)
import catalog_drift as cd  # noqa: E402  (call, Catalog, Unreachable: the catalog client)

OK, FAILED, REFUSED = 0, 1, 2
KEY = ("K2_RESTORE_S3_ACCESS_KEY", "K2_RESTORE_S3_SECRET_KEY")
PLATFORM = "governance.run_records."  # the run records' namespace: the holds and the run record (model.PLATFORM_SQL)
OPERATOR, RUNS = "svc.operator", "svc.runs"
BY = "make restore"
CATALOG = cd.CATALOG_URI
MOUNT = {"release": "/k2/releases", "backup": "/backup", "runs": "/run/k2-secrets/runs.password",
         "ca": "/run/k2-secrets/tls/ca.pem", "operator": "/run/k2-secrets/operator.password"}
DRILL_PREFIXES = ("k2-lake/warehouse/k2/", "k2-lake/warehouse/governance/", "k2-landing/")


class Refused(Exception):
    """Nothing in the lake changed for this reason; exit 2."""


class Failed(Exception):
    """A step did not complete; exit 1."""


# ---- 1. the run ---------------------------------------------------------------------------------

def set_problems(backup_set: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """How the run's tables and landings differ from what the live backup set names (each table by
    name and mode, each landing by bucket and prefix)."""
    want = {e["table"]: e["mode"] for e in backup_set["entries"] if e["kind"] == "table"}
    have = {t["table"]: "restore" for t in manifest["tables"]} | {n: "rebuild" for n in manifest["rebuild"]}
    out = [f"{t}: the live backup set says {want[t]}, the run {have[t]}" for t in sorted(want.keys() & have.keys()) if want[t] != have[t]]
    out += [f"{t}: in the live backup set, not in the run" for t in sorted(want.keys() - have.keys())]
    out += [f"{t}: in the run, not in the live backup set" for t in sorted(have.keys() - want.keys())]
    lw = {(e["bucket"], e["prefix"]) for e in backup_set["entries"] if e["kind"] == "landing"}
    lh = {(g["bucket"], g["prefix"]) for g in manifest["landings"]}
    out += [f"landing s3://{b}/{p}: in the live backup set, not in the run" for b, p in sorted(lw - lh)]
    out += [f"landing s3://{b}/{p}: in the run, not in the live backup set" for b, p in sorted(lh - lw)]
    return out


def choose_run(backup: Path, backup_set: dict[str, Any], want: str | None) -> tuple[Path, dict[str, Any]]:
    """The run to restore and its manifest: the newest complete one, verified. Refused otherwise."""
    runs = bl.complete_runs(backup / "runs")
    if not runs:
        raise Refused(f"no complete backup run under {backup / 'runs'}")
    newest = runs[-1]
    if want and want != newest.name:
        if want in {r.name for r in runs}:
            raise Refused(f"run {want} is older than the newest complete run {newest.name}: only the newest is restored")
        raise Refused(f"run {want} is not a complete run under {backup / 'runs'} (a .partial never finished)")
    _, bad = bl.check(backup, backup_set)
    if bad:
        raise Refused(f"run {newest.name} is incomplete: " + "; ".join(bad[:5]))
    manifest = json.loads((newest / "manifest.json").read_text())
    if bad := set_problems(backup_set, manifest):
        raise Refused(f"run {newest.name} does not hold what the live backup set names: " + "; ".join(bad[:5]))
    return newest, manifest


# ---- storage: put back, never overwrite ---------------------------------------------------------

def pair() -> tuple[str, str]:
    if missing := [n for n in KEY if not os.environ.get(n)]:
        raise Failed(f"no restore storage key: {' and '.join(missing)} not set (make engine-keys)")
    return os.environ[KEY[0]], os.environ[KEY[1]]


def s3(method: str, uri: str, body: bytes | None = None) -> tuple[int, Any]:
    """(status, headers) of one signed request with the restore key; the key is never printed."""
    av, (bucket, key) = bl._av(), bl.split(uri)
    path = urllib.parse.quote(f"/{bucket}/{key}", safe="/-_.~")
    url = urllib.parse.urlsplit(bl.endpoint())
    headers = av.sigv4(method, url.netloc, path, "", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), pair(),
                       hashlib.sha256(body).hexdigest() if body is not None else av.EMPTY_SHA256)
    if body is not None:
        headers |= {"If-None-Match": "*", "Content-Type": "application/octet-stream"}  # MinIO refuses a PUT over an object
    req = urllib.request.Request(bl.endpoint().rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers
    except OSError as e:
        raise Failed(f"{uri}: cannot reach the storage ({type(e).__name__})") from None


def head(uri: str) -> int | None:
    """The object's size, or None when the store has none at that key."""
    status, h = s3("HEAD", uri)
    if status == 404:
        return None
    if status != 200:
        raise Failed(f"{uri}: storage answered HTTP {status} to HEAD")
    return int(h["Content-Length"])


def put(uri: str, data: bytes) -> None:
    status, _ = s3("PUT", uri, data)
    if status == 412 and head(uri) == len(data):
        return  # put by another between the HEAD and the PUT, at the same size
    if status != 200:
        raise Failed(f"{uri}: storage answered HTTP {status} to PUT")


def put_back(files: dict[str, int], run: Path, head: Callable[[str], int | None],
             put: Callable[[str, bytes], None]) -> tuple[int, int]:
    """Each file of the run back at its own bucket and key: (put, already there). An object present
    at another size is never overwritten: the restore stops."""
    n = there = 0
    for rel, size in sorted(files.items()):
        uri = "s3://" + rel.removeprefix("objects/")
        have = head(uri)
        if have == size:
            there += 1
            continue
        if have is not None:
            raise Failed(f"{uri}: the store holds {have} bytes there, the backup {size}; never overwritten")
        data = (run / rel).read_bytes()
        if len(data) != size:
            raise Failed(f"{run / rel}: {len(data)} bytes, the manifest says {size}")
        put(uri, data)
        n += 1
    return n, there


# ---- the catalog ----------------------------------------------------------------------------------

class Lake:
    """The tables of the catalog, by <warehouse>.<namespace>.<table>, over its REST API."""

    def __init__(self, uri: str = CATALOG):
        self.uri, self._base = uri, {}

    def base(self, wh: str) -> str:
        if wh not in self._base:
            self._base[wh] = cd.Catalog(self.uri, wh).base
        return self._base[wh]

    def _path(self, table: str) -> tuple[str, str, str]:
        wh, ns, t = table.split(".")
        return self.base(wh), urllib.parse.quote(ns, safe=""), urllib.parse.quote(t, safe="")

    def load(self, table: str) -> dict[str, Any] | None:
        b, ns, t = self._path(table)
        st, doc = cd.call("GET", f"{b}/namespaces/{ns}/tables/{t}")
        if st == 404:
            return None
        if st != 200:
            raise Failed(f"{table}: the catalog answered HTTP {st} to its load")
        return doc

    def register(self, table: str, metadata: str) -> None:
        b, ns, t = self._path(table)
        st, _ = cd.call("POST", f"{b}/namespaces", {"namespace": [table.split(".")[1]]})
        if st not in (200, 409):
            raise Failed(f"{table}: the catalog answered HTTP {st} to its namespace")
        st, _ = cd.call("POST", f"{b}/namespaces/{ns}/register", {"name": table.split(".")[2], "metadata-location": metadata})
        if st != 200:
            raise Failed(f"{table}: the catalog answered HTTP {st} to its register at {metadata}")

    def drop(self, table: str) -> None:
        """Out of the catalog without purge: no file is deleted."""
        b, ns, t = self._path(table)
        st, _ = cd.call("DELETE", f"{b}/namespaces/{ns}/tables/{t}?purgeRequested=false")
        if st not in (200, 204):
            raise Failed(f"{table}: the catalog answered HTTP {st} to its drop")

    def create(self, table: str, meta: dict[str, Any]) -> None:
        """An empty table of `meta`'s current schema, default partition spec, sort order and properties."""
        b, ns, _ = self._path(table)
        pick = lambda key, ident, cur: next(x for x in meta[key] if x[ident] == meta[cur])  # noqa: E731
        body = {"name": table.split(".")[2], "schema": pick("schemas", "schema-id", "current-schema-id"),
                "partition-spec": pick("partition-specs", "spec-id", "default-spec-id"),
                "write-order": pick("sort-orders", "order-id", "default-sort-order-id"),
                "properties": meta.get("properties") or {}, "stage-create": False}
        st, doc = cd.call("POST", f"{b}/namespaces/{ns}/tables", body)
        if st != 200:
            raise Failed(f"{table}: the catalog answered HTTP {st} to its create ({str(doc.get('error', ''))[:200]})")


def snapshot_at(meta: dict[str, Any], ref: str) -> int | None:
    sid = ((meta.get("refs") or {}).get(ref) or {}).get("snapshot-id")
    if sid is None and ref == "main" and meta.get("current-snapshot-id") not in (None, -1):
        sid = meta["current-snapshot-id"]
    return sid


def whole(entry: dict[str, Any], head: Callable[[str], int | None]) -> bool:
    """The catalog's entry readable from the store: its metadata file there, and its main snapshot's
    manifest list (a lost prefix takes both)."""
    meta = entry["metadata"]
    if head(entry["metadata-location"]) is None:
        return False
    sid = snapshot_at(meta, "main")
    snap = next((s for s in meta.get("snapshots") or [] if s["snapshot-id"] == sid), None)
    return sid is None or (snap is not None and head(snap["manifest-list"]) is not None)


def lake_problems(tables: Iterable[dict[str, Any]], load: Callable[[str], dict[str, Any] | None]) -> list[str]:
    """Each `restore` table the catalog holds at another location than the backup's: another table by
    that name, which putting files back would not make whole and re-pointing would replace."""
    out = []
    for t in tables:
        cat = load(t["table"])
        if cat and cat["metadata"]["location"].rstrip("/") != t["location"].rstrip("/"):
            out.append(f"{t['table']}: the catalog's is at {cat['metadata']['location']}, the backup's at {t['location']}")
    return out


def restore_table(t: dict[str, Any], run: Path, lake: Any, head: Callable[[str], int | None],
                  put: Callable[[str, bytes], None]) -> str:
    n, there = put_back(t["files"], run, head, put)
    files = f"{n} file(s) put back, {there} already there"
    cat = lake.load(t["table"])
    if cat is None:
        lake.register(t["table"], t["metadata_location"])
        return f"{files}; registered at the backup's metadata"
    if cat["metadata-location"] == t["metadata_location"]:
        return f"{files}; in the catalog at the backup's metadata"
    backup = json.loads(bl.plain((run / ("objects/" + "/".join(bl.split(t["metadata_location"])))).read_bytes()))
    sid = snapshot_at(cat["metadata"], "main")
    if sid is None or sid in {s["snapshot-id"] for s in backup.get("snapshots") or []} or not whole(cat, head):
        lake.drop(t["table"])
        lake.register(t["table"], t["metadata_location"])
        return f"{files}; re-pointed from {cat['metadata-location']} to the backup's metadata"
    return f"{files}; the catalog's is newer than the backup and whole: kept"


def rebuild_table(name: str, lake: Any, head: Callable[[str], int | None]) -> tuple[str, bool]:
    """(the line, recreated)."""
    cat = lake.load(name)
    if cat is None:
        return "absent: its first build, or the deploy's DDL, makes it", False
    if whole(cat, head):
        return "whole: kept", False
    lake.drop(name)
    lake.create(name, cat["metadata"])
    return "its files are gone: dropped without purge, recreated empty, left for its rebuild", True


# ---- holds ------------------------------------------------------------------------------------------

def products(release: Path) -> list[str]:
    return sorted(d["product"] for d in json.loads((release / "compiled/airflow/dags.json").read_text())["dags"])


def dags(release: Path) -> list[str]:
    return sorted(d["dagId"] for d in json.loads((release / "compiled/airflow/dags.json").read_text())["dags"])


def unheld(names: Iterable[str], held: Callable[[str], Any]) -> list[str]:
    """The products no open hold covers (any kind: each makes the DAG's resolve refuse a run)."""
    return [p for p in names if not held(p)]


def k2runs_conn(user: str, password_file: str, trino: str | None = None, ca: str = MOUNT["ca"]) -> Any:
    import k2runs
    host, _, port = (trino or f"{os.environ.get('K2_TRINO_HOST', 'trino')}:{os.environ.get('K2_TRINO_INTERNAL_PORT', '8443')}").partition(":")
    return k2runs.connect(host=host, port=int(port or 8443), user=user, password_file=password_file, ca=ca)


# ---- the in-network subcommands --------------------------------------------------------------------

def run_lake(a: argparse.Namespace, lake: Any = None, held: Callable[[str], Any] | None = None,
             head_: Callable[[str], int | None] = head, put_: Callable[[str, bytes], None] = put) -> dict[str, Any]:
    release, run = Path(a.release), Path(a.run_dir)
    m = json.loads((run / "manifest.json").read_text())
    lake = lake or Lake()
    if bad := lake_problems(m["tables"], lake.load):
        raise Refused("the lake's tables are not the ones the backup holds: " + "; ".join(bad))
    platform = [t for t in m["tables"] if t["table"].startswith(PLATFORM)]
    if a.phase == "rest":
        if held is None:
            import k2runs
            conn = k2runs_conn(RUNS, MOUNT["runs"])
            held = lambda p: k2runs.held(conn, p)  # noqa: E731
        if missing := unheld(products(release), held):
            raise Refused(f"not every product is held, so a DAG could write while its tables come back: {', '.join(missing)} "
                          "(make restore holds them all first)")
    out: dict[str, Any] = {"phase": a.phase, "lines": [], "recreated": []}
    for t in platform if a.phase == "platform" else [t for t in m["tables"] if t not in platform]:
        out["lines"].append(f"table {t['table']}: {restore_table(t, run, lake, head_, put_)}")
    if a.phase == "rest":
        for g in m["landings"]:
            n, there = put_back(g["files"], run, head_, put_)
            out["lines"].append(f"landing s3://{g['bucket']}/{g['prefix']}: {n} object(s) put back, {there} already there")
        for name in m["rebuild"]:
            line, made = rebuild_table(name, lake, head_)
            out["lines"].append(f"rebuild {name}: {line}")
            out["recreated"] += [name] if made else []
    return out


def run_holds(a: argparse.Namespace, conn: Any = None) -> list[str]:
    import k2runs
    conn = conn or k2runs_conn(OPERATOR, a.password_file, a.trino, a.ca)
    out = []
    for p in products(Path(a.release)):
        try:
            if a.verb == "hold":
                k2runs.hold(conn, p, f"make restore of backup run {a.run}", BY, kind="restore")
                out.append(f"held {p} (restore)")
            else:
                k2runs.release(conn, p, BY, kind="restore")
                out.append(f"released {p} (restore)")
        except k2runs.HoldError as e:  # a restore hold an interrupted restore left open; or none to release
            out.append(f"{p}: {e}")
    return out


def touches(line: str, tables: Iterable[str]) -> bool:
    # ponytail: a drift line names a table by its dotted name; a new line shape that names it otherwise lands here
    return any(re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", line) for t in tables)


def classify(found: dict[str, list[str]], recreated: list[str]) -> tuple[list[str], list[str]]:
    """(differences that fail the restore, differences that are a recreated table awaiting its rebuild)."""
    left = [d for d in found["differences"] if touches(d, recreated)]
    return [d for d in found["differences"] if d not in left], left


def run_drift(a: argparse.Namespace) -> dict[str, Any]:
    found = cd.drift(CATALOG, cd.expected(Path(a.release)), newest_audited=cd.runs_reader(MOUNT["runs"], MOUNT["ca"]))
    bad, left = classify(found, json.loads(a.recreated))
    return {"bad": bad, "rebuild": left, "pending": found["pending"], "left": found["left"], "skipped": found["skipped"]}


# ---- the host side ---------------------------------------------------------------------------------

@dataclass
class Stack:
    releases: Path
    backup: Path
    secrets: Path
    network: str
    tools_image: str
    minio_image: str
    postgres: str = "k2-prefect-db"
    lakekeeper: str = "k2-lakekeeper"
    scheduler: str = "k2-airflow-scheduler"
    trino: str = "localhost:8443"  # the holds' Trino, from where svc.operator connects
    holds_in_network: bool = False  # a throwaway lake publishes no Trino port: the holds go in-network
    operator_password: Path | None = None  # default <secrets>/principals/svc.operator.password
    sh: Callable[..., subprocess.CompletedProcess] = field(default=subprocess.run, repr=False)


def docker_run(st: Stack, rid: str, *argv: str, operator: bool = False) -> list[str]:
    """`restore.py <argv>` in the release's tools image on the catalog's network, as this user."""
    m = [f"{st.releases}:{MOUNT['release']}:ro", f"{HERE}:/opt/k2/scripts:ro", f"{st.backup}:{MOUNT['backup']}:ro",
         f"{st.secrets / 'principals' / (RUNS + '.password')}:{MOUNT['runs']}:ro", f"{st.secrets / 'tls/ca.pem'}:{MOUNT['ca']}:ro"]
    if operator:
        m.append(f"{operator_file(st)}:{MOUNT['operator']}:ro")
    return ["docker", "run", "--rm", "--network", st.network, "--user", f"{os.getuid()}:{os.getgid()}", "--cpus", "1",
            "--memory", "512m", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "-e", KEY[0], "-e", KEY[1], "-e", "K2_S3_ENDPOINT",
            "-e", "K2_TRINO_HOST=trino", "-e", "K2_TRINO_INTERNAL_PORT=8443", *(x for v in m for x in ("-v", v)),
            st.tools_image, "/opt/k2/venv/bin/python", "/opt/k2/scripts/restore.py", *argv]


def operator_file(st: Stack) -> Path:
    return st.operator_password or st.secrets / "principals" / (OPERATOR + ".password")


def last_json(done: subprocess.CompletedProcess, what: str) -> dict[str, Any]:
    lines = [x for x in (done.stdout or "").splitlines() if x.strip()]
    for x in (done.stderr or "").splitlines():
        if x.startswith("restore: "):
            print(x, file=sys.stderr)
    if done.returncode == REFUSED:
        raise Refused(f"{what}: " + ((done.stderr or "").strip().splitlines() or ["refused"])[-1].removeprefix("restore: REFUSED: "))
    if done.returncode != 0 or not lines:
        raise Failed(f"{what} exited {done.returncode}: {((done.stderr or '') + (done.stdout or '')).strip()[-400:]}")
    return json.loads(lines[-1])


def catalog_lost(st: Stack) -> bool:
    done = st.sh(["docker", "exec", st.postgres, "sh", "-c",
                  'psql -U "$POSTGRES_USER" -d lakekeeper -Atc "SELECT count(*) FROM warehouse"'], capture_output=True, text=True)
    return done.returncode != 0 or done.stdout.strip() in ("", "0")


def restore_db(st: Stack, dump: Path, say: Callable[[str], None]) -> None:
    sh = lambda *argv, **kw: st.sh(list(argv), check=True, capture_output=True, **kw)  # noqa: E731
    sh("docker", "stop", st.lakekeeper)
    sh("docker", "exec", st.postgres, "sh", "-c",
       'dropdb -U "$POSTGRES_USER" --force --if-exists lakekeeper && createdb -U "$POSTGRES_USER" lakekeeper')
    sh("docker", "exec", "-i", st.postgres, "sh", "-c", 'psql -U "$POSTGRES_USER" -d lakekeeper -v ON_ERROR_STOP=1 -q',
       input=gzip.decompress(dump.read_bytes()))
    sh("docker", "start", st.lakekeeper)
    for _ in range(60):
        if st.sh(["docker", "exec", st.lakekeeper, "/home/nonroot/lakekeeper", "healthcheck"], capture_output=True).returncode == 0:
            say(f"catalog database: replayed from {dump.name}; Lakekeeper healthy, with the encryption key its environment holds")
            return
        time.sleep(1)
    raise Failed("Lakekeeper did not report healthy after the catalog database was replayed")


def airflow(st: Stack, verb: str, dag: str) -> None:
    done = st.sh(["docker", "exec", st.scheduler, "airflow", "dags", verb, dag, "-y"], capture_output=True, text=True)
    if done.returncode != 0:
        raise Failed(f"airflow dags {verb} {dag} exited {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}")


def live_release(st: Stack) -> Path:
    rel = (st.releases / "pointers" / "trino").resolve()
    if not (rel / "compiled/lake/backup-set.json").is_file():
        raise Refused(f"no live release at {st.releases / 'pointers/trino'} with compiled/lake/backup-set.json")
    return rel


def restore(st: Stack, want: str | None, say: Callable[[str], None] = print) -> int:
    t0 = time.monotonic()
    pair()  # the restore key, by name, before anything moves
    rel = live_release(st)
    backup_set = json.loads((rel / "compiled/lake/backup-set.json").read_text())
    run, manifest = choose_run(st.backup, backup_set, want)
    say(f"restore: run {run.name} (release {manifest['release']}, tag {manifest['tag']}) onto live release {rel.name}")
    crel, crun = f"{MOUNT['release']}/{rel.name}", f"{MOUNT['backup']}/runs/{run.name}"
    paused = []
    for dag in dags(rel) if st.scheduler else []:
        airflow(st, "pause", dag)
        paused.append(dag)
    say(f"restore: {len(paused)} DAG(s) paused" if st.scheduler else "restore: no scheduler on this lake: no DAG to pause")
    try:
        if "lakekeeper-db" in {e["kind"] for e in backup_set["entries"]} and catalog_lost(st):
            restore_db(st, st.backup / manifest["dumps"]["lakekeeper"]["file"], say)
        else:
            say("catalog database: present, not replaced")
        for line in last_json(st.sh(docker_run(st, rel.name, "lake", "--phase", "platform", "--release", crel, "--run-dir", crun),
                                    capture_output=True, text=True), "lake --phase platform")["lines"]:
            say(f"  {line}")
    except Refused:
        for dag in paused:
            airflow(st, "unpause", dag)  # nothing written yet: the DAGs run again
        raise
    holds_step(st, rel, "hold", run.name, say)
    rest = last_json(st.sh(docker_run(st, rel.name, "lake", "--phase", "rest", "--release", crel, "--run-dir", crun),
                           capture_output=True, text=True), "lake --phase rest")
    for line in rest["lines"]:
        say(f"  {line}")
    d = last_json(st.sh(docker_run(st, rel.name, "drift", "--release", crel, "--recreated", json.dumps(rest["recreated"])),
                        capture_output=True, text=True), "drift")
    for line in d["rebuild"]:
        say(f"  REBUILD {line}")
    if d["bad"]:
        for line in d["bad"]:
            say(f"  {line}")
        say(f"restore: FAIL: {len(d['bad'])} difference(s) besides the tables left for rebuild; every hold kept, every DAG left paused")
        return FAILED
    say(f"restore: the audited tags equal the run records; {len(d['pending'])} pending, {len(rest['recreated'])} table(s) left for rebuild")
    holds_step(st, rel, "release", run.name, say)
    for dag in paused:
        airflow(st, "unpause", dag)
    say(f"restore: OK in {time.monotonic() - t0:.1f} s: run {run.name}" + (f"; rebuild next: {', '.join(rest['recreated'])}" if rest["recreated"] else ""))
    return OK


def holds_step(st: Stack, rel: Path, verb: str, run: str, say: Callable[[str], None]) -> None:
    """A restore hold on every product, or its release, as svc.operator: from the host (live, as
    scripts/hold.py connects), or in-network on a throwaway lake, which publishes no Trino port."""
    if st.holds_in_network:
        cmd = docker_run(st, rel.name, "holds", verb, "--release", f"{MOUNT['release']}/{rel.name}", "--run", run,
                         "--trino", "trino:8443", "--password-file", MOUNT["operator"], "--ca", MOUNT["ca"], operator=True)
    else:
        if not operator_file(st).is_file():
            raise Failed(f"no password file for {OPERATOR} at {operator_file(st)}: `make password PRINCIPAL={OPERATOR} TO_FILE=1`")
        cmd = [sys.executable, str(HERE / "restore.py"), "holds", verb, "--release", str(rel), "--run", run, "--trino", st.trino,
               "--password-file", str(operator_file(st)), "--ca", str(st.secrets / "tls/ca.pem")]
    for line in last_json(st.sh(cmd, capture_output=True, text=True), f"holds {verb}")["lines"]:
        say(f"  {line}")


def drill(st: Stack, confirm: str, say: Callable[[str], None] = print) -> int:
    rel = live_release(st)
    if confirm != rel.name:
        raise Refused(f"the drill empties the lake: CONFIRM={rel.name} (the live release) is required")
    t0 = time.monotonic()
    root = {n: os.environ.get(n, "") for n in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")}
    if not all(root.values()):
        raise Failed("the drill's damage needs MINIO_ROOT_USER and MINIO_ROOT_PASSWORD in the environment (.env loaded)")
    st.sh(["docker", "exec", st.postgres, "sh", "-c",
           'dropdb -U "$POSTGRES_USER" --force lakekeeper && createdb -U "$POSTGRES_USER" lakekeeper'], check=True, capture_output=True)
    q = urllib.parse.quote
    env = {**os.environ, "MC_HOST_k2": f"http://{q(root['MINIO_ROOT_USER'], safe='')}:{q(root['MINIO_ROOT_PASSWORD'], safe='')}@minio:9000"}
    for prefix in DRILL_PREFIXES:
        st.sh(["docker", "run", "--rm", "--network", st.network, "-e", "MC_CONFIG_DIR=/tmp/.mc", "-e", "MC_HOST_k2", "--entrypoint", "mc",
               st.minio_image, "rm", "--recursive", "--force", f"k2/{prefix}"], check=True, capture_output=True, env=env)
    say(f"drill: damage done in {time.monotonic() - t0:.1f} s: the catalog database emptied; every object under "
        f"{', '.join(DRILL_PREFIXES)} deleted")
    t1 = time.monotonic()
    code = restore(st, None, say)
    say(f"drill: restore {'passed' if code == OK else 'FAILED'} in {time.monotonic() - t1:.1f} s (the damage and restore {time.monotonic() - t0:.1f} s)")
    return code


def stack(a: argparse.Namespace) -> Stack:
    env, home = os.environ, Path.home()
    releases = Path(a.releases or env.get("K2_RELEASES") or home / "k2-releases")
    net = a.network
    if not net:
        nets = subprocess.run(["docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", "k2-minio"],
                              capture_output=True, text=True).stdout.split()
        net = next((n for n in nets if n.endswith("_k2-catalog")), "")
        if not net:
            raise Failed("k2-minio is not running on a *_k2-catalog network")
    image = a.tools_image or json.loads(((releases / "pointers/trino").resolve() / "manifest.json").read_text())["images"]["tools"]
    minio = a.minio_image or subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", "k2-minio"], capture_output=True, text=True).stdout.strip()
    return Stack(releases=releases, backup=Path(a.backup or env.get("K2_BACKUP_DIR") or home / "k2-backup").resolve(),
                 secrets=Path(a.secrets or env.get("K2_SECRETS") or home / "k2-secrets"), network=net, tools_image=image,
                 minio_image=minio, postgres=a.postgres, lakekeeper=a.lakekeeper, scheduler=a.scheduler,
                 trino=a.trino or f"localhost:{env.get('K2_TRINO_PORT', '8443')}", holds_in_network=a.holds_in_network,
                 operator_password=Path(a.operator_password) if a.operator_password else None)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="restore.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "drill"):
        p = sub.add_parser(name)
        p.add_argument("--run", help="the run's stamp; refused unless it is the newest complete run")
        for f in ("--releases", "--backup", "--secrets", "--network", "--tools-image", "--minio-image", "--trino", "--operator-password"):
            p.add_argument(f)
        p.add_argument("--postgres", default="k2-prefect-db")
        p.add_argument("--lakekeeper", default="k2-lakekeeper")
        p.add_argument("--scheduler", default="k2-airflow-scheduler", help="empty: a lake with no Airflow")
        p.add_argument("--holds-in-network", action="store_true", help="a throwaway lake: no Trino port on the host")
    lk = sub.add_parser("lake")
    lk.add_argument("--phase", choices=("platform", "rest"), required=True)
    lk.add_argument("--release", required=True)
    lk.add_argument("--run-dir", required=True)
    h = sub.add_parser("holds")
    h.add_argument("verb", choices=("hold", "release"))
    for f in ("--release", "--run", "--trino", "--password-file", "--ca"):
        h.add_argument(f, required=True)
    dr = sub.add_parser("drift")
    dr.add_argument("--release", required=True)
    dr.add_argument("--recreated", default="[]")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "lake":
            print(json.dumps(run_lake(a)))
        elif a.cmd == "holds":
            print(json.dumps({"lines": run_holds(a)}))
        elif a.cmd == "drift":
            print(json.dumps(run_drift(a)))
        elif a.cmd == "run":
            return restore(stack(a), a.run)
        else:
            return drill(stack(a), os.environ.get("CONFIRM", ""))
        return OK
    except Refused as e:
        print(f"restore: REFUSED: {e}", file=sys.stderr)
        return REFUSED
    except (Failed, cd.Unreachable, OSError, subprocess.CalledProcessError, KeyError, ValueError) as e:
        print(f"restore: FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
