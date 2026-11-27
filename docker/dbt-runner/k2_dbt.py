"""k2-dbt, the dbt wrapper: what guarantees readers see only tested data (ADR-061 parts 4 and 5).

    k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>

The frozen contract is docs/development/phase-001-interfaces.md, "Run-time contracts", "The dbt
wrapper"; the measurements behind it are spikes (d), (f) and (g) of
docs/research/2026-11-26-release-spine-spikes.md. In order:

1. reconcile: an output whose `main` is not its `audited` tag (an earlier run died) is restored to
   the tag as the builder (no tag: DROP TABLE), `reconciled`; a tag ahead of the run records, which
   only an unfinished run explains (it died between the tag commit and its `audited` row), gets
   that row; every view is put back to C and the tagged columns; then the catalog drift check,
   with the run-record comparison, must find nothing in this product's namespaces (Live.drift).
2. resolve each input's tested snapshot from `t$refs` as svc.runs, the whole set read twice and
   compared (a publish between two reads restarts it, RESOLVE_ATTEMPTS times); a required input
   never tested exits 4.
3. `started` per output, with `snapshot_before`, its main.
4. one `dbt build --selector <fence> --vars <k2_inputs, k2_run_id, k2_release>` as the builder,
   unit tests inside it (spike d). Any non-zero exit is a failed build.
5. on a failure, every output whose main moved is restored as the builder, `rolled_back`.
6. on a pass, as the publisher: views narrowed, one compare-and-set tag commit, views widened,
   `audited` (publish()).

Exits: 0 passed and published; 1 tests failed, outputs restored; 2 build error, outputs restored;
3 a restore, the tag commit, the view sync, the reconcile or the drift check failed (pages); 4 a
required input was never tested. The last stdout line, on every path, is the XCom
{"exit": n, "expire": [{"table": <relation>, "ids": [...]}]}: the failed build's snapshots, which
the Spark `expire` task removes by id. dbt's own output goes to stderr.

Trust: identifiers come from the compiled release only, each part held to k2gen's IDENTIFIER and
quoted by k2gen's quote_relation; snapshot ids pass snap() (an int, never a string spliced in);
passwords are read from their files at connect time, never logged (log() scrubs every one read),
never in an argument list. Trino cannot move a tag (spike g), so the tag commit and the current
view SQL are the two calls to Lakekeeper's REST API, at K2_CATALOG_URI (steps 1 and 6), beside the
drift check's own reads.

Imports beyond the standard library: trino, k2gen (platform/k2gen), and catalog_drift
(scripts/verify/catalog_drift.py), which the image puts beside this file; k2runs is loaded from the
release itself (<release>/platform/platform/runs/k2runs.py).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import catalog_drift
from k2gen import model as k2
from k2gen.emit import lake

SECRETS = "/run/k2-secrets"
PASSWORDS = {"builder": f"{SECRETS}/builder.password", "publisher": f"{SECRETS}/publisher.password",
             "runs": f"{SECRETS}/runs.password"}
CA = f"{SECRETS}/tls/ca.pem"
RELEASES = Path(k2.RELEASES_MOUNT)
WORK = Path("/tmp/k2-dbt")  # the one-shot's only writable path (docker/airflow/dags/oneshot.py TMPFS)
PROFILES_DIR = Path(__file__).resolve().parent  # dbt/profiles.yml, which the image puts beside this file
ENV = ("K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER", "K2_PUB_USER", "K2_RUNS_USER",
       "K2_CATALOG_URI", "K2_RELEASE")
RUNS_USER = "svc.runs"
RESOLVE_ATTEMPTS = 3
TERMINAL = ("rolled_back", "audited", "reconciled")
RELEASE_ID = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?-[0-9a-f]{12}")  # run-record.schema.json
PRODUCT_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z][a-z0-9]*(-[a-z0-9]+)*")
RUN_ID = re.compile(r"([a-z][a-z0-9_]*)/([A-Za-z0-9][A-Za-z0-9_.:+-]{0,200})")
HOST = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
INVOCATION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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


@dataclass(frozen=True)
class Input:
    contract: str
    relation: str  # model.input_relation, read through a read-only catalog
    required: bool
    ref: str  # model.tested_ref: audited (a tag) or main (a branch)
    key: str  # "<product fence>/<table>", k2_input's key
    state: str  # the input product's status in this release


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
             views: list[dict[str, Any]], tables: dict[str, tuple[str, str, str]]) -> Ctx:
    """The run's context from the release's compiled pipeline.json and views.json; `tables` maps
    each input's contract id to (input_relation, tested_ref, product status), from k2gen's model."""
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
        outputs.append(Output(rel, view))
    if not outputs:
        raise Refused(f"{product}: its build names no output")
    inputs = []
    for i in pipeline.get("inputs", []):
        cid = i["contractId"]
        if cid not in tables or tables[cid][0] != i["relation"]:
            raise Refused(f"{product}: input {cid} is not the release's table at {i['relation']!r}")
        relation(i["relation"])
        _, producer, table = cid.split(".")
        inputs.append(Input(cid, i["relation"], bool(i["required"]), tables[cid][1], f"{k2.fence(producer)}/{table}",
                            tables[cid][2]))
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
    m = k2.load(docset.read(path / k2.DOCUMENTS_DIR))
    status = {p.id: p.status for p in m.products}
    tables = {t.contract_id: (k2.input_relation(t), k2.tested_ref(t), status[k2.product_id(p.domain, p.name)])
              for p in m.products for t in p.tables if t.build.kind in k2.LAKE_BUILDS}
    return ctx_from(release, path, product, run_id, pipeline, views, tables)


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
                      now: dict[str, str]) -> str:
    """How an output goes back to `target`, as the builder (spike f): no target, DROP TABLE (never a
    tagged table); the same columns and types in the same order, rollback_to_snapshot; otherwise a
    create-or-replace from the old snapshot, since a rollback restores rows but not the schema."""
    q = relation(rel)
    if target is None:
        if tagged:
            raise Refused(f"{rel}: no snapshot to restore to, and it carries a tag: never dropped")
        return f"DROP TABLE {q}"
    if at_target is not None and list(at_target.items()) == list(now.items()):
        return f"ALTER TABLE {q} EXECUTE rollback_to_snapshot({snap(target)})"
    return f"CREATE OR REPLACE TABLE {q} AS SELECT * FROM {q} {version(target)}"


def restore(io: Any, rel: str, target: int | None, tagged: bool) -> None:
    at_target = io.columns(rel, target) if target is not None else None
    io.sql("builder", restore_statement(rel, target, tagged, at_target, io.columns(rel, None)))


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
            restore(io, o.relation, target, tag is not None)
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


def resolve(ctx: Ctx, io: Any) -> dict[str, int | None]:
    """Step 2: every input's tested snapshot, as svc.runs. One full read can straddle a publish,
    so the set is read twice and must agree; tags only move forward, so two equal reads hold one
    instant between them."""
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
    return first


def dbt_argv(ctx: Ctx, inputs: dict[str, int | None]) -> list[str]:
    """Step 4's one invocation. An optional input never tested is left out of k2_inputs, so a model
    that reads it fails at parse (k2_input), never reads it unpinned."""
    pinned = {i.key: {"snapshot": snap(inputs[i.contract])} for i in ctx.inputs if inputs[i.contract] is not None}
    variables = {"k2_inputs": pinned, "k2_run_id": ctx.run_id, "k2_release": ctx.release}
    return ["dbt", "build", "--selector", ctx.selector, "--vars", json.dumps(variables, sort_keys=True),
            "--project-dir", str(ctx.release_path / "compiled" / "dbt"), "--profiles-dir", str(PROFILES_DIR),
            "--target-path", str(WORK / "target"), "--log-path", str(WORK / "logs")]


def dbt_env(environ: dict[str, str], builder_password: str) -> dict[str, str]:
    """The dbt subprocess's whole environment: what dbt and dbt/profiles.yml read, the builder's
    credentials only (never the publisher's, svc.runs's or the catalog's)."""
    return {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(WORK), "LANG": "C.UTF-8",
            "K2_TRINO_HOST": environ["K2_TRINO_HOST"], "K2_TRINO_INTERNAL_PORT": environ["K2_TRINO_INTERNAL_PORT"],
            "K2_TRINO_USER": environ["K2_TRINO_USER"], "DBT_ENV_SECRET_K2_TRINO_PASSWORD": builder_password,
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DBT_USE_COLORS": "false"}


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
            restore(io, o.relation, b["main"], tag is not None)
            if b["main"] is not None:
                main, tag = heads(io, o.relation)
                if ids := sorted(io.snapshots(o.relation) - b["ids"] - {main, tag}):
                    expire.append({"table": o.relation, "ids": ids})
            else:
                main = None
        record(ctx, io, event="rolled_back", table_name=o.relation, snapshot_before=b["main"], snapshot_after=main,
               dbt_invocation_id=invocation)


def tag_change(o: Output, tested: int, previous: int | None) -> dict[str, Any]:
    """One output's part of the commit: the tag onto the tested snapshot, only while main is still
    that snapshot and the tag is still where this run found it (spike g, ADR-061 part 4)."""
    _, ns, table = o.relation.split(".")
    return {"identifier": {"namespace": [ns], "name": table},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": snap(tested)},
                             {"type": "assert-ref-snapshot-id", "ref": k2.AUDITED_TAG,
                              "snapshot-id": None if previous is None else snap(previous)}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": k2.AUDITED_TAG, "type": "tag", "snapshot-id": snap(tested)}]}


def publish(ctx: Ctx, io: Any, before: dict[str, dict[str, Any]], invocation: str | None) -> int:
    """Step 6, as the publisher, after every test passed.

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
        tested[o.relation] = main
        old = io.columns(o.relation, tag) if tag is not None else None
        new = io.columns(o.relation, main)
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
    for o in ctx.outputs:
        record(ctx, io, event="audited", table_name=o.relation, snapshot_before=before[o.relation]["tag"],
               snapshot_after=tested[o.relation], dbt_invocation_id=invocation)
    if deliberate:
        log(f"published, but no column was common to the old and new snapshots of {', '.join(deliberate)}: "
            "readers lost the view between the tag commit and the widen")
        return 3
    return 0


def run(ctx: Ctx, io: Any) -> tuple[int, list[dict[str, Any]]]:
    """Steps 1 to 6; (exit code, expire)."""
    expire: list[dict[str, Any]] = []
    try:
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
                   "lagMinutes": None} for i in ctx.inputs]
        for o in ctx.outputs:
            record(ctx, io, event="started", table_name=o.relation, snapshot_before=before[o.relation]["main"], inputs=listed)
    except Exception as e:
        log(f"started: {type(e).__name__}: {e}")
        return 3, expire
    try:
        code, results = io.dbt(dbt_argv(ctx, inputs))
    except Exception as e:
        log(f"dbt: {type(e).__name__}: {e}")
        code, results = 2, None
    invocation = ((results or {}).get("metadata") or {}).get("invocation_id")
    invocation = invocation if isinstance(invocation, str) and INVOCATION.fullmatch(invocation) else None
    outcome = verdict(code, results)
    try:
        if outcome:
            log(f"dbt build {'failed its tests' if outcome == 1 else 'failed'} (exit {code}); restoring the outputs")
            rollback(ctx, io, before, invocation, expire)
            return outcome, expire
        return publish(ctx, io, before, invocation), expire
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
        self.catalog: Any = None

    def conn(self, role: str) -> Any:
        if role not in self.conns:
            import trino
            user = self.environ[self.USERS[role]]
            self.conns[role] = trino.dbapi.connect(
                host=self.environ["K2_TRINO_HOST"], port=int(self.environ["K2_TRINO_INTERNAL_PORT"]), user=user,
                http_scheme="https", verify=CA, auth=trino.auth.BasicAuthentication(user, password(role)))
        return self.conns[role]

    def query(self, role: str, sql: str, params: list[Any] | None = None) -> tuple[list[Any], list[Any]]:
        cur = self.conn(role).cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()  # the client runs the statement as the result is drawn
        return rows, list(cur.description or [])

    def refs(self, role: str, rel: str) -> dict[str, tuple[str, int]] | None:
        relation(rel)
        from trino.exceptions import TrinoUserError
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

    def snapshots(self, rel: str) -> set[int]:
        relation(rel)
        rows, _ = self.query("builder", f"SELECT snapshot_id FROM {k2.quote_relation(rel + '$snapshots')}")
        return {snap(r[0]) for r in rows}

    def sql(self, role: str, statement: str) -> None:
        self.query(role, statement)

    def record(self, **row: Any) -> None:
        self.k2runs.record(self.conn("runs"), **row)

    def history(self, product: str) -> list[dict[str, Any]]:
        rows, _ = self.query("runs", f"SELECT run_id, event, table_name, snapshot_before, snapshot_after FROM {self.k2runs.TABLE} "
                                     "WHERE product_id = ? AND task = 'build' ORDER BY recorded_at", [product])
        return [dict(zip(("run_id", "event", "table_name", "snapshot_before", "snapshot_after"), r, strict=True)) for r in rows]

    def dbt(self, argv: list[str]) -> tuple[int, dict[str, Any] | None]:
        results = WORK / "target" / "run_results.json"
        results.unlink(missing_ok=True)
        done = subprocess.run(argv, env=dbt_env(self.environ, password("builder")), stdin=subprocess.DEVNULL,
                              stdout=sys.stderr, stderr=sys.stderr, check=False)
        return done.returncode, json.loads(results.read_text()) if results.exists() else None

    def cat(self) -> Any:
        if self.catalog is None:
            self.catalog = catalog_drift.Catalog(self.uri, self.ctx.warehouse)
        return self.catalog

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


def main(argv: list[str] | None = None) -> int:
    code, expire = 3, []
    try:
        ap = argparse.ArgumentParser(prog="k2-dbt", description=__doc__.split("\n\n")[0])
        sub = ap.add_subparsers(dest="cmd", required=True)
        b = sub.add_parser("build")
        b.add_argument("--release", required=True)
        b.add_argument("--product", required=True)
        b.add_argument("--run-id", required=True)
        try:
            a = ap.parse_args(argv)
        except SystemExit:
            raise Refused("usage: k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>") from None
        ctx = load(a.release, a.product, a.run_id)
        code, expire = run(ctx, Live(ctx, dict(os.environ)))
    except Exception as e:
        log(f"{type(e).__name__}: {e}")
        code = 3
    finally:
        print(json.dumps({"exit": code, "expire": expire}, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
