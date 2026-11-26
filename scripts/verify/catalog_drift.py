#!/usr/bin/env python3
"""The catalog drift check (ADR-061 part 6, step S20): the catalog as its REST API shows it,
against what one release generates. Run from inside the catalog's network, the only place its API
answers once the fence is up (ADR-059 part 7): the `tools` one-shot, or a throwaway lake's tools
container (scripts/lib/throwaway-lake.sh py).

    catalog_drift.py --release <release dir> [--catalog <uri>] [--out <result.json>]

<uri> defaults to $K2_CATALOG_URI, else http://lakekeeper:8181/catalog. What the release generates:

- the namespaces of every warehouse its storage plan lists (compiled/lake/storage-plan.json), and
  nothing else;
- the tables of each namespace: every location of compiled/lake/retention.json, and each dbt or
  code product's seed tables in its base namespace (model.dbt_seed); nothing else, so a table
  planted in a reader namespace, or beside the bases, is named; and each an Iceberg TABLE: a view
  at a base's name (a builder owns every name of its base namespace, so it can drop the table and
  create one) is named, since Trino ignores the view SQL's `FOR VERSION AS OF 'audited'` on a
  view and readers would be served its rows (the review of S12, C1);
- the views of compiled/trino/views.json, each with one representation, in the `trino` dialect, its
  SQL equal token for token (Trino stores it reformatted, so a column aliased past its mask, the
  review of S12's M1, is a difference) to what the release's lake emitter generates
  (generated_sql, never a text written here) over the contract's
  columns that the tagged snapshot holds (ADR-061 part 5; while a publish is between its two
  statements, the columns main holds as well), and `trino.run-as-owner` (a definer view) equal to
  the product's publisher; nothing else, so a planted view is named whatever owner it claims;
- each such view's base carrying an `audited` tag (a tag, not a branch) on a snapshot that was
  once the table's own main (in its snapshot log) and whose manifest list lies under the table's
  own location, so a snapshot grafted from another table is named. A product whose outputs have
  neither a tag nor a view has not published yet: PENDING, not drift; anything it does hold is
  still checked. Whether each tag equals its table's newest audited run record needs the run
  records of phase 002: until then each tag prints a SKIP line, never a pass.

Exits 0 when the catalog equals the release (PENDING and SKIP lines allowed), 1 naming every
difference on a DRIFT line, 2 when the catalog cannot be reached or the release cannot be read.
The JSON result, for the deploy record, is {release, catalog, at, exit, differences, pending,
skipped}. Stdlib and k2gen only; no secret is involved, since the catalog has no authentication.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

OWNER_PROPERTY = "trino.run-as-owner"  # where Trino's Iceberg REST views keep a definer view's owner
CATALOG_URI = "http://lakekeeper:8181/catalog"  # emit/trino_etc.CATALOG_URI, Lakekeeper's name on k2-catalog
_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z0-9_$]+|\S")


class Unreachable(Exception):
    """No HTTP answer: refused, no route, timed out, or the name does not resolve."""


def call(method: str, url: str, body: object = None, timeout: float = 10) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Unreachable(str(getattr(e, "reason", e))) from None


class Catalog:
    """One warehouse through the Iceberg REST API."""

    def __init__(self, uri: str, warehouse: str):
        st, cfg = call("GET", f"{uri.rstrip('/')}/v1/config?warehouse={urllib.parse.quote(warehouse)}")
        if st != 200:
            raise Unreachable(f"config of warehouse {warehouse}: HTTP {st}")
        self.base = f"{uri.rstrip('/')}/v1/{cfg['defaults']['prefix']}"

    def _list(self, path: str, key: str) -> list[Any]:
        """Every page of a listing: a plant past the first page is still found."""
        out: list[Any] = []
        token = None
        while True:
            st, doc = call("GET", f"{self.base}/{path}" + (f"?pageToken={urllib.parse.quote(token)}" if token else ""))
            if st != 200:
                raise Unreachable(f"GET {path}: HTTP {st}")
            out += doc.get(key, [])
            token = doc.get("next-page-token")
            if not token:
                return out

    def namespaces(self) -> list[str]:
        return [".".join(ns) for ns in self._list("namespaces", "namespaces")]

    def tables(self, ns: str) -> list[str]:
        return [i["name"] for i in self._list(f"namespaces/{ns}/tables", "identifiers")]

    def views(self, ns: str) -> list[str]:
        return [i["name"] for i in self._list(f"namespaces/{ns}/views", "identifiers")]

    def table(self, ns: str, t: str) -> dict[str, Any] | None:
        st, doc = call("GET", f"{self.base}/namespaces/{ns}/tables/{t}")
        return doc["metadata"] if st == 200 else None

    def view(self, ns: str, v: str) -> dict[str, Any] | None:
        st, doc = call("GET", f"{self.base}/namespaces/{ns}/views/{v}")
        return doc["metadata"] if st == 200 else None


def tokens(sql: str) -> list[str]:
    """SQL as a token list: identifiers folded to lower case whether quoted or not (Trino folds
    both), keywords likewise, string literals exact. Trino stores a view's SQL reformatted, so
    only this comparison is fair."""
    out = []
    for t in _TOKEN.findall(sql):
        if t.startswith('"'):
            out.append(t[1:-1].replace('""', '"').lower())
        elif t.startswith("'"):
            out.append(t)
        else:
            out.append(t.lower())
    return out


def view_state(meta: dict[str, Any]) -> dict[str, Any]:
    """A view's current version as the drift check compares it."""
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    return {"owner": (meta.get("properties") or {}).get(OWNER_PROPERTY),
            "dialects": sorted(r.get("dialect", "") for r in v["representations"]),
            "sql": [r["sql"] for r in v["representations"]]}


