#!/usr/bin/env python3
"""access-verify (A9, step S20): every hand-written row of tests/access/expectations.yaml, run
against a RUNNING Trino as the principal it names. The rows are written and reviewed by a person
and never generated from the rules they test, so a pass is not the compiler agreeing with itself
(tests/access/README.md). make access-verify drives it (mk/access.mk).

    access_verify.py run   --release <dir> --scope release|live [--env <env.json>] [--out <result.json>]
    access_verify.py stage --release <dir> --env <env.json>

The release is the one the Trino under test runs (a pointer resolves once): its documents give the
rows, the principals and the products, so a row naming something the running release does not
know fails. Every row prints one line, `PASS|FAIL|SKIP <n> <what it asserts>: <evidence>`, and a
skipped row always says why. Exit 0 when no row fails and at least one passed, 1 otherwise, 2 on a
usage error or an unreachable Trino. --out writes the JSON result a deploy record can cite:
{release, setSha256, scope, trino, at, exit, counts, rows: [{n, row, verdict, reason}]}.

A row passes only on the exact outcome it names, never on "some error". `denied` is Trino's
PERMISSION_DENIED and nothing else: a syntax error, a missing catalog, schema, table or column
(SCHEMA_NOT_FOUND, TABLE_NOT_FOUND, COLUMN_NOT_FOUND), NOT_SUPPORTED, or a refused login fails the
row, since none of them is the rules at work. `error` is NOT_SUPPORTED and nothing else, the one
error the schema gives it (a version on a view: "Views do not support versioning"); a row needing
another class needs a field the frozen schema lacks. A read row reads the relation, gets exactly its `visible` columns, finds every `masked`
column null in every row, no row younger than `minRowAgeMinutes` by the table's delay column, no
row matching `mustNotAppear`, and at least `mustAppear.minRows` matching its `where`.

Where the endpoint and each principal's password come from:
- scope release (a throwaway lake, scripts/lib/throwaway-lake.sh, run in its tools container):
  --env names its env.json; a principal's password is <secrets>/principals/<id>.password when the
  lake wrote one, else the lake's one run password (env.json passwordFile). Every principal of
  the release has a line, so every row runs; a planted-view row plants its view by REST first and
  removes it after, and the drift row runs the drift check.
- scope live (the running stack, from the host or the tools one-shot): Trino at
  https://${K2_TRINO_HOST:-localhost}:${K2_TRINO_PORT:-8443}, its CA ${K2_TRINO_CA:-${K2_SECRETS}/tls/ca.pem},
  each password ${K2_SECRETS:-$HOME/k2-secrets}/principals/<id>.password. No row impersonates
  anyone and no verification principal exists: a principal that may become consumers would be a
  key to every consumer's rows. Live runs only the rows whose principal's password the platform
  holds, a service principal (svc.*, dbt_*, pub_*) or a grantee of the access canary only (it
  exists to be tested), and only when every product the row names is the canary, so a loosened
  rule can damage nothing real. Every other row is a SKIP naming the throwaway run at release
  time, where generated passwords stand in for the consumers'. A planted-view row is a SKIP (it
  would write the live catalog), and so is the drift row unless the catalog API answers at
  $K2_CATALOG_URI (it does only from inside k2-catalog). An eligible principal with no password
  file fails its row.

stage (throwaway lake only) makes the lake the release describes, as its build and deploy would:
every namespace of the storage plan by REST; each dbt or code product's seeds and models, built as
its builder through `<domain>_build` (only ref() and config() are rendered; anything else is
refused, being dbt-runner's work in phase 002); one tag commit per warehouse; each view as its
publisher (lake.create_view); then one untested write per output, every row again with its key
moved (+1000 or a zz-untested- prefix), which no reader may ever see; and the platform's Trino DDL
as svc.platform.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/: verify.catalog_drift
from verify import catalog_drift  # noqa: E402

CANARY_PRODUCT = "governance.access-canary"  # contracts/governance/access-canary: exists to be tested on live
DENIED = "PERMISSION_DENIED"  # an `expect: denied` row passes on this errorName alone
UNSUPPORTED = "NOT_SUPPORTED"  # and an `expect: error` row on this one
SKIP_REST = "via rest: k2-api's REST rows join in phase 003"


# ---- Trino ---------------------------------------------------------------------------------------

@dataclass
class Answer:
    rows: list[list[Any]] = field(default_factory=list)
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, Trino type)
    error: str | None = None  # Trino's errorName, or HTTP_<code>
    message: str = ""

    def says(self) -> str:
        return f"{self.error}: {self.message[:160]}" if self.error else f"ok, {len(self.rows)} row(s)"


class NoPassword(Exception):
    pass


class Trino:
    """Trino's client protocol over HTTPS, logged in as each principal with its own password."""

    def __init__(self, url: str, ca: str, password: Callable[[str], str]):
        self.url, self.ctx, self.password = url.rstrip("/"), ssl.create_default_context(cafile=ca), password

    def run(self, user: str, sql: str, session: dict[str, str] | None = None, as_user: str | None = None) -> Answer:
        auth = "Basic " + base64.b64encode(f"{user}:{self.password(user)}".encode()).decode()
        # SESSION_AUTHORIZATION lets SET SESSION AUTHORIZATION reach the impersonation rules.
        headers = {"Authorization": auth, "X-Trino-User": as_user or user, "X-Trino-Client-Capabilities": "SESSION_AUTHORIZATION"}
        if session:
            headers["X-Trino-Session"] = ",".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in session.items())
        out = Answer()
        try:
            req: urllib.request.Request | None = urllib.request.Request(f"{self.url}/v1/statement", sql.encode(), headers)
            deadline = time.monotonic() + 300
            while req is not None:
                with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
                    doc = json.load(r)
                out.rows += doc.get("data", [])
                out.columns = out.columns or [(c["name"], c["type"]) for c in doc.get("columns", [])]
                if "error" in doc:
                    out.error, out.message = doc["error"].get("errorName", "ERROR"), doc["error"].get("message", "")
                    return out
                if time.monotonic() > deadline:
                    out.error, out.message = "CLIENT_TIMEOUT", "no answer in 300 s"
                    return out
                req = urllib.request.Request(doc["nextUri"], headers={"Authorization": auth}) if doc.get("nextUri") else None
        except urllib.error.HTTPError as e:
            out.error, out.message = f"HTTP_{e.code}", "the login was refused" if e.code == 401 else e.reason
        return out

    def ok(self, user: str, sql: str, **kw: Any) -> list[list[Any]]:
        a = self.run(user, sql, **kw)
        if a.error:
            raise RuntimeError(f"{user}: {sql[:160]}: {a.says()}")
        return a.rows


