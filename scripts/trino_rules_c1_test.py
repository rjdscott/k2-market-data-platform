"""The review's C1, H1 and H2 on a throwaway lake, as rows for the rules suite (scripts/trino_rules_test.py).

    scripts/lib/throwaway-lake.sh py <prefix> scripts/trino_rules_c1_test.py

Run after `throwaway-lake.sh up` from a release `trino_rules_test.py compile` made, and after the
suite or alone: it rebuilds station-readings-twin's base, tag and view itself, and leaves them as
the release makes them. Rows (PASS, FAIL or INFO, as the suite's):

- c1.*: the builder `dbt_<fence>` owns every name in `<fence>__base`, and Trino 484 ignores
  `FOR VERSION AS OF '<string>'` on a view, so the builder swaps its base for a view of its own
  (invoker or definer), an untagged table, a view named `<table>$refs`, or nothing: every reader
  read fails, and no forged value is served. The rebuild by CREATE OR REPLACE TABLE, a publish and
  the schema-change sequence (emit/lake.view_update) still serve the tagged rows. Branch DDL fails
  for the builder and the operator alike (the review's L1), so no branch named `audited` appears.
- h1.delay-zone: a delay filter over each time type K2 takes, read under four session time zones:
  only `timestamp(6) with time zone` gives one answer, and the fixture's delayed reader gets the
  same rows under every zone (the check access.delay-type and the emitter's refusal).
- h2.row-scope-partition: the processed-row count of a scoped reader's probes, with the twin's
  base unpartitioned and then partitioned by the scope column: hidden values show only in the
  first (the check access.row-scope-unpartitioned).

h1 and h2 rewrite the running copy's rules.json in place and put it back before they return.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trino_rules_test as T  # noqa: E402
from k2gen import model  # noqa: E402
from k2gen.emit import lake, trino_rules  # noqa: E402
from k2gen.model import quote_ident as qi  # noqa: E402
from k2gen.model import quote_relation as qr  # noqa: E402
from k2gen.model import sql_literal as sl  # noqa: E402

MARK = T.STRANGERS[0]  # a stranger with a password line, let execute exactly while a changed rules file is live
ZONES = (None, "Pacific/Kiritimati", "Etc/GMT+12", "America/Los_Angeles")


def q(user: str, sql: str, zone: str | None = None) -> tuple[list, str | None, dict]:
    """T.tq, with the X-Trino-Time-Zone header a client sends after SET TIME ZONE."""
    if zone is None:
        return T.tq(user, sql)
    auth = T.basic(user)
    req = urllib.request.Request(f"{T.ENV['trino']}/v1/statement", sql.encode(),
                                 {"Authorization": auth, "X-Trino-User": user, "X-Trino-Time-Zone": zone})
    doc, rows = json.load(urllib.request.urlopen(req, context=T.CTX, timeout=60)), []
    while True:
        rows += doc.get("data", [])
        if "error" in doc:
            return rows, f"{doc['error']['errorName']}: {doc['error']['message']}", {}
        if not doc.get("nextUri"):
            return rows, None, doc.get("stats", {})
        doc = json.load(urllib.request.urlopen(urllib.request.Request(doc["nextUri"], headers={"Authorization": auth}), context=T.CTX, timeout=60))


class Twin:
    def __init__(self, name: str = "station-readings-twin") -> None:
        self.f = T.fixtures_by_name(name)
        self.dbt, self.pub = model.builder(self.f.p.name), model.publisher(self.f.p.name)
        self.reader = next(g.principal for g in self.f.p.grants)
        self.view = model.View(self.f.view, self.f.base, tuple(self.f.cols), self.pub)
        self.group = model.group(self.f.p.name, "full_reader")

    def tag(self, snap: int) -> int:
        f = self.f
        st, _ = T.rest("POST", f"{T.catalog_base(model.warehouse(f.p.domain))}/transactions/commit", {"table-changes": [{
            "identifier": {"namespace": [model.base_namespace(f.p.name)], "name": f.t.name},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": snap}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": model.AUDITED_TAG, "type": "tag", "snapshot-id": snap}]}]})
        return st

    def rebuild(self, rows: list[str] | None = None, props: str = "") -> None:
        """The base back to the release's state: rows 1 to 5 tagged, row 6 untested, the
        release's view SQL; any relation the attacks left at the base's names dropped."""
        f = self.f
        # The main suite's control ends by DROP SCHEMA CASCADE on a base namespace: stage both
        # schemas here, so a shared lake and a fresh one reach the same state.
        T.ok(self.dbt, f"CREATE SCHEMA IF NOT EXISTS {qr(f.build.rsplit('.', 1)[0])}")
        T.ok(self.pub, f"CREATE SCHEMA IF NOT EXISTS {qr(f.view_build.rsplit('.', 1)[0])}")
        T.tq(self.dbt, f"DROP VIEW IF EXISTS {qr(f.build)}")
        T.tq(self.dbt, f"DROP TABLE IF EXISTS {qr(f.build)}")
        # A view named <table>$refs cannot be dropped through Trino, which reads the name as the
        # metadata table of <table>: by the catalog's REST API, as the drift check would.
        T.rest("DELETE", f"{T.catalog_base(model.warehouse(f.p.domain))}/namespaces/{model.base_namespace(f.p.name)}"
                         f"/views/{urllib.parse.quote(f.t.name + '$refs', safe='')}")
        T.ok(self.dbt, f"CREATE TABLE {qr(f.build)} ({', '.join(f'{qi(c)} {f.types[c]}' for c in f.cols)}){props}")
        T.ok(self.dbt, f"INSERT INTO {qr(f.build)} VALUES " + ", ".join(rows or [f.row(i) for i in range(1, 6)]))
        assert self.tag(T.main_id(f)) == 200
        T.ok(self.pub, f"CREATE OR REPLACE VIEW {qr(f.view_build)} SECURITY DEFINER AS {T.view_sql(f)}")
        if rows is None:
            T.ok(self.dbt, f"INSERT INTO {qr(f.build)} VALUES {f.row(6)}")

    def keys(self) -> list[int] | str:
        rows, err, _ = T.tq(self.reader, f"SELECT {qi(self.f.key)} FROM {qr(self.f.view)} ORDER BY 1")
        return err or [int(r[0].rsplit("-", 1)[1]) for r in rows]

    def forged(self) -> str:
        return ", ".join(f"CAST('zz-forged' AS varchar) AS {qi(c)}" if self.f.types[c] == "varchar" else f"CAST(NULL AS {self.f.types[c]}) AS {qi(c)}"
                         for c in self.f.cols)


# ---- rules rewritten in place --------------------------------------------------------------------


def live_rules(change: Any) -> None:
    """The running copy's rules.json as `change` makes it (None: as released), live on return."""
    path = Path(T.ENV["release"]) / trino_rules.RULES
    original = Path("/work/rules.released.json")
    if not original.exists():
        original.write_bytes(path.read_bytes())
    path.chmod(0o644)
    try:
        T.write_in_place(path, original.read_bytes())
        assert T.wait(lambda: T.denied(T.tq(MARK, "SELECT 1")[1])), "the released rules are not live"
        if change is not None:
            rules = json.loads(original.read_bytes())
            change(rules)
            rules["queries"][-1]["user"] += "|" + T.re.escape(MARK)
            T.write_in_place(path, (json.dumps(rules, indent=2) + "\n").encode())
            assert T.wait(lambda: T.tq(MARK, "SELECT 1")[1] is None), "the changed rules are not live"
    finally:
        path.chmod(0o444)


