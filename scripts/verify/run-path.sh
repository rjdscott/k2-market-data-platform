#!/usr/bin/env bash
# W1-25 and W2-11 (phase 002), stage `run` of make spine-e2e: one product's whole run path on ONE
# throwaway lake (scripts/lib/throwaway-lake.sh, prefix run-path), as a deploy of <release> would
# run it. Every task runs the release's own code (<release>/platform/) with the command and the
# container shape docker/airflow/dags/factory.py gives it:
#
#   Spark tasks  `python3 /k2/releases/<id>/platform/docker/lake/<connector|expire|maintain>.py ...`
#                through factory.WRAPPER, by `docker exec` into one long-lived Spark container:
#                compose's spark-iceberg image and environment names, Spark's key and the
#                maintenance key, the releases root and the spool read-only
#   the build    `k2-dbt build --release <id> --product <p> --run-id <dag>/<run>` in the dbt-runner
#                image built from this tree, with oneshot.py's flags and the release's
#                compiled/oneshots.json limits, the product's builder and publisher files mounted
#   the fetch    not run: a local fixture server sits on a private address the fetch must refuse
#                (the fetch is proven by its own tests). Each batch's spool entry is written as
#                fetch.py leaves it after its download (raw.<ext>, fetch.json with the bytes'
#                sha256 and size), then fetch.parse() itself runs the release's parser and its
#                declared audits into rows.jsonl and audits.json.
#
#   bash scripts/verify/run-path.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run, since every
# later claim builds on the lake the one before left:
#   1 calendars builds through the wrapper and its audited tag moves (ECB's gold reads it)
#   2 ECB's good fixture (domains/fx-reference/tests/fixtures/ecb-90d-synthetic.xml): load lands it
#     in k2-landing at its landing key with one landing_files row and stages its rows on `audit`;
#     audit passes every declared check (its audit_checks rows); publish fast-forwards bronze main
#   3 the wrapper builds ECB (silver, then gold), its input pinned to calendars' tested snapshot:
#     tests pass, audited moves on both outputs, p.analyst-a reads gold through the reader view,
#     silver holds bronze's distinct (rate_date, currency), each gold rate is the fixture's string
#     as DECIMAL(20,10)
#   4 the same bytes again (a new run): no new landing object or landing_files row, no new bronze
#     row, and the build passes with the same rows (fetch itself would exit 10 here: unchanged)
#   5 the corrupt fixture lands, fails its audit (rate_positive, no_future_date) and leaves bronze
#     main where it was; a third good batch (the good fixture and one more day, made here) is
#     refused at load (exit 2) while `audit` holds the failed batch, its file landed all the same;
#     the operator's clearing step (`audit` reset to main) lets its load proceed
#   6 a failing test (gold given a duplicate row, in a release of this tree compiled here): the
#     wrapper restores silver and lists the build's snapshots in its XCom line; expire.py removes
#     them with the maintenance key; maintain.py runs clean on the product
#   7 every build's run records are in governance_build.run_records.run_record under its release
#
# One container job: refuses to start while a container that is not the stack's runs, or under
# 10 GiB available. The lake, the image and the scratch directory go in the trap; a killed run
# leaves the lake to `bash scripts/lib/throwaway-lake.sh down run-path`. Never touches the running
# stack, never prints a secret (the keys and passwords are this lake's, passed by name or file).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
REPO=$PWD
REL=$(cd "${1:?usage: run-path.sh <release dir>}" && pwd) || exit 2
if [ ! -f "$REL/manifest.json" ] || [ ! -d "$REL/platform/docker/lake" ]; then echo "run-path: $REL is not a release with run code" >&2; exit 2; fi
ID=$(basename "$REL")
L=scripts/lib/throwaway-lake.sh
P="run-path"
NET=$P-net
SPARK=$P-spark-iceberg
IMAGE=dbt-runner:run-path  # built from this tree, never a release's pin (spine-e2e's are placeholders)
ECB=fx-reference.ecb-fx-rates
CAL=reference-data.calendars
FIX=domains/fx-reference/tests/fixtures
avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
[ "$avail" -ge 10 ] || { echo "run-path: MemAvailable ${avail} GiB, under 10 GiB: not starting" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "run-path: no Docker" >&2; exit 2; }
others=$(docker ps --format '{{.Names}}' | grep -v '^k2-' || true)
[ -z "$others" ] || { echo "run-path: another container job is running ($others): not starting" >&2; exit 2; }

