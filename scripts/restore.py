#!/usr/bin/env python3
"""make restore and make restore-drill (plan 004, steps W0-10 and W1-16): the lake back from the newest
complete run of `make backup`, every product held while it comes back. What it restores is generated
from the live release's compiled/lake/backup-set.json, entry by entry, as the backup copied it.

    restore.py run   [--run <stamp>] [stack flags]  # make restore: on the host, the stack's .env loaded
    restore.py drill [stack flags]                  # make restore-drill: checks, the damage below, then run
    restore.py lake  --phase check|platform|rest --release <dir> --run-dir <dir> [--fresh]  # tools image, k2-catalog
    restore.py holds hold|release --release <dir> --run <stamp> --trino <host:port> --password-file <f> --ca <f>
                     [--plan <json>] [--keep <json>]
    restore.py drift --release <dir> --recreated <json list>                 # tools image, k2-catalog

The writers, each named before it writes, none of them MinIO's root pair:
- MinIO: the restore key, K2_RESTORE_S3_ACCESS_KEY and _SECRET_KEY (docker/lake/minio/policies/restore.json:
  get and put on every warehouse prefix and on k2-landing, list there, no delete), given by name to the
  two `lake` phases that write (platform, rest) and to nothing else. The read-only `check` phase reads
  with the backup key (K2_BACKUP_S3_*); `holds` and `drift` get no storage key.
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
 1. checks that touch nothing: both storage keys set, the live release's own files holding what the
    restore needs (its lake-init's restore user, the holds table), svc.operator's file there, no
    `make rebuild` one-shot running, and the run: the newest complete one under ${K2_BACKUP_DIR}/runs (refused: a --run
    that is not the newest, a .partial, one `make backup-verify`'s check finds incomplete, one whose
    tables and landings are not the live backup set's);
 2. every DAG of dags.json that is not paused already paused (and only those are ever unpaused here),
    then each drained as scripts/rebuild.py drains one: every task of a running run waited for; then
    `lake --phase key`: the restore key proven against MinIO before the first write (run_key says how);
 3. the catalog database from the run's dump, only when it is lost: the query answers no warehouse row,
    or Postgres says the database or its table does not exist; any other answer touches nothing;
 4. `lake --phase check`: refused unless every `restore` table the catalog holds sits at the location the
    backup holds; it plans each table's action (below) and so which products are left for rebuild: an
    owner of a table registered, re-pointed or recreated, and every product downstream of one by the
    release's compiled inputs (a downstream incremental table keeps a watermark from its input, which a
    full refresh of that input renumbers; candles' through_load_seq over canonical-trades' load_seq);
 5. `lake --phase platform`: the run records' namespace (the holds and the run record) put back, since
    the holds can be written only once their table is whole (phase-001-interfaces.md, Holds);
 6. a `restore` hold on every product, as svc.operator, refused while any product has a rebuild lock open;
    a product left for rebuild gets the reason scripts/rebuild.py's AWAITING names;
 7. `lake --phase rest`: refused unless every product has an open restore hold and none a rebuild lock;
    each `restore` table's files back at their own keys (Iceberg metadata holds absolute paths), never
    overwriting, and the table registered, or re-pointed (dropped without purge, registered again) when
    the catalog's entry is the backup's or older, or not whole; kept when newer than the backup and whole;
    each landing's objects back; each `rebuild` table not whole dropped without purge and recreated empty
    with its schema, partitioning, sort order and properties. Whole: its metadata file, and every manifest
    list, manifest, data and delete file of its main and audited snapshots, in one listing of its location;
 8. the drift check with the run records (catalog_drift's runs_reader): each audited tag equal to its
    newest audited run record, nothing else different but the tables recreated; else exit 1, every hold
    kept and every DAG left paused;
 9. the holds released but those of products left for rebuild, which only `make rebuild PRODUCT=...` ends
    once its full refresh passes (upstream first: the order is printed); the DAGs this restore paused
    unpaused but theirs. A refusal before any write unpauses what step 2 paused; after a catalog replay,
    or any failure, nothing is unpaused and the output says what is left.
`drill` checks before it damages, in order: every check of step 1; `lake --phase key`; `lake --phase check --fresh` (nothing
committed since the run: each restore table's catalog metadata the run's, each landing's listing the
run's, and no run record written since the run began; no rebuild lock open); when it destroys a rebuild table, the release's own k2-dbt taking
--full-refresh (scripts/rebuild.py's full_refresh_problem: its pinned dbt-runner image is asked); its statement of what it
destroys, refused without CONFIRM=<the live release id>; step 2; the fresh check again, now drained; a
restore hold on every product. Then the damage, with MinIO's root pair from the environment in MinIO's
own image (the damage, not the restore): the catalog database dropped and made empty, and every object
deleted at each restore table's location, each landing prefix, and each `rebuild` table's location in
warehouses k2 and governance that `make rebuild` can rebuild (a file product's bronze is its parser's,
so it is left). Then `run` from step 3. Every rebuild table it destroys is named in the statement: the
run does not hold it, and its product and everything downstream stay held until rebuilt. The domain
warehouses are left, so their audited tags come back from the dump and are held to the run records. The
releases root is never restored here (runbook section 3c).
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(HERE / "verify"), str(ROOT / "platform" / "runs")]
import backup_lake as bl  # noqa: E402  (complete_runs, check, split, plain, ls, snapshot_files: the backup's reading)
import catalog_drift as cd  # noqa: E402  (call, Catalog, Unreachable: the catalog client)
import rebuild as rb  # noqa: E402  (drain, Live, oneshot_name, open_holds, AWAITING, inputs: make rebuild's)

OK, FAILED, REFUSED = 0, 1, 2
KEY = ("K2_RESTORE_S3_ACCESS_KEY", "K2_RESTORE_S3_SECRET_KEY")  # writes: the platform and rest phases
READ_KEY = ("K2_BACKUP_S3_ACCESS_KEY", "K2_BACKUP_S3_SECRET_KEY")  # reads only: the check phase
PLATFORM = "governance.run_records."  # the run records' namespace: the holds and the run record (model.PLATFORM_SQL)
OPERATOR, RUNS = "svc.operator", "svc.runs"
BY = "make restore"
CATALOG = cd.CATALOG_URI
MOUNT = {"release": "/k2/releases", "backup": "/backup", "runs": "/run/k2-secrets/runs.password",
         "ca": "/run/k2-secrets/tls/ca.pem", "operator": "/run/k2-secrets/operator.password"}
DRILL_WAREHOUSES = ("k2", "governance")  # the drill destroys their tables; the domain warehouses are left
CHANGED = ("register", "re-point", "recreate")  # what leaves a product for rebuild
LAST = "governance.quality-results"  # reads every builder's results, which no input port declares: rebuilt last


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


def stamp_time(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


# ---- storage --------------------------------------------------------------------------------------

def pair(names: tuple[str, str] = KEY) -> tuple[str, str]:
    if missing := [n for n in names if not os.environ.get(n)]:
        raise Failed(f"no storage key: {' and '.join(missing)} not set (make engine-keys)")
    return os.environ[names[0]], os.environ[names[1]]


def split_prefix(uri: str) -> tuple[str, str]:
    """s3://bucket/some/prefix/ -> (bucket, some/prefix/), refused unless plain."""
    bucket, key = bl.split(uri.rstrip("/") + "/x")
    return bucket, key[:-1]


