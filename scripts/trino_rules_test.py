"""The access rules of a compiled release, attacked on a throwaway lake (scripts/trino-rules-test.sh).

    compile <out>   the test's document set into <out>/set, compiled by k2gen into <out>/releases
    suite           every row against the running throwaway lake; exit 1 on any FAIL
    negative        one reader rule widened in the running copy: the rows it breaks must FAIL
    control         experiment (e)'s control, a builder on its own table; destructive, so last

Runs inside the throwaway lake's tools container (scripts/lib/throwaway-lake.sh py), which reaches
Trino as https://trino:8443 by name, verified against the run's CA, and Lakekeeper as
http://lakekeeper:8181; each user logs in with its own password (ADR-060), every path read from
/work/env.json. The document set is the good fixture set
(platform/k2gen/tests/fixtures/v2/good/) plus one test-only product, sensor-net's
station-readings-twin: in the same domain, so the same `_build` catalog, as station-readings; its
fence a prefix-extension of station-readings' (so of its builder, publisher and groups too), so an
unanchored name in a rule would match it; reading station-readings as its input port. Every row is
synthetic; hidden values carry `zz-` markers.

The base tables, tags and views are made as the gate made them
(docs/research/2026-11-26-tested-data-view-gate.md): the builder creates and fills its base through
`<domain>_build`, the tag moves by one REST commit, the publisher creates the view through
`<domain>_build` over `<domain>.<fence>__base.<table> FOR VERSION AS OF 'audited'`, and then the
builder writes one more, untested, row. Each statement is one row of a result table; a row's
verdict is PASS, FAIL or INFO (recorded, not a criterion).
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from k2gen import docset, model, release
from k2gen.emit import trino_rules
from k2gen.model import Table, quote_ident, quote_relation, sql_literal

REPO = Path(__file__).resolve().parents[1]
GOOD = REPO / "platform/k2gen/tests/fixtures/v2/good"
TWIN = "contracts/sensor-net/station-readings-twin"
TAG = "v0.0.0-rules-test"
IMAGES = {"trino": "trinodb/trino@sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f"}
OPERATOR = trino_rules.OPERATOR
# Authenticated users no rule names: one undeclared, two an unescaped `.` would let in, and an
# older release's canary.
STRANGERS = ("p.nobody", "svcxplatform", "pxanalyst-full", "k2_release_000000000000")
TWIN_PRODUCT = """\
apiVersion: v1.1.0
kind: DataProduct
id: sensor-net.station-readings-twin
name: station-readings-twin
version: 1.0.0
status: active
domain: sensor-net
type: aggregate
description:
  purpose: Test only. station-readings' twin in its domain, its fence a prefix-extension of the other's.
team:
  name: sensor-team
inputPorts:
  - name: silver_readings
    version: 1.0.0
    contractId: sensor-net.station-readings.silver_readings
outputPorts:
  - name: silver_readings
    version: 1.0.0
    contractId: sensor-net.station-readings-twin.silver_readings
    inputContracts:
      - id: sensor-net.station-readings.silver_readings
        version: 1.0.0
"""
TWIN_MODEL = """\
-- Synthetic, test only: the input's tested rows, as they are.
{{ config(materialized='table') }}
select station_id, read_at, value, region, operator_note
from {{ k2_input('station_readings', 'silver_readings') }}
"""
TWIN_EXPECTATIONS = """\
  - principal: p.twin-reader
    table: sensor_net.station_readings_twin.silver_readings
    via: [sql]
    visible: [station_id, read_at, value, region, operator_note]
    mustAppear: {minRows: 1}
  - {principal: p.twin-reader, table: sensor_net.station_readings_twin__base.silver_readings, denied: true}
"""
TWIN_PRINCIPALS = """\
  - id: p.twin-reader
    kind: person
    owner: sensor-team
  - id: dbt_station_readings_twin
    kind: service
    owner: sensor-team
  - id: pub_station_readings_twin
    kind: service
    owner: sensor-team