W=$(mktemp -d "$HOME/.k2-run-path.XXXXXX")  # under $HOME, the one place Docker Desktop shares
cleanup() {
  [ "${KEEP:-}" = 1 ] && [ -n "${STATE:-}" ] && cp -a "$STATE/rp" "$W/" 2>/dev/null
  bash "$L" down "$P" >/dev/null 2>&1
  docker rmi -f "$IMAGE" >/dev/null 2>&1
  if [ "${KEEP:-}" = 1 ]; then echo "run-path: scratch kept in $W"; else chmod -R u+w "$W"; rm -rf "$W"; fi
}
trap cleanup EXIT
T0=$SECONDS
die() { echo "run-path: $*" >&2; exit 1; }
finish() {
  local pass fail
  pass=$(grep -c '^PASS' "$W/claims" 2>/dev/null); fail=$(grep -c '^FAIL' "$W/claims" 2>/dev/null)
  [ -z "${1:-}" ] || echo "run-path: $1"
  echo "run-path: ${pass:-0} PASS, ${fail:-0} FAIL in $((SECONDS - T0)) s"
  [ "${fail:-0}" = 0 ] && [ "${pass:-0}" = 7 ]
  exit $?
}

# ---- claim 6's release: this tree with gold given a duplicate row ---------------------------
mkdir -p "$W/fail/clone"
git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$W/fail/clone"
python3 - "$W/fail/clone/contracts/fx-reference/ecb-fx-rates/models/gold_eur_rates_daily.sql" <<'PY' || die "the gold model no longer ends 'from aged': move claim 6's plant"
import sys
from pathlib import Path
p = Path(sys.argv[1])
sql = p.read_text()
assert sql.endswith("\nfrom aged\n") and sql.count("\nfrom aged\n") == 1
p.write_text(sql.removesuffix("from aged\n") + "from (select * from aged union all (select * from aged limit 1)) as aged\n")
PY
python3 - docker-compose.yml > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "run-path/placeholder@sha256:" + "0" * 64 for n in names}))
PY
GIT=(git -C "$W/fail/clone" -c user.name=run-path -c user.email=run-path@example.org)
{ "${GIT[@]}" init -q -b main && "${GIT[@]}" add -A && "${GIT[@]}" commit -q -m "run-path: a failing gold test" &&
  "${GIT[@]}" tag -a v0.0.0-runpath.1 -m run-path; } || die "the failing tree did not commit"
k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
"${k2gen[@]}" docset --tag v0.0.0-runpath.1 --repo "$W/fail/clone" --out "$W/fail/set" > "$W/compile.log" 2>&1 &&
  "${k2gen[@]}" release --set "$W/fail/set" --previous none --images "$W/images.json" --out "$W/fail/releases" 2>>"$W/compile.log" \
  | tail -1 > "$W/fail/id"
IDF=$(cat "$W/fail/id")
if [ -z "$IDF" ] || [ ! -d "$W/fail/releases/$IDF" ]; then tail -20 "$W/compile.log" >&2; die "claim 6's release did not compile"; fi

# ---- the image, the lake, the Spark container -----------------------------------------------
docker build -q -f docker/dbt-runner/Dockerfile -t "$IMAGE" . > "$W/build.log" 2>&1 \
  || { tail -20 "$W/build.log" >&2; die "the dbt-runner image did not build"; }
bash "$L" up "$P" "$REL" > "$W/up.log" 2>&1 || { tail -20 "$W/up.log" >&2; die "the throwaway lake did not start"; }
STATE=$(bash "$L" state "$P")
S=$STATE/secrets/k2
RP=$STATE/rp  # /work/rp in the tools container: the fixtures, fetch.py, every task's output and the probe's state
cp -a "$W/fail/releases/$IDF" "$STATE/releases/"
mkdir -p "$RP" "$STATE/spool"
cp docker/fetch/fetch.py "$RP/fetch.py"  # the fetch image's program (docker/fetch/Dockerfile copies this file)
cp "$FIX/ecb-90d-synthetic.xml" "$RP/good.xml"
cp "$FIX/ecb-90d-synthetic-corrupt.xml" "$RP/corrupt.xml"
python3 - "$RP/good.xml" "$RP/third.xml" <<'PY' || die "the third batch could not be made"
# The third batch: the good fixture and one more synthetic day, the day after its newest, with that
# day's rates (ECB's file lists the newest day first).
import re, sys
from datetime import date, timedelta
text = open(sys.argv[1]).read()
newest = max(re.findall(r'<Cube time="(\d{4}-\d\d-\d\d)">', text))
day = re.search(rf'[ \t]*<Cube time="{newest}">.*?</Cube>\n', text, re.S)
extra = day.group(0).replace(newest, (date.fromisoformat(newest) + timedelta(days=1)).isoformat())
open(sys.argv[2], "w").write(text[:day.start()] + extra + text[day.start():])
PY

