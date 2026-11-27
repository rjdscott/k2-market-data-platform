"""k2-dbt, the dbt wrapper: what guarantees readers see only tested data (ADR-061 parts 4 and 5).

    k2-dbt build --release <id> --product <domain>.<product> --run-id <dag_id>/<run_id>

The frozen contract is docs/development/phase-001-interfaces.md, "Run-time contracts", "The dbt
wrapper"; the measurements behind it are spikes (d), (f) and (g) of
docs/research/2026-11-26-release-spine-spikes.md. In order:

1. reconcile: an output whose `main` is not its `audited` tag (an earlier run died) is restored to
   the tag as the builder (no tag: DROP TABLE), `reconciled`; a tag ahead of the run records, which
   only an unfinished run explains (it died between the tag commit and its `audited` row), gets
   that row; every view is put back to C and the tagged columns; then the catalog drift check,
   with the run-record comparison, must find nothing.
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

from k2gen import model as k2

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


def run(ctx: Ctx, io: Any) -> tuple[int, list[dict[str, Any]]]:
    """Steps 1 to 6; (exit code, expire)."""
    expire: list[dict[str, Any]] = []
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
    log(f"dbt build exit {code} (invocation {invocation}): verdict {outcome}")
    return outcome, expire  # W1-15 restores a failed build (step 5), W1-16 publishes a pass (step 6)


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

    def snapshots(self, rel: str) -> set[int]:
        relation(rel)
        rows, _ = self.query("builder", f"SELECT snapshot_id FROM {k2.quote_relation(rel + '$snapshots')}")
        return {snap(r[0]) for r in rows}

    def record(self, **row: Any) -> None:
        self.k2runs.record(self.conn("runs"), **row)

    def dbt(self, argv: list[str]) -> tuple[int, dict[str, Any] | None]:
        results = WORK / "target" / "run_results.json"
        results.unlink(missing_ok=True)
        done = subprocess.run(argv, env=dbt_env(self.environ, password("builder")), stdin=subprocess.DEVNULL,
                              stdout=sys.stderr, stderr=sys.stderr, check=False)
        return done.returncode, json.loads(results.read_text()) if results.exists() else None


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