"""

# ---- compile -------------------------------------------------------------------------------------


def document_set() -> dict[str, bytes]:
    docs = {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}
    # The twin's contract: silver_readings' columns and checks, offered to full_reader only, so the
    # checks' inheritance rule (an output as strict as its input, role by role) holds unchanged.
    twin = model.parse("silver", docs["contracts/sensor-net/station-readings/silver_readings.odcs.yaml"])
    twin["id"] = "sensor-net.station-readings-twin.silver_readings"
    twin["schema"][0]["physicalName"] = "station_readings_twin.silver_readings"
    twin["roles"] = [r for r in twin["roles"] if r["role"] == "full_reader"]
    twin["customProperties"] = [c for c in twin["customProperties"] if c["property"] != "policies"]
    docs[f"{TWIN}/silver_readings.odcs.yaml"] = yaml.safe_dump(twin, sort_keys=False).encode()
    docs[model.EXPECTATIONS] += TWIN_EXPECTATIONS.encode()
    docs[f"{TWIN}/product.odps.yaml"] = TWIN_PRODUCT.encode()
    docs[f"{TWIN}/grants.yaml"] = b"grants:\n  - principal: p.twin-reader\n    role: full_reader\n"
    docs[f"{TWIN}/models/silver_readings.sql"] = TWIN_MODEL.encode()
    docs[model.PRINCIPALS] = docs[model.PRINCIPALS] + TWIN_PRINCIPALS.encode()
    docs[model.COMPOSE] = b"services:\n  trino:\n    image: trinodb/trino:484\n"
    return docs


def compile_release(out: Path) -> str:
    docs, set_dir = document_set(), out / "set"
    commit = "0" * 40
    prov = {"source": "git", "tag": TAG, "commit": commit, "tagger": "k2 rules test <rules-test@example.org>",
            "taggedAt": "2026-11-27T00:00:00Z", "setSha256": model.set_sha256(docs),
            "documents": [{"path": p, "sha256": hashlib.sha256(docs[p]).hexdigest(), "lastCommit": commit, "pr": None} for p in sorted(docs)]}
    for path, data in docs.items():
        (set_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (set_dir / path).write_bytes(data)
    (set_dir / model.PROVENANCE).write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n")
    (out / "images.json").write_text(json.dumps(IMAGES) + "\n")
    rid = release.release(set_dir, None, out / "images.json", out / "releases", set_dir / model.COMPOSE)
    (out / "release-id").write_text(rid + "\n")
    return rid


# ---- clients -------------------------------------------------------------------------------------

ENV = json.loads(Path("/work/env.json").read_text()) if Path("/work/env.json").exists() else {}
CTX = ssl.create_default_context(cafile=ENV["ca"]) if ENV else None


def basic(user: str) -> str:
    """The Authorization header of `user`, with its own password (throwaway-lake.sh wrote each)."""
    secret = Path(ENV["passwords"][user]).read_text().strip()
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def tq(user: str, sql: str, session: dict[str, str] | None = None, as_user: str | None = None) -> tuple[list, str | None, dict]:
    """One statement over Trino's client protocol, logged in as `user` and run as `as_user` (an
    impersonation) or `user`: (rows, error or None, the final stats)."""
    auth = basic(user)
    headers = {"Authorization": auth, "X-Trino-User": as_user or user}
    if session:
        headers["X-Trino-Session"] = ",".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in session.items())
    rows: list = []
    columns: list[str] = []
    try:
        req = urllib.request.Request(f"{ENV['trino']}/v1/statement", sql.encode(), headers)
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            doc = json.load(r)
        deadline = time.monotonic() + 120
        while True:
            rows += doc.get("data", [])
            columns = columns or [c["name"] for c in doc.get("columns", [])]
            if "error" in doc:
                e = doc["error"]
                return rows, f"{e.get('errorName')}: {e.get('message')}", doc.get("stats", {})
            if not doc.get("nextUri") or time.monotonic() > deadline:
                return rows, None if not doc.get("nextUri") else "client timeout", {**doc.get("stats", {}), "columns": columns}
            with urllib.request.urlopen(urllib.request.Request(doc["nextUri"], headers={"Authorization": auth}), context=CTX, timeout=60) as r:
                doc = json.load(r)
    except urllib.error.HTTPError as e:
        return rows, f"HTTP {e.code}", {}


def ok(user: str, sql: str, **kw: Any) -> list:
    rows, err, _ = tq(user, sql, **kw)
    if err:
        raise RuntimeError(f"{user}: {sql[:200]}: {err}")
    return rows


def denied(err: str | None) -> bool:
    return bool(err) and "Access Denied" in err


def short(err: str | None) -> str:
    return (err or "ok").replace("PERMISSION_DENIED: Access Denied: ", "denied: ")[:140]


def rest(method: str, url: str, body: object = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw.strip().startswith(b"{") else {}


def catalog_base(warehouse: str) -> str:
    st, cfg = rest("GET", f"{ENV['lakekeeper']}/catalog/v1/config?warehouse={warehouse}")
    assert st == 200, f"config {warehouse}: {st}"
    return f"{ENV['lakekeeper']}/catalog/v1/{cfg['defaults']['prefix']}"


# ---- results -------------------------------------------------------------------------------------

RESULTS: list[tuple[str, str, str, str]] = []


def record(rid: str, assertion: str, passed: bool | None, detail: object) -> None:
    verdict = "INFO" if passed is None else "PASS" if passed else "FAIL"
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    RESULTS.append((rid, verdict, assertion, text))
    print(f"{verdict} {rid} {assertion}\n      {text[:600]}", flush=True)


# ---- the fixture lake ----------------------------------------------------------------------------


def literal(physical_type: str, value: object) -> str:
    """A typed SQL literal for a synthetic value."""
    if physical_type == "varchar":
        return sql_literal(str(value))
    if physical_type in ("bigint", "integer"):
        return f"CAST({int(value)} AS {physical_type})"  # type: ignore[call-overload]
    if physical_type.startswith("decimal("):
        return f"CAST({int(value)} AS {physical_type})"  # type: ignore[call-overload]
    if physical_type == "date":
        return f"DATE '2026-01-{int(value):02d}'"  # type: ignore[call-overload]
    if physical_type.startswith("timestamp(6)"):
        return f"CAST({value} AS {physical_type})"
    raise ValueError(f"the test makes no synthetic {physical_type}")


class Fixture:
    """One built table: its rows 1 to 5 tested, row 6 written after the tag, so never visible."""

    OLD, NOW = "current_timestamp - INTERVAL '2' HOUR", "current_timestamp"

    def __init__(self, product: model.Product, table: Table):
        self.p, self.t = product, table
        self.cols = [c.name for c in table.columns]
        self.types = {c.name: c.physical_type for c in table.columns}
        used: set[str] = set()
        for pol in table.policies.values():
            used |= set(pol.mask) | ({pol.delay.column} if pol.delay else set()) | ({pol.row_scope.column} if pol.row_scope else set())
        self.key = next(c for c in self.cols if self.types[c] == "varchar" and c not in used)
        self.base = model.base_relation(product.domain, product.name, table.name)
        self.build = model.build_relation(product.domain, product.name, table.name)
        self.view = model.reader_relation(table)
        self.view_build = model.view_build_relation(product.domain, product.name, table.name)
        self.tag = f"zz-{model.fence(product.name)}"

    def value(self, i: int, c: str) -> object:
        for pol in self.t.policies.values():
            if pol.row_scope and c == pol.row_scope.column:
                return "zz-out" if i == 3 else pol.row_scope.values[i % len(pol.row_scope.values)]
            if pol.delay and c == pol.delay.column:
                return self.NOW if i == 5 else self.OLD
            if c in pol.mask:
                return f"zz-mask-{i}"
        if c == self.key:
            return f"{self.tag}-{i}"
        return self.OLD if self.types[c].startswith("timestamp") else i  # small: inside every made-up range

    def row(self, i: int) -> str:
        return "(" + ", ".join(literal(self.types[c], self.value(i, c)) for c in self.cols) + ")"

    def visible(self, role: str) -> list[int]:
        pol = self.t.policies.get(role)
        ids = [1, 2, 3, 4, 5]
        if pol and pol.row_scope:
            ids = [i for i in ids if i != 3]
        if pol and pol.delay:
            ids = [i for i in ids if i != 5]
        return ids

    def masked(self, role: str) -> set[str]:
        pol = self.t.policies.get(role)
        return set(pol.mask) if pol else set()


def tables_of(m: model.Model) -> list[Fixture]:
    return [Fixture(p, t) for p in sorted(m.products, key=lambda p: p.id) for t in p.tables if t.build.kind in ("dbt", "code")]


def main_id(f: Fixture) -> int:
    st, doc = rest("GET", f"{catalog_base(model.warehouse(f.p.domain))}/namespaces/{model.base_namespace(f.p.name)}/tables/{f.t.name}")
    assert st == 200, f"{f.base}: {st}"
    return doc["metadata"]["refs"]["main"]["snapshot-id"]


def view_sql(f: Fixture) -> str:
    """The release's own view SQL (emit/lake.py, ADR-061 part 1): the contract's columns over the
    tag, through the reader catalog."""
    return (Path(ENV["release"]) / model.view_sql_path(f.view)).read_text().strip()


def setup(m: model.Model, fixtures: list[Fixture]) -> dict[str, int]:
    """Each base made and filled by its builder, tagged by one REST commit per warehouse, viewed by
    its publisher, then one untested row added. Returns each base's tested snapshot id."""
    tested: dict[str, int] = {}
    for f in fixtures:
        dbt, pub = model.builder(f.p.name), model.publisher(f.p.name)
        ok(dbt, f"CREATE SCHEMA IF NOT EXISTS {quote_relation(f.build.rsplit('.', 1)[0])}")
        ok(pub, f"CREATE SCHEMA IF NOT EXISTS {quote_relation(f.view_build.rsplit('.', 1)[0])}")
        ok(dbt, f"CREATE TABLE {quote_relation(f.build)} ({', '.join(f'{quote_ident(c)} {f.types[c]}' for c in f.cols)})")
        ok(dbt, f"INSERT INTO {quote_relation(f.build)} VALUES " + ", ".join(f.row(i) for i in range(1, 6)))
        tested[f.base] = main_id(f)
    by_warehouse: dict[str, list[dict]] = {}
    for f in fixtures:
        by_warehouse.setdefault(model.warehouse(f.p.domain), []).append({
            "identifier": {"namespace": [model.base_namespace(f.p.name)], "name": f.t.name},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested[f.base]},
                             {"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": None}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": tested[f.base]}]})
    for w, changes in by_warehouse.items():
        st, doc = rest("POST", f"{catalog_base(w)}/transactions/commit", {"table-changes": changes})
        assert st in (200, 204), f"tag commit {w}: {st} {doc}"
    for f in fixtures:
        ok(model.publisher(f.p.name), f"CREATE OR REPLACE VIEW {quote_relation(f.view_build)} SECURITY DEFINER AS {view_sql(f)}")
        ok(model.builder(f.p.name), f"INSERT INTO {quote_relation(f.build)} VALUES {f.row(6)}")
    return tested


def lake_tables(m: model.Model) -> Table | None:
    """Every connector table, and its product's landing_files and audit_checks, created empty in
    warehouse k2 through the REST API (Trino's `lake` is read-only, Spark its writer), so a lake
    denial is a denial and not an absent table. Returns the first connector table."""
    iceberg = {"varchar": "string", "bigint": "long", "integer": "int", "date": "date", "boolean": "boolean",
               "varbinary": "binary", "timestamp(6) with time zone": "timestamptz", "timestamp(6)": "timestamp"}
    first = None
    for p in sorted(m.products, key=lambda p: p.id):
        made = [(t.name, [(c.name, c.physical_type) for c in t.columns]) for t in p.tables if t.build.kind == "connector"]
        if not made:
            continue
        made += [(pt.name, [(c.name, c.type) for c in pt.columns]) for pt in (model.PLATFORM_TABLES[model.LANDING_FILES],
                                                                          model.PLATFORM_TABLES[model.AUDIT_CHECKS])]
        base, ns = catalog_base(model.LAKE_WAREHOUSE), model.fence(p.name)
        st, _ = rest("POST", f"{base}/namespaces", {"namespace": [ns]})
        assert st in (200, 409), f"namespace {ns}: {st}"
        for name, cols in made:
            fields = [{"id": i + 1, "name": c, "required": False, "type": ty if ty.startswith("decimal(") else iceberg[ty]}
                      for i, (c, ty) in enumerate(cols)]
            st, doc = rest("POST", f"{base}/namespaces/{ns}/tables", {"name": name, "schema": {"type": "struct", "schema-id": 0, "fields": fields}})
            assert st == 200, f"lake table {ns}.{name}: {st} {doc}"
        first = first or next(t for t in p.tables if t.build.kind == "connector")
    return first


def plant_view(f: Fixture, name: str) -> None:
    """A definer view no release declares, put in f's reader namespace by plain REST calls (the
    catalog fence's forgery, h3.04): f's real publisher as owner, SQL over the base's `main`, so its
    rows include the untested one. Only a rule that names it could let a reader in (h1.31)."""
    base = catalog_base(model.warehouse(f.p.domain))
    ns = model.reader_namespace(f.p.name)
    st, doc = rest("GET", f"{base}/namespaces/{ns}/views/{f.t.name}")
    assert st == 200, f"view {f.view}: {st}"
    meta = doc["metadata"]
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    schema = next(x for x in meta["schemas"] if x["schema-id"] == v["schema-id"])
    sql = view_sql(f).replace(" FOR VERSION AS OF 'audited'", "")
    version = {"version-id": 1, "timestamp-ms": int(time.time() * 1000), "schema-id": schema["schema-id"], "summary": v["summary"],
               "representations": [{"type": "sql", "sql": sql, "dialect": "trino"}], "default-namespace": [ns]}
    st, doc = rest("POST", f"{base}/namespaces/{ns}/views", {"name": name, "schema": schema, "view-version": version,
                                                           "properties": {"trino.run-as-owner": model.publisher(f.p.name)}})
    assert st == 200, f"planted view {name}: {st} {doc}"


# ---- the suite -----------------------------------------------------------------------------------

META = ["snapshots", "files", "history", "partitions", "refs", "manifests", "all_manifests", "properties",
        "metadata_log_entries", "entries", "all_entries"]


def base_paths(cat: str, ns: str, t: str, snap: int) -> dict[str, str]:
    """Every way into a base the gate tried (h1.08)."""
    rel = f"{quote_ident(cat)}.{quote_ident(ns)}.{quote_ident(t)}"
    return {"table": f"SELECT count(*) FROM {rel}",
            "version id": f"SELECT count(*) FROM {rel} FOR VERSION AS OF {snap}",
            "version 'audited'": f"SELECT count(*) FROM {rel} FOR VERSION AS OF 'audited'",
            "timestamp": f"SELECT count(*) FROM {rel} FOR TIMESTAMP AS OF current_timestamp - INTERVAL '1' SECOND",
            **{f"${x}": f"SELECT count(*) FROM {quote_ident(cat)}.{quote_ident(ns)}.{quote_ident(t + '$' + x)}" for x in META},
            "table_changes": f"SELECT count(*) FROM TABLE({quote_ident(cat)}.system.table_changes({sql_literal(ns)}, {sql_literal(t)}, 0, {snap}))",
            "describe": f"DESCRIBE {rel}", "show stats": f"SHOW STATS FOR {rel}", "show create": f"SHOW CREATE TABLE {rel}"}


def consumers(m: model.Model) -> list[str]:
    return sorted({g.principal for p in m.products for g in p.grants})


def roles_and_views(m: model.Model, fixtures: list[Fixture]) -> None:
    """Each member of each role reads exactly its policy's rows; nobody reads row 6 or another
    product's view; svc.api, impersonating, gets the caller's rows."""
    for f in fixtures:
        for g in f.p.grants:
            want = f.visible(g.role)
            masked = ", ".join(quote_ident(c) for c in sorted(f.masked(g.role))) or "1"
            rows, err, _ = tq(g.principal, f"SELECT {quote_ident(f.key)}, {masked} FROM {quote_relation(f.view)} ORDER BY 1")
            got = [int(r[0].rsplit("-", 1)[1]) for r in rows] if not err else err
            masks_null = not err and all(v is None for r in rows for v in r[1:]) if f.masked(g.role) else True
            probe = None
            if f.masked(g.role) and not err:
                c = sorted(f.masked(g.role))[0]
                probe = ok(g.principal, f"SELECT count(*) FROM {quote_relation(f.view)} WHERE {quote_ident(c)} LIKE 'zz-mask-%'")
            record(f"role.{f.view}.{g.principal}", f"{g.principal} ({g.role}) reads exactly rows {want} of the view, "
                   f"{sorted(f.masked(g.role)) or 'no column'} masked to null, never the untested row 6",
                   got == want and masks_null and probe in (None, [[0]]), f"rows {got}; masked null {masks_null}; masked probe {probe}")
        errs = {u: tq(u, f"SELECT count(*) FROM {quote_relation(f.view)}")[1] for u in not_its_readers(f)}
        record(f"view.{f.view}.others", "every principal with no role on the product, an unanchored name's would-be matches "
               "included, is refused its view", all(denied(e) for e in errs.values()), {u: short(e) for u, e in errs.items()})
    f, g = next((f, g) for f in fixtures for g in f.p.grants if g.role in f.t.policies)
    rows, err, _ = tq("svc.api", f"SELECT {quote_ident(f.key)} FROM {quote_relation(f.view)} ORDER BY 1", as_user=g.principal)
    got = [int(r[0].rsplit("-", 1)[1]) for r in rows] if not err else err
    record("impersonation.policy", f"svc.api impersonating {g.principal} gets exactly that caller's rows", got == f.visible(g.role), got)


FIXTURES: list[Fixture] = []
MODEL: model.Model | None = None


def not_its_readers(f: Fixture) -> list[str]:
    assert MODEL
    mine = {g.principal for g in f.p.grants if any(r.role == g.role for r in f.t.roles)}
    return [u for u in consumers(MODEL) + list(STRANGERS) + ["svc.api", OPERATOR, model.builder(f.p.name)] if u not in mine]


def matrix(m: model.Model, fixtures: list[Fixture], tested: dict[str, int], label: str = "h1.matrix") -> None:
    """h1.08: every base path, both catalogs, every consumer and stranger; each statement also run
    by the builder through `_build`, so a refusal is the fence and not a typo."""
    jobs, invalid = [], []
    users = consumers(m) + list(STRANGERS) + ["svc.api"]
    for f in fixtures:
        ns, t, snap = model.base_namespace(f.p.name), f.t.name, tested[f.base]
        controls = base_paths(model.build_catalog(f.p.domain), ns, t, snap)
        for key, sql in controls.items():
            if key != "table_changes":  # no function rules: table_changes is refused to everyone
                _, err, _ = tq(model.builder(f.p.name), sql)
                if err:
                    invalid.append(f"{f.base} {key}: {err[:80]}")
        for cat in (model.reader_catalog(f.p.domain), model.build_catalog(f.p.domain)):
            for key, sql in base_paths(cat, ns, t, snap).items():
                # svc.api reads exactly `$refs` through the reader catalog (model.REFS_READERS, the refs row)
                jobs += [(u, f"{cat}.{ns}.{t} {key}", sql) for u in users
                         if not (u in model.REFS_READERS and key == "$refs" and cat == model.reader_catalog(f.p.domain))]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda j: (j, tq(j[0], j[2])), jobs))
    bad = [f"{u} {what}: {short(err) if err else rows}" for (u, what, _), (rows, err, _) in out if not denied(err)]
    record(label, f"every base path ({len(base_paths('c', 'n', 't', 1))} kinds: the table, time travel by id, 'audited' and "
           f"timestamp, {len(META)} $ tables, table_changes, DESCRIBE, SHOW STATS, SHOW CREATE) through the reader and the "
           f"writer catalog, refused to every consumer, stranger and svc.api (but svc.api's $refs, the refs row)", not bad and not invalid,
           f"{len(out) - len(bad)}/{len(out)} refused; not refused {bad[:4]}; invalid controls {invalid}")