cat > "$W/probe.py" <<'PY'
"""run-path.sh's reads of the lake and its claims, in the throwaway lake's tools container (/work
is the lake's state, /work/rp the run's files). `seed` is the one write: a spool entry."""
import hashlib, json, sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
sys.path[:0] = ["/src/scripts", "/src/scripts/verify"]
import access_verify as av
import catalog_drift as cd
from k2gen import model
from k2gen.model import sql_literal

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
        s[k] = {"refs": {n: r["snapshot-id"] for n, r in (meta.get("refs") or {}).items()},
                "snapshots": sorted(x["snapshot-id"] for x in meta.get("snapshots") or [])}
    s |= {"bronze_rows": count(BUILDER, f"SELECT count(*) FROM {BRONZE}"),
          "bronze_keys": count(BUILDER, f"SELECT count(*) FROM (SELECT DISTINCT rate_date, currency FROM {BRONZE})"),
          "silver_rows": count(BUILDER, f"SELECT count(*) FROM {SILVER}"),
          "gold_rows": count(BUILDER, f"SELECT count(*) FROM {GOLD}")}
    (RP / f"state-{args[0]}.json").write_text(json.dumps(s, indent=1))
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
        verdict("1 calendars builds through the wrapper and its audited tag moves",
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
        verdict("2 ECB's good file lands, passes every check and is published to bronze main", [
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
        verdict("3 the wrapper builds ECB on calendars' tested snapshot; a reader reads gold, exact decimals", [
            task("rp-good", "build"),
            (xcom.get("exit") == 0, f"the XCom line {xcom}"),
            (len(started) == 2 and cal is not None and pins.get("reference-data.calendars.target_days") == cal,
             f"{len(started)} started rows, pinned {pins}, calendars' audited {cal}"),
            (tagged(s, "silver") and tagged(s, "gold"), f"silver {s['silver']['refs']}, gold {s['gold']['refs']}"),
            (bool(dq) and {x for x, _ in dq} == {"pass"}, f"dq_test_results of the run {dq}"),
            (not read.error and len(read.rows) > 0, f"{READER} on {READER_GOLD}: {read.says()}"),
            (s["silver_rows"] == s["bronze_keys"] and bool(s["silver_rows"]),
             f"silver {s['silver_rows']} rows, bronze {s['bronze_keys']} distinct (rate_date, currency)"),
            (dict(read.columns).get("rate") == "decimal(20,10)" and bool(pairs)
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
        verdict("4 the same bytes again land nothing new, stage no row, and the build passes unchanged", [
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
        a, c, cl, f = state("again"), state("corrupt"), state("cleared"), facts("corrupt")
        bad = {r[1]: r[2] for r in f["checks"] if r[0] == "ecb_fx_rates/rp-corrupt"}
        failed = sorted(k for k, ok in bad.items() if not ok)
        landed = [r[2] for r in f["landing"]]
        retry = result("rp-third.load.2")
        n3 = len(rows("rp-third") or [])
        shas = {run: seed(run)["sha256"] for run in ("rp-good", "rp-corrupt", "rp-third")}
        verdict("5 the corrupt file lands and fails its audit; a third batch waits behind it until audit is cleared", [
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
        verdict("6 a failed build is restored, expire removes its snapshots by id, maintain runs clean", [
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
        verdict("7 every build's run records are in the run-record table under its release",
                [(not problems, "; ".join(problems))], f"{total} rows for 4 builds, each output started and ended, under {rid} "
                                                       f"(the failing build under {fail_id})")
    else:
        sys.exit(f"no claim {name}")
else:
    sys.exit(f"probe: no command {cmd}")
PY
probe() { bash "$L" py "$P" - "$@" < "$W/probe.py"; }

# The Spark side's two reads that need the engine: landing_files and audit_checks (no Trino rule reads them).
cat > "$W/facts.py" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/platform/docker/lake")  # the release's session builder
from spark_conf import lake_session
p = json.loads((Path(sys.argv[1]) / "compiled/products" / sys.argv[2] / "pipeline.json").read_text())
spark = lake_session("run-path-facts")
q = lambda sql: [list(r) for r in spark.sql(sql).collect()]
print(json.dumps({"landing": q(f"SELECT object_key, sha256, batch_id, parsed FROM {p['landing']['log']} ORDER BY landed_at"),
                  "checks": q(f"SELECT batch_id, check_name, passed, snapshot_id FROM {p['bronze'][0]['checks']} ORDER BY checked_at")},
                 default=str))
spark.stop()
PY
# The operator's clearing step after a failed batch: `audit` reset to main, the statement the
# connector's own SparkLake.reset_branch runs when maintenance moved main on.
cat > "$W/clear.py" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/platform/docker/lake")
from spark_conf import lake_session
table = json.loads((Path(sys.argv[1]) / "compiled/products" / sys.argv[2] / "pipeline.json").read_text())["bronze"][0]["table"]
spark = lake_session("run-path-clear-audit")
main = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'").collect()[0][0]
statement = f"ALTER TABLE {table} REPLACE BRANCH audit AS OF VERSION {main}"
spark.sql(statement)
spark.stop()
print(statement)
PY

probe setup "$ID" > "$W/setup.log" 2>&1 || { tail -5 "$W/setup.log" >&2; die "the namespaces or the run-record table could not be made"; }

# compose's service spark-iceberg: its image, its environment names (Spark's pair also under the AWS
# names, the region), the releases root and the spool read-only; one long-lived container, as the stack's.
set -a
# shellcheck source=/dev/null
. "$STATE/secrets/s3.env"
set +a
export K2_S3_REGION=local-01 AWS_ACCESS_KEY_ID=$K2_SPARK_S3_ACCESS_KEY AWS_SECRET_ACCESS_KEY=$K2_SPARK_S3_SECRET_KEY AWS_REGION=us-east-1
docker run -d --name "$SPARK" --label "k2.throwaway=$P" --network "$NET" --cpus 2 --memory 3g \
  -e K2_SPARK_S3_ACCESS_KEY -e K2_SPARK_S3_SECRET_KEY -e K2_MAINTENANCE_S3_ACCESS_KEY -e K2_MAINTENANCE_S3_SECRET_KEY \
  -e K2_S3_ENDPOINT -e K2_S3_REGION -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION \
  -v "$STATE/releases:/k2/releases:ro" -v "$STATE/spool:/spool:ro" \
  "$(grep -oE 'image: *k2-spark-iceberg:[^ ]*' docker-compose.yml | head -1 | sed 's/image: *//')" sleep infinity > /dev/null \
  || die "the Spark container did not start"
echo "run-path: release $ID (claim 6's $IDF); lake, image and Spark up in $((SECONDS - T0)) s"

# shellcheck disable=SC2016  # factory.WRAPPER, verbatim: $@ and $0 are the shell's in the container
WRAPPER='python3 "$@" & echo $! > "$0"; wait $!; rc=$?; rm -f "$0"; exit $rc'
task() {  # task <release id> <run> <load|audit|publish|expire|maintain> [args]: factory.SparkTask's argv; TRY=n labels a rerun
  local id=$1 run=$2 t=$3 label=$2.$3${TRY:+.$TRY}; shift 3
  local argv=("/k2/releases/$id/platform/docker/lake/connector.py" "$t")
  case $t in expire|maintain) argv=("/k2/releases/$id/platform/docker/lake/$t.py") ;; esac
  docker exec "$SPARK" sh -c "$WRAPPER" "/tmp/k2-airflow-$label.pid" "${argv[@]}" --release "$id" --product "$ECB" \
    --run-id "ecb_fx_rates/$run" "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  echo "run-path: $label exit $(cat "$RP/$label.rc")"
}
export K2_TRINO_HOST=trino K2_TRINO_INTERNAL_PORT=8443 K2_RUNS_USER=svc.runs K2_CATALOG_URI=http://lakekeeper:8181/catalog
build() {  # build <release id> <product> <run>: k2-dbt build as factory.PinnedOneShot and oneshot.py run it
  local id=$1 product=$2 run=$3 dag writer publisher cpus memory
  read -r dag writer publisher cpus memory < <(python3 -c '
import json, sys
r, p = sys.argv[1], sys.argv[2]
doc, k = json.load(open(f"{r}/compiled/products/{p}/pipeline.json")), json.load(open(f"{r}/compiled/oneshots.json"))["oneshots"]["dbt-runner"]
print(doc["dagId"], doc["build"]["writer"], doc["build"]["publisher"], k["cpus"], k["memory"])' "$STATE/releases/$id" "$product")
  K2_RELEASE=$id K2_TRINO_USER=$writer K2_PUB_USER=$publisher timeout 900 docker run --rm --name "$P-dbt-runner-$dag" \
    --label "k2.throwaway=$P" --pull never --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 256 --user "$(id -u):$(id -g)" --cpus "$cpus" --memory "$memory" --network "$NET" \
    -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_TRINO_USER -e K2_PUB_USER -e K2_RUNS_USER -e K2_CATALOG_URI -e K2_RELEASE \
    --mount "type=bind,source=$STATE/releases/$id,target=/k2/releases/$id,readonly" \
    --mount "type=bind,source=$S/principals/$writer.password,target=/run/k2-secrets/builder.password,readonly" \
    --mount "type=bind,source=$S/principals/$publisher.password,target=/run/k2-secrets/publisher.password,readonly" \
    --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
    --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
    "$IMAGE" k2-dbt build --release "$id" --product "$product" --run-id "$dag/$run" > "$RP/$run.build.out" 2> "$RP/$run.build.err"
  echo $? > "$RP/$run.build.rc"
  echo "run-path: $run build ($product, $id) exit $(cat "$RP/$run.build.rc")"
}
spark_py() {  # spark_py <script> <label>: a harness or operator script in the Spark container, on the release
  docker exec -i "$SPARK" python3 - "/k2/releases/$ID" "$ECB" < "$W/$1" > "$RP/$2.out" 2> "$RP/$2.err"
  echo $? > "$RP/$2.rc"
}
objects() { docker exec "$P-minio" mc ls -r --json local/k2-landing > "$RP/objects-$1.json"; }
claim() {  # claim <name> [args]: the probe's one line; a FAIL stops the run
  local out
  out=$(probe claim "$@" 2>&1)
  local code=$?
  printf '%s\n' "$out" | grep -E '^(PASS|FAIL|INFO) ' | tee -a "$W/claims"
  if [ $code != 0 ]; then
    grep -qE '^FAIL ' <<< "$out" || echo "FAIL $1: the probe failed: $(tail -1 <<< "$out")" | tee -a "$W/claims"
    finish "stopped at claim $1: the claims after it build on the lake it left"
  fi
}

# 1. calendars, ECB's input
build "$ID" "$CAL" rp-cal
probe state cal
claim cal
# 2. the good file: seed, load, audit, publish
probe seed "$ID" good.xml rp-good
for t in load audit publish; do task "$ID" rp-good "$t"; done
objects good; spark_py facts.py facts-good; probe state good
claim good "$ID"
# 3. the build on the published bronze
build "$ID" "$ECB" rp-good
probe state built
claim build "$ID"
# 4. the same bytes again, a new run
probe seed "$ID" good.xml rp-again
for t in load audit publish; do task "$ID" rp-again "$t"; done
build "$ID" "$ECB" rp-again
objects again; probe state again
claim again "$ID"
# 5. the corrupt file; a third batch refused behind it; the clearing step; the third's load again
probe seed "$ID" corrupt.xml rp-corrupt
for t in load audit publish; do task "$ID" rp-corrupt "$t"; done
probe state corrupt
probe seed "$ID" third.xml rp-third
task "$ID" rp-third load
objects corrupt
spark_py clear.py clear
TRY=2 task "$ID" rp-third load
spark_py facts.py facts-corrupt; probe state cleared
claim corrupt "$ID"
# 6. a failing test, its rollback, expire by the XCom list, maintain (one DAG run, pinned to claim 6's release)
build "$IDF" "$ECB" rp-fail
probe state fail
expire=$(python3 -c 'import json, sys; lines = [x for x in open(sys.argv[1]) if x.strip()]; print(json.dumps(json.loads(lines[-1])["expire"], separators=(",", ":")))' \
  "$RP/rp-fail.build.out" 2>/dev/null)
[ -n "$expire" ] && [ "$expire" != "[]" ] && task "$IDF" rp-fail expire --expire "$expire"  # factory: an empty list skips expire
task "$IDF" rp-fail maintain
probe state expired
claim expire "$ID"
# 7. the run records of every build above
claim records "$ID" "$IDF"
finish