def reader_rule(rules: dict, group: str) -> dict:
    (rule,) = [r for r in rules["tables"] if r.get("group") == group]
    return rule


# ---- C1 ------------------------------------------------------------------------------------------


def c1(tw: Twin) -> None:
    f = tw.f
    sql = T.view_sql(f)
    guard = f"WHERE EXISTS (SELECT 1 FROM {qr(f.base + '$refs')} WHERE \"name\" = 'audited' AND \"type\" = 'TAG')"
    tw.rebuild()
    T.record("c1.view-shape", "the release's view SQL reads the base at the tag and guards on the base's own $refs",
             sql.endswith(f"FOR VERSION AS OF 'audited' {guard}") and tw.keys() == [1, 2, 3, 4, 5], {"sql": sql, "reader": tw.keys()})
    attacks = {
        "invoker-view": f"CREATE VIEW {qr(f.build)} SECURITY INVOKER AS SELECT {tw.forged()}",
        "definer-view": f"CREATE VIEW {qr(f.build)} SECURITY DEFINER AS SELECT {tw.forged()}",
        "untagged-table": f"CREATE TABLE {qr(f.build)} AS SELECT {tw.forged()}",
        "gone": None,
    }
    for name, statement in attacks.items():
        tw.rebuild()
        made = [T.tq(tw.dbt, f"DROP TABLE {qr(f.build)}")[1]] + ([T.tq(tw.dbt, statement)[1]] if statement else [])
        rows, err, _ = T.tq(tw.reader, f"SELECT * FROM {qr(f.view)}")
        T.record(f"c1.base-{name}", f"the builder drops its base and puts {name.replace('-', ' ')} at its name: every reader read "
                 "fails, and no forged value is served", all(e is None for e in made) and err is not None and not rows,
                 {"builder": [T.short(e) for e in made], "reader": T.short(err) if err else rows})
    # A relation named <table>$refs: the builder can create one while the base is absent, but
    # Trino resolves `<table>$refs` as the metadata table of <table> and never as that view.
    tw.rebuild()
    refs = f"{qr(f.build.rsplit('.', 1)[0])}.{qi(f.t.name + '$refs')}"
    steps = {"drop base": T.tq(tw.dbt, f"DROP TABLE {qr(f.build)}")[1],
             "view <table>$refs": T.tq(tw.dbt, f"CREATE VIEW {refs} SECURITY INVOKER AS SELECT 'audited' AS name, 'TAG' AS type")[1],
             "view at base": T.tq(tw.dbt, f"CREATE VIEW {qr(f.build)} SECURITY INVOKER AS SELECT {tw.forged()}")[1]}
    rows, err, _ = T.tq(tw.reader, f"SELECT * FROM {qr(f.view)}")
    planted = steps["view <table>$refs"] is None
    T.tq(tw.dbt, f"DROP VIEW {qr(f.build)}")
    T.ok(tw.dbt, f"CREATE TABLE {qr(f.build)} ({', '.join(f'{qi(c)} {f.types[c]}' for c in f.cols)})")
    T.ok(tw.dbt, f"INSERT INTO {qr(f.build)} VALUES " + ", ".join(f.row(i) for i in range(1, 6)))
    tw.tag(T.main_id(f))
    seen = T.tq(tw.pub, f"SELECT name, type FROM {qr(f.base + '$refs')} ORDER BY 1")
    T.record("c1.refs-forgery", "a view the builder names <table>$refs is never read as the base's refs: with the base a view, "
             "the reader's read fails; with the base a tagged table again, the guard reads the table's real refs and readers its rows",
             planted and err is not None and not rows and seen[1] is None and seen[0] == [["audited", "TAG"], ["main", "BRANCH"]]
             and tw.keys() == [1, 2, 3, 4, 5],
             {"builder": {k: T.short(e) for k, e in steps.items()}, "reader, base a view": T.short(err) if err else rows,
              "$refs, base a table": seen[0] if seen[1] is None else T.short(seen[1]), "reader, base a table": tw.keys()})
    dropped = T.tq(tw.dbt, f"DROP VIEW {refs}")[1]
    T.record("c1.refs-litter", "for the record: the builder cannot drop the <table>$refs view it planted through Trino (the drift "
             "check's REST listing finds it)", None, T.short(dropped))
    tw.rebuild()  # drops the planted <table>$refs view by REST
    # Inputs are pinned by snapshot id (k2_input): Trino refuses an id on a view outright.
    T.ok(tw.dbt, f"DROP TABLE {qr(f.build)}")
    T.ok(tw.dbt, f"CREATE VIEW {qr(f.build)} SECURITY INVOKER AS SELECT {tw.forged()}")
    _, err, _ = T.tq(tw.dbt, f"SELECT count(*) FROM {qr(f.build)} FOR VERSION AS OF {T.TESTED.get(f.base, 1)}")
    T.record("c1.pin-by-id", "an input read pinned by snapshot id (k2_input's form) over a base swapped for a view fails: "
             "Trino 484 refuses versioning on a view", err is not None and "Views do not support versioning" in err, T.short(err))
    branch(tw)
    rebuild_and_publish(tw)
    schema_change(tw)
    tw.rebuild()
    cost(tw)