def views(m: model.Model, fixtures: list[Fixture], tested: dict[str, int]) -> None:
    f = fixtures[0]
    reader = next(g.principal for g in f.p.grants)
    errs = {k: tq(reader, f"SELECT count(*) FROM {quote_relation(f.view)} {v}")[1]
            for k, v in (("id", f"FOR VERSION AS OF {tested[f.base]}"), ("timestamp", "FOR TIMESTAMP AS OF current_timestamp - INTERVAL '1' SECOND"))}
    record("view.time-travel", "time travel on a view, by snapshot id and by timestamp, fails", all(errs.values()), {k: short(e) for k, e in errs.items()})
    cat, ns, t = f.view.split(".")
    errs = {x: tq(reader, f"SELECT count(*) FROM {quote_ident(cat)}.{quote_ident(ns)}.{quote_ident(t + '$' + x)}")[1] for x in ("snapshots", "files", "refs")}
    errs["through _build"] = tq(reader, f"SELECT count(*) FROM {quote_relation(f.view_build)}")[1]
    errs["SHOW CREATE VIEW"] = tq(reader, f"SHOW CREATE VIEW {quote_relation(f.view)}")[1]
    record("view.metadata", "a view has no $ tables for a reader, no route through _build, no SHOW CREATE VIEW",
           all(errs.values()) and denied(errs["through _build"]) and denied(errs["SHOW CREATE VIEW"]), {k: short(e) for k, e in errs.items()})
    listed = {}
    for u in consumers(m):
        tables = []
        for d in sorted({x.p.domain for x in fixtures}):
            rows, err, _ = tq(u, f"SELECT table_schema || '.' || table_name FROM {quote_ident(model.reader_catalog(d))}.information_schema.tables "
                                 "WHERE table_schema <> 'information_schema'")
            tables += [f"{model.reader_catalog(d)}.{r[0]}" for r in rows] if not err else []
        jdbc, err, _ = tq(u, "SELECT table_cat || '.' || table_schem || '.' || table_name FROM system.jdbc.tables "
                         "WHERE table_cat <> 'system' AND table_schem <> 'information_schema'")
        mine = sorted({x.view for x in fixtures for g in x.p.grants if g.principal == u})
        listed[u] = {"information_schema": sorted(tables), "system.jdbc": sorted(r[0] for r in jdbc) if not err else short(err), "granted": mine}
    record("listing", "each consumer's information_schema and system.jdbc list exactly its granted views: no base, no other product",
           all(v["information_schema"] == v["granted"] == v["system.jdbc"] for v in listed.values()), listed)