def connect(env_file: Path | None) -> tuple[Trino, dict[str, Any]]:
    """The endpoint and passwords of a throwaway lake (its env.json) or of the live stack."""
    if env_file:
        env = json.loads(env_file.read_text())
        secrets = Path(env.get("secrets", Path(env["ca"]).parents[1]))
        shared = Path(env["passwordFile"]) if env.get("passwordFile") else None

        def lake_password(u: str) -> str:
            own = secrets / "principals" / f"{u}.password"  # per-principal, as ${K2_SECRETS} lays them out
            if own.is_file():
                return own.read_text().strip()
            if shared:
                return shared.read_text().strip()
            raise NoPassword(f"no {own} and no passwordFile in {env_file}")

        return Trino(env["trino"], env["ca"], lake_password), env
    root = Path(os.environ.get("K2_SECRETS") or Path.home() / "k2-secrets")
    url = f"https://{os.environ.get('K2_TRINO_HOST') or 'localhost'}:{os.environ.get('K2_TRINO_PORT') or 8443}"

    def live_password(u: str) -> str:
        f = root / "principals" / f"{u}.password"  # model.password_file
        if not f.is_file():
            raise NoPassword(f"no {f}")
        return f.read_text().strip()

    return Trino(url, os.environ.get("K2_TRINO_CA") or str(root / "tls/ca.pem"), live_password), {"trino": url}


