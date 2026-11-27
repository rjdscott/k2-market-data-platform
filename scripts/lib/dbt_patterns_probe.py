"""scripts/verify/dbt-patterns.sh's reads of the lake and its claims, in the throwaway lake's tools
container (/work the lake's state, /work/rp the run's files). Reads only.

    dbt_patterns_probe.py state <run>    each output's rows at `audited`, refs and snapshots, the
                                         builder's statements (Trino's query list), silver_trade's
                                         files and trade ids at main, to dp-<run>.json
    dbt_patterns_probe.py claims <run> ...   one numbered PASS or FAIL line per claim; exit 1 on a FAIL
"""
import base64
import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path

sys.path[:0] = ["/src/scripts", "/src/scripts/verify"]
import access_verify as av  # noqa: E402
import catalog_drift as cd  # noqa: E402
from k2gen.model import sql_literal  # noqa: E402

RP = Path("/work/rp")
PRODUCT, BUILDER = "reference-data.pattern-lab", "dbt_pattern_lab"
BASE = "reference_data_build.pattern_lab__base"
HIST, SILVER, HELD = "instrument_history", "silver_trade", "silver_trade_unresolved"
COLS = {HIST: "symbol, cast(valid_from as varchar), cast(valid_to as varchar), cast(tick_size as varchar), cast(recorded_at as varchar)",
        SILVER: "trade_id, symbol, instrument_id, cast(price as varchar), cast(quantity as varchar)",
        HELD: "trade_id, symbol, cast(price as varchar), cast(quantity as varchar)"}
RECORDS = "governance_build.run_records.run_record"
OPEN, FIRST = "9999-12-31 23:59:59.000000 UTC", "1970-01-01 00:00:00.000000 UTC"
trino, env = av.connect(Path("/work/env.json"))
LK = f"{env['lakekeeper']}/catalog"
cmd, args = sys.argv[1], sys.argv[2:]


def statement(sql):
    """(dbt's node id from its query comment, the statement without its leading comments)."""
    node = re.search(r'"node_id": "([^"]+)"', sql)
    body = re.sub(r"^(\s*(/\*.*?\*/|--[^\n]*\n))*", "", sql, flags=re.S)
    return node and node[1], " ".join(body.split())[:160]