def builders_and_publishers(m: model.Model, fixtures: list[Fixture]) -> None:
    a, b = fixtures_by_name("station-readings"), fixtures_by_name("station-readings-twin")
    planted = f"{a.view.rsplit('.', 1)[0]}.foreign_view"
    errs = {u: tq(u, f"SELECT count(*) FROM {quote_relation(planted)}")[1] for u in consumers(m) + ["svc.api"]}
    record("h1.31.planted-view", f"{planted}, a definer view over the base's main planted by REST with A's publisher as its "
           "owner, is denied to every consumer: only an exact name in a rule lets a reader in", all(denied(e) for e in errs.values()),
           {u: short(e) for u, e in errs.items()})
    sel = view_sql(a)
    before = view_state(a)

    def in_reader_ns(name: str) -> str:  # a name in A's reader namespace, through A's _build catalog
        return f"{quote_relation(a.view_build.rsplit('.', 1)[0])}.{quote_ident(name)}"

    attempts = {
        "A's builder CREATE VIEW in A's reader namespace": (model.builder(a.p.name), f"CREATE VIEW {in_reader_ns('x')} SECURITY DEFINER AS {sel}"),
        "A's builder CREATE OR REPLACE VIEW A's view": (model.builder(a.p.name), f"CREATE OR REPLACE VIEW {quote_relation(a.view_build)} SECURITY DEFINER AS SELECT * FROM {quote_relation(a.build)}"),
        "A's builder DROP VIEW": (model.builder(a.p.name), f"DROP VIEW {quote_relation(a.view_build)}"),
        "A's builder RENAME VIEW": (model.builder(a.p.name), f"ALTER VIEW {quote_relation(a.view_build)} RENAME TO {in_reader_ns(a.t.name + '_old')}"),
        "A's builder COMMENT ON VIEW": (model.builder(a.p.name), f"COMMENT ON VIEW {quote_relation(a.view_build)} IS 'x'"),
        "A's builder CREATE TABLE in A's reader namespace": (model.builder(a.p.name), f"CREATE TABLE {in_reader_ns(a.t.name + '2')} AS SELECT * FROM {quote_relation(a.build)}"),
        "A's builder CREATE OR REPLACE VIEW through the reader catalog": (model.builder(a.p.name), f"CREATE OR REPLACE VIEW {quote_relation(a.view)} AS SELECT 1 x"),
        "B's builder CREATE OR REPLACE VIEW A's view": (model.builder(b.p.name), f"CREATE OR REPLACE VIEW {quote_relation(a.view_build)} AS SELECT 1 x"),
        "B's publisher CREATE OR REPLACE VIEW A's view": (model.publisher(b.p.name), f"CREATE OR REPLACE VIEW {quote_relation(a.view_build)} AS SELECT 1 x"),
        "svc.platform CREATE OR REPLACE VIEW A's view": (OPERATOR, f"CREATE OR REPLACE VIEW {quote_relation(a.view_build)} SECURITY DEFINER AS {sel}"),
        "A's publisher CREATE VIEW at a name the release does not declare": (model.publisher(a.p.name), f"CREATE VIEW {in_reader_ns('foreign_view')} SECURITY DEFINER AS {sel}"),
        "A's publisher CREATE TABLE at a name the release does not declare": (model.publisher(a.p.name), f"CREATE TABLE {in_reader_ns('planted')} (x integer)"),
    }
    res = {k: tq(u, sql)[1] for k, (u, sql) in attempts.items()}
    record("h1.12.views", "nobody but a view's publisher can create, replace, drop, rename or comment it, and its publisher "
           "only at a declared name; nobody plants a table in a reader namespace; the view is unchanged",
           all(denied(e) for e in res.values()) and view_state(a) == before, {k: short(e) for k, e in res.items()})
    # h1.13: B's publisher over A's base: refused at creation (no SELECT on it) or every read refused.
    leak = f"{quote_relation(b.view_build.rsplit('.', 1)[0])}.{quote_ident(b.t.name)}"
    _, e_make, _ = tq(model.publisher(b.p.name), f"CREATE OR REPLACE VIEW {leak} SECURITY DEFINER AS {sel}")
    rows, e_read, _ = tq(next(g.principal for g in b.p.grants), f"SELECT {quote_ident(a.key)} FROM {quote_relation(b.view)}")
    record("h1.13.foreign-publisher", "B's publisher cannot serve A's rows through B's own view", denied(e_make) or denied(e_read),
           f"create: {short(e_make)}; B's reader: {short(e_read)} {rows[:3]}")
    if e_make is None:  # put B's view back from the release
        ok(model.publisher(b.p.name), f"CREATE OR REPLACE VIEW {quote_relation(b.view_build)} SECURITY DEFINER AS {view_sql(b)}")
    writes = {"INSERT": f"INSERT INTO {quote_relation(a.base)} SELECT * FROM {quote_relation(a.base)} LIMIT 1",
              "DELETE": f"DELETE FROM {quote_relation(a.base)}",
              "rollback_to_snapshot": f"ALTER TABLE {quote_relation(a.base)} EXECUTE rollback_to_snapshot(1)",
              "optimize": f"ALTER TABLE {quote_relation(a.base)} EXECUTE optimize",
              "expire_snapshots": f"ALTER TABLE {quote_relation(a.base)} EXECUTE expire_snapshots(retention_threshold => '7d')",
              "CALL rollback_to_snapshot": f"CALL {quote_ident(model.reader_catalog(a.p.domain))}.system.rollback_to_snapshot("
                                           f"{sql_literal(model.base_namespace(a.p.name))}, {sql_literal(a.t.name)}, 1)",
              "CREATE VIEW in the reader catalog": f"CREATE VIEW {quote_relation(a.view.rsplit('.', 1)[0])}.y AS {sel}",
              "DROP TABLE": f"DROP TABLE {quote_relation(a.base)}"}
    main_before = main_id(a)
    res = {k: tq(model.publisher(a.p.name), sql)[1] for k, sql in writes.items()}
    record("h1.02.publisher-floor", "the publisher, with catalog access all on its reader catalog, is refused every write there",
           all(res.values()) and main_id(a) == main_before, {k: short(e) for k, e in res.items()})
    auth = {"publisher: view to B's publisher": (model.publisher(a.p.name), f"ALTER VIEW {quote_relation(a.view_build)} SET AUTHORIZATION USER {quote_ident(model.publisher(b.p.name))}"),
            "publisher: schema to the builder": (model.publisher(a.p.name), f"ALTER SCHEMA {quote_relation(a.view_build.rsplit('.', 1)[0])} SET AUTHORIZATION USER {quote_ident(model.builder(a.p.name))}"),
            "builder: base schema to the publisher": (model.builder(a.p.name), f"ALTER SCHEMA {quote_relation(a.build.rsplit('.', 1)[0])} SET AUTHORIZATION USER {quote_ident(model.publisher(a.p.name))}"),
            "builder: base table to the publisher": (model.builder(a.p.name), f"ALTER TABLE {quote_relation(a.build)} SET AUTHORIZATION USER {quote_ident(model.publisher(a.p.name))}"),
            "svc.platform: view to itself": (OPERATOR, f"ALTER VIEW {quote_relation(a.view_build)} SET AUTHORIZATION USER {quote_ident(OPERATOR)}")}
    res = {k: tq(u, sql)[1] for k, (u, sql) in auth.items()}
    record("h1.19.authorization", "SET AUTHORIZATION is refused to every principal: the rules have no authorization section",
           all(denied(e) for e in res.values()) and view_state(a)["owner"] == model.publisher(a.p.name), {k: short(e) for k, e in res.items()})
    errs = {u: tq(u, f"SELECT count(*) FROM {quote_relation(a.view)}")[1] for u in (OPERATOR, model.builder(a.p.name), model.publisher(b.p.name))}
    record("h1.33.no-pipeline-reader", "svc.platform, the builder and another publisher read no reader view", all(denied(e) for e in errs.values()),
           {u: short(e) for u, e in errs.items()})


