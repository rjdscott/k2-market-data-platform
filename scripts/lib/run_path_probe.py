"""The run-path scripts' reads of the lake and their claims (scripts/lib/run-path-lib.sh), in the
throwaway lake's tools container: /work is the lake's state, /work/rp the run's files.

    run_path_probe.py setup <release id>            the namespaces and the run-record table
    run_path_probe.py seed <release id> <fixture> <run>   a spool entry, as fetch.py leaves one
    run_path_probe.py seed-api <release id> <product> <response> <run>   a public-API page's entry, likewise
    run_path_probe.py chain-state <label>           the phase 004 chain's tables, every row, to chain-<label>.json
    run_path_probe.py state <label>                 refs, snapshots and counts, to state-<label>.json
    run_path_probe.py poll <label>                  a reader on gold every 50 ms until stop-<label>
    run_path_probe.py wait-started <run id>         until the wrapper's started row of the run is in
    run_path_probe.py wait-trino                    until Trino answers again
    run_path_probe.py claim <name> [args]           one PASS or FAIL line; exit 1 on a FAIL

`seed` and `seed-api` write the spool; nothing else here writes the lake."""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
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
# The phase 004 chain's tables (run-path.sh claims 10 to 18), each read as its product's builder
CHAIN = {"pairs": ("dbt_kraken_instruments", "lake.kraken_instruments.bronze_pairs"),
         "listing": ("dbt_kraken_instruments", "market_capture_build.kraken_instruments__base.silver_listing"),
         "venue_symbol": ("dbt_security_master", "reference_data_build.security_master__base.venue_symbol"),
         "symbol_held": ("dbt_security_master", "reference_data_build.security_master__base.venue_symbol_held"),
         "bronze_trade": ("dbt_kraken_trades", "lake.kraken_trades.bronze_trade"),
         "silver_trade": ("dbt_kraken_trades", "market_capture_build.kraken_trades__base.silver_trade"),
         "unresolved": ("dbt_kraken_trades", "market_capture_build.kraken_trades__base.silver_trade_unresolved"),
         "rejected": ("dbt_kraken_trades", "market_capture_build.kraken_trades__base.silver_trade_rejected"),
         "canonical": ("dbt_canonical_trades", "market_analytics_build.canonical_trades__base.trades"),
         **{f"ohlcv_{w}": ("dbt_candles", f"market_analytics_build.candles__base.ohlcv_{w}") for w in ("1m", "5m", "1h", "1d")}}
KT, CT, KI, SM, CA = ("market-capture.kraken-trades", "market-analytics.canonical-trades", "market-capture.kraken-instruments",
                      "reference-data.security-master", "market-analytics.candles")
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
elif cmd == "seed-api":  # seed-api <release id> <product> <response> <run>: fetch_api()'s entry for one page, then its parse
    sys.path.insert(0, str(RP))
    import fetch
    rid, product, fixture, run = args
    p = pipeline(rid, product)
    c, dag = p["connector"], SPOOL / p["dagId"]
    data = (RP / fixture).read_bytes()
    content = fetch.content_sha256(data, set(c.get("volatileFields", [])))
    before = fetch.newest(dag).get("pages", []) if dag.is_dir() else []
    if [content] == [x.get("contentSha256") for x in before]:  # fetch.main() exits 10 here and spools nothing
        (RP / f"{run}.seed.json").write_text(json.dumps({"exit": 10, "unchanged": True}))
        sys.exit(0)
    entry = dag / run
    entry.mkdir(parents=True)
    (entry / "raw.1.json").write_bytes(data)
    at = datetime.now(fetch.UTC).isoformat(timespec="microseconds")
    url = c["url"].replace("{{ds}}", at[:10])
    page = {"page": 1, "url": url, "finalUrl": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "fetchedAt": at, "httpStatus": 200, "address": None, "contentSha256": content, "raw": "raw.1.json"}  # no address: nothing was dialled
    (entry / "fetch.json").write_text(json.dumps({"fetchedAt": at, "pages": [page]}, indent=1) + "\n")
    audits = list(dict.fromkeys(a for b in p.get("bronze", []) for a in b["audits"]))  # as fetch.main() lists them
    code = fetch.parse(release(rid) / fetch.PARSER.format(domain=p["domain"], parser=c["parser"]), entry, ["raw.1.json"], audits)
    (RP / f"{run}.seed.json").write_text(json.dumps({"exit": code, "unchanged": False, "fetchedAt": at, "sha256": page["sha256"],
                                                     "audits": audits}))