def columns_at(meta: dict[str, Any], snapshot_id: int) -> list[str]:
    """The column names of one snapshot's schema."""
    snap = next(s for s in meta.get("snapshots", []) if s["snapshot-id"] == snapshot_id)
    sid = snap.get("schema-id", meta["current-schema-id"])
    return [f["name"] for f in next(s for s in meta["schemas"] if s["schema-id"] == sid)["fields"]]


def tag_problems(meta: dict[str, Any], tag: str) -> list[str]:
    """What is wrong with `tag` on a base table: absent, not a tag, or on a snapshot this table
    never had as its main or whose files are another table's (a graft)."""
    ref = (meta.get("refs") or {}).get(tag)
    if ref is None:
        return [f"no {tag!r} tag"]
    sid = ref["snapshot-id"]
    out = [] if ref.get("type") == "tag" else [f"{tag!r} is a {ref.get('type')}, not a tag"]
    snap = next((s for s in meta.get("snapshots", []) if s["snapshot-id"] == sid), None)
    if snap is None:
        return [*out, f"{tag!r} names snapshot {sid}, which the table does not hold"]
    if sid not in {e["snapshot-id"] for e in meta.get("snapshot-log", [])}:
        out.append(f"{tag!r} names snapshot {sid}, never this table's main (not in its snapshot log): a moved or grafted tag")
    location = meta["location"].rstrip("/") + "/"
    if not str(snap.get("manifest-list", "")).startswith(location):
        out.append(f"{tag!r} names snapshot {sid}, whose manifest list is outside the table's location {location}: "
                   "another table's snapshot, grafted")
    return out


def generated_sql(view: Any, columns: list[str], tagged: int) -> str:
    """The view's SQL as the release's own lake emitter generates it for these columns and this
    tagged snapshot: the one place this check meets the emitter, so the generated shape (ADR-061
    part 1, and the fail-closed form the review of S12 asks for) can change without this check."""
    from k2gen.emit import lake
    # ponytail: an emitter that pins the view by snapshot id takes it as a third argument; any other
    # change of view_sql's signature lands on this one line.
    if len(inspect.signature(lake.view_sql).parameters) > 2:
        return str(lake.view_sql(view, columns, tagged))  # type: ignore[call-arg]
    return lake.view_sql(view, columns)