# ---- the release ---------------------------------------------------------------------------------

def load(release: Path) -> Any:
    from k2gen import docset, model
    return model.load(docset.read(release / model.DOCUMENTS_DIR))


def relations(m: Any) -> dict[str, Any]:
    """Every relation the release declares, reader names, bases and their names through the
    writer catalog alike, to its table (a seed's to None)."""
    from k2gen import model
    out: dict[str, Any] = {}
    for p in m.products:
        for t in p.tables:
            if t.build.kind in model.LAKE_BUILDS:
                out[model.reader_relation(t)] = t
                out[model.input_relation(t)] = t
            if t.build.kind in ("dbt", "code"):
                out[model.build_relation(p.domain, p.name, t.name)] = t
                out[model.view_build_relation(p.domain, p.name, t.name)] = t
        if any(t.build.kind in ("dbt", "code") for t in p.tables):
            for path in p.seeds:
                stem = path.removesuffix(".csv")
                out[model.base_relation(p.domain, p.name, stem)] = out[model.build_relation(p.domain, p.name, stem)] = None
    return out


def planted(e: Any, m: Any) -> Any:
    """The product whose reader namespace a denied row names under a name no table has: the
    foreign definer view the run plants (gate h1.31). None for any other row."""
    from k2gen import model
    if not isinstance(e, model.DeniedExpectation):
        return None
    catalog, ns, name = e.table.split("$")[0].split(".")
    for p in m.products:
        built = [t for t in p.tables if t.build.kind in ("dbt", "code")]
        if built and catalog == model.reader_catalog(p.domain) and ns == model.reader_namespace(p.name) \
                and name not in {t.name for t in p.tables}:
            return p
    return None


def unknown(e: Any, m: Any) -> str | None:
    """Why a row names something the running release does not have, or None."""
    from k2gen.emit import trino_rules
    if getattr(e, "principal", None) and e.principal not in trino_rules.known_principals(m):
        return f"{e.principal} is no principal of the running release"
    table = getattr(e, "table", None)
    if table and not table.startswith("system.") and table.split("$")[0] not in relations(m) and not planted(e, m):
        return f"{table} is no relation of the running release (no reader name, base or seed of it)"
    return None


def live_skip(e: Any, m: Any) -> str | None:
    """Why a row does not run on the live system, or None when it does."""
    from k2gen import model
    if isinstance(e, model.ProbeExpectation | model.DriftExpectation):
        return None
    if planted(e, m):
        return "it plants a view, which would write the live catalog; it ran on the throwaway lake at release time"
    who = e.principal
    service = re.fullmatch(r"(svc\.|dbt_|pub_).+", who)
    granted = {p.id for p in m.products for g in p.grants if g.principal == who}
    if not service and granted != {CANARY_PRODUCT}:
        return f"{who}'s password is its owner's, not the platform's; its row ran on the throwaway lake at release time"
    named = set(re.findall(r"[a-z0-9_]+", json.dumps(asdict(e))))
    others = sorted(p.id for p in m.products if p.id != CANARY_PRODUCT
                    and {model.fence(p.name), model.base_namespace(p.name)} & named)
    if others:
        return f"it names {', '.join(others)}, not the canary, and a live run risks nothing real"
    return None


# ---- one row -------------------------------------------------------------------------------------

def denial(a: Answer) -> tuple[bool, str]:
    if a.error == DENIED:
        return True, a.says()
    return False, f"expected an access denial, got {a.says()}"


def unsupported(a: Answer) -> tuple[bool, str]:
    if a.error == UNSUPPORTED:
        return True, a.says()
    return False, f"expected {UNSUPPORTED}, got {a.says()}"