def branch(tw: Twin) -> None:
    """L1: branch DDL, as the builder on its own base (recreated, so untagged) and as the operator."""
    f = tw.f
    tw.rebuild()
    T.ok(tw.dbt, f"DROP TABLE {qr(f.build)}")
    T.ok(tw.dbt, f"CREATE TABLE {qr(f.build)} AS SELECT {tw.forged()}")
    res = {f"{u} {k}": T.tq(u, s.format(t=qr(f.build)))[1] for u in (tw.dbt, T.OPERATOR) for k, s in (
        ("CREATE BRANCH", "CREATE BRANCH audited IN TABLE {t}"),
        ("CREATE OR REPLACE BRANCH", "CREATE OR REPLACE BRANCH audited IN TABLE {t}"),
        ("DROP BRANCH", "DROP BRANCH main IN TABLE {t}"),
        ("FAST FORWARD", "ALTER BRANCH main IN TABLE {t} FAST FORWARD TO main"))}
    refs = T.ok(T.OPERATOR, f"SELECT name, type FROM {qr(f.build + '$refs')} ORDER BY 1")
    rows, err, _ = T.tq(tw.reader, f"SELECT * FROM {qr(f.view)}")
    T.record("c1.branch-ddl", "Trino 484 refuses branch DDL to the builder and the operator alike (an access-control error, "
             "not a rule: revisit on any Trino upgrade); the recreated base keeps main alone, and readers get an error",
             all(e is not None for e in res.values()) and refs == [["main", "BRANCH"]] and err is not None and not rows,
             {"statements": {k: T.short(e) for k, e in res.items()}, "refs": refs, "reader": T.short(err) if err else rows})


