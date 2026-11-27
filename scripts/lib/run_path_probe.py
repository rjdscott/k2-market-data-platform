"""The run-path scripts' reads of the lake and their claims (scripts/lib/run-path-lib.sh), in the
throwaway lake's tools container: /work is the lake's state, /work/rp the run's files.

    run_path_probe.py setup <release id>            the namespaces and the run-record table
    run_path_probe.py seed <release id> <fixture> <run>   a spool entry, as fetch.py leaves one
    run_path_probe.py state <label>                 refs, snapshots and counts, to state-<label>.json
    run_path_probe.py poll <label>                  a reader on gold every 50 ms until stop-<label>
    run_path_probe.py wait-started <run id>         until the wrapper's started row of the run is in
    run_path_probe.py wait-trino                    until Trino answers again
    run_path_probe.py claim <name> [args]           one PASS or FAIL line; exit 1 on a FAIL

`seed` writes the spool; nothing else here writes the lake."""
import hashlib
import json
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path[:0] = ["/src/scripts", "/src/scripts/verify"]
import access_verify as av  # noqa: E402
import catalog_drift as cd  # noqa: E402
from k2gen import model  # noqa: E402
from k2gen.model import sql_literal  # noqa: E402

RP, SPOOL = Path("/work/rp"), Path("/work/spool")
ECB, CAL = "fx-reference.ecb-fx-rates", "reference-data.calendars"
BRONZE = "lake.ecb_fx_rates.bronze_daily"
SILVER = "fx_reference_build.ecb_fx_rates__base.silver_rates"
GOLD = "fx_reference_build.ecb_fx_rates__base.gold_eur_rates_daily"
READER_GOLD = "fx_reference.ecb_fx_rates.gold_eur_rates_daily"  # the reader view
BUILDER, READER = "dbt_ecb_fx_rates", "p.analyst-a"
RECORDS = "governance_build.run_records.run_record"
TABLES = {"bronze": ("k2", "ecb_fx_rates", "bronze_daily"), "landing_log": ("k2", "ecb_fx_rates", "landing_files"),
          "silver": ("fx_reference", "ecb_fx_rates__base", "silver_rates"),
          "gold": ("fx_reference", "ecb_fx_rates__base", "gold_eur_rates_daily"),
          "calendars": ("reference_data", "calendars__base", "target_days")}
trino, env = av.connect(Path("/work/env.json"))
LK = f"{env['lakekeeper']}/catalog"
cmd, args = sys.argv[1], sys.argv[2:]


def release(rid):
    return Path("/work/releases") / rid


def pipeline(rid, product=ECB):
    return json.loads((release(rid) / model.pipeline_path(product)).read_text())


def count(user, sql):
    a = trino.run(user, sql)
    return None if a.error else a.rows[0][0]


if cmd == "setup":  # what deploy makes before a product's first run, as dbt-wrapper.sh's setup: namespaces, the run-record table
    r = release(args[0])
    for w in json.loads((r / model.STORAGE_PLAN).read_text())["warehouses"]:
        base = cd.Catalog(LK, w["name"]).base
        for ns in w["namespaces"]:
            st, _ = cd.call("POST", f"{base}/namespaces", {"namespace": [ns]})
            assert st in (200, 409), f"namespace {w['name']}.{ns}: HTTP {st}"
    for ddl in sorted((r / "compiled/lake/ddl/trino").glob("*.sql")):
        trino.ok("svc.platform", "\n".join(x for x in ddl.read_text().splitlines() if not x.startswith("--")).strip().rstrip(";"))
    print("run-path: namespaces and the run-record table made")