elif cmd == "chain-state":  # chain-state <label>: every row of the chain's tables, each read as its product's builder; refs by the catalog
    s = {}
    for k, (user, rel) in CHAIN.items():
        # Times cast to varchar: Trino rounds a timestamp to milliseconds for a client that does not declare
        # the PARAMETRIC_DATETIME capability, as access_verify's does not; the varchar is every digit
        cols = trino.run(user, f"SELECT * FROM {rel} LIMIT 0", zone="UTC").columns
        a = trino.run(user, "SELECT " + (", ".join(f'CAST("{c}" AS varchar) AS "{c}"' if t.startswith("timestamp") else f'"{c}"' for c, t in cols)
                                         or "*") + f" FROM {rel}", zone="UTC")
        a.columns = [(c, t) for (c, t), _ in zip(cols, a.columns, strict=True)] if cols else a.columns
        catalog, ns, t = rel.split(".")
        meta = cd.Catalog(LK, "k2" if catalog == "lake" else catalog.removesuffix("_build")).table(ns, t) or {}
        s[k] = {"error": a.says() if a.error else None, "columns": [c for c, _ in a.columns],
                "rows": [{c: v if ty.startswith(("decimal", "varbinary")) else av.norm(ty, v) for (c, ty), v in zip(a.columns, r)} for r in a.rows],
                "refs": {n: x["snapshot-id"] for n, x in (meta.get("refs") or {}).items()}}
    meta = cd.Catalog(LK, "k2").table("kraken_trades", "landing") or {}
    main = next((x for x in meta.get("snapshots") or [] if x["snapshot-id"] == (meta.get("refs") or {}).get("main", {}).get("snapshot-id")), {})
    s["landing"] = {"records": int(main.get("summary", {}).get("total-records", 0)), "snapshots": len(meta.get("snapshots") or [])}
    (RP / f"chain-{args[0]}.json").write_text(json.dumps(s, indent=1, default=str))
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
    elif name == "rest":  # gold's rows through k2-api against the same principal's SQL; the refusals
        rid = args[1]
        key = av.api_key(env, READER)  # read from the file the lake made; sent in the header alone
        api = (os.environ.get("K2_API_URL") or av.API_URL).rstrip("/")
        found = {rel: av.route(release(rid), rel) for rel in (READER_GOLD, "lake.ecb_fx_rates.bronze_daily", "fx_reference.ecb_fx_rates.silver_rates")}
        gold_path, pk = found[READER_GOLD] or ("", [])
        published = len(rows("rp-good") or [])

        def same(where=""):  # (SQL's rows, REST's rows, REST's status line), each a list in key order, values in one form
            sql = trino.run(READER, f"SELECT * FROM {READER_GOLD}{where} ORDER BY {', '.join(pk)}", zone="UTC")
            types, cols = dict(sql.columns), [c for c, _ in sql.columns]

            def one(t, v):  # a decimal as its exact string; dates and times as access_verify's REST leg puts them
                return str(v) if t.startswith("decimal") and v is not None else av.norm(t, v)
            query = {"limit": str(av.REST_PAGE)} | ({"filter.currency": where.split("'")[1]} if where else {})
            status, body = av.api_get(api + gold_path, key, query)
            got = body.get("rows", []) if status == 200 else []
            return ([tuple(one(types[c], v) for c, v in zip(cols, r, strict=True)) for r in sql.rows],
                    [tuple(one(types[c], r.get(c)) for c in cols) for r in got if sorted(r) == sorted(cols)] if len(got) else [],
                    f"HTTP {status}" if status == 200 else av.said(status, body), sql.says())
        whole_sql, whole_rest, whole_said, whole_sql_said = same()
        currency = whole_sql[0][1] if whole_sql else "USD"
        one_sql, one_rest, one_said, _ = same(f" WHERE currency = '{currency}'")
        try:  # no key at all
            with urllib.request.urlopen(f"{api}{gold_path}?limit=1", timeout=60) as r:
                anonymous = r.status
        except urllib.error.HTTPError as x:
            anonymous = x.code
        except OSError:
            anonymous = 0
        bronze_ok, bronze_why = av.rest_denied(api, found["lake.ecb_fx_rates.bronze_daily"][0], key) if key and found["lake.ecb_fx_rates.bronze_daily"] else (False, "no key or no route")
        st, body = av.api_get(api + found["fx_reference.ecb_fx_rates.silver_rates"][0], key, {"limit": "1"}) if key and found["fx_reference.ecb_fx_rates.silver_rates"] else (0, {"error": "none", "message": "no key or no route"})
        verdict("gold through k2-api is exactly the reader's SQL rows; no key 401, bronze and silver 403", [
            (key is not None and all(found.values()), f"key file {'there' if key else 'missing'} under {env['apiKeys']}; routes {found}"),
            (whole_rest == whole_sql and len(whole_sql) == published > 0,
             f"REST ({whole_said}) {len(whole_rest)} rows, SQL ({whole_sql_said}) {len(whole_sql)}, published {published}; "
             f"first difference {next(((a, b) for a, b in zip(whole_rest, whole_sql) if a != b), None)}"),
            (one_rest == one_sql and 0 < len(one_sql) < len(whole_sql) and all(r[1] == currency for r in one_rest),
             f"filter.currency={currency}: REST ({one_said}) {len(one_rest)} rows, SQL {len(one_sql)}"),
            (anonymous == 401, f"no key: HTTP {anonymous}"),
            (bronze_ok, f"bronze: {bronze_why}"),
            # 403: k2-api asks Trino as the caller before it says anything of a relation's state, and Trino
            # denies silver's reader view to a principal no role on it is granted to (a 404 is for an output
            # with no reader relation, or one not yet published, told only to a caller who may read it).
            ((st, body.get("error")) == (403, "forbidden"), f"silver: {av.said(st, body)}")],
            f"{len(whole_rest)} rows equal in key order ({', '.join(pk)}), decimals as strings; filter.currency={currency} "
            f"{len(one_rest)} rows equal; no key 401; bronze 403; silver 403 (Trino denies the caller its view)")
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
    # ---- the phase 004 chain, claims 10 to 18 (run-path.sh): chain-<label>.json, the task files, the run records
    elif name in ("instruments", "master", "trades", "held", "candles", "idle", "paused", "venue-fail", "rejected"):
        def chain(label):
            return json.loads((RP / f"chain-{label}.json").read_text())

        def rows(st, table, *cols):
            return sorted(tuple(r[c] for c in cols) for r in st[table]["rows"]) if cols else \
                sorted(st[table]["rows"], key=lambda r: json.dumps(r, sort_keys=True))  # Trino's order is no order

        def checks(label):  # the audit task's "  pass  <check> ..." lines, by check
            return {x.split()[1]: x.split()[0] == "pass" for x in text(label).splitlines() if x.startswith(("  pass ", "  FAIL "))}

        def fixture_trades(*paths):  # every trade of the frames' trade channel, as the frame carries it, numbers exact
            out = []
            for path in paths:
                for line in (RP / path).read_text().splitlines():
                    f = json.loads(json.loads(line)["payload"], parse_float=Decimal) if line.strip() else {}
                    out += f.get("data", []) if f.get("channel") == "trade" else []
            return out

        def candles(trades, width):  # this probe's own arithmetic: {(instrument, window_start): (o, h, l, c, volume, count)}
            def start(t):
                at = datetime.fromisoformat(t["timestamp"])
                return {"1m": at.replace(second=0, microsecond=0), "5m": at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0),
                        "1h": at.replace(minute=0, second=0, microsecond=0), "1d": at.replace(hour=0, minute=0, second=0, microsecond=0)}[width]
            out = {}
            for t in sorted(trades, key=lambda t: t["timestamp"]):  # the fixture's trade times are distinct
                k = (t["symbol"].replace("/", "-"), start(t).isoformat(timespec="microseconds"))
                o, h, lo, _, v, n = out.get(k, (t["price"], t["price"], t["price"], None, Decimal(0), 0))
                out[k] = (o, max(h, t["price"]), min(lo, t["price"]), t["price"], v + t["qty"], n + 1)
            return out

        def table_candles(st, width):
            return {(r["instrument_id"], r["window_start"]): tuple(Decimal(r[c]) for c in ("open", "high", "low", "close", "volume")) + (int(r["trade_count"]),)
                    for r in rows(st, f"ohlcv_{width}")}

        def state_of(product, through=None):  # k2runs.state over the product's run records (those up to run `through`'s last), the release's own client
            import importlib.util
            spec = importlib.util.spec_from_file_location("k2runs", release(args[1]) / "platform/platform/runs/k2runs.py")
            k2runs = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(k2runs)
            got = trino.ok("svc.runs", f"SELECT run_id, task, event, recorded_at, detail, inputs FROM {RECORDS} "
                                       f"WHERE product_id = {sql_literal(product)}")
            recs = [dict(zip(("run_id", "task", "event", "recorded_at", "detail", "inputs"), r)) for r in got]
            end = max((r["recorded_at"] for r in recs if r["run_id"] == through), default=None) if through else None
            return k2runs.state([r for r in recs if end is None or r["recorded_at"] <= end])

        def built(run, want=0):  # a wrapper build: its exit and XCom's
            return (rc(f"{run}.build") == want and result(f"{run}.build").get("exit") == want,
                    f"{run} build exit {rc(f'{run}.build')}, XCom {result(f'{run}.build')} (want {want}): {why(f'{run}.build')}")

        def tagged_on(st, *tables):
            bad = [t for t in tables if st[t]["refs"].get("main") is None or st[t]["refs"].get("audited") != st[t]["refs"].get("main")]
            return not bad, f"not audited on main: {[(t, st[t]['refs']) for t in bad]}"

        def readable(st, *tables):
            bad = [(k, st[k]["error"]) for k in tables if st[k]["error"]]
            return not bad, f"read as the product's builder: {bad}"

        trades_1 = fixture_trades("frames.jsonl")
        n1 = len(trades_1)
        if name == "instruments":  # 10
            sd, st = seed("ki-1"), chain("ki-1")
            listed = json.loads((RP / "listing-1.json").read_text())["result"]
            entry = SPOOL / "kraken_instruments/ki-1"
            fetched = json.loads((entry / "fetch.json").read_text())["pages"][0]["fetchedAt"]
            audits = json.loads((entry / "audits.json").read_text())["audits"] if (entry / "audits.json").exists() else None
            sys.path.insert(0, str(lake))
            import connector  # the release's own check names, pure
            [bronze] = pipeline(args[1], KI)["bronze"]
            want = connector.check_names(bronze)
            got, load, pub = checks("ki-1.audit"), result("ki-1.load"), result("ki-1.publish")
            firsts = {r["pair_code"]: r["first_fetched_at"] for r in rows(st, "listing")}
            verdict("kraken-instruments: the spooled listing lands, audits, publishes; silver_listing one version per pair at the fetch", [
                (sd["exit"] == 0 and audits is not None and all(a["passed"] for a in audits) and [a["name"] for a in audits] == sd["audits"],
                 f"the spool: parse exit {sd['exit']}, audits.json {audits}, declared {sd['audits']}"),
                task("ki-1", "load"),
                (load.get("rows") == len(listed), f"load's result {load}, want {len(listed)} rows"),
                task("ki-1", "audit"),
                (sorted(got) == sorted(want) and all(got.values()), f"audit's checks {got}, declared {want}"),
                task("ki-1", "publish"),
                (st["pairs"]["refs"].get("main") == pub.get("snapshotAfter") is not None and len(rows(st, "pairs")) == len(listed),
                 f"bronze_pairs refs {st['pairs']['refs']}, {len(rows(st, 'pairs'))} rows; publish {pub}"),
                built("ki-1"), tagged_on(st, "listing"), readable(st, "pairs", "listing"),
                (sorted(firsts) == sorted(listed) and len(rows(st, "listing")) == len(listed)
                 and {datetime.fromisoformat(v) for v in firsts.values()} == {datetime.fromisoformat(fetched)},
                 f"silver_listing {[(r['pair_code'], r['first_fetched_at']) for r in rows(st, 'listing')]}, the spool's fetchedAt {fetched}")],
                f"{len(listed)} pairs loaded, {len(want)} checks passed ({len(audits or [])} parser audits declared, each passed), "
                f"main {pub.get('snapshotAfter')}; silver_listing {len(firsts)} rows, one per pair, each first_fetched_at {fetched}")
        elif name == "master":  # 11
            st = chain("sm-1")
            listed = json.loads((RP / "listing-1.json").read_text())["result"]
            seed_csv = release(args[1]) / "documents/contracts/reference-data/security-master/seeds/code_exceptions.csv"
            older = {}
            for line in seed_csv.read_text().splitlines()[1:]:
                venue, code, asset, _ = line.split(",", 3)
                older[(venue, code.upper())] = asset.upper()
            spell = {code: "/".join(older.get(("kraken", x.upper()), x.upper()) for x in a["wsname"].split("/")) for code, a in listed.items()}
            got = {r["pair_code"]: r for r in rows(st, "venue_symbol")}
            symbols = {t["symbol"] for t in trades_1}
            mapped = [c for c, a in listed.items() if spell[c] != a["wsname"].upper()]
            by_seed = ", ".join(f"{listed[c]['wsname']} -> {spell[c]}" for c in mapped)
            verdict("security-master on the tested listing: one open venue_symbol per pair, as the trades stream spells it", [
                built("sm-1"), tagged_on(st, "venue_symbol", "symbol_held"), readable(st, "venue_symbol", "symbol_held"),
                (bool(older) and bool(mapped), f"the legacy-code mapping is not exercised: seed {older} ({seed_csv}), pairs needing it {mapped}"),
                (sorted(got) == sorted(listed) and all(got[c]["venue_symbol"] == spell[c] for c in listed),
                 f"venue_symbol {[(r['pair_code'], r['venue_symbol']) for r in rows(st, 'venue_symbol')]}, want {spell}"),
                (symbols <= {r["venue_symbol"] for r in got.values()}, f"the frames' symbols {symbols} not all spelled"),
                (all(r["valid_from"] == "1970-01-01T00:00:00.000000+00:00" and r["valid_to"] == "9999-12-31T23:59:59.000000+00:00" for r in got.values()),
                 f"valid_from/valid_to {[(r['venue_symbol'], r['valid_from'], r['valid_to']) for r in got.values()]}"),
                (rows(st, "symbol_held") == [], f"venue_symbol_held {rows(st, 'symbol_held')}")],
                f"{len(got)} open rows from 1970-01-01 to 9999-12-31 23:59:59: {', '.join(sorted(spell[c] for c in listed))}; "
                f"{by_seed} by the seed; none held")
        elif name == "trades":  # 12
            st, ca = chain("kt-1"), chain("ca-1")
            load, pub = result("kt-1.load"), result("kt-1.publish")
            frames = [x for x in (RP / "frames.jsonl").read_text().splitlines() if x.strip()]
            trade_frames = [x for x in frames if json.loads(json.loads(x)["payload"]).get("channel") == "trade"]
            got = checks("kt-1.audit")
            silver = rows(st, "silver_trade")
            one = next((r for r in silver if r["trade_id"] == trades_1[0]["trade_id"]), {})
            verdict(f"kraken-trades: every frame lands, the {len(trade_frames)} trade frames stage, silver_trade one row per trade ({n1})", [
                task("kt-1", "load"),
                (load.get("landed") == len(frames) == st["landing"]["records"] and load.get("rows") == len(trade_frames)
                 and load.get("rows", 0) + load.get("skipped", -1) == load.get("landed"),
                 f"load's result {load}; the landing holds {st['landing']['records']} of {len(frames)} frames"),
                task("kt-1", "audit"),
                (got == {"accounting": True, "parses": True, "keys_unique": True}, f"audit's checks {got}"),
                task("kt-1", "publish"),
                (st["bronze_trade"]["refs"].get("main") == pub.get("snapshotAfter") is not None and len(rows(st, "bronze_trade")) == len(trade_frames)
                 and all(r["stream"] == "trade" for r in rows(st, "bronze_trade")), f"bronze_trade refs {st['bronze_trade']['refs']}, {len(rows(st, 'bronze_trade'))} rows"),
                built("kt-1"), tagged_on(st, "silver_trade", "unresolved"), readable(st, "bronze_trade", "silver_trade", "unresolved"),
                (len(silver) == n1 and all(r["instrument_id"] == r["venue_symbol"].replace("/", "-") for r in silver)
                 and not any(r["is_duplicate"] for r in silver), f"silver_trade {[(r['trade_id'], r['venue_symbol'], r['instrument_id'], r['is_duplicate']) for r in silver]}"),
                (one and Decimal(one["price"]) == trades_1[0]["price"] and Decimal(one["quantity"]) == trades_1[0]["qty"]
                 and one["price"] == f"{trades_1[0]['price']:.18f}" and one["quantity"] == f"{trades_1[0]['qty']:.18f}",
                 f"trade {trades_1[0]['trade_id']}: price {one.get('price')}, quantity {one.get('quantity')}; the frame's {trades_1[0]['price']}, {trades_1[0]['qty']}"),
                (rows(st, "unresolved") == [], f"silver_trade_unresolved {rows(st, 'unresolved')}"),
                built("ct-1"), built("ca-1")],
                f"{load.get('landed')} frames landed, {load.get('rows')} staged + {load.get('skipped')} skipped; accounting, parses, keys_unique passed; "
                f"silver_trade {len(silver)} rows, each resolved ({', '.join(sorted({r['instrument_id'] for r in silver}))}), none a duplicate; "
                f"trade {trades_1[0]['trade_id']} price {one.get('price')} quantity {one.get('quantity')}; nothing held")
        elif name == "held":  # 13
            h, r, a = chain("kt-2"), chain("kt-3"), chain("ca-2")
            late = fixture_trades("unknown.jsonl")[0]
            x2, x3 = result("kt-2.build"), result("kt-3.build")

            def in_silver(st):
                return [x for x in rows(st, "silver_trade") if x["venue_symbol"] == late["symbol"]]
            held_rows = [x for x in rows(h, "unresolved") if x["venue_symbol"] == late["symbol"]]
            rec = history(KT)
            degraded = [x for x in rec if x["run"] == "kraken_trades/kt-2" and x["event"] == "audited"]
            verdict(f"a trade of {late['symbol']}, unlisted, is held and degraded; the listing naming it takes it in once", [
                task("kt-2", "load"), task("kt-2", "audit"), task("kt-2", "publish"), built("kt-2"),
                (state_of(KT, "kraken_trades/kt-2") == "degraded", f"k2runs.state through kt-2 {state_of(KT, 'kraken_trades/kt-2')!r}"),
                (len(held_rows) == 1 == len(rows(h, "unresolved")) and not in_silver(h) and len(rows(h, "silver_trade")) == n1,
                 f"after kt-2: held {[(x['venue_symbol'], x['trade_id']) for x in rows(h, 'unresolved')]}, silver {len(rows(h, 'silver_trade'))} rows"),
                # the test carries its rule's name (k2gen's dbt emitter): the held table's one warn rule,
                # unresolved_rows_zero (silver_trade_unresolved.odcs.yaml)
                (x2.get("degraded") is True and len(x2.get("warned", [])) == 1
                 and x2["warned"][0].startswith("test.k2.unresolved_rows_zero."),
                 f"kt-2's XCom {x2}"),
                (bool(degraded) and all(json.loads(x["detail"] or "{}").get("degraded") is True for x in degraded),
                 f"kt-2's audited rows {[(x['table'], x['detail']) for x in degraded]}"),
                (len(rows(a, "canonical")) == n1, f"canonical-trades after the held build: {len(rows(a, 'canonical'))} rows, want {n1}"),
                (seed("ki-2")["exit"] == 0, f"the second listing's parse: {seed('ki-2')}"),
                task("ki-2", "load"), task("ki-2", "audit"), task("ki-2", "publish"), built("ki-2"), built("sm-2"),
                (any(v["venue_symbol"] == late["symbol"] and v["valid_from"].startswith("1970-01-01") for v in rows(r, "venue_symbol")),
                 f"venue_symbol after the second listing {[(v['venue_symbol'], v['valid_from']) for v in rows(r, 'venue_symbol')]}"),
                task("kt-3", "load"), built("kt-3"),
                (len(in_silver(r)) == 1 and len(rows(r, "silver_trade")) == n1 + 1 and rows(r, "unresolved") == []
                 and in_silver(r)[0]["instrument_id"] == late["symbol"].replace("/", "-") and not in_silver(r)[0]["is_duplicate"],
                 f"after kt-3: silver {[(x['venue_symbol'], x['trade_id'], x['instrument_id']) for x in rows(r, 'silver_trade')]}, held {rows(r, 'unresolved')}"),
                (x3.get("degraded") is not True, f"kt-3's XCom {x3}"),
                (state_of(KT, "kraken_trades/kt-3") == "complete", f"k2runs.state through kt-3 {state_of(KT, 'kraken_trades/kt-3')!r}")],
                f"after kt-2 {late['symbol']} trade {late['trade_id']} held, silver {n1} rows, XCom degraded, warned "
                f"{[w.split('.')[2] for w in x2.get('warned', [])]}, k2runs.state degraded; after the second listing, kt-3 took it in "
                f"once ({n1 + 1} rows), nothing held, state complete")
        elif name == "candles":  # 14
            one, two, three = chain("ca-1"), chain("ca-2"), chain("ca-3")
            late = fixture_trades("unknown.jsonl")
            canon = rows(three, "canonical")
            key = ("kafka_partition", "kafka_offset", "trade_ordinal")
            mine = candles(trades_1, "1m")
            (inst, minute), want = next(iter(sorted(mine.items())))
            checks_ = [built("ct-1"), built("ca-1"), built("ct-3"), built("ca-3"), readable(three, *[k for k in CHAIN if k.startswith(("canonical", "ohlcv"))]),
                       (sorted(rows(three, "canonical", *key)) == sorted(rows(three, "silver_trade", *key)) and len(canon) == n1 + 1
                        and "conn_id" not in three["canonical"]["columns"] and all(r["venue"] == "kraken" for r in canon),
                        f"canonical {len(canon)} rows, silver {len(rows(three, 'silver_trade'))}; columns {three['canonical']['columns']}"),
                       (table_candles(one, "1m").get((inst, minute)) == want,
                        f"ohlcv_1m {inst} {minute}: {table_candles(one, '1m').get((inst, minute))}, the frames' (o, h, l, c, volume, count) {want}"),
                       (table_candles(one, "1d") == candles(trades_1, "1d"), f"ohlcv_1d {table_candles(one, '1d')}, the frames' {candles(trades_1, '1d')}")]
            for w in ("1m", "5m", "1h", "1d"):
                mine_all, before, after = candles(trades_1 + late, w), table_candles(two, w), table_candles(three, w)
                new = {k: v for k, v in mine_all.items() if k[0] == late[0]["symbol"].replace("/", "-")}
                checks_.append((after == mine_all and not set(new) & set(before) and before == candles(trades_1, w),
                                f"ohlcv_{w}: before the release {before}, after {after}; the frames' {mine_all}"))
            verdict("canonical-trades one row per silver_trade row; candles equal the frames' own arithmetic", checks_,
                    f"canonical {len(canon)} rows, no conn_id; ohlcv_1m {inst} {minute[:16]} (o, h, l, c, volume, count) = {tuple(str(x) for x in want)}; "
                    f"ohlcv_1d {len(rows(one, 'ohlcv_1d'))} row; the released trade's 1m, 5m, 1h and 1d buckets written after its release, the others unchanged")
        elif name == "idle":  # 15
            b, a = chain("ca-3"), chain("idle")
            moved = {k: (len(b[k]["rows"]), len(a[k]["rows"])) for k in CHAIN
                     if sorted(json.dumps(r, sort_keys=True) for r in b[k]["rows"]) != sorted(json.dumps(r, sort_keys=True) for r in a[k]["rows"])}
            verdict("every product again with nothing new adds no row and changes none", [
                (seed("ki-3").get("exit") == 10, f"the listing again: {seed('ki-3')}, want fetch's exit 10"),
                built("ki-3"), built("sm-3"), task("kt-4", "load"), task("kt-4", "audit"), task("kt-4", "publish"),
                ("no new records" in text("kt-4.load"), f"kt-4 load: {why('kt-4.load')}"),
                built("kt-4"), built("ct-4"), built("ca-4"), readable(a, *CHAIN),
                (not moved and a["landing"]["records"] == b["landing"]["records"], f"changed {moved}, landing {b['landing']} -> {a['landing']}")],
                "fetch exit 10, load, audit, publish skipped; " + ", ".join(f"{k} {len(a[k]['rows'])}" for k in CHAIN) +
                f" rows, as before; the landing {a['landing']['records']} records")
        elif name == "paused":  # 16
            b, a = chain("idle"), chain("paused")
            started = [x for x in records("canonical_trades/ct-5") if x["event"] == "started"]
            inputs = [i for x in started for i in json.loads(x["inputs"] or "[]")]
            st = state_of(CT, "canonical_trades/ct-5")
            verdict("canonical-trades with kraken-trades paused builds, adds nothing, and is partial", [
                built("ct-5"),
                (all(x["release"] == args[2] for x in started) and bool(started), f"ct-5 started rows {started}"),
                (bool(inputs) and all(i["state"] == "paused" and i["snapshot"] is None for i in inputs), f"its inputs {inputs}"),
                (rows(a, "canonical") == rows(b, "canonical"), f"canonical {len(rows(b, 'canonical'))} -> {len(rows(a, 'canonical'))} rows"),
                (st == f"partial: {KT}.silver_trade", f"k2runs.state {st!r}")],
                f"exit 0 on release {args[2]}; the input read as zero rows, its snapshot null, state paused; canonical {len(rows(a, 'canonical'))} rows "
                f"as before; k2runs.state {st!r}")
        elif name == "venue-fail":  # 17
            b, v, a = chain("paused"), chain("kt-5"), chain("failed")
            bad = fixture_trades("more.jsonl")[0]
            rolled = [x for x in history(KT) if x["run"] == "kraken_trades/kt-5" and x["event"] == "rolled_back"]
            failed = trino.ok("dbt_kraken_trades", "SELECT * FROM market_capture_build.kraken_trades__base.dq_test_results "
                                                   "WHERE run_id = 'kraken_trades/kt-5' AND status = 'fail'")
            derived = [k for k in ("canonical", "ohlcv_1m", "ohlcv_5m", "ohlcv_1h", "ohlcv_1d") if rows(b, k) != rows(a, k)]
            verdict("a failed venue test (silver_trade given a duplicate row): the build rolls back, the derived products unchanged", [
                task("kt-5", "load"), task("kt-5", "audit"), task("kt-5", "publish"),
                (len(rows(v, "bronze_trade")) == len(rows(b, "bronze_trade")) + 1, f"bronze_trade {len(rows(b, 'bronze_trade'))} -> {len(rows(v, 'bronze_trade'))} rows"),
                built("kt-5", 1),
                # silver_trade's unit tests run first in the build and catch the duplicate, so its data test never runs
                (bool(failed) and all(".kraken_trades__silver_trade" in json.dumps(f) for f in failed), f"the failed tests of kt-5 {failed}"),
                (all(x["release"] == args[2] for x in history(KT) if x["run"] == "kraken_trades/kt-5"), "kt-5's records not all under its release"),
                (all(v[t]["refs"].get("main") == v[t]["refs"].get("audited") == b[t]["refs"].get("audited") is not None for t in ("silver_trade", "unresolved"))
                 and rows(v, "silver_trade") == rows(b, "silver_trade"),
                 f"silver_trade refs {b['silver_trade']['refs']} -> {v['silver_trade']['refs']}; unresolved {b['unresolved']['refs']} -> {v['unresolved']['refs']}"),
                (bool(rolled), f"rolled_back rows of kt-5 {rolled}"),
                (not any(x["trade_id"] == bad["trade_id"] for x in rows(a, "silver_trade")), "the bad trade reached silver_trade"),
                built("ct-6"), built("ca-6"), (not derived, f"changed after the failed venue build: {derived}")],
                f"kt-5 exit 1 on release {args[2]}, failed {sorted({x for f in failed for x in f if isinstance(x, str) and x.startswith(('unit_test.', 'test.'))})}; silver_trade and the held table back on audited "
                f"{v['silver_trade']['refs'].get('audited')}; canonical-trades and candles then build and change nothing")
        elif name == "rejected":  # 18
            b, r, n = chain("failed"), chain("kt-6"), chain("kt-7")
            bad = json.loads((RP / "malformed.jsonl").read_text().splitlines()[0])  # the frame without side; the good one follows
            want = [t["trade_id"] for t in fixture_trades("more.jsonl", "malformed.jsonl") if "side" in t]  # kt-5's, then the good frame's
            x6, x7 = result("kt-6.build"), result("kt-7.build")

            def ids(st):
                return sorted(x["trade_id"] for x in rows(st, "silver_trade"))
            held = rows(r, "rejected")
            one = held[0] if len(held) == 1 else {}

            def warned_rule(x):  # the build's warn tests, by the rule's name (the dbt emitter names each test by it)
                return x.get("degraded") is True and [w.split(".")[2] for w in x.get("warned", [])] == ["rejected_frames_zero"]
            verdict("a trade frame the model cannot type (no side) is rejected whole, with its reason; the rest publishes, degraded while it stands", [
                (len(rows(r, "bronze_trade")) == len(rows(b, "bronze_trade")) + 2, f"bronze_trade {len(rows(b, 'bronze_trade'))} -> {len(rows(r, 'bronze_trade'))} rows"),
                task("kt-6", "load"), task("kt-6", "audit"), task("kt-6", "publish"), built("kt-6"),
                (set(want) <= set(ids(r)) and len(ids(r)) == len(ids(b)) + len(want) and json.loads(bad["payload"])["data"][0]["trade_id"] not in ids(r),
                 f"silver_trade trade ids {ids(b)} -> {ids(r)}; want {want} added, the bad frame's not"),
                (bool(one) and one["reason"] == "field_missing:side" and (one["kafka_partition"], one["kafka_offset"]) not in
                 {(x["kafka_partition"], x["kafka_offset"]) for x in rows(r, "silver_trade")}
                 # the payload as Trino returns varbinary, compared with bronze's own row of the key
                 and any((x["kafka_partition"], x["kafka_offset"], x["payload"]) == (one["kafka_partition"], one["kafka_offset"], one["payload"])
                         for x in rows(r, "bronze_trade")),
                 f"silver_trade_rejected {held}"),
                (rows(r, "unresolved") == [], f"silver_trade_unresolved {rows(r, 'unresolved')}"),
                (warned_rule(x6), f"kt-6's XCom {x6}"),
                (state_of(KT, "kraken_trades/kt-6") == "degraded", f"k2runs.state through kt-6 {state_of(KT, 'kraken_trades/kt-6')!r}"),
                task("kt-7", "load"), ("no new records" in text("kt-7.load"), f"kt-7 load: {why('kt-7.load')}"), built("kt-7"),
                (rows(n, "silver_trade") == rows(r, "silver_trade") and rows(n, "rejected") == held and rows(n, "unresolved") == [],
                 f"after kt-7: silver {len(rows(r, 'silver_trade'))} -> {len(rows(n, 'silver_trade'))} rows, rejected {rows(n, 'rejected')}"),
                (warned_rule(x7), f"kt-7's XCom {x7}"),
                (state_of(KT, "kraken_trades/kt-7") == "degraded", f"k2runs.state through kt-7 {state_of(KT, 'kraken_trades/kt-7')!r}")],
                f"kt-6 exit 0: silver_trade took trades {want}, the frame without side rejected at partition {one.get('kafka_partition')} "
                f"offset {one.get('kafka_offset')} ({one.get('reason')}, its bytes bronze's), warned {[w.split('.')[2] for w in x6.get('warned', [])]}, "
                f"degraded; kt-7 with nothing new: no row added or changed, the frame still rejected once, still degraded")
    else:
        sys.exit(f"no claim {name}")
else:
    sys.exit(f"probe: no command {cmd}")