def view_state(f: Fixture) -> dict:
    st, doc = rest("GET", f"{catalog_base(model.warehouse(f.p.domain))}/namespaces/{model.reader_namespace(f.p.name)}/views/{f.t.name}")
    if st != 200:
        return {"missing": st}
    meta = doc["metadata"]
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    return {"owner": (meta.get("properties") or {}).get("trino.run-as-owner"), "sql": [r["sql"] for r in v["representations"]]}


def fixtures_by_name(name: str) -> Fixture:
    return next(f for f in FIXTURES if f.p.name == name)


def session_properties(m: model.Model, fixtures: list[Fixture]) -> None:
    cats = sorted({model.reader_catalog(f.p.domain) for f in fixtures})
    res, hdr = {}, {}
    for u in consumers(m) + ["svc.api"]:
        for c in cats:
            res[f"{u} {c}"] = tq(u, f"SET SESSION {quote_ident(c)}.statistics_enabled = true")[1]
            hdr[f"{u} {c}"] = tq(u, "SELECT 1", session={f"{c}.statistics_enabled": "true"})[1]
    record("stats.statement", "SET SESSION <reader catalog>.statistics_enabled = true is refused to every consumer and svc.api",
           all(denied(e) for e in res.values()), {k: short(e) for k, e in res.items()})
    record("stats.header", "the same in the X-Trino-Session header (the CLI's --session) is refused too",
           all(denied(e) for e in hdr.values()), {k: short(e) for k, e in hdr.items()})
    props = ok(OPERATOR, "SHOW SESSION")
    catalog_props = sorted(r[0] for r in props if r[0].startswith(f"{cats[0]}."))
    system_props = sorted(r[0] for r in props if "." not in r[0])
    reader = consumers(m)[0]
    open_ = []
    for name in catalog_props + system_props:
        default = next(r[2] for r in props if r[0] == name)
        for how, err in (("SET SESSION", tq(reader, f"SET SESSION {name} = {sql_literal(default) if not re.fullmatch(r'-?[0-9]+|true|false', default) else default}")[1]),
                         ("header", tq(reader, "SELECT 1", session={name: default})[1])):
            if not denied(err):
                open_.append(f"{how} {name}: {short(err)}")
    record("session.all", f"{reader} is refused every catalog session property of {cats[0]} ({len(catalog_props)}) and every system "
           f"session property ({len(system_props)}), by statement and by header, each set to its own default", not open_,
           f"not refused {open_[:6]}; catalog properties {catalog_props}")
    b, bc = model.builder(fixtures[0].p.name), model.build_catalog(fixtures[0].p.domain)
    checks = {"builder may set statistics_enabled": (b, {f"{bc}.statistics_enabled": "true"}, False),
              "builder may not set expire_snapshots_min_retention": (b, {f"{bc}.expire_snapshots_min_retention": "0s"}, True),
              "builder may not set remove_orphan_files_min_retention": (b, {f"{bc}.remove_orphan_files_min_retention": "0s"}, True),
              "builder may not set another catalog property": (b, {f"{bc}.orc_bloom_filters_enabled": "true"}, True),
              "svc.platform may set expire_snapshots_min_retention": (OPERATOR, {f"{bc}.expire_snapshots_min_retention": "0s"}, False),
              "svc.api may not set a system session property": ("svc.api", {"query_max_run_time": "1m"}, True)}
    res = {k: tq(u, "SELECT 1", session=s)[1] for k, (u, s, _) in checks.items()}
    record("session.pipeline", "pipeline principals may set statistics_enabled; only svc.platform a retention floor",
           all(denied(res[k]) == want for k, (_, _, want) in checks.items()), {k: short(e) for k, e in res.items()})


def system_and_queries(m: model.Model, fixtures: list[Fixture]) -> None:
    reader = consumers(m)[0]
    tables = {}
    for s in ("runtime", "metadata", "jdbc"):
        tables[s] = sorted(r[0] for r in ok(OPERATOR, f"SHOW TABLES FROM system.{s}"))
    missing = {s: sorted(set(ts) - set(tables[s])) for s, ts in trino_rules.SYSTEM_TABLES.items()}
    record("system.names", "every system table the rules name exists in Trino 484 (the operator lists them all)",
           not any(missing.values()), {"missing": missing, "484 has": tables})
    errs = {f"system.runtime.{t}": tq(reader, f"SELECT count(*) FROM system.runtime.{t}")[1] for t in tables["runtime"]}
    rows, err, _ = tq(reader, "SELECT count(*) FROM system.metadata.table_comments WHERE catalog_name <> 'system'")
    record("system.consumer", f"{reader} is refused every system.runtime table, query history included; reads system.metadata",
           all(denied(e) for e in errs.values()) and err is None, {**{k: short(e) for k, e in errs.items()}, "metadata.table_comments": short(err) or rows})
    others = [*STRANGERS, model.canary_user(m.provenance.set_sha256)]
    errs = {u: tq(u, "SELECT count(*) FROM system.jdbc.tables")[1] for u in others}
    record("system.strangers", "strangers and the canary read no system table", all(denied(e) for e in errs.values()), {u: short(e) for u, e in errs.items()})
    builder = model.builder(fixtures[0].p.name)
    ok(builder, f"SELECT count(*) FROM {quote_relation(fixtures[0].build)} WHERE {quote_ident(fixtures[0].key)} = 'zz-history-marker'")
    req = urllib.request.Request(f"{ENV['trino']}/v1/query", headers={
        "Authorization": basic(reader), "X-Trino-User": reader})
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        foreign = [q for q in json.load(r) if q["session"]["user"] != reader]
    seen = ok(OPERATOR, "SELECT count(*) FROM system.runtime.queries WHERE query LIKE '%zz-history-marker%' AND \"user\" <> 'svc.platform'")
    qid = ok(OPERATOR, f"SELECT query_id FROM system.runtime.queries WHERE \"user\" = {sql_literal(builder)} LIMIT 1")[0][0]
    _, e_kill, _ = tq(reader, f"CALL system.runtime.kill_query(query_id => {sql_literal(qid)}, message => 'x')")
    _, e_op, _ = tq(OPERATOR, f"CALL system.runtime.kill_query(query_id => {sql_literal(qid)}, message => 'x')")
    record("queries", "a consumer sees no other principal's query over REST /v1/query and cannot kill one; the operator sees "
           "the builder's query text and may call kill_query", not foreign and seen[0][0] >= 1 and denied(e_kill) and not denied(e_op),
           f"foreign queries shown to {reader}: {len(foreign)}; operator saw {seen}; consumer kill: {short(e_kill)}; operator kill: {short(e_op)}")
    errs = {"table_changes": None, "catalog scalar function": tq(reader, f"SELECT {quote_ident(model.reader_catalog(fixtures[0].p.domain))}.system.bucket('x', 4)")[1],
            "builtin function": tq(reader, "SELECT abs(-1)")[1]}
    record("functions", "only system.builtin functions run: a catalog function is refused, abs() runs",
           denied(errs["catalog scalar function"]) and errs["builtin function"] is None, {k: short(e) for k, e in errs.items() if k != "table_changes"})