def rebuild_and_publish(tw: Twin) -> None:
    """dbt's CREATE OR REPLACE TABLE while a reader polls: the tagged rows throughout; then a
    publish: the new tested rows."""
    f = tw.f
    tw.rebuild()
    seen: list[Any] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            seen.append(tw.keys())

    th = threading.Thread(target=poll)
    th.start()
    time.sleep(1)
    new = [f.row(i) for i in (7, 8)]
    made = T.tq(tw.dbt, f"CREATE OR REPLACE TABLE {qr(f.build)} AS SELECT * FROM (VALUES {', '.join(new)}) AS v({', '.join(qi(c) for c in f.cols)})")[1]
    time.sleep(1)
    before = len(seen)
    st = tw.tag(T.main_id(f))
    time.sleep(1.5)
    stop.set()
    th.join()
    pre, post = seen[:before], seen[before:]
    T.record("c1.rebuild-publish", "with the guard, a reader polling through dbt's CREATE OR REPLACE TABLE sees the tagged rows "
             "only, and after the tag commit the new tested rows only",
             made is None and st == 200 and pre and all(s == [1, 2, 3, 4, 5] for s in pre) and post and post[-1] == [7, 8]
             and all(s in ([1, 2, 3, 4, 5], [7, 8]) for s in post),
             {"rebuild": T.short(made), "commit": st, "polls before": len(pre), "polls after": len(post),
              "other": [s for s in seen if s not in ([1, 2, 3, 4, 5], [7, 8])][:3]})