elif cmd == "seed":  # seed <release id> <fixture> <run>: the entry fetch.py leaves after its download, then its own parse
    sys.path.insert(0, str(RP))
    import fetch
    rid, fixture, run = args
    p = pipeline(rid)
    c, dag = p["connector"], SPOOL / p["dagId"]
    data = (RP / fixture).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    unchanged = dag.is_dir() and fetch.newest_sha256(dag) == sha  # fetch itself would exit 10 here
    entry, raw = dag / run, f"raw.{c['format']}"
    entry.mkdir(parents=True)
    (entry / raw).write_bytes(data)
    at = datetime.now(fetch.UTC)
    url = c["url"].replace("{{ds}}", at.date().isoformat())
    record = {"url": url, "finalUrl": url, "sha256": sha, "bytes": len(data),
              "fetchedAt": at.isoformat(timespec="microseconds"), "httpStatus": 200, "address": None}  # no address: nothing was dialled
    (entry / "fetch.json").write_text(json.dumps(record, indent=1) + "\n")
    audits = list(dict.fromkeys(a for b in p.get("bronze", []) for a in b["audits"]))  # as fetch.main() lists them
    code = fetch.parse(release(rid) / fetch.PARSER.format(domain=p["domain"], parser=c["parser"]), entry, raw, audits)
    (RP / f"{run}.seed.json").write_text(json.dumps({"exit": code, "sha256": sha, "unchanged": unchanged}))
elif cmd == "state":  # state <label>: refs and snapshots by the catalog, row counts by Trino
    s = {}
    for k, (wh, ns, t) in TABLES.items():
        meta = cd.Catalog(LK, wh).table(ns, t) or {}
        refs = {n: r["snapshot-id"] for n, r in (meta.get("refs") or {}).items()}
        main = next((x for x in meta.get("snapshots") or [] if x["snapshot-id"] == refs.get("main")), {})
        s[k] = {"refs": refs, "snapshots": sorted(x["snapshot-id"] for x in meta.get("snapshots") or []),
                "records": int(main.get("summary", {}).get("total-records", 0))}  # main's rows, by its snapshot summary
    s |= {"bronze_rows": count(BUILDER, f"SELECT count(*) FROM {BRONZE}"),
          "bronze_keys": count(BUILDER, f"SELECT count(*) FROM (SELECT DISTINCT rate_date, currency FROM {BRONZE})"),
          "silver_rows": count(BUILDER, f"SELECT count(*) FROM {SILVER}"),
          "gold_rows": count(BUILDER, f"SELECT count(*) FROM {GOLD}")}
    (RP / f"state-{args[0]}.json").write_text(json.dumps(s, indent=1))
elif cmd == "poll":  # poll <label>: p.analyst-a on gold's reader view every 50 ms until stop-<label>; the first answer is the baseline
    stop, polls, errors, bad, base = RP / f"stop-{args[0]}", 0, [], [], None
    while not stop.exists():
        a = trino.run(READER, f"SELECT CAST(rate_date AS varchar), currency, CAST(rate AS varchar) FROM {READER_GOLD} ORDER BY 1, 2")
        polls += 1
        if a.error:
            errors.append(a.says())
        elif base is None:
            base = a.rows
        elif a.rows != base:
            bad.append([r for r in a.rows if r not in base][:3] or f"{len(a.rows)} rows, not {len(base)}")
        time.sleep(0.05)
    (RP / f"poll-{args[0]}.json").write_text(json.dumps({"polls": polls, "base": base, "n_errors": len(errors), "errors": errors[:3],
                                                         "n_bad": len(bad), "bad": bad[:3]}))
elif cmd == "wait-started":  # wait-started <run id>: the wrapper's started row of that run, 300 s at most
    t0 = time.monotonic()
    while not trino.ok("svc.runs", f"SELECT 1 FROM {RECORDS} WHERE run_id = {sql_literal(args[0])} AND event = 'started' LIMIT 1"):
        if time.monotonic() - t0 > 300:
            sys.exit(f"no started row of {args[0]} in 300 s")
        time.sleep(0.2)
elif cmd == "wait-trino":  # wait-trino: Trino answers a query again, 300 s at most
    t0 = time.monotonic()
    while True:
        try:
            if not trino.run("svc.platform", "SELECT 1").error:
                break
        except OSError:  # refused while it starts
            pass
        if time.monotonic() - t0 > 300:
            sys.exit("Trino did not answer in 300 s")
        time.sleep(2)