def no_bounds(a: Answer) -> tuple[bool, str]:
    if a.error:
        return False, f"expected SHOW STATS with no bound, got {a.says()}"
    # SHOW STATS: column_name, data_size, distinct_values_count, nulls_fraction, row_count, low_value, high_value
    bounds = [r for r in a.rows if r[5] is not None or r[6] is not None or (r[0] is None and r[4] is not None)]
    if not a.rows or bounds:
        return False, f"a bound or row count shows: {bounds[:3]}" if bounds else "SHOW STATS returned no row"
    return True, f"{len(a.rows)} stats rows, no low, high or row count"


def delay_column(m: Any, e: Any) -> str | None:
    t = relations(m).get(e.table)
    if t is None:
        return None
    role = next((g.role for p in m.products for g in p.grants if g.principal == e.principal and t in p.tables), None)
    policies = [t.policies[role]] if role in t.policies else list(t.policies.values())
    return next((pol.delay.column for pol in policies if pol.delay),
                next((c.name for c in t.columns if c.time == "event"), None))


def read_row(trino: Trino, m: Any, e: Any) -> tuple[bool, str]:
    from k2gen.model import quote_ident, quote_relation
    rel = quote_relation(e.table)
    first = trino.run(e.principal, f"SELECT * FROM {rel} LIMIT 1")
    if first.error:
        return False, f"expected to read it, got {first.says()}"
    bad, seen = [], {}
    cols = sorted(c for c, _ in first.columns)
    if cols != sorted(e.visible):
        bad.append(f"columns {cols}, the row says {sorted(e.visible)}")

    def count(where: str) -> int:
        return int(trino.ok(e.principal, f"SELECT count(*) FROM {rel} WHERE {where}")[0][0])

    for c in e.masked:
        if (n := count(f"{quote_ident(c)} IS NOT NULL")) != 0:
            bad.append(f"{n} row(s) show masked {c}")
    if e.min_row_age_minutes is not None:
        col = delay_column(m, e)
        if col is None:
            bad.append("minRowAgeMinutes, but the table has no delay or event-time column")
        elif (n := count(f"{quote_ident(col)} > current_timestamp - INTERVAL '{int(e.min_row_age_minutes)}' MINUTE")) != 0:
            bad.append(f"{n} row(s) younger than {e.min_row_age_minutes} minutes by {col}")
    if e.must_not_appear and (n := count(e.must_not_appear)) != 0:
        bad.append(f"{n} row(s) match mustNotAppear ({e.must_not_appear})")
    if e.must_appear:
        n = seen["mustAppear"] = count(e.must_appear.where or "true")
        if n < e.must_appear.min_rows:
            bad.append(f"{n} row(s) match mustAppear ({e.must_appear.where or 'any row'}), at least {e.must_appear.min_rows} wanted")
    return not bad, "; ".join(bad) or f"columns {cols}; masked {list(e.masked) or 'none'} null; " + \
        f"min age {e.min_row_age_minutes}; mustNotAppear none; mustAppear {seen.get('mustAppear', 'n/a')}"


def plant(env: dict[str, Any], m: Any, p: Any, name: str) -> Callable[[], None]:
    """A definer view no release declares, put in p's reader namespace by REST (the catalog fence's
    forgery, gate h3.04): p's real publisher as owner, SQL over the base's main. Returns its undo."""
    from k2gen import model
    view = next(t for t in p.tables if t.build.kind in ("dbt", "code"))
    cat = catalog_drift.Catalog(f"{env['lakekeeper']}/catalog", model.warehouse(p.domain))
    ns = model.reader_namespace(p.name)
    meta = cat.view(ns, view.name)
    if meta is None:
        raise RuntimeError(f"no view {ns}.{view.name} to copy for the planted view; stage the lake first")
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    schema = next(x for x in meta["schemas"] if x["schema-id"] == v["schema-id"])
    sql = f"SELECT * FROM {model.quote_relation(model.input_relation(view))}"  # main: the untested rows too
    body = {"name": name, "schema": schema, "properties": {catalog_drift.OWNER_PROPERTY: model.publisher(p.name)},
            "view-version": {"version-id": 1, "timestamp-ms": int(time.time() * 1000), "schema-id": schema["schema-id"],
                             "summary": v["summary"], "representations": [{"type": "sql", "sql": sql, "dialect": "trino"}],
                             "default-namespace": [ns]}}
    st, doc = catalog_drift.call("POST", f"{cat.base}/namespaces/{ns}/views", body)
    if st != 200:
        raise RuntimeError(f"planting {ns}.{name}: HTTP {st} {doc}")

    def undo() -> None:
        st, _ = catalog_drift.call("DELETE", f"{cat.base}/namespaces/{ns}/views/{name}")
        if st not in (200, 204):
            raise RuntimeError(f"removing the planted view {ns}.{name}: HTTP {st}")

    return undo