def expected(release: Path) -> dict[str, Any]:
    """What the release generates, read from its own compiled files and documents."""
    from k2gen import docset, model

    compiled = {p: json.loads((release / p).read_text()) for p in (model.STORAGE_PLAN, model.RETENTION, model.VIEWS)}
    m = model.load(docset.read(release / model.DOCUMENTS_DIR))
    warehouse_of = {model.reader_catalog(d.name): model.warehouse(d.name) for d in m.domains}
    namespaces = {w["name"]: set(w["namespaces"]) for w in compiled[model.STORAGE_PLAN]["warehouses"]}
    tables: dict[tuple[str, str], set[str]] = defaultdict(set)
    for location in compiled[model.RETENTION]:
        w, ns, t = location.split(".")
        tables[w, ns].add(t)
    pending_tables: dict[tuple[str, str], set[str]] = defaultdict(set)  # what a product creates at its first build
    for p in m.products:
        if any(t.build.kind in ("dbt", "code") for t in p.tables):
            for path in p.seeds:  # <stem>.csv: the table model.dbt_seed aliases to <stem>
                key = (model.warehouse(p.domain), model.base_namespace(p.name))
                tables[key].add(path.removesuffix(".csv"))
                pending_tables[key].add(path.removesuffix(".csv"))
    views: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    for v in compiled[model.VIEWS]["views"]:
        view = model.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
        catalog, ns, name = view.relation.split(".")
        _, base_ns, base_t = view.base.split(".")
        views[warehouse_of[catalog], ns][name] = {"view": view, "base": (base_ns, base_t)}
        pending_tables[warehouse_of[catalog], base_ns].add(base_t)
    return {"namespaces": namespaces, "tables": tables, "views": views, "pending_tables": pending_tables,
            "sql": generated_sql, "release": release.name}