def schema_change(tw: Twin) -> None:
    """ADR-061 part 5 with the guarded view: the contract drops operator_note and adds extra."""
    f = tw.f
    tw.rebuild()
    contract = tuple(c for c in f.cols if c != "operator_note") + ("extra",)
    view = model.View(f.view, f.base, contract, tw.pub)
    kept = ", ".join(qi(c) for c in f.cols if c != "operator_note")
    T.ok(tw.dbt, f"CREATE OR REPLACE TABLE {qr(f.build)} AS SELECT {kept}, CAST('zz-extra' AS varchar) AS extra "
                 f"FROM (VALUES {', '.join(f.row(i) for i in (7, 8))}) AS v({', '.join(qi(c) for c in f.cols)})")
    cat, ns, t = f.build.split(".")
    new = dict(T.ok(T.OPERATOR, f"SELECT column_name, data_type FROM {qi(cat)}.information_schema.columns WHERE table_schema = {sl(ns)} "
                                f"AND table_name = {sl(t)} ORDER BY ordinal_position"))
    before, after = lake.view_update(view, dict(f.types), new)
    steps = {"before": T.tq(tw.pub, before or "SELECT 1")[1]}
    between = tw.keys()
    steps["commit"] = None if tw.tag(T.main_id(f)) == 200 else "409"
    committed = tw.keys()
    steps["after"] = T.tq(tw.pub, after)[1]
    rows, err, st = T.tq(tw.reader, f"SELECT * FROM {qr(f.view)} ORDER BY 1")
    T.record("c1.schema-change", "view_update's two guarded statements around the tag commit: readers read the old tested rows, "
             "then the new, then the new columns, never an error",
             all(e is None for e in steps.values()) and between == [1, 2, 3, 4, 5] and committed == [7, 8] and err is None
             and st.get("columns") == list(contract) and all(r[-1] == "zz-extra" for r in rows),
             {"steps": {k: T.short(e) for k, e in steps.items()}, "between": between, "committed": committed,
              "after": st.get("columns") if err is None else T.short(err)})


def cost(tw: Twin) -> None:
    """INFO: Trino's elapsed time for one reader query, the view unguarded against guarded."""
    f = T.fixtures_by_name("station-readings")
    Twin("station-readings").rebuild()  # the control left this base's schema dropped
    pub, reader = model.publisher(f.p.name), "p.analyst-delayed"
    guarded = T.view_sql(f)
    plain = guarded.split(" WHERE EXISTS ")[0]
    times: dict[str, list[float]] = {"plain": [], "guarded": []}
    for _ in range(15):
        for kind, sql in (("plain", plain), ("guarded", guarded)):
            T.ok(pub, f"CREATE OR REPLACE VIEW {qr(f.view_build)} SECURITY DEFINER AS {sql}")
            T.ok(reader, f"SELECT count(*) FROM {qr(f.view)}")
            _, err, st = T.tq(reader, f"SELECT * FROM {qr(f.view)}")
            assert err is None, err
            times[kind].append(st["elapsedTimeMillis"])
    T.ok(pub, f"CREATE OR REPLACE VIEW {qr(f.view_build)} SECURITY DEFINER AS {guarded}")
    T.record("c1.guard-cost", f"for the record: Trino's elapsedTimeMillis for {reader}'s SELECT * of its view, n=15 each, "
             "the release's view put back after", None, {k: {"median": statistics.median(v), "max": max(v)} for k, v in times.items()})


# ---- H1, H2 --------------------------------------------------------------------------------------


def h1(tw: Twin) -> None:
    f = tw.f
    now = dt.datetime.now(dt.timezone.utc)
    at = {"old": now - dt.timedelta(hours=2), "fresh": now - dt.timedelta(minutes=1), "tomorrow": now + dt.timedelta(days=1)}
    kinds = {"tz": ("timestamp(6) with time zone", "TIMESTAMP '{:%Y-%m-%d %H:%M:%S.%f} UTC'"),
             "ntz": ("timestamp(6)", "TIMESTAMP '{:%Y-%m-%d %H:%M:%S.%f}'"),
             "date": ("date", "DATE '{:%Y-%m-%d}'"), "time": ("time(6)", "TIME '{:%H:%M:%S.%f}'")}
    values = ", ".join("(" + ", ".join([sl(k)] + [f"CAST({lit.format(t)} AS {ty})" for ty, lit in kinds.values()]) + ")" for k, t in at.items())
    T.ok(tw.pub, f"CREATE OR REPLACE VIEW {qr(f.view_build)} SECURITY DEFINER AS SELECT * FROM (VALUES {values}) "
                 f"AS v(k, {', '.join('c_' + c for c in kinds)})")
    seen: dict[str, dict[str, Any]] = {}
    try:
        for c in kinds:
            live_rules(lambda r, c=c: reader_rule(r, tw.group).update(filter=f"\"c_{c}\" < current_timestamp - INTERVAL '20' MINUTE"))
            seen[kinds[c][0]] = {z or "default": (lambda r: T.short(r[1]) if r[1] else [x[0] for x in r[0]])(q(tw.reader, f"SELECT k FROM {qr(f.view)} ORDER BY k", z))
                                 for z in ZONES}
    finally:
        live_rules(None)
        tw.rebuild()
    one = {ty: len({json.dumps(v) for v in res.values()}) == 1 and isinstance(next(iter(res.values())), list) for ty, res in seen.items()}
    T.record("h1.delay-zone", "a delay filter over each time type K2 takes, under four session time zones: only timestamp(6) with "
             "time zone gives every zone one answer (the check access.delay-type); time(6) fails every read",
             one == {"timestamp(6) with time zone": True, "timestamp(6)": False, "date": False, "time(6)": False}, seen)
    s = T.fixtures_by_name("station-readings")
    reader = "p.analyst-delayed"
    res = {z or "default": q(reader, f"SELECT {qi(s.key)} FROM {qr(s.view)} ORDER BY 1", z) for z in ZONES}
    got = {z: [r[0] for r in v[0]] if v[1] is None else T.short(v[1]) for z, v in res.items()}
    T.record("h1.delayed-reader-zones", f"{reader}, delayed on a timestamp(6) with time zone column, reads the same rows under every "
             "session time zone", len({json.dumps(v) for v in got.values()}) == 1 and all(isinstance(v, list) and v for v in got.values()), got)