class Store:
    """The lake's objects with one key: listed once per prefix, read, and put without overwriting."""

    def __init__(self, names: tuple[str, str]):
        self.names, self._lists = names, {}

    def _request(self, method: str, uri: str, body: bytes | None = None) -> tuple[int, bytes]:
        av, (bucket, key) = bl._av(), bl.split(uri)
        path = urllib.parse.quote(f"/{bucket}/{key}", safe="/-_.~")
        url = urllib.parse.urlsplit(bl.endpoint())
        headers = av.sigv4(method, url.netloc, path, "", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), pair(self.names),
                           hashlib.sha256(body).hexdigest() if body is not None else av.EMPTY_SHA256)
        if body is not None:
            headers |= {"If-None-Match": "*", "Content-Type": "application/octet-stream"}  # MinIO refuses a PUT over an object
        req = urllib.request.Request(bl.endpoint().rstrip("/") + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except OSError as e:
            raise Failed(f"{uri}: cannot reach the storage ({type(e).__name__})") from None

    def list(self, prefix: str) -> dict[str, int]:
        """{s3 uri: size} of every object under the prefix (a table's location, a landing)."""
        prefix = prefix.rstrip("/") + "/"
        if prefix not in self._lists:
            bucket, key = split_prefix(prefix)
            try:
                self._lists[prefix] = {f"s3://{bucket}/{k}": n for k, n in bl.ls(bucket, key, pair(self.names))}
            except bl.Failed as e:
                raise Failed(str(e)) from None
        return self._lists[prefix]

    def get(self, uri: str) -> bytes:
        status, data = self._request("GET", uri)
        if status != 200:
            raise Failed(f"{uri}: storage answered HTTP {status} to GET")
        return data

    def put_if_absent(self, uri: str, data: bytes) -> int:
        """PUT with If-None-Match: *: 200 put, 412 an object there already (nothing written), 403 refused."""
        status, _ = self._request("PUT", uri, data)
        self._lists.clear()
        return status

    def put(self, uri: str, data: bytes) -> None:
        status = self.put_if_absent(uri, data)
        if status not in (200, 412):  # 412: an object there already; put_back's listing said none, so the caller re-checks
            raise Failed(f"{uri}: storage answered HTTP {status} to PUT")
        if status == 412:
            bucket, key = bl.split(uri)
            if self.list(f"s3://{bucket}/{key.rsplit('/', 1)[0]}").get(uri) != len(data):
                raise Failed(f"{uri}: put by another at another size; never overwritten")


def put_back(files: dict[str, int], run: Path, have: dict[str, int], put: Callable[[str, bytes], None]) -> tuple[int, int]:
    """Each file of the run back at its own bucket and key: (put, already there). `have` is one listing
    of where they go. An object present at another size is never overwritten: the restore stops."""
    n = there = 0
    for rel, size in sorted(files.items()):
        uri = "s3://" + rel.removeprefix("objects/")
        if have.get(uri) == size:
            there += 1
            continue
        if uri in have:
            raise Failed(f"{uri}: the store holds {have[uri]} bytes there, the backup {size}; never overwritten")
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


def whole(entry: dict[str, Any], store: Any) -> bool:
    """The catalog's entry readable from the store: its metadata file, and every manifest list,
    manifest, data and delete file of its main and audited snapshots, against one listing of its
    location (bl.snapshot_files reads the manifests, as the backup does)."""
    meta = entry["metadata"]
    have = store.list(meta["location"])
    if entry["metadata-location"] not in have:
        return False
    for ref in ("main", "audited"):
        if snapshot_at(meta, ref) is None:
            continue
        try:
            _, files = bl.snapshot_files(meta, ref, lambda u: store.get(u) if u in have else b"")
        except (Failed, bl.Failed, IndexError, KeyError, ValueError):
            return False  # a manifest gone (read as no bytes) or unreadable
        if any(uri not in have or (size is not None and have[uri] != size) for uri, size in files):
            return False
    return True


def lake_problems(tables: Iterable[dict[str, Any]], load: Callable[[str], dict[str, Any] | None]) -> list[str]:
    """Each `restore` table the catalog holds at another location than the backup's: another table by
    that name, which putting files back would not make whole and re-pointing would replace."""
    out = []
    for t in tables:
        cat = load(t["table"])
        if cat and cat["metadata"]["location"].rstrip("/") != t["location"].rstrip("/"):
            out.append(f"{t['table']}: the catalog's is at {cat['metadata']['location']}, the backup's at {t['location']}")
    return out


def backup_meta(t: dict[str, Any], run: Path) -> dict[str, Any]:
    return json.loads(bl.plain((run / ("objects/" + "/".join(bl.split(t["metadata_location"])))).read_bytes()))


def decide(t: dict[str, Any], cat: dict[str, Any] | None, backup: dict[str, Any], is_whole: Callable[[dict[str, Any]], bool]) -> str:
    """A `restore` table's action, decided before any file is put back: register, same, re-point, keep-newer."""
    if cat is None:
        return "register"
    if cat["metadata-location"] == t["metadata_location"]:
        return "same"
    sid = snapshot_at(cat["metadata"], "main")
    if sid is None or sid in {s["snapshot-id"] for s in backup.get("snapshots") or []} or not is_whole(cat):
        return "re-point"
    return "keep-newer"


def decide_rebuild(cat: dict[str, Any] | None, is_whole: Callable[[dict[str, Any]], bool]) -> str:
    return "absent" if cat is None else "keep" if is_whole(cat) else "recreate"


SAID = {"register": "registered at the backup's metadata", "same": "in the catalog at the backup's metadata",
        "re-point": "re-pointed to the backup's metadata (dropped without purge, registered again)",
        "keep-newer": "the catalog's is newer than the backup and whole: kept",
        "absent": "absent: its first build, or the deploy's DDL, makes it", "keep": "whole: kept",
        "recreate": "its files are gone: dropped without purge, recreated empty, left for its rebuild"}


def restore_table(t: dict[str, Any], run: Path, lake: Any, store: Any) -> tuple[str, str]:
    """(the action, the line): decided first, then the files back, then the catalog."""
    cat = lake.load(t["table"])
    action = decide(t, cat, backup_meta(t, run), lambda c: whole(c, store))
    n, there = put_back(t["files"], run, store.list(t["location"]), store.put)
    if action == "register":
        lake.register(t["table"], t["metadata_location"])
    elif action == "re-point":
        lake.drop(t["table"])
        lake.register(t["table"], t["metadata_location"])
    return action, f"{n} file(s) put back, {there} already there; {SAID[action]}"


def rebuild_table(name: str, lake: Any, store: Any) -> str:
    cat = lake.load(name)
    action = decide_rebuild(cat, lambda c: whole(c, store))
    if action == "recreate":
        lake.drop(name)
        lake.create(name, cat["metadata"])
    return action


# ---- products, holds --------------------------------------------------------------------------------

def dag_products(release: Path) -> dict[str, str]:
    """dagId -> product, from the release's dags.json; a table's namespace (less __base) is its dagId."""
    return {d["dagId"]: d["product"] for d in json.loads((release / "compiled/airflow/dags.json").read_text())["dags"]}


def products(release: Path) -> list[str]:
    return sorted(dag_products(release).values())


def dags(release: Path) -> list[str]:
    return sorted(dag_products(release))


def owner(table: str, by_dag: dict[str, str]) -> str | None:
    return by_dag.get(table.split(".")[1].removesuffix("__base"))


def affected(release: Path, changed: Iterable[str]) -> tuple[list[str], dict[str, list[str]]]:
    """(every product left for rebuild, upstream first, governance.quality-results last; each one's own
    changed tables): the owners of the changed tables and every product downstream of one."""
    by_dag, graph = dag_products(release), rb.inputs(release)
    own: dict[str, list[str]] = {}
    for t in sorted(changed):
        if p := owner(t, by_dag):
            own.setdefault(p, []).append(t)
    left = set(own)
    while more := {p for p, ins in graph.items() if ins & left and p not in left}:
        left |= more
    order: list[str] = []
    while len(order) < len(left):
        ready = sorted(p for p in left - set(order) if not (graph.get(p, set()) & left) - set(order))
        order += ready or sorted(left - set(order))  # ponytail: a cycle k2gen refuses; taken in name order
    return sorted(order, key=lambda p: (p == LAST, order.index(p))), own


def hold_reason(run: str, product: str, order: list[str], left: dict[str, Any] | None) -> str:
    if left is None:
        return f"make restore of backup run {run}"
    before = order[:order.index(product)]
    reason = (f"make restore of backup run {run} left it for rebuild: make rebuild PRODUCT={product}"
              + (f", after {', '.join(before)}" if before else "") + rb.AWAITING + json.dumps(left, sort_keys=True))
    if len(reason) > 1000:  # k2runs.hold keeps 1000 characters: a cut reason would not parse, so the hold could never end
        raise Failed(f"{product}: its restore hold's reason is {len(reason)} characters, over 1000")
    return reason


def hold_problems(names: Iterable[str], open_holds: Callable[[str], list[dict[str, Any]]], need_restore: bool) -> list[str]:
    """Products with a rebuild lock open (a `make rebuild` runs, or one was killed), and with
    need_restore, those no open restore hold covers."""
    out = []
    for p in names:
        kinds = {h["kind"] for h in open_holds(p)}
        if "rebuild" in kinds:
            out.append(f"{p} has a rebuild lock open (make rebuild runs, or was killed: `make rebuild PRODUCT={p} UNLOCK=1`)")
        if need_restore and "restore" not in kinds:
            out.append(f"{p} has no restore hold")
    return out


def k2runs_conn(user: str, password_file: str, trino: str | None = None, ca: str = MOUNT["ca"]) -> Any:
    import k2runs
    host, _, port = (trino or f"{os.environ.get('K2_TRINO_HOST', 'trino')}:{os.environ.get('K2_TRINO_INTERNAL_PORT', '8443')}").partition(":")
    return k2runs.connect(host=host, port=int(port or 8443), user=user, password_file=password_file, ca=ca)


def newest_record(conn: Any) -> datetime | None:
    import k2runs
    cur = conn.cursor()
    cur.execute(f"SELECT max(recorded_at) FROM {k2runs.TABLE}")
    rows = cur.fetchall()
    return rows[0][0] if rows else None


# ---- the in-network subcommands --------------------------------------------------------------------

def freshness(m: dict[str, Any], run: str, lake: Any, store: Any, newest: datetime | None) -> list[str]:
    """What was committed or landed since the run: each one a reason the drill would destroy what the run
    cannot put back."""
    out = []
    for t in m["tables"]:
        cat = lake.load(t["table"])
        if (cat or {}).get("metadata-location") != t["metadata_location"]:
            out.append(f"{t['table']}: the catalog's metadata is {(cat or {}).get('metadata-location')}, the run's {t['metadata_location']}")
    for g in m["landings"]:
        want = {"s3://" + k.removeprefix("objects/"): n for k, n in g["files"].items()}
        if (have := store.list(f"s3://{g['bucket']}/{g['prefix']}")) != want:
            out.append(f"landing s3://{g['bucket']}/{g['prefix']}: {len(set(have) - set(want))} object(s) since the run, {len(set(want) - set(have))} gone")
    if newest is not None and newest >= stamp_time(run):
        out.append(f"a run record was written at {newest.isoformat()}, after the run began ({run})")
    return out


def key_probes(m: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(scope, prefix to list, file of the run to put) for each scope the restore key writes: each
    warehouse a restore table sits in (its first table's location and metadata file) and the landing
    bucket (its first landed object)."""
    out, seen = [], set()
    for t in m["tables"]:
        bucket, key = bl.split(t["location"].rstrip("/") + "/x")
        scope = f"{bucket}/{'/'.join(key.split('/')[:2])}"  # <bucket>/warehouse/<name>: one policy resource each
        if scope not in seen:
            seen.add(scope)
            out.append((scope, t["location"], "objects/" + "/".join(bl.split(t["metadata_location"]))))
    for g in m["landings"]:
        if g["files"] and g["bucket"] not in seen:
            seen.add(g["bucket"])
            out.append((g["bucket"], f"s3://{g['bucket']}/{g['prefix']}", sorted(g["files"])[0]))
    return out


def run_key(m: dict[str, Any], run: Path, store: Any) -> tuple[list[str], list[str]]:
    """(lines, problems): the restore key proven against MinIO with the reach the restore needs, before
    anything moves. The key has no delete and no admin right, so it can neither clean up a probe object
    nor read its own policy; a listing proves MinIO knows it and its list reach, and a PUT with
    If-None-Match of a file the run holds, at its own key with its own bytes, proves put reach: MinIO
    authorizes s3:PutObject before it evaluates the precondition, so an object there answers 412 and
    nothing is written; one not there (a lost lake) is put, the very bytes the restore would put
    (Iceberg and landing objects are written once under unique keys, so even a store that ignored the
    precondition would rewrite the same bytes)."""
    lines, bad = [], []
    for scope, prefix, rel in key_probes(m):
        uri = "s3://" + rel.removeprefix("objects/")
        try:
            store.list(prefix)
        except Failed as e:
            bad.append(f"{scope}: the restore key cannot list it ({e})")
            continue
        status = store.put_if_absent(uri, (run / rel).read_bytes())
        if status == 412:
            lines.append(f"key {scope}: listed; a put of {uri} refused as existing (412): put reach proven, nothing written")
        elif status == 200:
            lines.append(f"key {scope}: listed; {uri} was not there and is put back, the run's own bytes: put reach proven")
        else:
            bad.append(f"{scope}: the restore key's put answered HTTP {status}")
    return lines, bad


def run_lake(a: argparse.Namespace, lake: Any = None, store: Any = None, holds: Callable[[str], list[dict[str, Any]]] | None = None,
             newest: Callable[[], datetime | None] | None = None) -> dict[str, Any]:
    release, run = Path(a.release), Path(a.run_dir)
    m = json.loads((run / "manifest.json").read_text())
    lake, store = lake or Lake(), store or Store(READ_KEY if a.phase == "check" else KEY)
    if a.phase == "key":
        lines, bad = run_key(m, run, store)
        if bad:
            raise Refused("MinIO does not grant the restore key the reach the restore needs (no user or policy for it: "
                          "`make engine-keys`, then deploy a release whose lake-init makes the restore user): " + "; ".join(bad))
        return {"lines": lines}  # before any catalog call: the catalog may be what was lost
    problems = lake_problems(m["tables"], lake.load)
    platform = [t for t in m["tables"] if t["table"].startswith(PLATFORM)]
    others = [t for t in m["tables"] if t not in platform]
    if a.phase == "check":
        changes, locations = {}, {t["table"]: t["location"] for t in m["tables"]}
        refs_lost = []
        for t in m["tables"]:
            cat = lake.load(t["table"])
            refs_lost += [f"{t['table']}@{k}" for k, r in ((cat or {}).get("metadata", {}).get("refs") or {}).items()
                          if k != t["ref"] and r.get("snapshot-id") != t["snapshot_id"]]
        if not problems:
            for t in others:
                changes[t["table"]] = decide(t, lake.load(t["table"]), backup_meta(t, run), lambda c: whole(c, store))
        for name in m["rebuild"]:
            cat = lake.load(name)
            if cat:
                locations[name] = cat["metadata"]["location"]
            changes[name] = decide_rebuild(cat, lambda c: whole(c, store))
        out = {"problems": problems, "changes": changes, "locations": locations, "refs_lost": sorted(refs_lost), "stale": []}
        if a.fresh:
            if holds is None or newest is None:
                conn = k2runs_conn(RUNS, MOUNT["runs"])
                holds, newest = holds or (lambda p: rb.open_holds(conn, p)), newest or (lambda: newest_record(conn))
            out["stale"] = freshness(m, run.name, lake, store, newest()) + hold_problems(products(release), holds, False)
        return out
    if problems:
        raise Refused("the lake's tables are not the ones the backup holds: " + "; ".join(problems))
    if a.phase == "rest":
        if holds is None:
            conn = k2runs_conn(RUNS, MOUNT["runs"])
            holds = lambda p: rb.open_holds(conn, p)  # noqa: E731
        if bad := hold_problems(products(release), holds, True):
            raise Refused("not every product is held by this restore alone, so a DAG or a rebuild could write while its "
                          "tables come back: " + "; ".join(bad))
    out: dict[str, Any] = {"phase": a.phase, "lines": [], "changed": [], "recreated": []}
    for t in platform if a.phase == "platform" else others:
        action, line = restore_table(t, run, lake, store)
        out["lines"].append(f"table {t['table']}: {line}")
        out["changed"] += [t["table"]] if action in CHANGED else []
    if a.phase == "rest":
        for g in m["landings"]:
            n, there = put_back(g["files"], run, store.list(f"s3://{g['bucket']}/{g['prefix']}"), store.put)
            out["lines"].append(f"landing s3://{g['bucket']}/{g['prefix']}: {n} object(s) put back, {there} already there")
        for name in m["rebuild"]:
            action = rebuild_table(name, lake, store)
            out["lines"].append(f"rebuild {name}: {SAID[action]}")
            out["changed"] += [name] if action in CHANGED else []
            out["recreated"] += [name] if action == "recreate" else []
    return out


def run_holds(a: argparse.Namespace, conn: Any = None) -> list[str]:
    """hold: refused while any product has a rebuild lock open; then a restore hold on each (a product of
    --plan with the reason make rebuild ends); one already open is kept. release: each product's restore
    hold released but --keep's, and never one an earlier restore left for rebuild."""
    import k2runs
    conn = conn or k2runs_conn(OPERATOR, a.password_file, a.trino, a.ca)
    names = products(Path(a.release))
    plan, keep = json.loads(a.plan or "{}"), set(json.loads(a.keep or "[]"))
    order = [p for p in plan]
    out = []
    if a.verb == "hold":
        if bad := hold_problems(names, lambda p: rb.open_holds(conn, p), False):
            raise Refused("; ".join(bad))
        for p in names:
            have = next((h for h in rb.open_holds(conn, p) if h["kind"] == "restore"), None)
            if have:  # an interrupted restore's, or one an earlier restore left for rebuild: kept as it is
                note = "; LOUD: this restore leaves it for rebuild, and its reason does not say so, so make rebuild " \
                       "refuses it: release it by hand once rebuilt" if p in plan and rb.awaiting(have) is None else ""
                out.append(f"{p}: a restore hold is open already ({have['held_at']}), kept{note}")
                continue
            k2runs.hold(conn, p, hold_reason(a.run, p, order, plan.get(p)), BY, kind="restore")
            out.append(f"held {p} (restore)" + (": left for rebuild" if p in plan else ""))
        return out
    for p in names:
        have = next((h for h in rb.open_holds(conn, p) if h["kind"] == "restore"), None)
        if not have:
            continue
        if p in keep or rb.awaiting(have) is not None:
            out.append(f"kept the restore hold on {p}: left for rebuild; `make rebuild PRODUCT={p}` ends it once it passes")
            continue
        k2runs.release(conn, p, BY, kind="restore")
        out.append(f"released {p} (restore)")
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
    io: Any = None  # Airflow, as scripts/rebuild.py reaches it (rb.Live); None on a lake with no Airflow
    trino: str = "localhost:8443"  # the holds' Trino, from where svc.operator connects
    holds_in_network: bool = False  # a throwaway lake publishes no Trino port: the holds go in-network
    operator_password: Path | None = None  # default <secrets>/principals/svc.operator.password
    dbt_image: str | None = None  # the dbt-runner image builds run; default the release's pin (a throwaway builds its own)
    sh: Callable[..., subprocess.CompletedProcess] = field(default=subprocess.run, repr=False)


def operator_file(st: Stack) -> Path:
    return st.operator_password or st.secrets / "principals" / (OPERATOR + ".password")


def docker_run(st: Stack, *argv: str, key: tuple[str, str] | None = None, operator: bool = False) -> list[str]:
    """`restore.py <argv>` in the release's tools image on the catalog's network, as this user, with
    `key` (a storage key's two names) only for a phase that touches the store."""
    # scripts/ and platform/runs from the checkout, as scripts/backup-lake.sh mounts scripts/: the holds client
    # (k2runs) and the drift check are this tree's, so the release's tools image needs neither
    m = [f"{st.releases}:{MOUNT['release']}:ro", f"{HERE}:/opt/k2/scripts:ro", f"{ROOT / 'platform/runs'}:/opt/k2/platform/runs:ro",
         f"{st.backup}:{MOUNT['backup']}:ro",
         f"{st.secrets / 'principals' / (RUNS + '.password')}:{MOUNT['runs']}:ro", f"{st.secrets / 'tls/ca.pem'}:{MOUNT['ca']}:ro"]
    if operator:
        m.append(f"{operator_file(st)}:{MOUNT['operator']}:ro")
    keys = [x for n in key or () for x in ("-e", n)] + (["-e", "K2_S3_ENDPOINT"] if key else [])
    return ["docker", "run", "--rm", "--network", st.network, "--user", f"{os.getuid()}:{os.getgid()}", "--cpus", "1",
            "--memory", "512m", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", *keys, "-e", "K2_TRINO_HOST=trino", "-e", "K2_TRINO_INTERNAL_PORT=8443",
            *(x for v in m for x in ("-v", v)), st.tools_image, "/opt/k2/venv/bin/python", "/opt/k2/scripts/restore.py", *argv]


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


def lake_step(st: Stack, rel: Path, run: Path, phase: str, fresh: bool = False) -> dict[str, Any]:
    argv = ["lake", "--phase", phase, "--release", f"{MOUNT['release']}/{rel.name}", "--run-dir", f"{MOUNT['backup']}/runs/{run.name}"]
    return last_json(st.sh(docker_run(st, *argv, *(["--fresh"] if fresh else []), key=READ_KEY if phase == "check" else KEY),
                           capture_output=True, text=True), f"lake --phase {phase}")


def catalog_lost(st: Stack) -> bool:
    """True only on a definite answer: the database or its warehouse table does not exist, or it holds
    no warehouse. Anything else (Postgres restarting, too many clients) is a failure that replaces nothing."""
    done = st.sh(["docker", "exec", st.postgres, "sh", "-c",
                  'psql -U "$POSTGRES_USER" -d lakekeeper -Atc "SELECT count(*) FROM warehouse"'], capture_output=True, text=True)
    out, err = (done.stdout or "").strip(), (done.stderr or "").strip()
    if done.returncode == 0 and out.isdigit():
        return out == "0"
    if done.returncode != 0 and re.search(r'database "lakekeeper" does not exist|relation "warehouse" does not exist', err):
        return True
    raise Failed(f"cannot tell whether the catalog database is lost (psql exit {done.returncode}: {(err or out)[-200:]}); "
                 "nothing was replaced")


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


def live_release(st: Stack) -> Path:
    rel = (st.releases / "pointers" / "trino").resolve()
    if not (rel / "compiled/lake/backup-set.json").is_file():
        raise Refused(f"no live release at {st.releases / 'pointers/trino'} with compiled/lake/backup-set.json")
    return rel


@dataclass
class Prep:
    rel: Path
    backup_set: dict[str, Any]
    run: Path
    manifest: dict[str, Any]


def release_lacks(rel: Path) -> list[str]:
    """What the live release lacks that the restore needs, from its own sealed files: the restore user its
    lake-init makes, and the holds table every product is held in."""
    out = []
    if not (rel / "platform/docker/lake/minio/policies/restore.json").is_file():
        out.append("its lake-init makes no restore MinIO user (no platform/docker/lake/minio/policies/restore.json)")
    if not (rel / "compiled/lake/ddl/trino/governance.run_records.holds.sql").is_file():
        out.append("it has no holds table (no compiled/lake/ddl/trino/governance.run_records.holds.sql)")
    return out


def prepare(st: Stack, want: str | None) -> Prep:
    """Step 1: every check that needs no change and no lake."""
    pair(KEY), pair(READ_KEY)
    rel = live_release(st)
    if lacks := release_lacks(rel):
        raise Refused(f"the live release {rel.name} cannot be restored onto: " + "; ".join(lacks) + "; deploy a release that has them")
    backup_set = json.loads((rel / "compiled/lake/backup-set.json").read_text())
    run, manifest = choose_run(st.backup, backup_set, want)
    if not st.holds_in_network and not operator_file(st).is_file():
        raise Refused(f"no password file for {OPERATOR} at {operator_file(st)}: `make password PRINCIPAL={OPERATOR} TO_FILE=1`")
    if st.io and (alive := [d for d in dags(rel) if st.io.running(rb.oneshot_name(d))]):
        raise Refused(f"make rebuild runs ({', '.join(rb.oneshot_name(d) for d in alive)}): restore after it")
    return Prep(rel, backup_set, run, manifest)


def quiesce(st: Stack, rel: Path, say: Callable[[str], None]) -> list[str]:
    """Step 2: the DAGs that are not paused paused (returned: the only ones ever unpaused here), then
    every DAG drained by scripts/rebuild.py's drain. A drain that times out unpauses what it paused."""
    if not st.io:
        say("restore: no scheduler on this lake: no DAG to pause or drain")
        return []
    paused = []
    try:
        for dag in dags(rel):
            if not st.io.paused(dag):
                st.io.pause(dag)
                paused.append(dag)
        for dag in dags(rel):
            rb.drain(dag, st.io)
    except Exception:
        resume(st, paused, say)
        raise
    say(f"restore: {len(paused)} DAG(s) paused here ({len(dags(rel)) - len(paused)} were paused already); every DAG drained")
    return paused


def resume(st: Stack, names: Iterable[str], say: Callable[[str], None]) -> None:
    names = list(names)
    for dag in names:
        st.io.unpause(dag)
    if names:
        say(f"restore: unpaused {', '.join(names)}, which this restore paused")


def holds_step(st: Stack, rel: Path, verb: str, run: str, say: Callable[[str], None], *, plan: dict[str, Any] | None = None,
               keep: list[str] | None = None) -> None:
    """The restore holds, or their release, as svc.operator: from the host (live, as scripts/hold.py
    connects), or in-network on a throwaway lake, which publishes no Trino port. No storage key."""
    extra = ["--plan", json.dumps(plan or {}), "--keep", json.dumps(keep or [])]
    if st.holds_in_network:
        cmd = docker_run(st, "holds", verb, "--release", f"{MOUNT['release']}/{rel.name}", "--run", run, "--trino", "trino:8443",
                         "--password-file", MOUNT["operator"], "--ca", MOUNT["ca"], *extra, operator=True)
    else:
        cmd = [sys.executable, str(HERE / "restore.py"), "holds", verb, "--release", str(rel), "--run", run, "--trino", st.trino,
               "--password-file", str(operator_file(st)), "--ca", str(st.secrets / "tls/ca.pem"), *extra]
    for line in last_json(st.sh(cmd, capture_output=True, text=True), f"holds {verb}")["lines"]:
        say(f"  {line}")


def restore(st: Stack, want: str | None, say: Callable[[str], None] = print, prep: Prep | None = None,
            paused: list[str] | None = None) -> int:
    t0 = time.monotonic()
    prep = prep or prepare(st, want)
    rel, run, m = prep.rel, prep.run, prep.manifest
    say(f"restore: run {run.name} (release {m['release']}, tag {m['tag']}) onto live release {rel.name}")
    own_quiesce = paused is None
    if own_quiesce:
        paused = quiesce(st, rel, say)
    by_dag = dag_products(rel)
    replayed = False
    try:
        if own_quiesce:  # the drill proved the key before its damage
            for line in lake_step(st, rel, run, "key")["lines"]:
                say(f"  {line}")
        if "lakekeeper-db" in {e["kind"] for e in prep.backup_set["entries"]} and catalog_lost(st):
            restore_db(st, st.backup / m["dumps"]["lakekeeper"]["file"], say)
            replayed = True
        else:
            say("catalog database: present, not replaced")
        check = lake_step(st, rel, run, "check")
        if check["problems"]:
            raise Refused("the lake's tables are not the ones the backup holds: " + "; ".join(check["problems"]))
        planned = sorted(t for t, a in check["changes"].items() if a in CHANGED)
        order, own = affected(rel, planned)
        for line in lake_step(st, rel, run, "platform")["lines"]:
            say(f"  {line}")
    except Refused:
        if replayed:
            say(f"restore: REFUSED after the catalog database was replayed: nothing is unpaused ({', '.join(paused) or 'none'} "
                "stay paused); fix the cause and run `make restore` again")
        else:
            resume(st, paused, say)  # nothing written: the DAGs this restore paused run again
        raise
    except Exception:
        say(f"restore: FAILED: the DAGs this restore paused stay paused ({', '.join(paused) or 'none'}); no hold was taken")
        raise
    plan = {p: {"run": run.name, "recreated": own.get(p, []), "unpause": any(by_dag[d] == p for d in paused)} for p in order}
    try:
        return finish(st, rel, run, say, t0, paused, by_dag, planned, order, own, plan)
    except Exception:
        say(f"restore: stopped after the run records came back: every hold this restore took stays, and the DAGs it "
            f"paused stay paused ({', '.join(paused) or 'none'}); fix the cause and run `make restore` again")
        raise


def finish(st: Stack, rel: Path, run: Path, say: Callable[[str], None], t0: float, paused: list[str], by_dag: dict[str, str],
           planned: list[str], order: list[str], own: dict[str, list[str]], plan: dict[str, Any]) -> int:
    """Steps 6 to 9: the holds, the rest, the drift check, then the release of what may run again."""
    holds_step(st, rel, "hold", run.name, say, plan=plan)
    rest = lake_step(st, rel, run, "rest")
    for line in rest["lines"]:
        say(f"  {line}")
    if unplanned := sorted(set(rest["changed"]) - set(planned)):
        say(f"restore: FAIL: {', '.join(unplanned)} changed though the check planned no change: their products' holds do not "
            "say they wait for a rebuild; every hold kept, every DAG left paused")
        return FAILED
    d = last_json(st.sh(docker_run(st, "drift", "--release", f"{MOUNT['release']}/{rel.name}", "--recreated",
                                   json.dumps(rest["recreated"])), capture_output=True, text=True), "drift")
    for line in d["rebuild"]:
        say(f"  REBUILD {line}")
    if d["bad"]:
        for line in d["bad"]:
            say(f"  {line}")
        say(f"restore: FAIL: {len(d['bad'])} difference(s) besides the tables left for rebuild; every hold kept, every DAG left paused")
        return FAILED
    say(f"restore: the audited tags equal the run records; {len(d['pending'])} pending, {len(rest['recreated'])} table(s) recreated")
    holds_step(st, rel, "release", run.name, say, keep=order)
    resume(st, [dag for dag in paused if by_dag[dag] not in order], say)
    for i, p in enumerate(order, 1):
        say(f"restore: rebuild {i} of {len(order)}: make rebuild PRODUCT={p}" + (f" (its {', '.join(own[p])} recreated)" if p in own else
                                                                                  " (downstream of one recreated)"))
    if order:
        say("restore: each product above stays held (restore), its DAG paused, until its make rebuild passes, in that order")
    say(f"restore: OK in {time.monotonic() - t0:.1f} s: run {run.name}")
    return OK


# ---- the drill ---------------------------------------------------------------------------------------

def wrapper_help(st: Stack, image: str) -> tuple[int, str]:
    """`k2-dbt build --help` of the release's dbt-runner image (or the image this stack's builds run, on
    a throwaway): read-only, no network, nothing pulled."""
    done = st.sh(["docker", "run", "--rm", "--network", "none", "--pull", "never", "--entrypoint", "k2-dbt", st.dbt_image or image,
                  "build", "--help"], capture_output=True, text=True)
    return done.returncode, done.stdout or ""


def drill_damage(prep: Prep, check: dict[str, Any]) -> dict[str, Any]:
    """What the drill deletes, and what it says it destroys that the run does not hold."""
    rel, m = prep.rel, prep.manifest
    file_bronze = set()
    for f in (rel / "compiled/products").glob("*/pipeline.json"):
        doc = json.loads(f.read_text())
        if (doc.get("connector") or {}).get("kind") not in (None, "kafka"):
            file_bronze |= {"k2." + b["table"].split(".", 1)[1] for b in doc.get("bronze", [])}
    rebuild = sorted(t for t in m["rebuild"] if t.split(".")[0] in DRILL_WAREHOUSES and t in check["locations"] and t not in file_bronze)
    spared = sorted(t for t in m["rebuild"] if t in file_bronze and t in check["locations"])
    prefixes = [t["location"] for t in m["tables"]] + [check["locations"][t] for t in rebuild] + \
        [f"s3://{g['bucket']}/{g['prefix']}" for g in m["landings"]]
    return {"prefixes": [p.rstrip("/") + "/" for p in prefixes], "rebuild": rebuild, "spared": spared, "refs_lost": check["refs_lost"]}


def statement(prep: Prep, dmg: dict[str, Any]) -> str:
    m = prep.manifest
    return (f"the drill destroys: the catalog database (back from {m['dumps']['lakekeeper']['file']}); {len(m['tables'])} restore "
            f"table(s) and {len(m['landings'])} landing prefix(es) (back from run {prep.run.name}, each table at its backed-up ref "
            f"only{'; not in the run, lost: ' + ', '.join(dmg['refs_lost']) if dmg['refs_lost'] else ''}); rebuild table(s) "
            f"{', '.join(dmg['rebuild']) or 'none'} (not in the run: recreated empty, their products and every product downstream "
            f"held until make rebuild passes); left as they are: {', '.join(dmg['spared']) or 'no'} file product bronze (nothing "
            "rebuilds a file product's bronze) and every table outside warehouses k2 and governance")


def drill(st: Stack, confirm: str, say: Callable[[str], None] = print) -> int:
    t0 = time.monotonic()
    prep = prepare(st, None)
    root = {n: os.environ.get(n, "") for n in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")}
    if not all(root.values()):
        raise Refused("the drill's damage needs MINIO_ROOT_USER and MINIO_ROOT_PASSWORD in the environment (.env loaded)")
    for line in lake_step(st, prep.rel, prep.run, "key")["lines"]:
        say(f"  {line}")
    first = lake_step(st, prep.rel, prep.run, "check", fresh=True)
    if first["problems"] or first["stale"]:
        raise Refused("the run cannot put back what the drill would destroy (take a fresh `make backup`): "
                      + "; ".join(first["problems"] + first["stale"]))
    if drill_damage(prep, first)["rebuild"] and (problem := rb.full_refresh_problem(prep.rel, lambda image: wrapper_help(st, image))):
        raise Refused(f"the drill destroys rebuild tables that only make rebuild brings back, and {problem}")
    said = statement(prep, drill_damage(prep, first))
    if confirm != prep.rel.name:
        raise Refused(f"{said}. To go ahead: CONFIRM={prep.rel.name} (the live release)")
    say(f"drill: {said}")
    paused = quiesce(st, prep.rel, say)
    try:
        check = lake_step(st, prep.rel, prep.run, "check", fresh=True)  # again, now that nothing runs
        if check["problems"] or check["stale"]:
            raise Refused("something was committed between the check and the drain: " + "; ".join(check["problems"] + check["stale"]))
        dmg = drill_damage(prep, check)
        if dmg["rebuild"] != drill_damage(prep, first)["rebuild"]:
            raise Refused("the rebuild tables to destroy changed since the statement; run it again")
    except Exception:
        resume(st, paused, say)
        raise
    holds_step(st, prep.rel, "hold", prep.run.name, say)  # against a manual trigger; the rewind of the holds table drops them
    t1 = time.monotonic()
    st.sh(["docker", "exec", st.postgres, "sh", "-c",
           'dropdb -U "$POSTGRES_USER" --force lakekeeper && createdb -U "$POSTGRES_USER" lakekeeper'], check=True, capture_output=True)
    q = urllib.parse.quote
    env = {**os.environ, "MC_HOST_k2": f"http://{q(root['MINIO_ROOT_USER'], safe='')}:{q(root['MINIO_ROOT_PASSWORD'], safe='')}@minio:9000"}
    for prefix in dmg["prefixes"]:
        bucket, key = split_prefix(prefix)
        st.sh(["docker", "run", "--rm", "--network", st.network, "-e", "MC_CONFIG_DIR=/tmp/.mc", "-e", "MC_HOST_k2", "--entrypoint", "mc",
               st.minio_image, "rm", "--recursive", "--force", f"k2/{bucket}/{key}"], check=True, capture_output=True, env=env)
    say(f"drill: damage done in {time.monotonic() - t1:.1f} s: the catalog database emptied; {len(dmg['prefixes'])} location(s) emptied")
    t2 = time.monotonic()
    code = restore(st, None, say, prep=prep, paused=paused)
    say(f"drill: restore {'passed' if code == OK else 'FAILED'} in {time.monotonic() - t2:.1f} s (checks, damage and restore "
        f"{time.monotonic() - t0:.1f} s)")
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
                 minio_image=minio, postgres=a.postgres, lakekeeper=a.lakekeeper, io=rb.Live() if not a.no_airflow else None,
                 trino=a.trino or f"localhost:{env.get('K2_TRINO_PORT', '8443')}", holds_in_network=a.holds_in_network,
                 operator_password=Path(a.operator_password) if a.operator_password else None, dbt_image=a.dbt_image)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="restore.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "drill"):
        p = sub.add_parser(name)
        p.add_argument("--run", help="the run's stamp; refused unless it is the newest complete run")
        for f in ("--releases", "--backup", "--secrets", "--network", "--tools-image", "--minio-image", "--trino", "--operator-password",
                  "--dbt-image"):
            p.add_argument(f)
        p.add_argument("--postgres", default="k2-prefect-db")
        p.add_argument("--lakekeeper", default="k2-lakekeeper")
        p.add_argument("--no-airflow", action="store_true", help="a lake with no Airflow: no DAG to pause, drain or unpause")
        p.add_argument("--holds-in-network", action="store_true", help="a throwaway lake: no Trino port on the host")
    lk = sub.add_parser("lake")
    lk.add_argument("--phase", choices=("key", "check", "platform", "rest"), required=True)
    lk.add_argument("--release", required=True)
    lk.add_argument("--run-dir", required=True)
    lk.add_argument("--fresh", action="store_true", help="check: also what was committed or landed since the run")
    h = sub.add_parser("holds")
    h.add_argument("verb", choices=("hold", "release"))
    for f in ("--release", "--run", "--trino", "--password-file", "--ca"):
        h.add_argument(f, required=True)
    h.add_argument("--plan", default="{}")
    h.add_argument("--keep", default="[]")
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