def drift(uri: str, want: dict[str, Any]) -> dict[str, list[str]]:
    """Every difference between the catalog and `want` (expected()), as named lines."""
    found: dict[str, list[str]] = {"differences": [], "pending": [], "skipped": []}

    def diff(line: str) -> None:
        found["differences"].append(line)

    for w in sorted(want["namespaces"]):
        cat = Catalog(uri, w)
        present = set(cat.namespaces())
        for ns in sorted(present - want["namespaces"][w]):
            diff(f"DRIFT namespace {w}.{ns}: the release declares no such namespace")
        for ns in sorted(want["namespaces"][w] - present):
            diff(f"DRIFT namespace {w}.{ns}: missing; the release's storage plan declares it")
        # A product has published once any of its outputs carries a tag or a view.
        products: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        for (vw, ns), vs in want["views"].items():
            if vw == w:
                for name, spec in vs.items():
                    products[spec["base"][0]].append((ns, name, spec))
        metas = {(spec["base"]): cat.table(*spec["base"]) if spec["base"][0] in present else None
                 for outs in products.values() for _, _, spec in outs}
        published = {base_ns: any(((metas[spec["base"]] or {}).get("refs") or {}).get("audited")
                                  or (ns in present and name in cat.views(ns)) for ns, name, spec in outs)
                     for base_ns, outs in products.items()}
        for ns in sorted(present & want["namespaces"][w]):
            here, listed = set(cat.tables(ns)), set(cat.views(ns))
            expect = want["tables"].get((w, ns), set())
            reader = ns in {n for (vw, n) in want["views"] if vw == w}
            for t in sorted(here - expect):
                where = "a reader namespace, which holds views only" if reader else "a namespace of the release"
                diff(f"DRIFT table {w}.{ns}.{t}: in {where}, and the release declares no such table")
            for t in sorted(expect - here):
                if t in listed:
                    diff(f"DRIFT base {w}.{ns}.{t}: a VIEW where the release's TABLE must be, so a reader view over it "
                         "serves the view's rows, not the tagged snapshot's (review of S12, C1)")
                elif ns in published and not published[ns] and t in want["pending_tables"].get((w, ns), set()):
                    found["pending"].append(f"PENDING table {w}.{ns}.{t}: its product has not published yet")
                else:
                    diff(f"DRIFT table {w}.{ns}.{t}: missing; the release declares it")
            expect_views = want["views"].get((w, ns), {})
            for v in sorted(listed - expect_views.keys() - expect):
                meta = cat.view(ns, v) or {}
                owner = (meta.get("properties") or {}).get(OWNER_PROPERTY)
                diff(f"DRIFT view {w}.{ns}.{v}: the release declares no such view (it claims owner {owner!r})")
            for name, spec in sorted(expect_views.items()):
                view, (base_ns, base_t) = spec["view"], spec["base"]
                if name not in listed:
                    if not published.get(base_ns, True):
                        found["pending"].append(f"PENDING view {view.relation}: its product has not published yet")
                    else:
                        diff(f"DRIFT view {view.relation} ({w}.{ns}.{name}): missing; its product has published")
                    continue
                s = view_state(cat.view(ns, name) or {})
                if s["owner"] != view.owner:
                    diff(f"DRIFT view {view.relation}: {OWNER_PROPERTY} {s['owner']!r}, the release says {view.owner!r}"
                         + ("" if s["owner"] else " (an invoker view, or none)"))
                if s["dialects"] != ["trino"]:
                    diff(f"DRIFT view {view.relation}: representations {s['dialects']}, the release says one, ['trino']")
                meta = metas.get((base_ns, base_t))
                tag = ((meta or {}).get("refs") or {}).get("audited")
                if not meta or not tag:
                    diff(f"DRIFT view {view.relation}: its base {w}.{base_ns}.{base_t} has no audited tag to read")
                    continue
                tagged = columns_at(meta, tag["snapshot-id"])
                main = (meta.get("refs") or {}).get("main")
                allowed = [want["sql"](view, [c for c in view.columns if c in tagged], tag["snapshot-id"])]
                if main:
                    on_main = columns_at(meta, main["snapshot-id"])
                    mid = [c for c in view.columns if c in tagged and c in on_main]
                    if mid:
                        allowed.append(want["sql"](view, mid, tag["snapshot-id"]))
                if not any(tokens(sql) == tokens(a) for sql in s["sql"][:1] for a in allowed) or len(s["sql"]) != 1:
                    diff(f"DRIFT view {view.relation}: SQL {' | '.join(s['sql'])!r}, the release says {allowed[0]!r}")
        for base_ns, outs in sorted(products.items()):
            for _, _, spec in outs:
                meta, (_, base_t) = metas[spec["base"]], spec["base"]
                where = f"{w}.{base_ns}.{base_t}"
                if meta is None:
                    continue  # missing table: named above, or pending
                tag = (meta.get("refs") or {}).get("audited")
                if not tag and not published[base_ns]:
                    found["pending"].append(f"PENDING tag {where} audited: its product has not published yet")
                    continue
                for problem in tag_problems(meta, "audited"):
                    diff(f"DRIFT tag {where}: {problem}")
                if tag:
                    found["skipped"].append(f"SKIP tag {where} audited = {tag['snapshot-id']} against its newest audited "
                                            "run record: run records are written from phase 002")
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", required=True, type=Path, help="a compiled release directory (a pointer resolves once)")
    ap.add_argument("--catalog", default=os.environ.get("K2_CATALOG_URI", CATALOG_URI), help="the catalog's REST root, .../catalog")
    ap.add_argument("--out", type=Path, help="write the JSON result here")
    a = ap.parse_args(argv)
    release = a.release.resolve()
    result: dict[str, Any] = {"release": release.name, "catalog": a.catalog, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        found = drift(a.catalog, expected(release))
    except Unreachable as e:
        print(f"catalog_drift: the catalog is unreachable at {a.catalog}: {e}", file=sys.stderr)
        found, result["error"] = {"differences": [], "pending": [], "skipped": []}, f"unreachable: {e}"
        code = 2
    except (OSError, ValueError, KeyError) as e:
        print(f"catalog_drift: cannot read release {release}: {e}", file=sys.stderr)
        found, result["error"] = {"differences": [], "pending": [], "skipped": []}, f"release: {e}"
        code = 2
    else:
        for line in found["differences"] + found["pending"] + found["skipped"]:
            print(line)
        n = len(found["differences"])
        print(f"catalog_drift: {release.name}: " + (f"{n} difference(s)" if n else "clean, the catalog equals the release")
              + f"; {len(found['pending'])} pending, {len(found['skipped'])} skipped")
        code = 1 if n else 0
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({**result, "exit": code, **found}, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
