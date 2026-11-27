"""k2-dbt, the dbt wrapper: what guarantees readers see only tested data (ADR-061 parts 4 and 5).

    k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>
    k2-dbt docs --release <id> --out <dir>    # the release's dbt docs site, no warehouse (docs())

The frozen contract is docs/development/phase-001-interfaces.md, "Run-time contracts", "The dbt
wrapper"; the measurements behind it are spikes (d), (f) and (g) of
docs/research/2026-11-26-release-spine-spikes.md. In order:

1. orphans: every query the builder still runs (an earlier run killed mid-statement leaves its
   CTAS or MERGE running in Trino for up to query.client.timeout) is killed, and none may remain
   after ORPHAN_WAIT seconds, before anything below reads the lake (stop_orphans()).
   reconcile: an output whose `main` is not its `audited` tag (an earlier run died) is restored to
   the tag as the builder (no tag: DROP TABLE), `reconciled`; a tag ahead of the run records, which
   only an unfinished run explains (it died between the tag commit and its `audited` row), gets
   that row; every view is put back to C and the tagged columns; then the catalog drift check,
   with the run-record comparison, must find nothing in this product's namespaces (Live.drift).
2. resolve each input's tested snapshot from `t$refs` as svc.runs, the whole set read twice and
   compared (a publish between two reads restarts it, RESOLVE_ATTEMPTS times); a required input
   never tested exits 4. An optional input whose port says whenAbsent empty (or emptyIntoTable)
   is absent while it is paused in the release or was never tested: no snapshot, and k2_input
   reads it as zero rows of its columns (W0-7).
3. `started` per output, with `snapshot_before`, its main, and each input's `lagMinutes` (lag()).
4. one `dbt build --selector <fence> --vars <k2_inputs, k2_run_id, k2_release>` as the builder,
   unit tests inside it (spike d). Any non-zero exit is a failed build.
5. on a failure, every output whose main moved is restored as the builder, `rolled_back`.
6. on a pass, as the publisher: each moved main must be this dbt invocation's own commit
   (built_here(): committed by a query that carried this run's client tag, or that dbt reported),
   then each incremental output compacted where it needs it (compact(): ALTER TABLE ... EXECUTE
   optimize as the builder, under this run's client tag; the one snapshot accepted on top of the
   tested one is that statement's `replace` of it reading as many rows, compaction_guard(); a
   failed optimize publishes the tested snapshot), views narrowed, one compare-and-set tag
   commit, views widened, `audited` (publish()). A pass with warn-severity test failures
   publishes degraded: each `audited` row's detail is {"degraded": true, "warned": [<test
   unique_id>, ...]}, as k2runs.state reads it (warned()); a compaction adds "compacted":
   {"files": [before, after], "deleteFiles": [before, after]} or "compactionSkipped": <why>.

Exits: 0 passed and published; 1 tests failed, outputs restored; 2 build error, outputs restored;
3 a restore, the tag commit, the view sync, the reconcile or the drift check failed, or a write
after the tests (pages); 4 a required input was never tested. The last stdout line, on every
path, is the XCom {"exit": n, "expire": [{"table": <relation>, "ids": [...]}]}: the failed build's
snapshots, which the Spark `expire` task removes by id; a degraded publish adds "degraded": true
and "warned", a compaction "compacted": [{"table", "files", "deleteFiles"}] or
"compactionSkipped": [{"table", "why"}]. dbt's own output goes to stderr.

Trust: identifiers come from the compiled release only, each part held to k2gen's IDENTIFIER and
quoted by k2gen's quote_relation; snapshot ids pass snap() (an int, never a string spliced in);
passwords are read from their files at connect time, never logged (log() scrubs every one read),
never in an argument list. Trino cannot move a tag (spike g), so the tag commit and the current
view SQL are the two calls to Lakekeeper's REST API, at K2_CATALOG_URI (steps 1 and 6), beside the
drift check's own reads and step 3's read of each input's table metadata (svc.runs may read only
`t$refs` in Trino, which holds no commit time); listing and cancelling the builder's orphaned
queries (step 1), and listing the queries that carried this run's client tag (step 6), are the
calls to Trino's own REST API, as the builder.

Imports beyond the standard library: trino, k2gen (platform/k2gen), and catalog_drift
(scripts/verify/catalog_drift.py), which docker/dbt-runner/Dockerfile puts beside this file, so
`k2-dbt --help` fails the image's build on a missing one; k2runs is loaded from the release itself
(<release>/platform/platform/runs/k2runs.py).
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import catalog_drift
import trino
from k2gen import model as k2
from k2gen.emit import lake
from trino.exceptions import TrinoUserError

SECRETS = "/run/k2-secrets"
PASSWORDS = {"builder": f"{SECRETS}/builder.password", "publisher": f"{SECRETS}/publisher.password",
             "runs": f"{SECRETS}/runs.password"}
CA = f"{SECRETS}/tls/ca.pem"
RELEASES = Path(k2.RELEASES_MOUNT)
WORK = Path("/tmp/k2-dbt")  # dbt's target, logs and HOME: the one-shot's only writable path (oneshot.TMPFS)
PROFILES_DIR = Path("/opt/k2/dbt")  # dbt/profiles.yml in the image (docker/dbt-runner/Dockerfile)
ENV = ("K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER", "K2_PUB_USER", "K2_RUNS_USER",
       "K2_CATALOG_URI", "K2_RELEASE")
RUNS_USER = "svc.runs"
RESOLVE_ATTEMPTS = 3
TERMINAL = ("rolled_back", "audited", "reconciled")
WHEN_ABSENT = ("fail", "empty", "emptyIntoTable")  # k2-v2.schema.json inputPortProperty whenAbsent
RELEASE_ID = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?-[0-9a-f]{12}")  # run-record.schema.json
PRODUCT_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z][a-z0-9]*(-[a-z0-9]+)*")
RUN_ID = re.compile(r"([a-z][a-z0-9_]*)/([A-Za-z0-9][A-Za-z0-9_.:+-]{0,200})")
HOST = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
INVOCATION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
QUERY_ID = re.compile(r"[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}")  # Trino's QueryId
CLIENT_TAG = "K2_DBT_CLIENT_TAG"  # dbt/profiles.yml's client_tags: every statement of the run carries it
ORPHAN_WAIT = 60  # seconds a killed query has to end (its commit or abort) before reconcile reads
# Compaction of an incremental output, after its tests (compact()), by docker/lake/maintain.py's
# rule for a Kafka product: a data file under SMALL_FILE_BYTES is small, and a table is compacted
# once it holds COMPACT_SMALL_FILES small files beyond one per partition or COMPACT_DELETE_FILES
# delete files. A `table` model is rebuilt whole by every build and never compacted.
SMALL_FILE_BYTES = 64 * 1024 * 1024
# ponytail: 48 is four hours of five-minute builds at one new file each (maintain.py's); 12 is an
# hour of five-minute builds that each leave one delete file, the F13 ceiling per build
# (docs/benchmarks/2026-11-26-product-run-cost.md), so most builds skip. Tune both from the soak's
# files and delete files per build and the optimize's time in the chain (W0-12).
COMPACT_SMALL_FILES, COMPACT_DELETE_FILES = 48, 12
COMPACTION_WHY_CHARS = 80  # a skip's reason, cut: with WARNED_MAX names beside it the detail stays under 1000


class Refused(Exception):
    """Something the wrapper will not do or read; its message never holds a secret."""


class Untested(Refused):
    """A required input has no tested snapshot: exit 4."""


# ---- secrets and logging -----------------------------------------------------------------------

_READ: list[str] = []  # every password read, so that log() can scrub it from any message


def password(role: str) -> str:
    """The role's password, read from its file now; registered for scrubbing, never returned in text."""
    with open(PASSWORDS[role]) as f:
        value = f.read().strip()
    if not value:
        raise Refused(f"{PASSWORDS[role]} is empty")
    _READ.append(value)
    return value