def verdict(trino: Trino, env: dict[str, Any], m: Any, release: Path, scope: str, e: Any) -> tuple[str, str]:
    from k2gen import model
    if why := unknown(e, m):
        return "FAIL", f"names something that does not exist: {why}"
    if scope == "live" and (why := live_skip(e, m)):
        return "SKIP", why
    if "sql" not in getattr(e, "via", ("sql",)):
        return "SKIP", SKIP_REST
    if isinstance(e, model.ProbeExpectation):
        return "SKIP", "a network probe: scripts/verify/catalog-fence.sh (step S22) runs it on the stack"
    if isinstance(e, model.DriftExpectation):
        uri = env.get("lakekeeper") and f"{env['lakekeeper']}/catalog" or os.environ.get("K2_CATALOG_URI")
        if not uri:
            return "SKIP", "the catalog API answers only inside k2-catalog: scripts/verify/catalog_drift.py runs there (release-verify)"
        found = catalog_drift.drift(uri, catalog_drift.expected(release))
        return ("FAIL" if found["differences"] else "PASS"), "; ".join(found["differences"]) or \
            f"clean; {len(found['pending'])} pending, {len(found['skipped'])} skipped (run records, phase 002)"
    try:
        if isinstance(e, model.TableExpectation):
            passed, why = read_row(trino, m, e)
        elif isinstance(e, model.DeniedExpectation):
            product = planted(e, m)
            undo = plant(env, m, product, e.table.split(".")[2]) if product else None
            try:
                passed, why = denial(trino.run(e.principal, f"SELECT count(*) FROM {model.quote_relation(e.table)}"))
            finally:
                if undo:
                    undo()
            why += " (view planted by REST for this row, then removed)" if undo else ""
        else:
            a = trino.run(e.principal, e.sql, session=dict(e.session) or None)
            passed, why = {"denied": denial, "error": unsupported, "noBounds": no_bounds}[e.expect](a)
    except NoPassword as x:
        return "FAIL", f"no password for {e.principal}: {x}"
    return ("PASS" if passed else "FAIL"), why


def what(n: int, e: Any) -> str:
    from k2gen import model
    if isinstance(e, model.TableExpectation):
        return f"{n:02d} {e.principal} reads {e.table}"
    if isinstance(e, model.DeniedExpectation):
        return f"{n:02d} {e.principal} is denied {e.table}"
    if isinstance(e, model.StatementExpectation):
        return f"{n:02d} {e.principal}: {e.sql[:100]}{' (session header)' if e.session else ''} is {e.expect}"
    if isinstance(e, model.ProbeExpectation):
        return f"{n:02d} probe from {e.origin} to {e.target} is {e.expect}"
    return f"{n:02d} catalog drift is {e.expect}"