elif cmd == "claim":  # claim <name> [release ids]: one PASS or FAIL line; exit 1 on a FAIL
    name = args[0]

    def text(label, ext="out"):
        f = RP / f"{label}.{ext}"
        return f.read_text(errors="replace") if f.exists() else ""

    def rc(label):
        t = text(label, "rc").strip()
        return int(t) if t else None

    def result(label):  # the last stdout line: a Spark task's result (factory's XCom `result`), the wrapper's XCom
        lines = [x for x in text(label).splitlines() if x.strip()]
        try:
            return json.loads(lines[-1])
        except (ValueError, IndexError):
            return {}

    def why(label):  # a task's first failure line (the connector's, then dbt's, then the wrapper's), else its last line
        lines = [x.strip() for x in (text(label) + text(label, "err")).splitlines() if x.strip()]
        for test in (lambda x: x.startswith(("FAIL", "load refused", "audit refused", "publish refused", "expire FAILED",
                                             "maintain FAILED", "Traceback")),
                     lambda x: any(m in x for m in ("Failure in", "Error in", "Compilation Error", "Exception:", "Error:")),
                     lambda x: x.startswith("k2-dbt:")):
            if hit := next((x for x in lines if test(x)), None):
                return hit[:300]
        return (lines or ["(no output)"])[-1][:300]

    def state(label):
        return json.loads((RP / f"state-{label}.json").read_text())

    def facts(label):
        return json.loads([x for x in text(f"facts-{label}").splitlines() if x.strip()][-1])

    def objects(label):  # mc ls --json of k2-landing
        return sorted(json.loads(x)["key"] for x in text(f"objects-{label}", "json").splitlines() if x.strip())

    def seed(run):
        return json.loads((RP / f"{run}.seed.json").read_text())

    def rows(run):  # rows.jsonl as fetch's parse wrote it
        f = SPOOL / "ecb_fx_rates" / run / "rows.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else None

    def records(run):
        got = trino.ok("svc.runs", f"SELECT task, event, table_name, release_id, inputs FROM {RECORDS} "
                                   f"WHERE run_id = {sql_literal(run)} ORDER BY recorded_at")
        return [dict(zip(("task", "event", "table", "release", "inputs"), r)) for r in got]

    def history(product):  # every run record of the product, in the order written
        got = trino.ok("svc.runs", f"SELECT run_id, task, event, table_name, release_id, snapshot_before, snapshot_after, detail "
                                   f"FROM {RECORDS} WHERE product_id = {sql_literal(product)} ORDER BY recorded_at")
        return [dict(zip(("run", "task", "event", "table", "release", "before", "after", "detail"), r)) for r in got]

    def task(run, t, want=0):
        return rc(f"{run}.{t}") == want, f"{run} {t} exit {rc(f'{run}.{t}')} (want {want}): {why(f'{run}.{t}')}"

    def verdict(title, checks, good):
        bad = [d for ok, d in checks if not ok]
        print(f"{'FAIL' if bad else 'PASS'} {title}: {bad[0] if bad else good}", flush=True)
        sys.exit(1 if bad else 0)

    def tagged(s, k):  # audited on main, and main exists
        r = s[k]["refs"]
        return r.get("main") is not None and r.get("audited") == r.get("main")

    lake = release(args[1]) / "platform/docker/lake" if len(args) > 1 else None
    if name == "cal":
        s = state("cal")
        verdict("calendars builds through the wrapper and its audited tag moves",
                [task("rp-cal", "build"), (tagged(s, "calendars"), f"calendars' refs {s['calendars']['refs']}")],
                f"exit 0, audited = main = {s['calendars']['refs'].get('main')}")
    elif name == "good":
        sys.path.insert(0, str(lake))
        import connector  # the release's own: its check names and landing key, pure functions
        p = pipeline(args[1])
        [bronze] = p["bronze"]
        sd, got, fx = seed("rp-good"), rows("rp-good"), (RP / "good.xml").read_bytes()
        record = json.loads((SPOOL / "ecb_fx_rates/rp-good/fetch.json").read_text())
        audits = SPOOL / "ecb_fx_rates/rp-good/audits.json"
        named = [a["name"] for a in json.loads(audits.read_text())["audits"]] if audits.exists() else []
        sha, n = hashlib.sha256(fx).hexdigest(), fx.count(b"currency=")
        key = connector.object_key(p, record, sha)
        load, pub, s, f = result("rp-good.load"), result("rp-good.publish"), state("good"), facts("good")
        landed = [r for r in f["landing"] if r[2] == "ecb_fx_rates/rp-good"]
        checked = {r[1]: r[2] for r in f["checks"] if r[0] == "ecb_fx_rates/rp-good"}
        want = connector.check_names(bronze)
        verdict("ECB's good file lands, passes every check and is published to bronze main", [
            (sd["exit"] == 0 and record["sha256"] == sha == sd["sha256"] and record["bytes"] == len(fx) and got is not None
             and len(got) == n and named == bronze["audits"],
             f"the spool: parse exit {sd['exit']}, fetch.json sha256 {record['sha256'][:12]} of {sha[:12]}, "
             f"{None if got is None else len(got)} rows of {n}, audits.json {named}"),
            task("rp-good", "load"),
            (load.get("objectKey") == f"s3://{connector.LANDING_BUCKET}/{key}" and load.get("rows") == n,
             f"load's result {load}, want s3://{connector.LANDING_BUCKET}/{key} and {n} rows"),
            (objects("good") == [key], f"k2-landing holds {objects('good')}, want [{key}]"),
            (len(landed) == 1 and landed[0][1] == sha, f"landing_files rows of the batch: {landed}"),
            task("rp-good", "audit"),
            (sorted(checked) == sorted(want) and all(checked.values()), f"audit_checks of the batch {checked}, declared {want}"),
            task("rp-good", "publish"),
            (s["bronze"]["refs"].get("main") == load.get("snapshotAfter") == pub.get("snapshotAfter") is not None
             and pub.get("snapshotBefore") != pub.get("snapshotAfter") and s["bronze_rows"] == n,
             f"bronze refs {s['bronze']['refs']}, {s['bronze_rows']} rows on main of {n}; publish {pub}")],
            f"{key}, 1 landing_files row, {n} rows on audit, {len(checked)} checks passed, main {pub.get('snapshotAfter')} holds {n} rows")
    elif name == "build":
        s, xcom = state("built"), result("rp-good.build")
        started = [r for r in records("ecb_fx_rates/rp-good") if r["event"] == "started"]
        pins = {i["contractId"]: i["snapshot"] for r in started for i in json.loads(r["inputs"] or "[]")}
        dq = trino.ok(BUILDER, "SELECT status, count(*) FROM fx_reference_build.ecb_fx_rates__base.dq_test_results "
                               "WHERE run_id = 'ecb_fx_rates/rp-good' GROUP BY status")
        read = trino.run(READER, f"SELECT CAST(rate_date AS varchar), currency, rate FROM {READER_GOLD}")
        published = {(r["rate_date"], r["currency"]): r["rate"] for r in rows("rp-good")}
        pairs = [(r[2], published[(r[0], r[1])]) for r in read.rows if (r[0], r[1]) in published]
        cal = s["calendars"]["refs"].get("audited")
        verdict("the wrapper builds ECB on calendars' tested snapshot; a reader reads gold, exact decimals", [
            task("rp-good", "build"),
            (xcom.get("exit") == 0, f"the XCom line {xcom}"),
            (len(started) == 2 and cal is not None and pins.get("reference-data.calendars.target_days") == cal,
             f"{len(started)} started rows, pinned {pins}, calendars' audited {cal}"),
            (tagged(s, "silver") and tagged(s, "gold"), f"silver {s['silver']['refs']}, gold {s['gold']['refs']}"),
            (bool(dq) and {x for x, _ in dq} == {"pass"}, f"dq_test_results of the run {dq}"),
            (not read.error and len(read.rows) > 0, f"{READER} on {READER_GOLD}: {read.says()}"),
            (s["silver_rows"] == s["bronze_keys"] and bool(s["silver_rows"]),
             f"silver {s['silver_rows']} rows, bronze {s['bronze_keys']} distinct (rate_date, currency)"),
            (dict(read.columns).get("rate", "").replace(" ", "") == "decimal(20,10)" and bool(pairs)
             and all(g == f"{Decimal(w):.10f}" for g, w in pairs),
             f"gold rate {dict(read.columns).get('rate')}; {sum(g != f'{Decimal(w):.10f}' for g, w in pairs)} of {len(pairs)} "
             f"published rates differ: {[(g, w) for g, w in pairs if g != f'{Decimal(w):.10f}'][:3]}")],
            f"pinned {cal}; audited = main on silver and gold; {sum(n for _, n in dq)} tests passed; {READER} reads "
            f"{len(read.rows)} gold rows; silver {s['silver_rows']} = bronze's distinct keys; {len(pairs)} rates exact, "
            f"e.g. {pairs[0] if pairs else None}")
    elif name == "again":
        g, b, a, sd = state("good"), state("built"), state("again"), seed("rp-again")
        load = result("rp-again.load")
        print(f"INFO fetch itself would exit 10 (unchanged) on these bytes: {sd['unchanged']}; the connector is proven on them anyway")
        print(f"INFO the no-op build's snapshots: silver {b['silver']['refs'].get('main')} -> {a['silver']['refs'].get('main')}, "
              f"gold {b['gold']['refs'].get('main')} -> {a['gold']['refs'].get('main')}")
        verdict("the same bytes again land nothing new, stage no row, and the build passes unchanged", [
            (sd["exit"] == 0, f"parse exit {sd['exit']}"),
            task("rp-again", "load"),
            ("already landed" in text("rp-again.load") and load.get("rows") == 0, f"load's result {load}"),
            (objects("again") == objects("good"), f"k2-landing {objects('again')}, was {objects('good')}"),
            (a["landing_log"]["refs"] == g["landing_log"]["refs"], f"landing_files main {g['landing_log']['refs']} -> {a['landing_log']['refs']}"),
            (a["bronze"]["refs"] == g["bronze"]["refs"] and a["bronze_rows"] == g["bronze_rows"],
             f"bronze refs {g['bronze']['refs']} -> {a['bronze']['refs']}, rows {g['bronze_rows']} -> {a['bronze_rows']}"),
            task("rp-again", "audit"), task("rp-again", "publish"), task("rp-again", "build"),
            (a["silver_rows"] == b["silver_rows"] and a["gold_rows"] == b["gold_rows"] and tagged(a, "silver") and tagged(a, "gold"),
             f"silver {b['silver_rows']} -> {a['silver_rows']} rows, gold {b['gold_rows']} -> {a['gold_rows']}; "
             f"silver {a['silver']['refs']}, gold {a['gold']['refs']}")],
            f"already landed, 0 rows staged, bronze main {a['bronze']['refs'].get('main')} and {a['bronze_rows']} rows as before; "
            f"the build passed, silver {a['silver_rows']} and gold {a['gold_rows']} rows")
    elif name == "corrupt":
        a, c, cl, f = state("good"), state("corrupt"), state("cleared"), facts("corrupt")
        bad = {r[1]: r[2] for r in f["checks"] if r[0] == "ecb_fx_rates/rp-corrupt"}
        failed = sorted(k for k, ok in bad.items() if not ok)
        landed = [r[2] for r in f["landing"]]
        retry = result("rp-third.load.2")
        n3 = len(rows("rp-third") or [])
        shas = {run: seed(run)["sha256"] for run in ("rp-good", "rp-corrupt", "rp-third")}
        verdict("the corrupt file lands and fails its audit; a third batch waits behind it until audit is cleared", [
            (seed("rp-corrupt")["exit"] == 0, f"the corrupt file's parse exit {seed('rp-corrupt')['exit']}"),
            task("rp-corrupt", "load"),
            task("rp-corrupt", "audit", 3),
            ({"rate_positive", "no_future_date"} & set(failed) != set(), f"failed checks of the corrupt batch {failed}"),
            task("rp-corrupt", "publish", 3),
            (c["bronze"]["refs"].get("main") == a["bronze"]["refs"].get("main") and c["bronze_rows"] == a["bronze_rows"],
             f"bronze main {a['bronze']['refs'].get('main')} -> {c['bronze']['refs'].get('main')}, rows {a['bronze_rows']} -> {c['bronze_rows']}"),
            task("rp-third", "load", 2),
            ("holds batch ecb_fx_rates/rp-corrupt" in text("rp-third.load", "err"), f"the refusal: {why('rp-third.load')}"),
            (len(objects("corrupt")) == 3 and all(any(sha in k for k in objects("corrupt")) for sha in shas.values()),
             f"k2-landing {objects('corrupt')}, want the three files {shas}"),
            (landed == ["ecb_fx_rates/rp-good", "ecb_fx_rates/rp-corrupt", "ecb_fx_rates/rp-third"], f"landing_files batches {landed}"),
            (rc("clear") == 0, f"the clearing step exit {rc('clear')}: {why('clear')}"),
            task("rp-third", "load.2"),
            (retry.get("rows") == n3 > 0 and cl["bronze"]["refs"].get("audit") == retry.get("snapshotAfter")
             and cl["bronze"]["refs"].get("main") == a["bronze"]["refs"].get("main"),
             f"the load after clearing {retry}, bronze refs {cl['bronze']['refs']}")],
            f"corrupt batch failed {failed}, main {c['bronze']['refs'].get('main')} unmoved; third batch refused (exit 2) and landed; "
            f"`{text('clear').strip().splitlines()[-1] if text('clear').strip() else ''}`; then {n3} rows staged")
    elif name == "expire":
        a, fb, ex = state("again"), state("fail"), state("expired")
        xcom, done, kept = result("rp-fail.build"), result("rp-fail.expire"), result("rp-fail.maintain")
        listed = {e["table"]: e["ids"] for e in xcom.get("expire") or []}
        key = {SILVER: "silver", GOLD: "gold"}
        left = {t: sorted(set(ids) & set(ex[key[t]]["snapshots"])) for t, ids in listed.items() if t in key}
        verdict("a failed build is restored, expire removes its snapshots by id, maintain runs clean", [
            task("rp-fail", "build", 1),
            (xcom.get("exit") == 1 and SILVER in listed and set(listed) <= set(key), f"the XCom line {xcom}"),
            (all(fb[k]["refs"].get("main") == fb[k]["refs"].get("audited") == a[k]["refs"].get("audited") for k in ("silver", "gold"))
             and fb["silver_rows"] == a["silver_rows"] and fb["gold_rows"] == a["gold_rows"],
             f"after the rollback silver {fb['silver']['refs']} ({fb['silver_rows']} rows), gold {fb['gold']['refs']} "
             f"({fb['gold_rows']} rows); before, audited silver {a['silver']['refs'].get('audited')}, gold {a['gold']['refs'].get('audited')}"),
            task("rp-fail", "expire"),
            ({e["table"] for e in done.get("expired", [])} == set(listed)
             and all(e["deletedManifestLists"] >= len(e["ids"]) for e in done.get("expired", [])), f"expire's result {done}"),
            (not any(left.values()), f"snapshots still in the catalog after expire {left}"),
            task("rp-fail", "maintain"),
            (kept.get("exit") == 0, f"maintain's result {kept}")],
            f"silver and gold back on their tags; expired {[(e['table'].split('.')[-1], len(e['ids']), e['deletedManifestLists']) for e in done.get('expired', [])]}"
            f" (ids, manifest lists deleted); maintain {kept.get('expired')}, landings kept {kept.get('landingsKept')}")
    elif name == "records":
        rid, fail_id = args[1], args[2]
        runs = {"calendars/rp-cal": (CAL, rid, "audited"), "ecb_fx_rates/rp-good": (ECB, rid, "audited"),
                "ecb_fx_rates/rp-again": (ECB, rid, "audited"), "ecb_fx_rates/rp-fail": (ECB, fail_id, "rolled_back")}
        problems, total = [], 0
        for run, (product, r, end) in runs.items():
            got = records(run)
            total += len(got)
            outputs = pipeline(r, product)["build"]["outputs"]
            missing = [(t.split(".")[-1], e) for t in outputs for e in ("started", end)
                       if not any(x["table"] == t and x["event"] == e and x["task"] == "build" for x in got)]
            wrong = sorted({x["release"] for x in got} - {r})
            if missing or wrong:
                problems.append(f"{run}: missing {missing}, other releases {wrong}")
        verdict("every build's run records are in the run-record table under its release",
                [(not problems, "; ".join(problems))], f"{total} rows for 4 builds, each output started and ended, under {rid} "
                                                       f"(the failing build under {fail_id})")
    elif name == "bytes":  # fx-bad-file: each landed object, read back from k2-landing, is the spooled raw byte for byte
        runs = ("rp-corrupt", "rp-third")
        got = {run: hashlib.sha256((RP / f"landed-{run}.bin").read_bytes()).hexdigest() if (RP / f"landed-{run}.bin").exists() else None
               for run in runs}
        raw = {run: hashlib.sha256((SPOOL / "ecb_fx_rates" / run / "raw.xml").read_bytes()).hexdigest() for run in runs}
        verdict("the corrupt file's and the waiting file's landing objects are their spooled raw bytes",
                [(got[r] == raw[r] == seed(r)["sha256"], f"{r}: landed {got[r]}, spooled {raw[r]}") for r in runs],
                ", ".join(f"{r} {raw[r][:12]}" for r in runs))
    elif name == "counts":  # fx-idempotent: every count before the same bytes again, and after
        g, b, a = state("good"), state("built"), state("again")
        pairs = {"k2-landing objects": (len(objects("good")), len(objects("again"))),
                 "landing_files rows": (g["landing_log"]["records"], a["landing_log"]["records"]),
                 "bronze rows": (g["bronze_rows"], a["bronze_rows"]), "silver rows": (b["silver_rows"], a["silver_rows"]),
                 "gold rows": (b["gold_rows"], a["gold_rows"])}
        verdict("the counts before and after the same bytes again are equal",
                [(x == y and x, f"{k} {x} -> {y}") for k, (x, y) in pairs.items()],
                "; ".join(f"{k} {x} -> {y}" for k, (x, y) in pairs.items()))
    elif name == "failing":  # dbt-failing-test: the planted data test fails, both outputs go back to their prior snapshots
        b, fb, xcom = state("built"), state("fail"), result("rp-fail.build")
        listed = {e["table"] for e in xcom.get("expire") or []}
        rolled = {x["table"]: x for x in history(ECB) if x["run"] == "ecb_fx_rates/rp-fail" and x["event"] == "rolled_back"}
        failed = trino.ok(BUILDER, "SELECT * FROM fx_reference_build.ecb_fx_rates__base.dq_test_results "
                                   "WHERE run_id = 'ecb_fx_rates/rp-fail' AND status = 'fail'")
        verdict("a failing data test rolls silver and gold back to their prior snapshots", [
            task("rp-fail", "build", 1),
            (any("k2_unique_key" in json.dumps(r, default=str) for r in failed), f"the failed tests of the run: {json.dumps(failed, default=str)[:300]}"),
            (xcom.get("exit") == 1 and GOLD in listed, f"the XCom line {xcom}"),
            (all(fb[k]["refs"].get("main") == b[k]["refs"].get("main") == fb[k]["refs"].get("audited") for k in ("silver", "gold")),
             f"before silver {b['silver']['refs']}, gold {b['gold']['refs']}; after silver {fb['silver']['refs']}, gold {fb['gold']['refs']}"),
            (all(t in rolled and rolled[t]["before"] == b[k]["refs"].get("main") == rolled[t]["after"] for t, k in ((SILVER, "silver"), (GOLD, "gold"))),
             f"rolled_back rows {rolled}")],
            f"k2_unique_key failed; silver back on {fb['silver']['refs'].get('main')}, gold on {fb['gold']['refs'].get('main')}, "
            f"each its prior snapshot; rolled_back for both; XCom lists {sorted(t.split('.')[-1] for t in listed)}")
    elif name == "poll":  # dbt-failing-test: the reader never saw a row of the failed build
        pl = json.loads((RP / "poll-fail.json").read_text())
        base = pl["base"] or []
        verdict("a reader polling gold every 50 ms through the failing build never sees a row of it", [
            (pl["polls"] >= 20 and pl["n_errors"] == 0 and pl["n_bad"] == 0 and len(base) > 0
             and not any(r[2].startswith("999") for r in base),
             f"{pl['polls']} polls, {pl['n_errors']} errors {pl['errors']}, {pl['n_bad']} with another answer {pl['bad']}, "
             f"baseline {len(base)} rows")],
            f"{pl['polls']} polls by {READER}, each the {len(base)} tested rows, no 999 rate, no error")
    elif name == "expired":  # dbt-failing-test: expire by the XCom ids, and the expire task's run record
        fb, ex, xcom, done = state("fail"), state("expired"), result("rp-fail.build"), result("rp-fail.expire")
        listed = {e["table"]: e["ids"] for e in xcom.get("expire") or []}
        key = {SILVER: "silver", GOLD: "gold"}
        left = {t: sorted(set(ids) & set(ex[key[t]]["snapshots"])) for t, ids in listed.items()}
        rows = [x for x in history(ECB) if x["run"] == "ecb_fx_rates/rp-fail" and x["task"] == "expire"]
        verdict("expire removes the failed build's snapshots by id; the expire task's run record is written", [
            task("rp-fail", "expire"),
            ({e["table"] for e in done.get("expired", [])} == set(listed) and all(e["deletedManifestLists"] >= len(e["ids"]) for e in done.get("expired", [])),
             f"expire's result {done}"),
            (not any(left.values()), f"snapshots still in the catalog {left}"),
            (task("rp-fail", "record")[0], task("rp-fail", "record")[1]),
            (len(rows) == 1 and rows[0]["event"] == "passed" and all(str(i) in (rows[0]["detail"] or "") for ids in listed.values() for i in ids),
             f"the expire task's rows {rows}")],
            f"{sum(len(v) for v in listed.values())} snapshot(s) expired, {[e['deletedManifestLists'] for e in done.get('expired', [])]} manifest "
            f"lists deleted; run record task expire event passed, its detail the ids")
    elif name == "env":  # trino-stop-mid-build: the running dbt-runner's environment names (env-check.json holds no value)
        e = json.loads((RP / "env-check.json").read_text())
        want = sorted(set(e["kind"]) | set(e["image"]))
        verdict("the running dbt-runner's environment is the kind's seven names and the image's own, no secret", [
            (len(e["kind"]) == 7 and e["names"] == want, f"names {e['names']}, want the kind's {e['kind']} and the image's {e['image']}"),
            (not e["forbidden"] and e["secretValues"] == 0, f"forbidden names {e['forbidden']}, {e['secretValues']} value(s) holding a secret")],
            f"{', '.join(e['kind'])}; the image's {', '.join(e['image'])}; no secret value")
    elif name == "stopped":  # trino-stop-mid-build: the build with Trino stopped under it published nothing
        b, st, xcom = state("built"), state("stopped"), result("rp-stop.build")
        recs = [x for x in history(ECB) if x["run"] == "ecb_fx_rates/rp-stop"]
        verdict("a build whose Trino stops after its started rows exits non-zero and publishes nothing", [
            (rc("rp-stop.build") not in (0, None), f"the build exit {rc('rp-stop.build')}"),
            (xcom.get("exit") not in (0, None), f"the XCom line {xcom}"),
            ([x["event"] for x in recs] == ["started", "started"], f"the run's records {[(x['event'], x['table']) for x in recs]}"),
            (all(st[k]["refs"].get("audited") == b[k]["refs"].get("audited") is not None for k in ("silver", "gold")),
             f"tags before silver {b['silver']['refs']}, gold {b['gold']['refs']}; after silver {st['silver']['refs']}, gold {st['gold']['refs']}")],
            f"exit {rc('rp-stop.build')}, records started x2 only, both tags where they were")
    elif name == "reconciled":  # trino-stop-mid-build: the next run's first record is reconciled, then it builds and publishes
        h = history(ECB)
        last = max(i for i, x in enumerate(h) if x["run"] == "ecb_fx_rates/rp-stop" and x["event"] == "started")
        after = [(x["event"], x["run"].split("/")[1]) for x in h[last + 1:]]
        want = [("reconciled", "rp-stop")] * 2 + [("started", "rp-rerun")] * 2 + [("audited", "rp-rerun")] * 2
        s = state("rerun")
        verdict("the next run writes reconciled first, then builds and publishes", [
            task("rp-rerun", "build"),
            (after == want, f"the records after the stopped run's started rows {after}"),
            (tagged(s, "silver") and tagged(s, "gold"), f"silver {s['silver']['refs']}, gold {s['gold']['refs']}")],
            f"{', '.join(e for e, _ in after)}; audited = main on both outputs")
    else:
        sys.exit(f"no claim {name}")
else:
    sys.exit(f"probe: no command {cmd}")