def scrub(text: str) -> str:
    for value in _READ:
        text = text.replace(value, "***")
    return text


def log(message: object) -> None:
    print(scrub(f"k2-dbt: {message}"), file=sys.stderr, flush=True)


# ---- names and ids: the only ways a value reaches SQL -------------------------------------------

def relation(text: str) -> str:
    """A three-part name from the release, each part a k2gen IDENTIFIER, returned quoted."""
    parts = text.split(".") if isinstance(text, str) else []
    if len(parts) != 3 or not all(k2.IDENTIFIER.fullmatch(p) and len(p) <= k2.MAX_IDENTIFIER for p in parts):
        raise Refused(f"{text!r} is not a <catalog>.<namespace>.<table> of k2gen identifiers")
    return k2.quote_relation(text)


def snap(value: Any) -> int:
    """A snapshot id as an integer: from an int or a string of digits, never a float or a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise Refused(f"snapshot id {value!r} is not an integer")
    if isinstance(value, str) and not (value.isascii() and value.isdigit()):
        raise Refused(f"snapshot id {value!r} is not an integer")
    n = int(value)
    if not 0 <= n < 2**63:
        raise Refused(f"snapshot id {n} is outside an Iceberg id's range")
    return n


def version(snapshot: Any) -> str:
    """`FOR VERSION AS OF BIGINT '<id>'`, k2_input's form: an id under 2^31 would otherwise be an
    integer literal, which Trino refuses as a table version (spike d)."""
    return f"FOR VERSION AS OF BIGINT '{snap(snapshot)}'"


# ---- the release, read once --------------------------------------------------------------------

@dataclass(frozen=True)
class Output:
    relation: str  # <domain>_build.<fence>__base.<table>: dbt writes it; the run records' table_name
    view: k2.View  # its reader view, compiled/trino/views.json
    partitioning: tuple[str, ...] = ()  # the contract's, compiled/lake/contract_schemas.json, as dbt sets it
    sort: tuple[str, ...] = ()
    incremental: bool = False  # its model's config(materialized='incremental'): the only kind compacted


@dataclass(frozen=True)
class Input:
    contract: str
    relation: str  # model.input_relation, read through a read-only catalog
    required: bool
    ref: str  # model.tested_ref: audited (a tag) or main (a branch)
    key: str  # "<product fence>/<table>", k2_input's key
    state: str  # the input product's status in this release
    when_absent: str = "fail"  # the port's whenAbsent (WHEN_ABSENT); empty and emptyIntoTable read an absent input as zero rows


@dataclass(frozen=True)
class Ctx:
    release: str
    release_path: Path
    product: str
    run_id: str
    warehouse: str
    selector: str
    writer: str
    publisher: str
    outputs: tuple[Output, ...]
    inputs: tuple[Input, ...]
    started_at: datetime


def ctx_from(release: str, release_path: Path, product: str, run_id: str, pipeline: dict[str, Any],
             views: list[dict[str, Any]], tables: dict[str, tuple[str, str, str]], schemas: dict[str, Any],
             incremental: frozenset[str] = frozenset()) -> Ctx:
    """The run's context from the release's compiled pipeline.json, views.json and
    contract_schemas.json; `tables` maps each input's contract id to (input_relation, tested_ref,
    product status), from k2gen's model; `incremental` names the product's tables whose model is
    incremental."""
    if not RELEASE_ID.fullmatch(release) or not PRODUCT_ID.fullmatch(product):
        raise Refused(f"release {release!r} or product {product!r} is not an id")
    m = RUN_ID.fullmatch(run_id)
    domain, name = product.split(".")
    if not m or m[1] != k2.dag_id(name) or pipeline.get("product") != product or pipeline.get("dagId") != m[1]:
        raise Refused(f"run id {run_id!r} is not <{k2.dag_id(name)}>/<run> of product {product}'s pipeline")
    build = pipeline.get("build")
    if not build or build.get("engine") != "dbt":
        raise Refused(f"{product}: its pipeline has no dbt build")
    writer, publisher = k2.builder(name), k2.publisher(name)
    if (build["writer"], build["publisher"], build["selector"]) != (writer, publisher, k2.dag_id(name)):
        raise Refused(f"{product}: the pipeline names another builder, publisher or selector than its own")
    by_base = {v["base"]: k2.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"]) for v in views}
    outputs = []
    for rel in build["outputs"]:
        relation(rel)
        catalog, ns, table = rel.split(".")
        if (catalog, ns) != (k2.build_catalog(domain), k2.base_namespace(name)):
            raise Refused(f"{product}: output {rel} is outside its own {k2.build_catalog(domain)}.{k2.base_namespace(name)}")
        view = by_base.get(k2.base_relation(domain, name, table))
        if view is None or view.owner != publisher or view.relation != k2.view_relation(domain, name, table):
            raise Refused(f"{product}: output {rel} has no view of its own in {k2.VIEWS}")
        relation(view.relation)
        schema = schemas.get(f"{k2.warehouse(domain)}.{ns}.{table}")
        if schema is None:
            raise Refused(f"{product}: output {rel} has no entry in {k2.CONTRACT_SCHEMAS}")
        outputs.append(Output(rel, view, tuple(schema.get("partitioning", ())), tuple(schema.get("sort", ())), table in incremental))
    if not outputs:
        raise Refused(f"{product}: its build names no output")
    inputs = []
    for i in pipeline.get("inputs", []):
        cid = i["contractId"]
        if cid not in tables or tables[cid][0] != i["relation"]:
            raise Refused(f"{product}: input {cid} is not the release's table at {i['relation']!r}")
        relation(i["relation"])
        when_absent = i.get("whenAbsent", "fail")  # a release compiled before W0-7 has none: fail, as it was
        if when_absent not in WHEN_ABSENT:
            raise Refused(f"{product}: input {cid} has whenAbsent {when_absent!r}, not one of {', '.join(WHEN_ABSENT)}")
        _, producer, table = cid.split(".")
        inputs.append(Input(cid, i["relation"], bool(i["required"]), tables[cid][1], f"{k2.fence(producer)}/{table}",
                            tables[cid][2], when_absent))
    return Ctx(release, release_path, product, run_id, k2.warehouse(domain), build["selector"], writer, publisher,
               tuple(outputs), tuple(inputs), datetime.now(timezone.utc))


def load(release: str, product: str, run_id: str) -> Ctx:
    """ctx_from over the release at /k2/releases/<release>."""
    from k2gen import docset
    if not RELEASE_ID.fullmatch(release) or not PRODUCT_ID.fullmatch(product):
        raise Refused(f"release {release!r} or product {product!r} is not an id")
    path = RELEASES / release
    pipeline = json.loads((path / k2.pipeline_path(product)).read_text())
    views = json.loads((path / k2.VIEWS).read_text())["views"]
    schemas = json.loads((path / k2.CONTRACT_SCHEMAS).read_text())
    m = k2.load(docset.read(path / k2.DOCUMENTS_DIR))
    status = {p.id: p.status for p in m.products}
    tables = {t.contract_id: (k2.input_relation(t), k2.tested_ref(t), status[k2.product_id(p.domain, p.name)])
              for p in m.products for t in p.tables if t.build.kind in k2.LAKE_BUILDS}
    incremental = frozenset(t.name for p in m.products if k2.product_id(p.domain, p.name) == product for t in p.tables
                            if t.build.kind == "dbt" and k2.model_config(p.models.get(f"{t.name}.sql", "")).get("materialized") == "incremental")
    return ctx_from(release, path, product, run_id, pipeline, views, tables, schemas, incremental)


# ---- the core: decisions from observed state; `io` does every read and write --------------------

def ref_id(refs: dict[str, tuple[str, int]] | None, name: str, kind: str) -> int | None:
    """The snapshot a ref names, or None; a ref of the wrong kind (a branch named audited) refuses."""
    if refs is None or name not in refs:
        return None
    if refs[name][0] != kind:
        raise Refused(f"ref {name!r} is a {refs[name][0]}, not a {kind}")
    return snap(refs[name][1])


def heads(io: Any, rel: str) -> tuple[int | None, int | None]:
    """(main, audited) of an output, as the builder."""
    refs = io.refs("builder", rel)
    return ref_id(refs, "main", "BRANCH"), ref_id(refs, k2.AUDITED_TAG, "TAG")


def record(ctx: Ctx, io: Any, **row: Any) -> None:
    io.record(**{"run_id": ctx.run_id, "product_id": ctx.product, "release_id": ctx.release, "task": "build",
                 "started_at": ctx.started_at, **row})


def restore_statement(rel: str, target: int | None, tagged: bool, at_target: dict[str, str] | None,
                      now: dict[str, str], partitioning: tuple[str, ...] = (), sort: tuple[str, ...] = ()) -> str:
    """How an output goes back to `target`, as the builder (spike f): no target, DROP TABLE (never a
    tagged table); the same columns and types in the same order, rollback_to_snapshot; otherwise a
    create-or-replace from the old snapshot, since a rollback restores rows but not the schema. The
    replace names the contract's partitioning and sort as dbt's build does: without them Trino 484
    makes the table unpartitioned (measured by scripts/verify/dbt-wrapper.sh), and a row scope's
    files would no longer be skipped by its partition (ADR-061's Outcome)."""
    q = relation(rel)
    if target is None:
        if tagged:
            raise Refused(f"{rel}: no snapshot to restore to, and it carries a tag: never dropped")
        return f"DROP TABLE {q}"
    if at_target is not None and list(at_target.items()) == list(now.items()):
        return f"ALTER TABLE {q} EXECUTE rollback_to_snapshot({snap(target)})"
    props = ", ".join(f"{k} = ARRAY[{', '.join(k2.sql_literal(v) for v in vs)}]"
                      for k, vs in (("partitioning", partitioning), ("sorted_by", sort)) if vs)
    with_props = f" WITH ({props})" if props else ""
    return f"CREATE OR REPLACE TABLE {q}{with_props} AS SELECT * FROM {q} {version(target)}"


def restore(io: Any, o: Output, target: int | None, tagged: bool) -> None:
    at_target = io.columns(o.relation, target) if target is not None else None
    io.sql("builder", restore_statement(o.relation, target, tagged, at_target, io.columns(o.relation, None), o.partitioning, o.sort))


def stop_orphans(ctx: Ctx, io: Any) -> None:
    """Step 1, first: a build killed mid-statement (docker rm -f, on_kill, a scheduler crash) stops
    the client, not the query, which Trino runs on until query.client.timeout; a CTAS or MERGE that
    commits after this run's tests would put an untested snapshot on main just before publish. So
    every query the builder still runs is killed and must end before reconcile reads anything. Only
    this product's one run is ever its builder (max_active_runs 1, the container-name mutex), so
    each one found is an orphan. Raises Refused when one outlives ORPHAN_WAIT; the caller exits 3."""
    for q in io.running():
        log(f"{ctx.writer}'s query {q} is still running, left by an earlier run: killed")
        io.kill(q)
    deadline = time.monotonic() + ORPHAN_WAIT
    while left := io.running():
        if time.monotonic() >= deadline:
            raise Refused(f"{ctx.writer}'s queries {left} still run {ORPHAN_WAIT} s after the kill")
        time.sleep(1)


def reconcile(ctx: Ctx, io: Any) -> None:
    """Step 1. Raises Refused on anything a reconcile must not repair; the caller exits 3."""
    rels = {o.relation for o in ctx.outputs}
    last: dict[str, dict[str, Any]] = {}
    audited: dict[str, int] = {}
    # A restore by create-or-replace leaves main on a new snapshot holding the tag's rows and
    # schema, never the tag's id (rolling back onto the tag's id would read its files through the
    # replaced field ids). So main is clean when it is the tag, or what a restore since the newest
    # audited row recorded as its snapshot_after.
    restored: dict[str, set[int]] = {}
    for row in io.history(ctx.product):  # oldest first
        rel = row["table_name"]
        if rel in rels and row["event"] in ("started", *TERMINAL):
            last[rel] = row
            after = None if row["snapshot_after"] is None else snap(row["snapshot_after"])
            if row["event"] == "audited" and after is not None:
                audited[rel], restored[rel] = after, set()
            elif row["event"] in ("rolled_back", "reconciled") and after is not None:
                restored.setdefault(rel, set()).add(after)
    tags: dict[str, int | None] = {}
    for o in ctx.outputs:
        main, tag = heads(io, o.relation)
        tags[o.relation] = tag
        prev = last.get(o.relation)
        unfinished = prev["run_id"] if prev and prev["event"] == "started" else None
        if unfinished is not None and not RUN_ID.fullmatch(unfinished):
            raise Refused(f"{o.relation}: run record {unfinished!r} is not a run id")
        clean = main == tag or main in restored.get(o.relation, set())
        if tag != audited.get(o.relation):
            # Only a run that died between its tag commit and its audited row explains this: its
            # build is main, the tag is on it, and its last record for the table is `started`.
            if unfinished is None or tag is None or main != tag:
                raise Refused(f"{o.relation}: tag {tag} and newest audited run record {audited.get(o.relation)} differ, "
                              "and no unfinished run explains it")
            log(f"reconcile {o.relation}: tag {tag} ahead of the records; {unfinished} gets its audited row")
            record(ctx, io, run_id=unfinished, event="audited", table_name=o.relation,
                   snapshot_before=None if prev["snapshot_before"] is None else snap(prev["snapshot_before"]), snapshot_after=tag, detail="reconciled: the tag moved and the run stopped before its audited row")
        elif not clean:
            # Back to where the dead run started (its main then held the tag's rows), else the tag.
            start = None if unfinished is None or prev["snapshot_before"] is None else snap(prev["snapshot_before"])
            target = start if start is not None and (start == tag or start in restored.get(o.relation, set())) else tag
            log(f"reconcile {o.relation}: main {main} is not its tag {tag}; restored to {target}")
            restore(io, o, target, tag is not None)
            record(ctx, io, run_id=unfinished or ctx.run_id, event="reconciled", table_name=o.relation,
                   snapshot_before=main, snapshot_after=heads(io, o.relation)[0] if target is not None else None,
                   detail="main restored to the audited tag's rows" if tag else "dropped: never tagged")
        elif unfinished:
            record(ctx, io, run_id=unfinished, event="reconciled", table_name=o.relation, snapshot_before=main,
                   snapshot_after=tag, detail="nothing moved")
    for o in ctx.outputs:  # the view rule of step 6, rerun: each view is C and the tagged columns
        if tags[o.relation] is None:
            continue
        cols = io.columns(o.relation, tags[o.relation])
        want = lake.view_sql(o.view, list(cols))
        have = io.view_sql(o.view)
        if have is None or catalog_drift.tokens(have) != catalog_drift.tokens(want):
            log(f"reconcile {o.view.relation}: the view is put back to the tagged columns")
            io.sql("publisher", lake.view_update(o.view, cols, cols)[1])
    found = io.drift()
    if found["differences"]:
        for line in found["differences"]:
            log(line)
        raise Refused(f"the catalog drift check found {len(found['differences'])} difference(s)")


def absent(i: Input, snapshot: int | None) -> bool:
    """Whether k2_input reads the input as zero rows: its port says so, and it is paused in this
    release (its state, the release's product status) or was never tested."""
    return i.when_absent != "fail" and (i.state == "paused" or snapshot is None)


def resolve(ctx: Ctx, io: Any) -> dict[str, int | None]:
    """Step 2: every input's tested snapshot, as svc.runs; None for one never tested or absent(),
    so the run record says no snapshot was read. One full read can straddle a publish, so the set
    is read twice and must agree; tags only move forward, so two equal reads hold one instant
    between them."""
    def read() -> dict[str, int | None]:
        return {i.contract: ref_id(io.refs("runs", i.relation), i.ref, "TAG" if i.ref == k2.AUDITED_TAG else "BRANCH")
                for i in ctx.inputs}
    for _ in range(RESOLVE_ATTEMPTS):
        first, second = read(), read()
        if first == second:
            break
        log("an input's tested ref moved between the two reads; resolving again")
    else:
        raise Refused(f"inputs still moving after {RESOLVE_ATTEMPTS} double reads")
    if never := [i.contract for i in ctx.inputs if i.required and first[i.contract] is None]:
        raise Untested(f"required input(s) never tested: {', '.join(never)}")
    for i in ctx.inputs:
        if absent(i, first[i.contract]):
            log(f"input {i.contract} is {'paused' if i.state == 'paused' else 'never tested'}: read as zero rows (whenAbsent {i.when_absent})")
            first[i.contract] = None
    return first


def commit_time(meta: dict[str, Any] | None, snapshot: int, rel: str) -> datetime:
    """When the data at `snapshot` was committed, from the table's catalog metadata: the snapshot's
    own timestamp-ms or, while it is a compaction (operation replace: files rewritten, table data
    unchanged, by the Iceberg spec), its parent's, so a compaction after the audited commit never
    makes an input look fresher (phase-001-interfaces.md, I-1: a Kafka input's lag is its
    connector snapshot's, never a compaction's after it)."""
    snaps = {s.get("snapshot-id"): s for s in (meta or {}).get("snapshots") or []}
    s = snaps.get(snap(snapshot))
    for _ in range(len(snaps)):  # bounded: a parent cycle in the metadata never loops
        if s is None or (s.get("summary") or {}).get("operation") != "replace" or s.get("parent-snapshot-id") not in snaps:
            break
        s = snaps[s["parent-snapshot-id"]]
    if s is None:
        raise Refused(f"{rel}: snapshot {snapshot} is not in the catalog's metadata of the table")
    return datetime.fromtimestamp(int(s["timestamp-ms"]) / 1000, timezone.utc)


def lag(started: datetime, committed: datetime | None) -> float | None:
    """`inputs[].lagMinutes` (run-record.schema.json): the run's start minus the input's audited
    commit time, in minutes, never below 0 (an input published after the run started); None when
    the run read no snapshot of it."""
    return None if committed is None else round(max(0.0, (started - committed).total_seconds() / 60), 1)


WARNED_MAX, WARNED_CHARS = 5, 160  # k2runs.record cuts a detail at 1000 characters: 5 names of 160 keep its JSON whole


def warned(results: dict[str, Any] | None) -> list[str]:
    """The tests dbt's run_results mark `warn` (a warn-severity test that failed), by unique_id,
    which joins dq_test_results; at most WARNED_MAX, each cut to WARNED_CHARS. The log names all."""
    names = sorted(str(r.get("unique_id")) for r in (results or {}).get("results", []) if r.get("status") == "warn")
    if names:
        log(f"{len(names)} warn-severity test(s) failed: {', '.join(names)}")
    return [n[:WARNED_CHARS] for n in names[:WARNED_MAX]]


def dbt_argv(ctx: Ctx, inputs: dict[str, int | None]) -> list[str]:
    """Step 4's one invocation. An absent() input is {"absent": true}, zero rows of its columns
    (k2_input); any other input with no snapshot (an optional one, whenAbsent fail) is left out of
    k2_inputs, so a model that reads it fails at compile (k2_input), never reads it unpinned."""
    pinned = {i.key: {"snapshot": snap(inputs[i.contract])} for i in ctx.inputs if inputs[i.contract] is not None}
    pinned |= {i.key: {"absent": True} for i in ctx.inputs if absent(i, inputs[i.contract])}
    variables = {"k2_inputs": pinned, "k2_run_id": ctx.run_id, "k2_release": ctx.release}
    return ["dbt", "build", "--selector", ctx.selector, "--vars", json.dumps(variables, sort_keys=True),
            "--project-dir", str(ctx.release_path / "compiled" / "dbt"), "--profiles-dir", str(PROFILES_DIR),
            "--target-path", str(WORK / "target"), "--log-path", str(WORK / "logs")]


def client_tag() -> str:
    """This run's own Trino client tag, which every statement of its dbt build carries (the
    profile's client_tags). Made after stop_orphans and fresh per run, a retry of the same run id
    included, so no query an earlier run left can carry it."""
    return f"k2-dbt-{secrets.token_hex(16)}"


def dbt_env(environ: dict[str, str], builder_password: str, tag: str) -> dict[str, str]:
    """The dbt subprocess's whole environment: what dbt and dbt/profiles.yml read, the builder's
    credentials only (never the publisher's, svc.runs's or the catalog's), and the run's client tag."""
    return {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(WORK), "LANG": "C.UTF-8",
            "K2_TRINO_HOST": environ["K2_TRINO_HOST"], "K2_TRINO_INTERNAL_PORT": environ["K2_TRINO_INTERNAL_PORT"],
            "K2_TRINO_USER": environ["K2_TRINO_USER"], "DBT_ENV_SECRET_K2_TRINO_PASSWORD": builder_password,
            CLIENT_TAG: tag, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DBT_USE_COLORS": "false"}


def verdict(code: int, results: dict[str, Any] | None) -> int:
    """0 passed, 1 tests failed, 2 build error. Any non-zero dbt exit fails the build; the results
    only tell a failed test (status fail) from everything else."""
    statuses = {r.get("status") for r in (results or {}).get("results", [])}
    if code == 0 and results is not None and not statuses & {"fail", "error"}:
        return 0
    if code != 0 and "fail" in statuses and "error" not in statuses:
        return 1
    return 2


def rollback(ctx: Ctx, io: Any, before: dict[str, dict[str, Any]], invocation: str | None,
             expire: list[dict[str, Any]]) -> None:
    """Step 5, as the builder: each output whose main moved goes back to snapshot_before; the build's
    snapshots are named for expire. Every output gets its rolled_back row, moved or not, with main
    after the restore as snapshot_after (a create-or-replace's new snapshot, which reconcile reads)."""
    for o in ctx.outputs:
        b = before[o.relation]
        main, tag = heads(io, o.relation)
        if main != b["main"]:
            log(f"rollback {o.relation}: main {main} back to {b['main']}")
            restore(io, o, b["main"], tag is not None)
            if b["main"] is not None:
                main, tag = heads(io, o.relation)
                if ids := sorted(io.snapshots(o.relation) - b["ids"] - {main, tag}):
                    expire.append({"table": o.relation, "ids": ids})
            else:
                main = None
        record(ctx, io, event="rolled_back", table_name=o.relation, snapshot_before=b["main"], snapshot_after=main,
               dbt_invocation_id=invocation)


def reported(results: dict[str, Any] | None) -> set[str]:
    """The query ids dbt-trino reported, one per node: its adapter_response, the node's LAST
    statement only. A contract table is CREATE OR REPLACE then INSERT, and an INSERT of no rows
    commits no snapshot, so main is then the CREATE's, which no result names (Live.tagged has it)."""
    ids = {((r.get("adapter_response") or {}).get("query_id")) for r in (results or {}).get("results", [])}
    return {q for q in ids if isinstance(q, str) and QUERY_ID.fullmatch(q)}


def built_here(rel: str, meta: dict[str, Any], queries: set[str], window: tuple[datetime, datetime]) -> None:
    """The tested snapshot, a main this run moved, is this dbt invocation's own commit, not a write
    that landed after its tests (an orphaned query of an earlier run, stop_orphans' other end).
    Trino's Iceberg connector writes the committing query's id into the snapshot summary; `queries`
    is every statement this dbt invocation ran (Live.tagged, by its client tag) and every one dbt
    reported: the snapshot's must be one of them. When either side lacks ids, only the time can
    decide: committed inside the dbt invocation. Weaker, since a write after a node's tests but
    before dbt returns passes it; the log says which was used. A statement of this invocation that
    writes an output after its tests (a later model's or hook's) is k2gen check's SQL lint's to
    refuse, not this guard's: it carries the tag."""
    qid = meta.get("query_id")
    if qid and queries:
        if qid not in queries:
            raise Refused(f"{rel}: main was committed by query {qid}, which this dbt build did not run: a write after its tests")
        return
    log(f"{rel}: {'the snapshot names no query' if not qid else 'dbt reported no query id'}; the dbt invocation's time decides")
    if not window[0] <= meta["committed_at"] <= window[1]:
        raise Refused(f"{rel}: main was committed at {meta['committed_at']}, outside this dbt build ({window[0]} to {window[1]})")


def tag_change(o: Output, tested: int, previous: int | None) -> dict[str, Any]:
    """One output's part of the commit: the tag onto the tested snapshot, only while main is still
    that snapshot and the tag is still where this run found it (spike g, ADR-061 part 4)."""
    _, ns, table = o.relation.split(".")
    return {"identifier": {"namespace": [ns], "name": table},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": snap(tested)},
                             {"type": "assert-ref-snapshot-id", "ref": k2.AUDITED_TAG,
                              "snapshot-id": None if previous is None else snap(previous)}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": k2.AUDITED_TAG, "type": "tag", "snapshot-id": snap(tested)}]}


def needs_compaction(files: dict[str, int]) -> str | None:
    """Why an output is compacted now, from its main's `$files` (Live.files), or None to skip."""
    beyond = files["small"] - files["partitions"]
    if files["deletes"] >= COMPACT_DELETE_FILES or beyond >= COMPACT_SMALL_FILES:
        return f"{files['deletes']} delete files, {beyond} small files beyond one per partition"
    return None


def total(meta: dict[str, Any], key: str, rel: str) -> int:
    """One of a snapshot summary's totals (Live.snapshot), a string of digits; anything else refuses."""
    value = meta.get(key)
    if not (isinstance(value, str) and value.isascii() and value.isdigit()):
        raise Refused(f"{rel}: its snapshot summary's {key} is {value!r}, not a count")
    return int(value)


def rows_read(meta: dict[str, Any], rel: str) -> int:
    """The rows a snapshot reads, from its summary: total-records counts every row of its data
    files, a row a position delete hides included, so those deletes are taken off; an equality
    delete hides any number of rows, so a snapshot with one has no count here (Trino writes none)."""
    if total(meta, "equality_deletes", rel):
        raise Refused(f"{rel}: the snapshot holds equality deletes: its rows are not countable from its summary")
    return total(meta, "records", rel) - total(meta, "position_deletes", rel)


def compaction_guard(rel: str, tested: int, main: int, meta: dict[str, Any], tested_meta: dict[str, Any],
                     ours: str | None) -> None:
    """The one snapshot the wrapper accepts on top of the tested one after the tests (finding 5 of
    the phases 004 and 005 breakdown: built_here passes any write carrying the client tag): a
    `replace` (files rewritten, no row changed, by the Iceberg spec) whose parent is exactly the
    tested snapshot, committed by this run's own optimize statement (`ours`, its query id, the
    statement under this run's client tag), reading as many rows. Anything else raises Refused,
    a write after the tests."""
    parent, op, qid = meta.get("parent"), meta.get("operation"), meta.get("query_id")
    if op != "replace" or parent is None or snap(parent) != tested or ours is None or qid != ours:
        raise Refused(f"{rel}: main {main} is {op!r} on {parent} by query {qid}, not this run's compaction ({ours}) of the "
                      f"tested snapshot {tested}: a write after its tests")
    if (after := rows_read(meta, rel)) != (was := rows_read(tested_meta, rel)):
        raise Refused(f"{rel}: compaction {main} reads {after} rows, the tested snapshot {tested} {was}: a write after its tests")


def compact(io: Any, o: Output, tested: int, client: str) -> tuple[int, dict[str, Any]]:
    """Step 6, after the tests and before the tag commit, as the builder: an incremental output
    that needs_compaction() is compacted by one optimize under the run's client tag, so the tag
    moves onto the compaction and the next reconcile finds main on it (a compaction after the tag
    moved main past it, and reconcile would undo it). Returns (the snapshot the tag moves to, what
    the run records say: {} when nothing was tried). Compaction is not correctness: an optimize
    that fails or commits nothing publishes the tested snapshot, its reason kept; a commit that
    compaction_guard() refuses is rolled back to the tested snapshot and refused, as any write
    after the tests is (exit 3)."""
    if not o.incremental:
        return tested, {}
    ours = failed = None
    try:
        why = needs_compaction(io.files(o.relation, bool(o.partitioning)))
        if why is None:
            return tested, {}
        log(f"{o.relation}: {why}: compacted after its tests")
        ours = io.optimize(o.relation, client)
    except Exception as e:
        failed = f"{type(e).__name__}: {e}"
    main = heads(io, o.relation)[0]
    if main == tested:
        why = failed or "optimize rewrote nothing"
        log(f"{o.relation}: compaction skipped ({why}); the tested snapshot {tested} is published")
        return tested, {"compactionSkipped": scrub(why)[:COMPACTION_WHY_CHARS]}
    try:
        meta, was = io.snapshot(o.relation, main), io.snapshot(o.relation, tested)
        compaction_guard(o.relation, tested, main, meta, was, ours)
        done = {"files": [total(was, "data_files", o.relation), total(meta, "data_files", o.relation)],
                "deleteFiles": [total(was, "delete_files", o.relation), total(meta, "delete_files", o.relation)]}
    except Exception:
        log(f"{o.relation}: main {main} is refused on top of the tested snapshot {tested}; rolled back to it")
        restore(io, o, tested, True)
        raise
    log(f"{o.relation}: compacted to {main}: data files {done['files']}, delete files {done['deleteFiles']}")
    return main, {"compacted": done}


def publish(ctx: Ctx, io: Any, before: dict[str, dict[str, Any]], invocation: str | None,
            queries: set[str], window: tuple[datetime, datetime], warns: list[str] | None = None,
            xcom: dict[str, Any] | None = None, client: str | None = None) -> int:
    """Step 6, as the publisher, after every test passed. With `warns` (warn-severity failures,
    warned()) each `audited` row's detail is {"degraded": true, "warned": [...]} and `xcom` gains
    the same two keys once those rows are written; with none, both are as before. With `client`,
    this run's client tag, each incremental output is first compacted where it needs it
    (compact()), and the tag moves onto the compaction; its row's detail and `xcom` say so.

    INVARIANT: at every instant each reader view (a) reads its base at the `audited` tag, and the
    tag names only a snapshot whose tests passed; (b) selects only columns that the tagged snapshot
    holds with the view's types. (a): the tag moves only here, onto the snapshot dbt's tests read,
    in one commit whose every change requires main to be that snapshot and the tag to be where step
    3 found it, so a write after the tests or a tag moved by anyone else gets 409 and moves nothing.
    (b): before the commit each view is narrowed to C and old and new (valid on both snapshots),
    and widened to C and new only after it. A crash between any two statements leaves narrowed
    views over the old tag, or narrowed or widened views over the new tested one; the next
    reconcile finishes from what the tag holds. Never widen before the commit."""
    tested: dict[str, int] = {}
    steps: dict[str, tuple[str | None, str | None]] = {}
    deliberate = []
    for o in ctx.outputs:
        main, tag = heads(io, o.relation)
        if main is None:
            raise Refused(f"{o.relation}: the build passed and left no main")
        if tag != before[o.relation]["tag"]:
            raise Refused(f"{o.relation}: the tag moved during the build ({before[o.relation]['tag']} to {tag})")
        if main != before[o.relation]["main"]:
            built_here(o.relation, io.snapshot(o.relation, main), queries, window)
        tested[o.relation] = main
    compaction: dict[str, dict[str, Any]] = {}
    for o in ctx.outputs:  # after every output's checks: none is compacted for a build refused anyway
        tested[o.relation], compaction[o.relation] = compact(io, o, tested[o.relation], client) if client else (tested[o.relation], {})
    for o in ctx.outputs:
        tag = before[o.relation]["tag"]
        old = io.columns(o.relation, tag) if tag is not None else None
        new = io.columns(o.relation, tested[o.relation])  # a compaction changes no column: the tested snapshot's
        if old == new:
            steps[o.relation] = (None, None)  # the view reads the tag by name: moving it is the publish
            continue
        try:
            steps[o.relation] = lake.view_update(o.view, old, new)
        except lake.NoCommonColumns:
            # The deliberate path (lake.NoCommonColumns): no view is valid on both snapshots, so
            # readers' view fails from the commit to the widen, closed, and the run pages.
            deliberate.append(o.view.relation)
            steps[o.relation] = (None, lake.create_view(o.view, list(new)))
    for narrow, _ in steps.values():
        if narrow:
            io.sql("publisher", narrow)
    changes = [tag_change(o, tested[o.relation], before[o.relation]["tag"]) for o in ctx.outputs]
    try:
        status = io.commit(ctx.warehouse, changes)
    except catalog_drift.Unreachable as e:
        log(f"tag commit: no answer from the catalog ({e}); views stay narrowed, the next reconcile decides")
        return 3
    if status not in (200, 204):
        log(f"tag commit: HTTP {status}{' (a stale expectation: the tag or main moved)' if status == 409 else ''}; "
            "nothing moved, views stay narrowed")
        return 3
    for _, widen in steps.values():
        if widen:
            io.sql("publisher", widen)
    degraded = {"degraded": True, "warned": list(warns)} if warns else {}
    for o in ctx.outputs:
        said = degraded | compaction[o.relation]
        detail = {"detail": json.dumps(said, separators=(",", ":"))} if said else {}
        record(ctx, io, event="audited", table_name=o.relation, snapshot_before=before[o.relation]["tag"],
               snapshot_after=tested[o.relation], dbt_invocation_id=invocation, **detail)
    if xcom is not None:
        xcom.update(degraded)
        if done := [{"table": r, **c["compacted"]} for r, c in compaction.items() if "compacted" in c]:
            xcom["compacted"] = done
        if skipped := [{"table": r, "why": c["compactionSkipped"]} for r, c in compaction.items() if "compactionSkipped" in c]:
            xcom["compactionSkipped"] = skipped
    if deliberate:
        log(f"published, but no column was common to the old and new snapshots of {', '.join(deliberate)}: "
            "readers lost the view between the tag commit and the widen")
        return 3
    return 0


def run(ctx: Ctx, io: Any, xcom: dict[str, Any] | None = None) -> tuple[int, list[dict[str, Any]]]:
    """Steps 1 to 6; (exit code, expire). A degraded publish adds its keys to `xcom` (publish())."""
    expire: list[dict[str, Any]] = []
    try:
        stop_orphans(ctx, io)
        reconcile(ctx, io)
    except Exception as e:
        log(f"reconcile: {type(e).__name__}: {e}")
        return 3, expire
    try:
        inputs = resolve(ctx, io)
    except Untested as e:
        log(f"resolve: {e}")
        return 4, expire
    except Exception as e:
        log(f"resolve: {type(e).__name__}: {e}")
        return 2, expire
    try:
        before = {}
        for o in ctx.outputs:
            main, tag = heads(io, o.relation)
            before[o.relation] = {"main": main, "tag": tag, "ids": io.snapshots(o.relation) if main is not None else set()}
        listed = [{"contractId": i.contract, "relation": i.relation, "snapshot": inputs[i.contract], "state": i.state,
                   "lagMinutes": lag(ctx.started_at, None if inputs[i.contract] is None else io.committed(i.relation, inputs[i.contract]))}
                  for i in ctx.inputs]
        for o in ctx.outputs:
            record(ctx, io, event="started", table_name=o.relation, snapshot_before=before[o.relation]["main"], inputs=listed)
    except Exception as e:
        log(f"started: {type(e).__name__}: {e}")
        return 3, expire
    window_start, tag = datetime.now(timezone.utc), client_tag()
    try:
        code, results = io.dbt(dbt_argv(ctx, inputs), tag)
    except Exception as e:
        log(f"dbt: {type(e).__name__}: {e}")
        code, results = 2, None
    window = (window_start, datetime.now(timezone.utc))
    invocation = ((results or {}).get("metadata") or {}).get("invocation_id")
    invocation = invocation if isinstance(invocation, str) and INVOCATION.fullmatch(invocation) else None
    outcome = verdict(code, results)
    try:
        if outcome:
            log(f"dbt build {'failed its tests' if outcome == 1 else 'failed'} (exit {code}); restoring the outputs")
            rollback(ctx, io, before, invocation, expire)
            return outcome, expire
        return publish(ctx, io, before, invocation, reported(results) | io.tagged(tag), window, warned(results), xcom, tag), expire
    except Exception as e:
        log(f"{'rollback' if outcome else 'publish'}: {type(e).__name__}: {e}")
        return 3, expire


# ---- the adapters: Trino as builder, publisher and svc.runs; the catalog; dbt ------------------

def load_k2runs(release_path: Path) -> Any:
    """The release's own run-records client, not the image's."""
    path = release_path / k2.PLATFORM_DIR / "platform" / "runs" / "k2runs.py"
    spec = importlib.util.spec_from_file_location("k2runs", path)
    if spec is None or spec.loader is None:
        raise Refused(f"{path}: no run-records client in the release")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_env(ctx: Ctx, environ: dict[str, str]) -> None:
    """The contract's seven names, each naming this run's own principals and release."""
    if missing := [e for e in ENV if not environ.get(e)]:
        raise Refused(f"environment lacks {missing}")
    want = {"K2_TRINO_USER": ctx.writer, "K2_PUB_USER": ctx.publisher, "K2_RUNS_USER": RUNS_USER, "K2_RELEASE": ctx.release}
    if wrong := [e for e, v in want.items() if environ[e] != v]:
        raise Refused(f"{wrong} do not name this run's principals and release")
    if not HOST.fullmatch(environ["K2_TRINO_HOST"]) or not environ["K2_TRINO_INTERNAL_PORT"].isdigit():
        raise Refused("K2_TRINO_HOST or K2_TRINO_INTERNAL_PORT is not a host name and port")
    if not re.fullmatch(r"https?://[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9/_-]*)?", environ["K2_CATALOG_URI"]):
        raise Refused("K2_CATALOG_URI is not a catalog URL")


class Live:
    """The real reads and writes. Trino over HTTPS with the CA; one connection per role."""

    USERS = {"builder": "K2_TRINO_USER", "publisher": "K2_PUB_USER", "runs": "K2_RUNS_USER"}

    def __init__(self, ctx: Ctx, environ: dict[str, str]):
        check_env(ctx, environ)
        self.ctx, self.environ, self.k2runs = ctx, environ, load_k2runs(ctx.release_path)
        self.uri = environ["K2_CATALOG_URI"]
        self.conns: dict[str, Any] = {}
        self.catalogs: dict[str, Any] = {}

    def connect(self, role: str, client_tags: list[str] | None = None) -> Any:
        user = self.environ[self.USERS[role]]
        return trino.dbapi.connect(host=self.environ["K2_TRINO_HOST"], port=int(self.environ["K2_TRINO_INTERNAL_PORT"]), user=user,
                                   http_scheme="https", verify=CA, auth=trino.auth.BasicAuthentication(user, password(role)),
                                   client_tags=client_tags)

    def conn(self, role: str) -> Any:
        if role not in self.conns:
            self.conns[role] = self.connect(role)
        return self.conns[role]

    def query(self, role: str, sql: str, params: list[Any] | None = None) -> tuple[list[Any], list[Any]]:
        cur = self.conn(role).cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()  # the client runs the statement as the result is drawn
        return rows, list(cur.description or [])

    def refs(self, role: str, rel: str) -> dict[str, tuple[str, int]] | None:
        relation(rel)
        try:
            rows, _ = self.query(role, f"SELECT name, type, snapshot_id FROM {k2.quote_relation(rel + '$refs')}")
        except TrinoUserError as e:
            if e.error_name in ("TABLE_NOT_FOUND", "SCHEMA_NOT_FOUND"):
                return None
            raise
        return {str(n): (str(t), snap(s)) for n, t, s in rows}

    def columns(self, rel: str, snapshot: int | None) -> dict[str, str]:
        at = "" if snapshot is None else f" {version(snapshot)}"
        _, description = self.query("builder", f"SELECT * FROM {relation(rel)}{at} LIMIT 0")
        return {d[0]: d[1] for d in description}  # type_code is Trino's type as DESCRIBE spells it

    def rest(self, method: str, path: str) -> Any:
        """Trino's coordinator REST API as the builder, over the same HTTPS and CA. Trino lists to a
        user, and lets it cancel, only its own queries unless a `queries` rule grants view or kill,
        which none does but the operator's (trino_rules_test.py's `queries` row), so the builder
        reaches its own orphans and nothing else, and no rule is added for it (review Q1)."""
        user = self.environ["K2_TRINO_USER"]
        token = base64.b64encode(f"{user}:{password('builder')}".encode()).decode()
        url = f"https://{self.environ['K2_TRINO_HOST']}:{self.environ['K2_TRINO_INTERNAL_PORT']}{path}"
        req = urllib.request.Request(url, method=method, headers={"Authorization": f"Basic {token}", "X-Trino-User": user})
        with urllib.request.urlopen(req, context=ssl.create_default_context(cafile=CA), timeout=30) as r:
            body = r.read()
        return json.loads(body) if body else None

    def running(self) -> list[str]:
        """The builder's queries Trino still runs (GET /v1/query: a listing is no query itself)."""
        return sorted(str(q["queryId"]) for q in self.rest("GET", "/v1/query")
                      if (q.get("session") or {}).get("user") == self.ctx.writer and q.get("state") not in ("FINISHED", "FAILED"))

    def tagged(self, tag: str) -> set[str]:
        """Every query of the builder's that carried the client tag `tag` (GET /v1/query, which lists
        each query's session with its clientTags): the statements of this run's dbt build.
        ponytail: Trino lists a finished query for query.min-expire-age (15 min) and after that only
        while it lists fewer than query.max-history (100), so a build whose untagged-by-dbt commit is
        older than that at publish is refused, never passed; raise min-expire-age (trino_etc.py) when
        a build is measured near 15 min."""
        return {str(q["queryId"]) for q in self.rest("GET", "/v1/query")
                if (s := q.get("session") or {}).get("user") == self.ctx.writer and tag in (s.get("clientTags") or [])
                and QUERY_ID.fullmatch(str(q["queryId"]))}

    def kill(self, query_id: str) -> None:
        """Cancel one of the builder's own queries (DELETE /v1/query/<id>). Refused credentials
        refuse; any other answer (it ended meanwhile, an unknown id) is left to the wait."""
        if not QUERY_ID.fullmatch(query_id):
            raise Refused(f"{query_id!r} is not a Trino query id")
        try:
            self.rest("DELETE", f"/v1/query/{query_id}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise Refused(f"Trino refused {self.ctx.writer} the cancel of its own query {query_id}: HTTP {e.code}") from None
            log(f"cancel {query_id}: HTTP {e.code}; the wait decides")

    SNAPSHOT = {"committed_at": "committed_at", "query_id": "element_at(summary, 'trino_query_id')", "parent": "parent_id",
                "operation": "operation", "records": "element_at(summary, 'total-records')",
                "position_deletes": "element_at(summary, 'total-position-deletes')",
                "equality_deletes": "element_at(summary, 'total-equality-deletes')",
                "data_files": "element_at(summary, 'total-data-files')", "delete_files": "element_at(summary, 'total-delete-files')"}

    def snapshot(self, rel: str, snapshot: int) -> dict[str, Any]:
        """One snapshot's commit time, committing query, parent, operation and summary totals, from `t$snapshots`."""
        relation(rel)
        rows, _ = self.query("builder", f"SELECT {', '.join(self.SNAPSHOT.values())} FROM "
                                        f"{k2.quote_relation(rel + '$snapshots')} WHERE snapshot_id = {snap(snapshot)}")
        if not rows:
            raise Refused(f"{rel}: snapshot {snapshot} is not in its $snapshots")
        return dict(zip(self.SNAPSHOT, rows[0], strict=True))

    def files(self, rel: str, partitioned: bool) -> dict[str, int]:
        """Main's files from `t$files`, as the builder: data files, those under SMALL_FILE_BYTES, the
        partitions those are in, and delete files (content 1, position, and 2, equality). Only a
        partitioned table's `$files` has a `partition` column; the contract says which this is, and
        a table that disagrees fails the read, so its compaction is skipped."""
        relation(rel)
        small, part = f"content = 0 AND file_size_in_bytes < {SMALL_FILE_BYTES}", '"partition"' if partitioned else "1"
        rows, _ = self.query("builder", f"SELECT count_if(content = 0), count_if({small}), count(DISTINCT CASE WHEN {small} "
                                        f"THEN {part} END), count_if(content <> 0) FROM {k2.quote_relation(rel + '$files')}")
        return dict(zip(("data", "small", "partitions", "deletes"), (int(v) for v in rows[0]), strict=True))

    def optimize(self, rel: str, tag: str) -> str | None:
        """Step 6's compaction: ALTER TABLE ... EXECUTE optimize as the builder, the files under
        SMALL_FILE_BYTES and the deletes on them rewritten, the statement carrying this run's client
        tag; its query id, which the commit's summary must name (compaction_guard), or None."""
        cur = self.connect("builder", [tag]).cursor()
        cur.execute(f"ALTER TABLE {relation(rel)} EXECUTE optimize(file_size_threshold => '{SMALL_FILE_BYTES // 2**20}MB')")
        cur.fetchall()
        qid = cur.query_id
        return qid if isinstance(qid, str) and QUERY_ID.fullmatch(qid) else None

    def snapshots(self, rel: str) -> set[int]:
        relation(rel)
        rows, _ = self.query("builder", f"SELECT snapshot_id FROM {k2.quote_relation(rel + '$snapshots')}")
        return {snap(r[0]) for r in rows}

    def sql(self, role: str, statement: str) -> None:
        self.query(role, statement)

    def record(self, **row: Any) -> None:
        self.k2runs.record(self.conn("runs"), **row)

    def history(self, product: str) -> list[dict[str, Any]]:
        # ponytail: every build row of the product, unbounded: about 3 rows per output per run, so
        # 144 runs a day of one output read some 150,000 rows after a year. Bound it by recorded_at
        # (the partition column) to the retention days when a reconcile's read shows in its time.
        rows, _ = self.query("runs", f"SELECT run_id, event, table_name, snapshot_before, snapshot_after FROM {self.k2runs.TABLE} "
                                     "WHERE product_id = ? AND task = 'build' ORDER BY recorded_at", [product])
        return [dict(zip(("run_id", "event", "table_name", "snapshot_before", "snapshot_after"), r, strict=True)) for r in rows]

    def dbt(self, argv: list[str], tag: str) -> tuple[int, dict[str, Any] | None]:
        results = WORK / "target" / "run_results.json"
        WORK.mkdir(parents=True, exist_ok=True)
        results.unlink(missing_ok=True)
        done = subprocess.run(argv, env=dbt_env(self.environ, password("builder"), tag), stdin=subprocess.DEVNULL,
                              stdout=sys.stderr, stderr=sys.stderr, check=False)
        return done.returncode, json.loads(results.read_text()) if results.exists() else None

    def cat(self, warehouse: str | None = None) -> Any:
        warehouse = warehouse or self.ctx.warehouse
        if warehouse not in self.catalogs:
            self.catalogs[warehouse] = catalog_drift.Catalog(self.uri, warehouse)
        return self.catalogs[warehouse]

    def committed(self, rel: str, snapshot: int) -> datetime:
        """An input's audited commit time (commit_time()), from its table's metadata in Lakekeeper:
        catalog `lake` is warehouse k2, any other reader catalog is its domain's warehouse."""
        relation(rel)
        catalog, ns, table = rel.split(".")
        return commit_time(self.cat(k2.LAKE_WAREHOUSE if catalog == k2.LAKE else catalog).table(ns, table), snapshot, rel)

    def commit(self, warehouse: str, changes: list[dict[str, Any]]) -> int:
        if warehouse != self.ctx.warehouse:
            raise Refused(f"warehouse {warehouse} is not this product's")
        status, _ = catalog_drift.call("POST", f"{self.cat().base}/transactions/commit", {"table-changes": changes})
        return status

    def view_sql(self, view: k2.View) -> str | None:
        _, ns, name = view.relation.split(".")
        meta = self.cat().view(ns, name)
        return catalog_drift.view_state(meta)["sql"][0] if meta else None

    def drift(self) -> dict[str, list[str]]:
        """The drift check over this product's own namespaces, its tags held to the run records as
        svc.runs. Another product running beside this one is mid-change for a moment (dbt's
        transient tables, a tag ahead of its audited row, a narrowed view), so it is left to
        release-verify, which checks the whole release."""
        runs = self.conn("runs")
        own = {(self.ctx.warehouse, rel.split(".")[1]) for o in self.ctx.outputs for rel in (o.relation, o.view.relation)}
        found: dict[str, list[str]] = catalog_drift.drift(self.uri, catalog_drift.expected(self.ctx.release_path),
                                                          newest_audited=lambda t: self.k2runs.newest_audited(runs, t), only=own)
        return found


# ---- k2-dbt docs (W3-11): the release's dbt docs site, which k2-api serves at /dbt-docs/ ---------

DOCS_FILES = ("index.html", "manifest.json", "catalog.json")  # what dbt's docs app loads, and all it loads
SITE_TREE, SITE_CURRENT = "tree-", "current"  # as platform/catalog-site/k2site.py names them


def docs_argv(project: Path) -> list[list[str]]:
    """dbt parse (manifest.json), then docs generate (index.html, catalog.json) without compiling
    and with an empty catalog: neither opens a connection, so the docs run as no principal and
    read no password (compiling would connect: is_incremental asks whether each table exists).
    Lost against a full generate: each model's compiled SQL, and the warehouse's column types and
    table statistics; the descriptions, raw SQL, tests, lineage and contract columns remain."""
    common = ["--project-dir", str(project), "--profiles-dir", str(PROFILES_DIR), "--target-path", str(WORK / "docs"),
              "--log-path", str(WORK / "logs"), "--no-partial-parse"]
    return [["dbt", "parse", *common], ["dbt", "docs", "generate", "--no-compile", "--empty-catalog", *common]]


def docs_env(environ: dict[str, str]) -> dict[str, str]:
    """The docs' whole environment, no secret in it: dbt/profiles.yml names the password variable
    with no default, so it must be set, and nothing connects, so it is a placeholder; the host is
    one that never resolves, so a dbt that did try would fail rather than reach a Trino."""
    return {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(WORK), "LANG": "C.UTF-8",
            "K2_TRINO_HOST": "trino.invalid", "DBT_ENV_SECRET_K2_TRINO_PASSWORD": "unused",
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DBT_USE_COLORS": "false"}


def publish_site(files: dict[str, bytes], out: Path) -> Path:
    """k2site.publish's swap, copied so neither image ships the other's code: a new tree beside the
    old, then a relative `current` symlink renamed onto it (atomic on one file system); the tree
    before is kept for a reader mid-request, older ones are removed."""
    current = out / SITE_CURRENT
    if current.exists() and not current.is_symlink():
        raise Refused(f"{current} is not a symlink; move it away once, then rerun")
    out.mkdir(parents=True, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix=SITE_TREE, dir=out))
    for name, data in files.items():
        (tree / name).write_bytes(data)
        (tree / name).chmod(0o644)
    tree.chmod(0o755)  # mkdtemp makes 0700; k2-api reads as another user
    previous = os.readlink(current) if current.is_symlink() else None
    staged = out / f".{SITE_CURRENT}.new"
    staged.unlink(missing_ok=True)
    staged.symlink_to(tree.name)
    os.replace(staged, current)
    for old in out.glob(f"{SITE_TREE}*"):
        if old.name not in (tree.name, previous):
            shutil.rmtree(old)
    return tree


def docs(release: str, out: Path, environ: dict[str, str]) -> Path:
    """`k2-dbt docs --release <id> --out <dir>`: the site of /k2/releases/<id>/compiled/dbt,
    published as <dir>/current. A failed dbt step publishes nothing, so the site before stays."""
    if not RELEASE_ID.fullmatch(release):
        raise Refused(f"release {release!r} is not an id")
    project = RELEASES / release / "compiled" / "dbt"
    if not (project / "dbt_project.yml").is_file():
        raise Refused(f"{project}: the release holds no compiled dbt project")
    shutil.rmtree(WORK / "docs", ignore_errors=True)
    for argv in docs_argv(project):
        code = subprocess.run(argv, env=docs_env(environ), stdin=subprocess.DEVNULL, check=False).returncode
        if code != 0:
            raise Refused(f"{' '.join(argv[:3])} exited {code}; the site is unchanged")
    return publish_site({name: (WORK / "docs" / name).read_bytes() for name in DOCS_FILES}, out)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="k2-dbt", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--release", required=True)
    b.add_argument("--product", required=True)
    b.add_argument("--run-id", required=True)
    d = sub.add_parser("docs")
    d.add_argument("--release", required=True)
    d.add_argument("--out", required=True, type=Path)
    if argv in (["--help"], ["-h"]):  # not a run, so no XCom: the image's build and CI check its imports by it
        ap.print_help()
        return 0
    if argv[:1] == ["docs"]:  # deploy's step after a swap, not an Airflow run: no XCom line
        try:
            a = ap.parse_args(argv)
            print(f"k2-dbt: wrote {docs(a.release, a.out, dict(os.environ))}", flush=True)
            return 0
        except SystemExit:
            return 2
        except Exception as e:
            log(f"{type(e).__name__}: {e}")
            return 1
    code, expire, xcom = 3, [], {}
    try:
        try:
            a = ap.parse_args(argv)
        except SystemExit:
            raise Refused("usage: k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>") from None
        ctx = load(a.release, a.product, a.run_id)
        code, expire = run(ctx, Live(ctx, dict(os.environ)), xcom)
    except Exception as e:
        log(f"{type(e).__name__}: {e}")
        code = 3
    finally:
        print(json.dumps({"exit": code, "expire": expire, **xcom}, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