def identities(m: model.Model, fixtures: list[Fixture]) -> None:
    canary = model.canary_user(m.provenance.set_sha256)
    rows, err, _ = tq(canary, trino_rules.CANARY_STATEMENT)
    _, e_view, _ = tq(canary, f"SELECT count(*) FROM {quote_relation(fixtures[0].view)}")
    _, e_old, _ = tq("k2_release_000000000000", trino_rules.CANARY_STATEMENT)
    record("canary", f"{canary} runs `{trino_rules.CANARY_STATEMENT}` (this release's rules are loaded), reaches no table; an "
           "older release's canary is refused that statement", err is None and rows == [[1]] and denied(e_view) and denied(e_old),
           f"this: {rows or short(err)}; a view: {short(e_view)}; older canary: {short(e_old)}")
    res = {u: [tq(u, "SELECT 1")[1], tq(u, "SHOW CATALOGS")[1]] for u in STRANGERS[:3]}
    record("strangers", "an authenticated user no rule names runs nothing, SELECT 1 and SHOW CATALOGS included",
           all(denied(a) and denied(b) for a, b in res.values()), {u: [short(a), short(b)] for u, (a, b) in res.items()})
    people = [p.id for p in m.principals if p.id.startswith("p.")]
    allowed = {u: tq("svc.api", "SELECT current_user", as_user=u) for u in people}
    never = [u for u in trino_rules.known_principals(m) if not u.startswith("p.") and u != "svc.api"] + list(STRANGERS)
    refused = {u: tq("svc.api", "SELECT 1", as_user=u)[1] for u in never}
    others = {f"{a} as {b}": tq(a, "SELECT 1", as_user=b)[1] for a, b in (
        (people[0], people[1]), (OPERATOR, people[0]), (model.builder(fixtures[0].p.name), people[0]),
        (model.publisher(fixtures[0].p.name), people[0]), ("svc.load-dashboard", people[0]))}
    record("impersonation", "svc.api becomes each declared p.* principal and nobody else (no service, builder, publisher, "
           "operator, canary or stranger); no other principal impersonates anyone",
           all(e is None and r == [[u]] for u, (r, e, _) in allowed.items()) and all(denied(e) for e in refused.values())
           and all(denied(e) for e in others.values()),
           {"allowed": {u: short(e) if e else r for u, (r, e, _) in allowed.items()}, "refused": {u: short(e) for u, e in refused.items()},
            "others": {k: short(e) for k, e in others.items()}})


def refs_and_lake(m: model.Model, fixtures: list[Fixture], lake: Table | None) -> None:
    f = fixtures[0]
    cat, ns, t = f.base.split(".")
    refs = f"{quote_ident(cat)}.{quote_ident(ns)}.{quote_ident(t + '$refs')}"
    res = {u: tq(u, f"SELECT name FROM {refs} WHERE name = 'audited'") for u in model.REFS_READERS}
    other = {u: tq(u, f"SELECT count(*) FROM {quote_ident(cat)}.{quote_ident(ns)}.{quote_ident(t + '$snapshots')}")[1] for u in model.REFS_READERS if u != OPERATOR}
    base = {u: tq(u, f"SELECT count(*) FROM {quote_relation(f.base)}")[1] for u in model.REFS_READERS}
    record("refs", "model.REFS_READERS read a base's $refs and nothing else of it through the reader catalog",
           all(e is None and r == [["audited"]] for r, e, _ in res.values()) and all(denied(e) for e in other.values())
           and all(denied(e) for e in base.values()),
           {"$refs": {u: short(e) if e else r for u, (r, e, _) in res.items()}, "$snapshots": {u: short(e) for u, e in other.items()},
            "base": {u: short(e) for u, e in base.items()}})
    if lake is None:
        record("lake", "no connector table in the set", None, "")
        return
    owner = next(p for p in m.products if lake in p.tables)
    rel = model.lake_relation(owner.name, lake.name)
    lcat, lns, lt = rel.split(".")
    dbt = model.builder(owner.name)
    checks = {
        f"{dbt} SELECT": (dbt, f"SELECT count(*) FROM {quote_relation(rel)}", False),
        f"{dbt} $files": (dbt, f"SELECT count(*) FROM {quote_ident(lcat)}.{quote_ident(lns)}.{quote_ident(lt + '$files')}", True),
        f"{dbt} INSERT": (dbt, f"INSERT INTO {quote_relation(rel)} SELECT * FROM {quote_relation(rel)}", True),
        f"{dbt} optimize": (dbt, f"ALTER TABLE {quote_relation(rel)} EXECUTE optimize", True),
        f"{OPERATOR} DROP TABLE": (OPERATOR, f"DROP TABLE {quote_relation(rel)}", True),
        f"{OPERATOR} CREATE SCHEMA": (OPERATOR, f"CREATE SCHEMA {quote_ident(lcat)}.zz_planted", True),
        "svc.api $refs": ("svc.api", f"SELECT count(*) FROM {quote_ident(lcat)}.{quote_ident(lns)}.{quote_ident(lt + '$refs')}", False),
        **{f"{u} SELECT": (u, f"SELECT count(*) FROM {quote_relation(rel)}", True) for u in consumers(m)},
    }
    res = {k: tq(u, sql)[1] for k, (u, sql, _) in checks.items()}
    record("lake", f"{rel}: its builder reads it and no $ table of it; nobody writes lake; svc.api reads $refs; no consumer reads "
           "a connector table that offers it no role", all(denied(res[k]) == want for k, (_, _, want) in checks.items()),
           {k: short(e) for k, e in res.items()})


def no_bounds(rows: list) -> bool:
    """SHOW STATS rows (column, size, distinct, nulls, row count, low, high): no bound, no row count."""
    return not [r for r in rows if r[5] is not None or r[6] is not None or (r[0] is None and r[4] is not None)]


def expectations(m: model.Model) -> None:
    """The document set's own rows (tests/access/expectations.yaml, A9) over SQL, as access-verify
    (S20) will run them on the stack. Probe and drift rows are S22's and S20's, recorded only."""
    tables = {model.reader_relation(t): (p, t) for p in m.products for t in p.tables if t.build.kind in model.LAKE_BUILDS}
    for i, e in enumerate(m.expectations):
        rid = f"expect.{i:02d}"
        if isinstance(e, model.TableExpectation):
            rel = quote_relation(e.table)
            rows, err, st = tq(e.principal, f"SELECT * FROM {rel}")
            checks: dict[str, object] = {"read": short(err)}
            if err is None:
                cols = st["columns"]
                checks["visible"] = sorted(cols) == sorted(e.visible)
                checks["masked null"] = all(r[cols.index(c)] is None for r in rows for c in e.masked if c in cols)
                if e.min_row_age_minutes is not None:
                    p, t = tables[e.table]
                    role = next(g.role for g in p.grants if g.principal == e.principal)
                    delay = t.policies[role].delay if role in t.policies else None
                    checks["min age"] = bool(delay) and ok(e.principal, f"SELECT count(*) FROM {rel} WHERE {quote_ident(delay.column)} > "
                                                                     f"current_timestamp - INTERVAL '{e.min_row_age_minutes}' MINUTE") == [[0]]
                if e.must_not_appear:
                    checks["mustNotAppear"] = ok(e.principal, f"SELECT count(*) FROM {rel} WHERE {e.must_not_appear}") == [[0]]
                if e.must_appear:
                    n = ok(e.principal, f"SELECT count(*) FROM {rel} WHERE {e.must_appear.where or 'true'}")[0][0]
                    checks["mustAppear"] = n >= e.must_appear.min_rows
            passed = err is None and all(v is True for k, v in checks.items() if k != "read")
            record(rid, f"{e.principal} reads {e.table} as its expectation row says (columns, masks, age, mustNotAppear, mustAppear)", passed, checks)
        elif isinstance(e, model.DeniedExpectation):
            _, err, _ = tq(e.principal, f"SELECT count(*) FROM {quote_relation(e.table)}")
            record(rid, f"{e.principal} is denied {e.table}", denied(err), short(err))
        elif isinstance(e, model.StatementExpectation):
            rows, err, _ = tq(e.principal, e.sql, session=dict(e.session) or None)
            passed = {"denied": denied(err), "error": err is not None, "noBounds": err is None and no_bounds(rows), "ok": err is None}[e.expect]
            record(rid, f"{e.principal}: {e.sql[:90]}{' (session header)' if e.session else ''} is {e.expect}", passed, short(err) if err else rows[:4])
        else:
            record(rid, "a probe or drift row: S22's catalog-fence.sh and S20's drift check run it, not this suite", None, repr(e))