def h2(tw: Twin) -> None:
    """A scoped reader's probes: 'zz-out' rows hold values 50 and 70; visible ones 1, 2 and 4."""
    f = tw.f

    def row(i: int, region: str, value: int) -> str:
        return (f"({sl(f'zz-p-{i}')}, CAST(current_timestamp - INTERVAL '2' HOUR AS timestamp(6) with time zone), "
                f"CAST({value} AS decimal(12,3)), {sl(region)}, 'n')")
    rows = [row(1, "north", 1), row(2, "south", 2), row(3, "north", 4), row(4, "zz-out", 50), row(5, "zz-out", 70)]
    probes = ("value > 4.5", "value > 69.5", "value > 70.5", "station_id = 'zz-p-4'", "station_id = 'zz-p-9'")
    out: dict[str, dict[str, Any]] = {}
    try:
        live_rules(lambda r: reader_rule(r, tw.group).update(filter="(\"region\" IN ('north', 'south'))"))
        for layout, props in (("unpartitioned", ""), ("partitioned by region", " WITH (partitioning = ARRAY['region'])")):
            tw.rebuild(rows, props)
            seen = T.ok(tw.reader, f"SELECT {qi(f.key)} FROM {qr(f.view)} ORDER BY 1")
            out[layout] = {"reader sees": [r[0] for r in seen]}
            for p in probes:
                rows_, err, st = T.tq(tw.reader, f"SELECT count(*) FROM {qr(f.view)} WHERE {p}")
                out[layout][p] = (rows_[0][0], st.get("processedRows")) if err is None else T.short(err)
    finally:
        live_rules(None)
        tw.rebuild()
    counts = {layout: {v[1] for p, v in res.items() if p != "reader sees"} for layout, res in out.items()}
    visible = all(res["reader sees"] == ["zz-p-1", "zz-p-2", "zz-p-3"] for res in out.values())
    T.record("h2.row-scope-partition", "a scoped reader's probes change the processed-row count with the base unpartitioned (a hidden "
             "value and key are found by search) and never with it partitioned by the scope column; the reader's rows are equal",
             visible and len(counts["unpartitioned"]) > 1 and len(counts["partitioned by region"]) == 1, out)


def main() -> int:
    m, _ = T.load()
    tested = Path("/work/tested.json")
    if tested.exists():
        T.TESTED.update({k: int(v) for k, v in json.loads(tested.read_text()).items()})
    tw = Twin()
    T.ok(tw.dbt, f"CREATE SCHEMA IF NOT EXISTS {qr(tw.f.build.rsplit('.', 1)[0])}")
    T.ok(tw.pub, f"CREATE SCHEMA IF NOT EXISTS {qr(tw.f.view_build.rsplit('.', 1)[0])}")
    c1(tw)
    h1(tw)
    h2(tw)
    return T.report()


if __name__ == "__main__":
    sys.exit(main())
