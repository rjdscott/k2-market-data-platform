#!/usr/bin/env bash
# The tested-data marker (ADR-061, step S20), on a throwaway lake that `make access-verify` or
# tests/access/prove.sh started and staged (scripts/lib/throwaway-lake.sh, access_verify.py stage):
#
#   scripts/verify/tested-data-marker.sh <prefix> [--no-schema-change]
#
# For each dbt or code output of the release the lake runs (compiled/trino/views.json):
#  1. its base is an Iceberg TABLE, not a view at the table's name (a builder owns every name of
#     its base namespace; Trino ignores the reader view's `FOR VERSION AS OF 'audited'` on a view,
#     so readers would get the view's rows: the review of S12, C1), and it carries an `audited`
#     tag, a tag on a snapshot the table once had as main and whose files are its own
#     (catalog_drift.tag_problems);
#  2. the reader view the catalog stores has one trino representation whose SQL equals, token for
#     token, what the release's lake emitter generates over the contract's columns the tagged
#     snapshot holds (catalog_drift.generated_sql, so a column aliased past its mask fails); it is a
#     definer view (trino.run-as-owner set) and its owner is the product's publisher;
#  3. an untagged commit is planted (every tagged row again, its key moved by 1000000, as the
#     builder): the view, read as the publisher, holds exactly the tagged snapshot's rows and none
#     of main's planted ones, and every grantee reads only tagged keys, never a planted one;
#  4. unless --no-schema-change, or a check above failed: one contract column is dropped from the
#     first output and added back, each as dbt's contracted path does it (CREATE OR REPLACE
#     TABLE), each published by lake.view_update's two statements around one tag commit (ADR-061
#     part 5), while a grantee polls the view every 50 ms: every poll answers, with the same rows.
#     The column comes back empty, so run this after the rows that read it.
# One PASS or FAIL line per check; exit 0 when all pass, 1 otherwise, 2 on a usage error.
# The live system's markers are the drift check's (scripts/verify/catalog_drift.py), which
# compares the same three things in step 1 and 2 without writing anything.
set -euo pipefail
cd "$(dirname "$0")/../.."
P=${1:?usage: scripts/verify/tested-data-marker.sh <throwaway prefix> [--no-schema-change]}
shift
exec bash scripts/lib/throwaway-lake.sh py "$P" - "$@" <<'PY'
import json, sys, threading, time
from pathlib import Path
sys.path.insert(0, "/src/scripts")
import access_verify as av
from verify import catalog_drift as cd
from k2gen import model
from k2gen.emit import lake
from k2gen.model import quote_ident, quote_relation

trino, env = av.connect(Path("/work/env.json"))
release = Path(env["release"])
m = av.load(release)
uri = f"{env['lakekeeper']}/catalog"
failed: list[str] = []


def check(name: str, ok: bool, detail: object) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    if not ok:
        failed.append(name)
    return ok


def outputs():
    for v in json.loads((release / model.VIEWS).read_text())["views"]:
        view = model.View(v["relation"], v["base"], tuple(v["columns"]), v["owner"])
        p = next(p for p in m.products if model.publisher(p.name) == view.owner)
        t = next(t for t in p.tables if model.reader_relation(t) == view.relation)
        yield view, p, t, cd.Catalog(uri, model.warehouse(p.domain))


def tag_of(cat, p, t):
    meta = cat.table(model.base_namespace(p.name), t.name)
    return meta, (((meta or {}).get("refs") or {}).get("audited") or {}).get("snapshot-id")


def key_of(t):
    return min((c for c in t.columns if c.primary_key_position), key=lambda c: c.primary_key_position, default=t.columns[0]).name


for view, p, t, cat in outputs():
    meta, tag = tag_of(cat, p, t)
    as_view = cat.view(model.base_namespace(p.name), t.name)
    if not check(f"{view.relation} base is a table", meta is not None and as_view is None,
                 f"{view.base} is " + ("a VIEW, so the reader view serves its rows, not the tagged snapshot's" if as_view
                                        else "an Iceberg table" if meta else "missing")):
        continue
    problems = cd.tag_problems(meta, model.AUDITED_TAG)
    check(f"{view.relation} tag", not problems, "; ".join(problems) or f"audited = {tag}, once main, its own files")
    if problems:
        continue
    stored = cat.view(model.reader_namespace(p.name), t.name)
    if not check(f"{view.relation} stored", stored is not None, "the catalog holds the view" if stored else "no view"):
        continue
    s = cd.view_state(stored)
    want = cd.generated_sql(view, [c for c in view.columns if c in cd.columns_at(meta, tag)], tag)
    check(f"{view.relation} SQL", s["dialects"] == ["trino"] and [cd.tokens(x) for x in s["sql"]] == [cd.tokens(want)],
          f"stored {s['sql']!r}, generated {want!r}")
    check(f"{view.relation} definer", s["owner"] is not None, f"trino.run-as-owner {s['owner']!r}")
    check(f"{view.relation} owner", s["owner"] == view.owner, f"{s['owner']!r}, the publisher is {view.owner!r}")

    # 3: an untagged commit, then what the view serves.
    dbt, pub = model.builder(p.name), model.publisher(p.name)
    build, base, vbuild = (quote_relation(model.build_relation(p.domain, p.name, t.name)), quote_relation(view.base),
                           quote_relation(model.view_build_relation(p.domain, p.name, t.name)))
    trino.ok(dbt, f"INSERT INTO {build} SELECT {av.moved_key(t, 1_000_000)} FROM {build} FOR VERSION AS OF {tag}")
    meta, tag_after = tag_of(cat, p, t)
    cols = ", ".join(quote_ident(c) for c in view.columns if c in cd.columns_at(meta, tag))
    at_tag = f"SELECT {cols} FROM {base} FOR VERSION AS OF {tag}"
    extra = trino.ok(pub, f"SELECT count(*) FROM (SELECT {cols} FROM {vbuild} EXCEPT ALL {at_tag})")[0][0]
    short = trino.ok(pub, f"SELECT count(*) FROM ({at_tag} EXCEPT ALL SELECT {cols} FROM {vbuild})")[0][0]
    hidden = trino.ok(pub, f"SELECT count(*) FROM (SELECT {cols} FROM {base} EXCEPT ALL SELECT {cols} FROM {vbuild})")[0][0]
    check(f"{view.relation} serves the tag", extra == 0 and short == 0 and hidden > 0 and tag_after == tag,
          f"rows the view has beyond the tag {extra}, the tag's rows it lacks {short}, main's untested rows it hides {hidden}; "
          f"tag unmoved {tag_after == tag}")
    key = key_of(t)
    tagged = {r[0] for r in trino.ok(pub, f"SELECT {quote_ident(key)} FROM {base} FOR VERSION AS OF {tag}")}
    planted = {r[0] for r in trino.ok(pub, f"SELECT {quote_ident(key)} FROM {base}")} - tagged
    for g in p.grants:
        pol = t.policies.get(g.role)
        if key in (pol.mask if pol else ()):
            print(f"SKIP {view.relation} {g.principal}: its role masks the key {key}, so its keys say nothing")
            continue
        seen = {r[0] for r in trino.ok(g.principal, f"SELECT {quote_ident(key)} FROM {quote_relation(view.relation)}")}
        check(f"{view.relation} {g.principal} reads tagged rows only", seen <= tagged and not seen & planted,
              f"{len(seen)} keys, {len(seen - tagged)} not tagged, {len(seen & planted)} planted")