# ---- experiment (e): writer A against writer B's table -------------------------------------------

E_STATEMENTS = [
    ("select", "SELECT count(*) FROM {t}", None),
    ("time_travel", "SELECT count(*) FROM {t} FOR VERSION AS OF {snap}", None),
    *[(f"meta_{x}", 'SELECT count(*) FROM {cat}.{sch}."{tbl}$' + x + '"', None) for x in META],
    ("show_create", "SHOW CREATE TABLE {t}", None),
    ("describe", "DESCRIBE {t}", None),
    ("show_stats", "SHOW STATS FOR {t}", None),
    ("insert", "INSERT INTO {t} VALUES {row}", None),
    ("delete", "DELETE FROM {t} WHERE {key} = 'zz-e-1'", None),
    ("update", "UPDATE {t} SET {key} = 'u' WHERE {key} = 'zz-e-2'", None),
    ("merge", "MERGE INTO {t} d USING (VALUES ('zz-e-3')) AS s(k) ON d.{key} = s.k WHEN MATCHED THEN DELETE", None),
    ("add_column", "ALTER TABLE {t} ADD COLUMN c2 varchar", None),
    ("set_properties", "ALTER TABLE {t} SET PROPERTIES sorted_by = ARRAY['{keyname}']", None),
    ("comment", "COMMENT ON TABLE {t} IS 'spike'", None),
    ("analyze", "ANALYZE {t}", None),
    ("create_table_in_schema", "CREATE TABLE {cat}.{sch}.t_extra (id bigint)", None),
    ("create_view_over", "CREATE VIEW {cat}.{own}.v_over AS SELECT * FROM {t}", None),
    ("optimize", "ALTER TABLE {t} EXECUTE optimize", None),
    ("optimize_manifests", "ALTER TABLE {t} EXECUTE optimize_manifests", None),
    ("expire_snapshots_7d", "ALTER TABLE {t} EXECUTE expire_snapshots(retention_threshold => '7d')", None),
    ("expire_snapshots_0s_session", "ALTER TABLE {t} EXECUTE expire_snapshots(retention_threshold => '0s')",
     "{catname}.expire_snapshots_min_retention"),
    ("remove_orphan_files_7d", "ALTER TABLE {t} EXECUTE remove_orphan_files(retention_threshold => '7d')", None),
    ("drop_extended_stats", "ALTER TABLE {t} EXECUTE drop_extended_stats", None),
    ("rollback_to_snapshot", "ALTER TABLE {t} EXECUTE rollback_to_snapshot({snap})", None),
    ("call_rollback_to_snapshot", "CALL {cat}.system.rollback_to_snapshot('{schname}', '{tblname}', {snap})", None),
    ("call_unregister_table", "CALL {cat}.system.unregister_table('{schname}', '{tblname}')", None),
    ("truncate", "TRUNCATE TABLE {t}", None),
    ("create_or_replace", "CREATE OR REPLACE TABLE {t} AS SELECT 'r' AS {key}", None),
    ("rename", "ALTER TABLE {t} RENAME TO {cat}.{sch}.{tbl}_moved", None),
    ("drop_table", "DROP TABLE {t}", None),
    ("drop_schema_cascade", "DROP SCHEMA {cat}.{sch} CASCADE", None),
]
assert len(E_STATEMENTS) == 40
READS = {"select", "time_travel", "show_create", "describe", "show_stats"}
BY_DESIGN = {"call_rollback_to_snapshot", "call_unregister_table", "expire_snapshots_0s_session"}


def fingerprint(build_cat: str, sch: str, tbl: str) -> object:
    """What a statement could change, read by the operator through `_build`: refs, snapshot count,
    rows, columns, comment."""
    rel = f"{quote_ident(build_cat)}.{quote_ident(sch)}"
    rows, err, _ = tq(OPERATOR, f"""SELECT
      (SELECT array_join(array_agg(name || ':' || CAST(snapshot_id AS varchar) ORDER BY name), ',') FROM {rel}.{quote_ident(tbl + '$refs')}),
      (SELECT count(*) FROM {rel}.{quote_ident(tbl + '$snapshots')}), (SELECT count(*) FROM {rel}.{quote_ident(tbl)}),
      (SELECT count(*) FROM {quote_ident(build_cat)}.information_schema.columns WHERE table_schema = {sql_literal(sch)} AND table_name = {sql_literal(tbl)}),
      (SELECT comment FROM system.metadata.table_comments WHERE catalog_name = {sql_literal(build_cat)} AND schema_name = {sql_literal(sch)} AND table_name = {sql_literal(tbl)})""")
    return rows if not err else f"gone: {short(err)}"


def e_matrix(user: str, cat: str, target: Fixture | None, own_scratch: tuple[str, str] | None = None) -> dict[str, str]:
    """The 40 statements as `user` against a table through catalog `cat`: B's base `target`, or the
    user's own scratch table (catalog, schema) made fresh before each statement."""
    out: dict[str, str] = {}
    for key, sql, session in E_STATEMENTS:
        if own_scratch:
            bcat, sch = own_scratch
            tbl = "e_own"
            ok(user, f"DROP TABLE IF EXISTS {quote_ident(bcat)}.{quote_ident(sch)}.{tbl}")
            for extra in ("t_extra", "e_own_moved"):
                ok(user, f"DROP TABLE IF EXISTS {quote_ident(bcat)}.{quote_ident(sch)}.{extra}")
            ok(user, f"DROP VIEW IF EXISTS {quote_ident(bcat)}.{quote_ident(sch)}.v_over")
            ok(user, f"CREATE TABLE {quote_ident(bcat)}.{quote_ident(sch)}.{tbl} AS SELECT * FROM (VALUES ('zz-e-1'), ('zz-e-2'), ('zz-e-3')) AS v(k)")
            ok(user, f"INSERT INTO {quote_ident(bcat)}.{quote_ident(sch)}.{tbl} VALUES ('zz-e-4')")
            snap = ok(OPERATOR, f"SELECT snapshot_id FROM {quote_ident(bcat)}.{quote_ident(sch)}.\"{tbl}$snapshots\" ORDER BY committed_at LIMIT 1")[0][0]
            keyname, row, own = "k", "('zz-e-5')", quote_ident(sch)
            before = None
        else:
            assert target
            bcat, sch, tbl = target.build.split(".")
            snap, keyname, row = TESTED[target.base], target.key, target.row(7)
            own = quote_ident(user.removeprefix("dbt_") + model.BASE_SUFFIX)
            before = fingerprint(bcat, sch, tbl)
        f = {"t": f"{quote_ident(cat)}.{quote_ident(sch)}.{quote_ident(tbl)}", "cat": quote_ident(cat), "sch": quote_ident(sch),
             "tbl": tbl, "own": own, "snap": snap, "key": quote_ident(keyname), "keyname": keyname, "row": row,
             "catname": cat, "schname": sch, "tblname": tbl}
        rows, err, _ = tq(user, sql.format(**f), session={session.format(**f): "0s"} if session else None)
        res = "ok" if err is None else "denied: " + err.split("Access Denied: ", 1)[-1][:70] if denied(err) else "error " + err[:90]
        if before is not None and fingerprint(bcat, sch, tbl) != before:
            res += " CHANGED"
        out[key] = res
    return out


TESTED: dict[str, int] = {}


def experiment_e(m: model.Model) -> None:
    a, b = fixtures_by_name("station-readings-twin"), fixtures_by_name("station-readings")
    A, other = model.builder(a.p.name), model.builder(fixtures_by_name("daily-load").p.name)
    b_build, b_reader = model.build_catalog(b.p.domain), model.reader_catalog(b.p.domain)
    for rid, user, cat, what in (("e.AonB.build", A, b_build, "in their shared writer catalog"),
                                 ("e.other-domain.build", other, b_build, "from another domain, through B's writer catalog")):
        res = e_matrix(user, cat, b)
        bad = {k: v for k, v in res.items() if not v.startswith("denied") or "CHANGED" in v}
        record(rid, f"writer {user} on writer B's base table {what}: all 40 statements refused, B's table unchanged",
               not bad, f"{40 - len(bad)}/40 refused; not refused {bad}")
    res = e_matrix(A, b_reader, b)
    bad = {k: v for k, v in res.items() if "CHANGED" in v or (k not in READS and v.startswith("ok"))}
    record("e.AonB.input", f"{A} on B's base, its declared input, through the read-only reader catalog: it selects, and runs "
           f"nothing but reads ({', '.join(sorted(READS))}; exact names, so no $ table); every write and procedure refused; B's table unchanged",
           not bad and res["select"].startswith("ok"), {"not refused": bad, "reads": {k: res[k] for k in sorted(READS)}})