def run(release: Path, scope: str, env_file: Path | None, out: Path | None) -> int:
    from k2gen import docset, model
    from k2gen.checks import access
    m = load(release)
    trino, env = connect(env_file)
    try:  # one request first, so an unreachable Trino is exit 2 and not a row of failures
        urllib.request.urlopen(f"{trino.url}/v1/info", context=trino.ctx, timeout=10).read()
    except OSError as x:
        print(f"access-verify: Trino is unreachable at {trino.url}: {x}", file=sys.stderr)
        return 2
    rows = []
    print(f"access-verify: release {release.name}, scope {scope}, Trino {trino.url}")
    for f in access.check(docset.read(release / model.DOCUMENTS_DIR), None):
        if f.code.startswith("access.expectation-"):
            rows.append({"n": None, "row": None, "verdict": "FAIL", "reason": f"coverage ({f.code}): {f.message}"})
            print(f"FAIL coverage {f.code}: {f.message}", flush=True)
    for n, e in enumerate(m.expectations, 1):
        v, why = verdict(trino, env, m, release, scope, e)
        rows.append({"n": n, "row": {k: v_ for k, v_ in asdict(e).items() if v_ not in (None, (), {})} if is_dataclass(e) else None,
                     "verdict": v, "reason": why})
        print(f"{v} {what(n, e)}: {why}", flush=True)
    counts = {k: sum(r["verdict"] == k for r in rows) for k in ("PASS", "FAIL", "SKIP")}
    code = 1 if counts["FAIL"] or not counts["PASS"] else 0
    print(f"access-verify: {len(rows)} rows: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP; exit {code}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"release": release.name, "setSha256": m.provenance.set_sha256, "scope": scope,
                                   "trino": trino.url, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                   "exit": code, "counts": counts, "rows": rows}, indent=2, default=list) + "\n")
    return code


# ---- stage: the lake the release describes, on a throwaway lake ----------------------------------

_REF = re.compile(r"\{\{\s*ref\(\s*'([a-z0-9_]+)'\s*\)\s*\}\}")
_CONFIG = re.compile(r"\{\{-?\s*config\(.*?\)\s*-?\}\}", re.S)