def queries(user):
    """Trino's query list as `user` (GET /v1/query): it lists a user only its own queries."""
    pw = trino.password(user)
    req = urllib.request.Request(f"{env['trino']}/v1/query", headers={
        "Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode(), "X-Trino-User": user})
    with urllib.request.urlopen(req, context=ssl.create_default_context(cafile=env["ca"]), timeout=30) as r:
        return [{"id": q["queryId"], "tags": (q.get("session") or {}).get("clientTags") or [], "state": q.get("state"),
                 "node": statement(str(q.get("query", "")))[0], "sql": statement(str(q.get("query", "")))[1],
                 "created": (q.get("queryStats") or {}).get("createTime")} for q in json.load(r)]


if cmd == "state":
    run, s = args[0], {}
    for t in (HIST, SILVER, HELD):
        meta = cd.Catalog(LK, "reference_data").table("pattern_lab__base", t) or {}
        refs = {n: r["snapshot-id"] for n, r in (meta.get("refs") or {}).items()}
        snaps = [{"id": x["snapshot-id"], "query": (x.get("summary") or {}).get("trino_query_id"),
                  "op": (x.get("summary") or {}).get("operation"), "records": (x.get("summary") or {}).get("total-records"),
                  "parent": x.get("parent-snapshot-id")} for x in meta.get("snapshots") or []]
        a = trino.run(BUILDER, f"SELECT {COLS[t]} FROM {BASE}.{t} FOR VERSION AS OF 'audited' ORDER BY 1, 2")
        s[t] = {"refs": refs, "snapshots": snaps, "rows": None if a.error else a.rows, "error": a.error and a.says()}
    # claim 12: silver_trade's files at main (content 0 data, else delete), and its trade ids there
    f = trino.run(BUILDER, f'SELECT count_if(content <> 0), count_if(content = 0) FROM {BASE}."{SILVER}$files"')
    s[SILVER]["files"] = None if f.error else {"deletes": f.rows[0][0], "data": f.rows[0][1]}
    m = trino.run(BUILDER, f"SELECT trade_id FROM {BASE}.{SILVER} ORDER BY 1")
    s[SILVER]["main_ids"] = None if m.error else [r[0] for r in m.rows]
    s["queries"] = queries(BUILDER)
    s["tests"] = trino.ok(BUILDER, "SELECT test_name, tested, status, failures, dbt_invocation_id FROM "
                                   f"{BASE}.dq_test_results WHERE run_id = {sql_literal('pattern_lab/' + run)} ORDER BY 1")
    s["records"] = trino.ok("svc.runs", f"SELECT event, table_name, snapshot_before, snapshot_after, detail, dbt_invocation_id "
                                        f"FROM {RECORDS} WHERE run_id = {sql_literal('pattern_lab/' + run)} ORDER BY recorded_at")
    (RP / f"dp-{run}.json").write_text(json.dumps(s, indent=1, default=str))
    sys.exit(0)

# ---- claims <run> ...: the builds in order, each with its state, XCom, exit and dbt log --------
runs, later = args[:3], args[3:]  # claims 1 to 11 read the first three builds, claim 12 the four on R3
st = {r: json.loads((RP / f"dp-{r}.json").read_text()) for r in args if (RP / f"dp-{r}.json").exists()}
n, failed = 0, 0


def text(run, ext):
    f = RP / f"{run}.build.{ext}"
    return f.read_text(errors="replace") if f.exists() else ""


def xcom(run):
    lines = [x for x in text(run, "out").splitlines() if x.strip()]
    try:
        return json.loads(lines[-1])
    except (ValueError, IndexError):
        return {}


def say(ok, title, detail):
    global n, failed
    n, failed = n + 1, failed + (not ok)
    print(f"{'PASS' if ok else 'FAIL'} {n} {title}: {detail}", flush=True)
    return ok


def rows(run, t):
    return [tuple(r) for r in (st.get(run, {}).get(t, {}).get("rows") or [])]


def published(run):
    """The wrapper exited 0, its XCom says 0, and each output's audited tag is its main."""
    if run not in st:
        return False, f"{run}: no state (the build or the probe failed)"
    if text(run, "rc").strip() != "0" or xcom(run).get("exit") != 0:
        k2 = [x for x in text(run, "err").splitlines() if x.startswith("k2-dbt:")]
        return False, f"{run} exit {text(run, 'rc').strip()}, XCom {xcom(run)}: {(k2 or ['(no k2-dbt line)'])[-1][:240]}"
    bad = [t for t in (HIST, SILVER, HELD) if st[run][t]["refs"].get("main") is None
           or st[run][t]["refs"].get("audited") != st[run][t]["refs"].get("main")]
    return not bad, f"{run}: {', '.join(bad)} not audited on main" if bad else f"{run} exit 0, every output audited on main"


def build_tag(run):
    """The build's client tag: the one on the statement that committed instrument_history's main,
    a table every build replaces."""
    s = st[run]
    main = next((x for x in s[HIST]["snapshots"] if x["id"] == s[HIST]["refs"].get("main")), {})
    q = next((q for q in s["queries"] if q["id"] == main.get("query")), {})
    return next((g for g in q.get("tags", []) if g.startswith("k2-dbt-")), None)


def statements(run, t):
    """(main's snapshot, whether main's statement carries the build's tag, the build's statements of
    the model by dbt's node id, those of them that committed a snapshot of the table)."""
    s, tag = st[run], build_tag(run)
    main = next((x for x in s[t]["snapshots"] if x["id"] == s[t]["refs"].get("main")), {})
    by_id = {q["id"]: q for q in s["queries"]}
    # dbt's comment names the node on most statements; a contract table's INSERT carries none, so
    # an uncommented statement is the model's when it names the table (or dbt's __dbt_tmp of it)
    mine = sorted((q for q in s["queries"] if tag in q["tags"] and (q["node"] == f"model.k2.pattern_lab__{t}" or (
        q["node"] is None and re.search(rf'"{t}(__dbt_tmp)?"', q["sql"])))), key=lambda q: q["created"])
    committed = {x["query"] for x in s[t]["snapshots"]}
    return main, tag is not None and tag in by_id.get(main.get("query"), {}).get("tags", []), mine, \
        [q for q in mine if q["id"] in committed]


def verb(q):
    return re.sub(r"[(\"].*", "", " ".join(q["sql"].lower().split()[:5])).strip()


def described(mine, committing):
    return (f"{len(mine)} statement(s) [{'; '.join(verb(q) for q in mine)}], snapshots by "
            f"[{'; '.join(verb(q) for q in committing) or 'none'}]")


# (a) type-2 from a listing
r1 = sorted(rows(runs[0], HIST))
want1 = [("ALPHA-USD", FIRST, OPEN, "0.01000000", "2026-01-05 10:00:00.000000 UTC"),
         ("BRAVO-USD", FIRST, "2026-01-05 11:00:00.000000 UTC", "0.50000000", "2026-01-05 10:00:00.000000 UTC"),
         ("BRAVO-USD", "2026-01-05 11:00:00.000000 UTC", OPEN, "0.25000000", "2026-01-05 11:00:00.000000 UTC")]
ok, why = published(runs[0])
say(ok and r1 == want1, "(a) the listing's first two fetches build as type-2 and publish",
    why if not ok else (f"rows {r1}" if r1 != want1 else "3 versions: each key's first opens at 1970-01-01, "
                        "the open row ends 9999-12-31 23:59:59, an unchanged fetch adds none"))


def abut(rs):
    bad, keys = [], sorted({r[0] for r in rs})
    for k in keys:
        v = sorted((r for r in rs if r[0] == k), key=lambda r: r[1])
        bad += [f"{k}: {a[2]} then {b[1]}" for a, b in zip(v, v[1:]) if a[2] != b[1]]
        if [r[2] for r in v].count(OPEN) != 1 or v[0][1] != FIRST or v[-1][2] != OPEN:
            bad.append(f"{k}: not one open row last, first at 1970")
    return bad


tests = {r: [t for t in st.get(r, {}).get("tests", []) if t[1].endswith(f".{HIST}")] for r in runs}
# dq_test_results names an ODCS sql rule by a slug of its query, never by its name, so the five
# rules of the contract (no overlap, no gap, one open row, first at 1970, not empty) are counted.
bad = [f"{r}: {t[0]} {t[2]}" for r in runs for t in tests[r] if t[2] != "pass"]
bad += [f"{r}: {len([t for t in tests[r] if t[0].startswith('k2_quality')])} k2_quality results, not 5"
        for r in runs if len([t for t in tests[r] if t[0].startswith("k2_quality")]) != 5]
bad += [f"{r}: {b}" for r in runs for b in abut(rows(r, HIST))]
say(not bad, "(a) dbt's data tests (no overlap, no gap, one open row per key) ran and passed in every build, "
    "and the published rows abut half-open", bad[0] if bad else f"{sum(len(t) for t in tests.values())} test results, "
    "every interval ends where its key's next begins")

r2 = set(rows(runs[1], HIST))
added, gone = sorted(r2 - set(r1)), sorted(set(r1) - r2)
want_add = [("ALPHA-USD", FIRST, "2026-01-05 12:00:00.000000 UTC", "0.01000000", "2026-01-05 10:00:00.000000 UTC"),
            ("ALPHA-USD", "2026-01-05 12:00:00.000000 UTC", OPEN, "0.05000000", "2026-01-05 12:00:00.000000 UTC")]
ok, why = published(runs[1])
say(ok and added == want_add and gone == [want1[0]], "(a) a new listing version adds exactly one closed row and one open row",
    why if not ok else f"added {added}, gone {gone}")

ok, why = published(runs[2])
m2, m3 = st.get(runs[1], {}).get(HIST, {}).get("refs", {}).get("main"), st.get(runs[2], {}).get(HIST, {}).get("refs", {}).get("main")
say(ok and sorted(rows(runs[2], HIST)) == sorted(r2) and m3 != m2, "(a) a rebuild on the same listing gives the same rows",
    why if not ok else f"main {m2} to {m3}, {len(r2)} rows, equal: {sorted(rows(runs[2], HIST)) == sorted(r2)}")

bad = []
for r in runs:
    if r in st:
        main, tagged, mine, committing = statements(r, HIST)
        if not tagged or len(committing) != 2 or verb(next((q for q in mine if q["id"] == main.get("query")), {"sql": "?"})) != "insert into":
            bad.append(f"{r}: main's query {main.get('query')} tagged {tagged}, {len(committing)} committing statement(s)")
        print(f"INFO {r} {HIST} (table): {described(mine, committing)}")
say(not bad, "(a) the publish guard passes the two-commit table build: main is the insert's commit, its statement under the build's client tag",
    bad[0] if bad else "every build's main committed by a tagged statement of that build")

# (b) the held table and the silver
s1, h1 = rows(runs[0], SILVER), rows(runs[0], HELD)
ok, why = published(runs[0])
say(ok and [r[0] for r in s1] == [1, 2, 4] and [r[0] for r in h1] == [3, 5], "(b) a trade whose symbol resolves to nothing is held, not in silver",
    why if not ok else f"silver {[r[0] for r in s1]}, held {[r[0] for r in h1]}")
s2, h2 = rows(runs[1], SILVER), rows(runs[1], HELD)
moved = [r for r in s2 if r[0] in (3, 5)]
say([r[0] for r in s2] == [1, 2, 3, 4, 5, 6] and h2 == [] and all(r[2] == 3 for r in moved),
    "(b) once the reference has the symbol, the next build moves the held trades into silver exactly once",
    f"silver {[r[0] for r in s2]}, held {[r[0] for r in h2]}, moved {moved}")
s3, h3 = rows(runs[2], SILVER), rows(runs[2], HELD)
say(s3 == s2 and h3 == [], "(b) a further build adds no row twice", f"silver {[r[0] for r in s3]}, held {[r[0] for r in h3]}")
bad = []
for r in runs:
    if r in st:
        for t, kind in ((SILVER, "incremental merge"), (HELD, "table")):
            main, tagged, mine, committing = statements(r, t)
            moved_main = r == runs[0] or main.get("id") != st[runs[runs.index(r) - 1]][t]["refs"].get("main")
            if moved_main and not tagged:
                bad.append(f"{r} {t}: main's query {main.get('query')} carries no client tag of the build")
            print(f"INFO {r} {t} ({kind}): main {'moved to ' + verb(next((q for q in mine if q['id'] == main.get('query')), {'sql': '?'})) if moved_main else 'unmoved'}; "
                  f"{described(mine, committing)}")
ok = all(published(r)[0] for r in runs)
say(ok and not bad, "(b) both outputs publish through the wrapper in each build", bad[0] if bad else
    ("every moved main committed by a tagged statement of its build" if ok else next(published(r)[1] for r in runs if not published(r)[0])))

# (c) the warn test
w = [t for t in st.get(runs[0], {}).get("tests", []) if t[0].startswith("k2_quality") and t[1].endswith(f".{HELD}")]
ok, why = published(runs[0])
say(ok and len(w) == 1 and w[0][2] == "warn" and w[0][3] == 1 and len(h1) == 2,
    "(c) the warn test 'held rows = 0' fails and the build still publishes the held rows",
    why if not ok else f"dq_test_results {w}, held rows at audited {len(h1)}")
rec = st.get(runs[0], {}).get("records", [])
print(f"INFO {runs[0]} XCom {xcom(runs[0])}; run records {[(e, t.split('.')[-1], d) for e, t, _, _, d, _ in rec]}; "
      f"dbt_invocation_id {sorted({x[5] for x in rec if x[5]})} joins dq_test_results {[(t[0][:40], t[2], t[4]) for t in w]}")
x0 = xcom(runs[0])
details = [x[4] for x in rec if x[0] == "audited"]
named = x0.get("degraded") is True and bool(w) and any(w[0][0] in n for n in x0.get("warned") or [])
rows_say = len(details) == 3 and all(d == json.dumps({"degraded": True, "warned": x0.get("warned")}, separators=(",", ":"))
                                     for d in details)
quiet = [r for r in runs[1:] if "degraded" in xcom(r)]  # the held rows resolved: no warn, no degraded
say(named and rows_say and not quiet, "(c) the wrapper's XCom and each output's audited run record say the build is degraded, "
    "naming the warn test; a later build with no warn does not",
    f"XCom {json.dumps(x0)}; audited details {details}; degraded later: {quiet}")

done = (re.findall(r"Done\. PASS=.*", text(runs[0], "err")) or ["none"])[-1]
own = [x for x in text(runs[0], "err").splitlines() if x.startswith("k2-dbt:")]
print(f"INFO {runs[0]} dbt's own summary on stderr: {done}; the wrapper's own stderr lines: {own}")
for r in runs:  # build times: dbt's own per-node lines
    times = re.findall(r"OK (?:created|loaded) \S+ (?:\S+ )?(?:model|seed file) (\S+) .*?in ([0-9.]+)s\]", text(r, "err"))
    print(f"INFO {r} dbt node times (s): {', '.join(f'{a.split(chr(46))[-1]} {b}' for a, b in times)}")


# 12 (W0-9): compaction after the tests, inside the wrapper; dp-4 to dp-7 on R3, whose merge updates every row
def silver(run, key):
    return st.get(run, {}).get(SILVER, {}).get(key) or {}


def snapshot(run, sid):
    return next((x for x in silver(run, "snapshots") if x["id"] == sid), {})


def records(run):
    return st.get(run, {}).get("records", [])


bad = []
if len(later) != 4 or any(r not in st for r in later):
    bad.append(f"no state for every one of {later}")
else:
    d4, d5, d6, d7 = later
    ok, why = published(d4)
    deletes4 = (silver(d4, "files") or {}).get("deletes")
    if not ok or not deletes4 or "compacted" in xcom(d4):
        bad.append(f"{d4}: {why}; delete files {deletes4}, XCom {xcom(d4)} (merges leave deletes, under the threshold no compaction)")
    ok, why = published(d5)
    main5 = silver(d5, "refs").get("main")
    s5 = snapshot(d5, main5)
    want = [c for c in xcom(d5).get("compacted", []) if c.get("table", "").endswith(f".{SILVER}")]
    if not ok or (silver(d5, "files") or {}).get("deletes") != 0 or s5.get("op") != "replace" \
            or snapshot(d5, s5.get("parent")).get("op") in (None, "replace") or len(want) != 1 \
            or want[0].get("deleteFiles", [0, 1])[1] != 0 or not want[0]["deleteFiles"][0]:
        bad.append(f"{d5}: {why}; files {silver(d5, 'files')}, main {s5}, its parent {snapshot(d5, s5.get('parent'))}, XCom {xcom(d5)}")
    if sorted(rows(d5, SILVER)) != sorted(rows(d4, SILVER)) or not rows(d5, SILVER):
        bad.append(f"{d5}: rows at audited {rows(d5, SILVER)}, {d4}'s {rows(d4, SILVER)}")
    ok, why = published(d6)
    started = [x for x in records(d6) if x[0] == "started" and x[1].endswith(f".{SILVER}")]
    touched = [x for x in text(d6, "err").splitlines() if x.startswith("k2-dbt: reconcile")]
    if not ok or [x[2] for x in started] != [main5] or any(x[0] == "reconciled" for x in records(d6)) or touched:
        bad.append(f"{d6}: {why}; started {started} (want snapshot_before {main5}), reconcile lines {touched}")
    refused = [x for x in text(d7, "err").splitlines() if "a write after its tests" in x]
    if text(d7, "rc").strip() != "3" or xcom(d7).get("exit") != 3 or not refused \
            or silver(d7, "refs").get("audited") != silver(d6, "refs").get("audited") \
            or any(x[0] == "audited" for x in records(d7)) or 1001 in (silver(d7, "main_ids") or [1001]):
        bad.append(f"{d7}: exit {text(d7, 'rc').strip()}, XCom {xcom(d7)}, refusal {refused[-1:]}, audited "
                   f"{silver(d7, 'refs').get('audited')} (was {silver(d6, 'refs').get('audited')}), main ids {silver(d7, 'main_ids')}")
say(not bad, "(W0-9) a build compacts the merged incremental output after its tests: delete files to 0, rows unchanged, audited "
    "on the replace of the tested snapshot, the next reconcile changes nothing, a tagged write after the optimize refused",
    bad[0] if bad else f"{d5} compacted {want[0]}; {d7} refused and rolled back")
sys.exit(1 if failed else 0)