def experiment_e_control(m: model.Model) -> None:
    """B's builder on its own scratch table, in its own base namespace."""
    b = fixtures_by_name("station-readings")
    res = e_matrix(model.builder(b.p.name), model.build_catalog(b.p.domain), None,
                   own_scratch=(model.build_catalog(b.p.domain), model.base_namespace(b.p.name)))
    ran = {k for k, v in res.items() if v.startswith("ok")}
    record("e.BonB.control", "control: B's builder on its own table runs every statement but the two CALLs and the 0 s retention "
           "floor, so the refusals above are the fence", ran == {k for k, _, _ in E_STATEMENTS} - BY_DESIGN,
           {k: v for k, v in res.items() if k not in ran or k in BY_DESIGN})


# ---- runs ----------------------------------------------------------------------------------------


def load() -> tuple[model.Model, list[Fixture]]:
    global MODEL, FIXTURES
    rel = Path(ENV["release"])
    MODEL = model.load(docset.read(rel / "documents"))
    FIXTURES = tables_of(MODEL)
    return MODEL, FIXTURES


def report() -> int:
    counts = {v: sum(1 for r in RESULTS if r[1] == v) for v in ("PASS", "FAIL", "INFO")}
    print("\n| row | verdict | assertion |\n|---|---|---|")
    for rid, verdict, assertion, _ in RESULTS:
        print(f"| {rid} | {verdict} | {assertion} |")
    print(f"\n{len(RESULTS)} rows: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['INFO']} INFO")
    return 1 if counts["FAIL"] or not counts["PASS"] else 0


def read_only_rows(m: model.Model, fixtures: list[Fixture]) -> None:
    roles_and_views(m, fixtures)
    matrix(m, fixtures, TESTED)
    views(m, fixtures, TESTED)


def suite() -> int:
    m, fixtures = load()
    TESTED.update(setup(m, fixtures))
    lake = lake_tables(m)
    plant_view(fixtures_by_name("station-readings"), "foreign_view")
    identities(m, fixtures)
    read_only_rows(m, fixtures)
    builders_and_publishers(m, fixtures)
    session_properties(m, fixtures)
    system_and_queries(m, fixtures)
    refs_and_lake(m, fixtures, lake)
    expectations(m)
    experiment_e(m)
    Path("/work/tested.json").write_text(json.dumps(TESTED))
    return report()


def control() -> int:
    """Last of all, as it drops the builder's own base namespace."""
    m, _ = load()
    experiment_e_control(m)
    return report()


LOOSENED_FROM = "station_readings__full_reader"
DELAYED = "station_readings__delayed_reader"


def swap(path: Path, original: bytes, change: Any, marker: str) -> bool:
    """The running copy's rules.json rewritten in place as `change` makes it, with `marker` (a
    stranger) let execute, so the new file is live exactly when the marker's SELECT 1 runs."""
    rules = json.loads(original)
    change(rules)
    rules["queries"][-1]["user"] += "|" + re.escape(marker)
    write_in_place(path, (json.dumps(rules, indent=2) + "\n").encode())
    return wait(lambda: tq(marker, "SELECT 1")[1] is None)


def rule_of(rules: dict, group_name: str) -> dict:
    (rule,) = [r for r in rules["tables"] if r.get("group") == group_name]
    return rule


def negative() -> int:
    """Rules edited in place in the running copy, each change live before it is read:
    1. the control: the full reader's schema `station_readings` widened to `station_readings.*`,
       the unanchored name a careless rule would carry; the read rows must FAIL (the base and the
       twin's view open up), so the suite can fail;
    2. a mask, then a filter, naming a column the view lacks (a release's rules ahead of its view
       sync): what Trino 484 does, and that no hidden row or value appears;
    3. for the record, the delayed reader's mask gone while the view still projects the column
       (a column dropped from the contract, the rules moved before the view): the value shows,
       which is why deploy syncs views before it moves the rules pointer (S19).
    The copy is put back, and the base and every marker refused again, before this returns."""
    m, fixtures = load()
    TESTED.update({k: int(v) for k, v in json.loads(Path("/work/tested.json").read_text()).items()})
    path = Path(ENV["release"]) / trino_rules.RULES
    original = path.read_bytes()
    a = fixtures_by_name("station-readings")
    full = next(g.principal for g in a.p.grants if g.role == "full_reader")
    delayed = next(g.principal for g in a.p.grants if g.role == "delayed_reader")
    query = f"SELECT {quote_ident(a.key)}, {quote_ident('operator_note')} FROM {quote_relation(a.view)} ORDER BY 1"
    normal = ok(delayed, query)
    observed: dict[str, Any] = {}
    path.chmod(0o644)
    try:
        def widen(r: dict) -> None:
            rule_of(r, LOOSENED_FROM)["schema"] += ".*"
        live = swap(path, original, widen, STRANGERS[0])
        RESULTS.clear()
        read_only_rows(m, fixtures)
        failed = {r[0] for r in RESULTS if r[1] == "FAIL"}
        RESULTS.clear()

        def absent_mask(r: dict) -> None:
            rule_of(r, DELAYED).setdefault("columns", []).append({"name": "zz_absent", "mask": "CAST(NULL AS varchar)"})

        def absent_filter(r: dict) -> None:
            rule_of(r, DELAYED)["filter"] = f"({rule_of(r, DELAYED)['filter']}) AND (\"zz_absent\" IS NULL)"

        def mask_dropped(r: dict) -> None:
            rule_of(r, DELAYED).pop("columns")
        for name, change, marker in (("mask", absent_mask, STRANGERS[1]), ("filter", absent_filter, STRANGERS[2]),
                                     ("dropped", mask_dropped, STRANGERS[3])):
            observed[name] = (swap(path, original, change, marker), tq(delayed, query))
    finally:
        write_in_place(path, original)
        restored = wait(lambda: denied(tq(full, f"SELECT count(*) FROM {quote_relation(a.base)}")[1])
                        and all(denied(tq(u, "SELECT 1")[1]) for u in STRANGERS))
        path.chmod(0o444)
    expected = {"h1.matrix", f"view.{fixtures_by_name('station-readings-twin').view}.others"}
    record("negative.control", f"with {LOOSENED_FROM}'s schema widened to `station_readings.*`, the suite fails "
           f"{sorted(expected)}; restored, the base and every stranger are refused again", live and expected <= failed and restored,
           f"rows that failed: {sorted(failed)}; loosened live {live}; restored {restored}")
    for name, what in (("mask", "a mask on a column the view lacks"), ("filter", "a row filter on a column the view lacks")):
        live, (rows, err, _) = observed[name]
        record(f"rules.absent-column.{name}", f"{what}: fails closed (an error) or changes nothing; no hidden row or value shows",
               live and (err is not None or rows == normal), f"live {live}; Trino 484: {short(err) if err else 'rows as without it'}")
    live, (rows, err, _) = observed["dropped"]
    record("rules.mask-dropped-before-view", "for the record: rules without the delayed reader's mask while the view still "
           "projects the column show the value (so deploy syncs views before the rules, S19)", None,
           f"live {live}; values of operator_note: {sorted({r[1] for r in rows}) if not err else short(err)}")
    return report()


def write_in_place(path: Path, data: bytes) -> None:
    """Rewrite without a rename: through Docker Desktop's file sharing a reader keeps the old file
    after a rename, and sees an in-place write at once (step S14's measurement)."""
    with open(path, "r+b") as f:
        f.write(data.ljust(max(len(data), path.stat().st_size), b" "))
        f.truncate()


def wait(cond: Any, seconds: int = 40) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(1)
    return False


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "compile":
        print(compile_release(Path(sys.argv[2])))
    elif command == "suite":
        sys.exit(suite())
    elif command == "negative":
        sys.exit(negative())
    elif command == "control":
        sys.exit(control())
    else:
        sys.exit(__doc__)