def stage(release: Path, env_file: Path) -> int:
    from k2gen import model
    from k2gen.emit import lake
    m = load(release)
    trino, env = connect(env_file)
    uri = f"{env['lakekeeper']}/catalog"
    if list((release / "compiled/lake/ddl/spark").glob("*.sql")) if (release / "compiled/lake/ddl/spark").exists() else []:
        raise SystemExit("stage: this release has Spark tables (warehouse k2); stage builds none, Spark does (phase 002)")
    for w in json.loads((release / model.STORAGE_PLAN).read_text())["warehouses"]:
        cat = catalog_drift.Catalog(uri, w["name"])
        for ns in w["namespaces"]:
            st, _ = catalog_drift.call("POST", f"{cat.base}/namespaces", {"namespace": [ns]})
            assert st in (200, 409), f"namespace {w['name']}.{ns}: HTTP {st}"
    commits: dict[str, list[dict[str, Any]]] = {}
    built = []
    for p in sorted(m.products, key=lambda p: p.id):
        tables = [t for t in p.tables if t.build.kind in ("dbt", "code")]
        if not tables:
            continue
        dbt, cat = model.builder(p.name), catalog_drift.Catalog(uri, model.warehouse(p.domain))
        names = {model.dbt_seed(p.name, s.removesuffix(".csv")): s.removesuffix(".csv") for s in p.seeds}
        names |= {model.dbt_model(p.name, t.name): t.name for t in tables}
        for path, data in sorted(p.seeds.items()):
            rows = list(csv.reader(io.StringIO(data.decode())))
            head, body = rows[0], rows[1:]
            types = ["bigint" if all(re.fullmatch(r"-?[0-9]+", r[i]) for r in body) else "varchar" for i in range(len(head))]
            rel = model.build_relation(p.domain, p.name, path.removesuffix(".csv"))
            trino.ok(dbt, f"CREATE TABLE {model.quote_relation(rel)} ({', '.join(f'{model.quote_ident(h)} {ty}' for h, ty in zip(head, types, strict=True))})")
            values = ", ".join("(" + ", ".join(v if ty == "bigint" else model.sql_literal(v) for v, ty in zip(r, types, strict=True)) + ")" for r in body)
            trino.ok(dbt, f"INSERT INTO {model.quote_relation(rel)} VALUES {values}")
        for t in tables:
            sql = _CONFIG.sub("", p.models[f"{t.name}.sql"])
            refs = {name: model.quote_relation(model.build_relation(p.domain, p.name, table)) for name, table in names.items()}
            sql = _REF.sub(lambda r, refs=refs: refs[r.group(1)], sql)
            if "{{" in sql or "{%" in sql:
                raise SystemExit(f"stage: {p.id} {t.name}: Jinja beyond ref() and config(); dbt-runner builds it (phase 002)")
            rel = model.build_relation(p.domain, p.name, t.name)
            trino.ok(dbt, f"CREATE TABLE {model.quote_relation(rel)} AS {sql}")
            meta = cat.table(model.base_namespace(p.name), t.name)
            tested = meta["refs"]["main"]["snapshot-id"]
            commits.setdefault(model.warehouse(p.domain), []).append({
                "identifier": {"namespace": [model.base_namespace(p.name)], "name": t.name},
                "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested},
                                 {"type": "assert-ref-snapshot-id", "ref": model.AUDITED_TAG, "snapshot-id": None}],
                "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": tested}]})
            built.append((p, t))
    for w, changes in commits.items():
        cat = catalog_drift.Catalog(uri, w)
        st, doc = catalog_drift.call("POST", f"{cat.base}/transactions/commit", {"table-changes": changes})
        assert st in (200, 204), f"tag commit {w}: HTTP {st} {doc}"
    for v in json.loads((release / model.VIEWS).read_text())["views"]:
        view = model.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
        trino.ok(view.owner, lake.create_view(view))
    for p, t in built:
        rel = model.quote_relation(model.build_relation(p.domain, p.name, t.name))
        trino.ok(model.builder(p.name), f"INSERT INTO {rel} SELECT {moved_key(t)} FROM {rel}")
    for ddl in sorted((release / "compiled/lake/ddl/trino").glob("*.sql")):
        text = "\n".join(line for line in ddl.read_text().splitlines() if not line.startswith("--")).strip().rstrip(";")
        trino.ok("svc.platform", text)
    print(f"stage: {release.name}: {len(built)} output(s) built, tagged and viewed, each with one untested write after its tag; "
          f"{len(list((release / 'compiled/lake/ddl/trino').glob('*.sql')))} platform table(s)")
    return 0


def moved_key(t: Any, by: int = 1000) -> str:
    """Every column of t, its primary key's first column moved so that no row of the result is a
    tested one: an integer key plus `by`, any other key prefixed zz-untested-."""
    from k2gen.model import quote_ident
    key = min((c for c in t.columns if c.primary_key_position), key=lambda c: c.primary_key_position, default=t.columns[0])
    moved = (f"{quote_ident(key.name)} + {by}" if key.physical_type in ("bigint", "integer")
             else f"'zz-untested-' || CAST({quote_ident(key.name)} AS varchar)")
    return ", ".join(f"{moved} AS {quote_ident(c.name)}" if c is key else quote_ident(c.name) for c in t.columns)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="every expectation row against a running Trino")
    r.add_argument("--release", required=True, type=Path)
    r.add_argument("--scope", required=True, choices=("release", "live"))
    r.add_argument("--env", type=Path, help="a throwaway lake's env.json (scope release)")
    r.add_argument("--out", type=Path, help="write the JSON result here")
    s = sub.add_parser("stage", help="build, tag and view the release's dbt products on a throwaway lake")
    s.add_argument("--release", required=True, type=Path)
    s.add_argument("--env", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        return stage(a.release.resolve(), a.env)
    if a.scope == "release" and not a.env:
        ap.error("scope release runs on a throwaway lake: --env <its env.json>")
    return run(a.release.resolve(), a.scope, a.env, a.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