if "--no-schema-change" in sys.argv or failed:
    print(f"SKIP schema change: {'--no-schema-change' if not failed else 'a check above failed'}")
else:
    view, p, t, cat = next(o for o in outputs() if o[1].grants)  # a grantee polls the view
    filters = {c for pol in t.policies.values() for c in ([pol.delay.column] if pol.delay else []) + ([pol.row_scope.column] if pol.row_scope else [])}
    col = next((c for c in view.columns if c != key_of(t) and c not in filters), None)
    if col is None:  # a contract of only its key and its filters' columns: nothing to drop and add back
        print(f"SKIP schema change: {view.relation} has no column but its key and its policies' filter columns")
        print("tested-data-marker: PASS")
        sys.exit(0)
    reader = next((g.principal for g in p.grants if not t.policies.get(g.role)), p.grants[0].principal)
    dbt, pub = model.builder(p.name), model.publisher(p.name)
    build, base = quote_relation(model.build_relation(p.domain, p.name, t.name)), quote_relation(view.base)
    count_sql = f"SELECT count(*) FROM {quote_relation(view.relation)}"
    before_rows = trino.ok(reader, count_sql)[0][0]
    polls: list[tuple[float, str | None, int | None]] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            a = trino.run(reader, count_sql)
            polls.append((time.monotonic(), a.says() if a.error else None, None if a.error else a.rows[0][0]))
            time.sleep(0.05)

    def types(sql: str) -> dict[str, str]:
        return dict(trino.run(pub, f"{sql} LIMIT 0").columns)

    def publish(select: str, label: str) -> None:
        _, old_tag = tag_of(cat, p, t)
        old = types(f"SELECT * FROM {base} FOR VERSION AS OF {old_tag}")
        trino.ok(dbt, f"CREATE OR REPLACE TABLE {build} AS {select} FROM {build} FOR VERSION AS OF {old_tag}")
        meta, _ = tag_of(cat, p, t)
        tested = meta["refs"]["main"]["snapshot-id"]
        new = types(f"SELECT * FROM {base}")
        first, second = lake.view_update(view, old, new)
        t0 = time.monotonic()
        if first:
            trino.ok(pub, first)
        st, doc = cd.call("POST", f"{cat.base}/transactions/commit", {"table-changes": [{
            "identifier": {"namespace": [model.base_namespace(p.name)], "name": t.name},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested},
                             {"type": "assert-ref-snapshot-id", "ref": model.AUDITED_TAG, "snapshot-id": old_tag}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": tested}]}]})
        assert st in (200, 204), f"tag commit: HTTP {st} {doc}"
        trino.ok(pub, second)
        time.sleep(0.5)  # let polls land on the far side too
        during = [x for x in polls if x[0] >= t0]
        print(f"     {label}: view {'C, old and new' if first else 'created'} then tag then C and new; {len(during)} polls since")

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    time.sleep(0.5)
    others = ", ".join(quote_ident(c) for c in view.columns if c != col)
    publish(f"SELECT {others}", f"drop {col}")
    typed = {c.name: c.physical_type for c in t.columns}
    back = ", ".join(f"CAST(NULL AS {typed[c]}) AS {quote_ident(c)}" if c == col else quote_ident(c) for c in view.columns)
    publish(f"SELECT {back}", f"add {col} back")
    stop.set()
    thread.join()
    errors = [e for _, e, _ in polls if e]
    counts = sorted({n for _, e, n in polls if not e})
    check(f"{view.relation} schema change keeps reads answering", len(polls) >= 10 and not errors and counts == [before_rows],
          f"{len(polls)} polls by {reader}, {len(errors)} errors {errors[:2]}, row counts seen {counts} (before {before_rows}); "
          f"column {col} dropped and added back")

print(f"tested-data-marker: {'FAIL ' + ', '.join(failed) if failed else 'PASS'}")
sys.exit(1 if failed else 0)
PY
