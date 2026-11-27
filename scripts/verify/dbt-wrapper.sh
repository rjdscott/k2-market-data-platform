#!/usr/bin/env bash
# W1-18 (phase 002): the dbt wrapper (docker/dbt-runner/k2_dbt.py) in its own image, run as the
# Airflow one-shot operator runs it (docker/airflow/dags/oneshot.py: read-only root, a 64m noexec
# /tmp, no capability, no new privileges, the host user, exactly the seven environment names, the
# four secret files and the release, nothing else), on a throwaway lake built by the real lake-init
# (scripts/lib/throwaway-lake.sh). Three releases of this tree, compiled as spine-e2e compiles one:
#
#   R1   the tree as it is: the access canary, governance.access-canary
#   R1f  R1 with one more seed row, a duplicate of key (1, north) noted w118-failed: k2_unique_key fails
#   R2   R1 with one more contract column, extra varchar, which the model fills
#   R3   R2 with extra retyped to bigint
#
# Nine runs of the one product, each claim below one PASS or FAIL line, then `<n> PASS, <m> FAIL`:
#   run1 R1   a first pass moves audited onto main and the reader view serves the five tested rows
#   run2 R1f  a failed test leaves main at the tag (rollback_to_snapshot) while a reader polling the
#             view every 50 ms through the whole run never sees a row of the failed build
#   run3 R1   killed once its started row is written; run4 R1 reconciles it, then passes
#   run5 R2   an added column: the previous tag is not null, so the commit carried both
#             requirements and passed
#   run5b R3  a retyped column narrows the real view, moves the tag, then widens it (the view's
#             version log around the table's last update), a reader polling the view every 50 ms
#             answered every time (an added column's narrowing is the view reconcile already left)
#   run6 R1f  a failed build after a schema change is restored by create-or-replace: main is a new
#             snapshot with the tag's rows and schema, and the table keeps its partitioning
#   run7 R1   passes (the tag back on four columns)
#   run8 R2   another publisher moves the tag just before the commit (the wrapper's own code in its
#             image, its commit wrapped): a real 409, exit 3, the view never widened
#   run9 R2   reconciles run8 (its audited row) and passes
#   and for every run that ran dbt: its dq_test_results rows, the drift check clean (the whole
#   release, the tags against the run records) and its XCom the last stdout line, its exit the
#   container's.
# Then the review's questions (2026-11-27), each one claim or INFO lines:
#   F2   R1 holds a product reading an input, so run1 built a product with none from k2_inputs {}
#   Q3   the image's dbt refuses classic Jinja sandbox escapes (no network, no lake)
#   Q1   q1a: a slow INSERT into the output as the builder, its client gone after 2 s: does it
#        commit? run10 reconciles whatever landed; q1b: the same, and run11's step 1 cancels it
#        (Trino's REST API as the builder) before reconcile, so no snapshot of it ever exists
#   Q2   q2a: 100 polls each of the base by tag name, by snapshot id, and of the reader view across
#        dbt's create-or-replace that drops note then retypes extra; q2b: region and note swapped
#        by renames on main, read through the view by each canary reader
#
#   bash scripts/verify/dbt-wrapper.sh        # KEEP=1 keeps the scratch directory
#
# One container job: refuses to start while a container that is not the stack's runs. Builds the
# image under a throwaway tag and removes it; the lake, the image and the scratch directory go in
# the trap. Never touches the running stack, never prints a secret (the passwords are this lake's,
# mounted by path).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
REPO=$PWD
L=scripts/lib/throwaway-lake.sh
avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
[ "$avail" -ge 8 ] || { echo "dbt-wrapper: MemAvailable ${avail} GiB, under 8 GiB: not starting" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "dbt-wrapper: no Docker" >&2; exit 2; }
others=$(docker ps --format '{{.Names}}' | grep -v '^k2-' || true)
[ -z "$others" ] || { echo "dbt-wrapper: another container job is running ($others): not starting" >&2; exit 2; }

RUN_TAG=$(openssl rand -hex 3)
P=w118-$RUN_TAG
IMAGE=dbt-runner:w118-$RUN_TAG
PRODUCT=governance.access-canary
W=$(mktemp -d "$HOME/.k2-dbt-wrapper.XXXXXX")  # under $HOME, the one place Docker Desktop shares
cleanup() {
  [ "${KEEP:-}" = 1 ] && [ -n "${STATE:-}" ] && cp -a "$STATE/w118" "$W/" 2>/dev/null
  bash "$L" down "$P" >/dev/null 2>&1
  docker rmi -f "$IMAGE" >/dev/null 2>&1
  [ "${KEEP:-}" = 1 ] && echo "dbt-wrapper: scratch kept in $W" || { chmod -R u+w "$W"; rm -rf "$W"; }
}
trap cleanup EXIT
T0=$SECONDS
die() { echo "dbt-wrapper: $*" >&2; exit 1; }

# ---- three releases of this tree ------------------------------------------------------------
k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
python3 - docker-compose.yml > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "w118/placeholder@sha256:" + "0" * 64 for n in names}))
PY
patch_none() { :; }
patch_failing() { echo "1,north,w118-failed,1440" >> contracts/governance/access-canary/seeds/event_offsets.csv; }
patch_column() {  # W118_EXTRA: the type of the column extra
  W118_EXTRA=${W118_EXTRA:-varchar} python3 - <<'PY'
import os
from pathlib import Path
typ = os.environ["W118_EXTRA"]
c = Path("contracts/governance/access-canary/events.odcs.yaml")
text = c.read_text()
column = (f"      - name: extra\n        physicalType: {typ}\n        required: false\n"
          "        description: A column scripts/verify/dbt-wrapper.sh adds, retypes and drops.\n")
assert text.count("\nroles:\n") == 1
c.write_text(text.replace("\nroles:\n", "\n" + column + "roles:\n"))
m = Path("contracts/governance/access-canary/models/events.sql")
sql = m.read_text()
assert sql.count("\nfrom ") == 1
value = "'w118-extra'" if typ == "varchar" else "7"
m.write_text(sql.replace("\nfrom ", f",\n  cast({value} as {typ}) as extra\nfrom "))
PY
}
patch_retype() { W118_EXTRA=bigint patch_column; }
compile() {  # compile <name> <patch>: the release's directory on stdout
  local d=$W/$1
  mkdir -p "$d/clone"
  git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$d/clone"
  (cd "$d/clone" && "$2") || return 1
  git -C "$d/clone" init -q -b main && git -C "$d/clone" add -A
  git -C "$d/clone" -c user.name=w118 -c user.email=w118@example.org commit -q -m "dbt-wrapper: $1"
  git -C "$d/clone" -c user.name=w118 -c user.email=w118@example.org tag -a "v0.0.0-w118.$1" -m dbt-wrapper
  "${k2gen[@]}" docset --tag "v0.0.0-w118.$1" --repo "$d/clone" --out "$d/set" >> "$W/compile.log" 2>&1 &&
    "${k2gen[@]}" check --set "$d/set" --previous none >> "$W/compile.log" 2>&1 &&
    "${k2gen[@]}" release --set "$d/set" --previous none --images "$W/images.json" --out "$d/releases" 2>>"$W/compile.log" \
      | tail -1 | xargs basename | sed "s|^|$d/releases/|"
}
R1=$(compile r1 patch_none) && R1F=$(compile r1f patch_failing) && R2=$(compile r2 patch_column) && R3=$(compile r3 patch_retype) \
  || { tail -20 "$W/compile.log" >&2; die "a release did not compile ($W/compile.log)"; }
ID1=$(basename "$R1") ID1F=$(basename "$R1F") ID2=$(basename "$R2") ID3=$(basename "$R3")
echo "dbt-wrapper: releases $ID1, $ID1F, $ID2, $ID3"

# ---- the image, and the lake ----------------------------------------------------------------
docker build -q -f docker/dbt-runner/Dockerfile -t "$IMAGE" . > "$W/build.log" 2>&1 \
  || { tail -20 "$W/build.log" >&2; die "the dbt-runner image did not build"; }
Q3=$W/q3-out; mkdir -p "$Q3"
q3() {  # q3 <n> <jinja>: one model holding a sandbox escape, compiled by the image's dbt with no network (review Q3)
  local d=$W/q3-$1; mkdir -p "$d/models"
  printf 'name: w118q3\nversion: "1.0"\nprofile: q3\nconfig-version: 2\n' > "$d/dbt_project.yml"
  printf 'q3:\n  target: t\n  outputs:\n    t: {type: trino, method: none, http_scheme: http, host: 127.0.0.1, port: 1, user: x, database: x, schema: x, threads: 1}\n' > "$d/profiles.yml"
  printf 'select %s as x\n' "$2" > "$d/models/m.sql"
  docker run --rm --label "k2.throwaway=$P" --network none --read-only --tmpfs /tmp:rw,size=64m --user "$(id -u):$(id -g)" --pull never \
    -e HOME=/tmp -e DBT_SEND_ANONYMOUS_USAGE_STATS=false -v "$d:/p:ro" --entrypoint dbt "$IMAGE" \
    compile --project-dir /p --profiles-dir /p --target-path /tmp/t --log-path /tmp/l --no-partial-parse > "$Q3/q3-$1.out" 2>&1
  echo $? > "$Q3/q3-$1.rc"
}
# shellcheck disable=SC2016
{ q3 1 "'{{ cycler.__init__.__globals__.os.popen('id').read() }}'"
  q3 2 "'{{ ''.__class__.__mro__[1].__subclasses__() }}'"
  q3 3 "'{{ self._TemplateReference__context.cycler.__init__.__globals__.os.popen('id').read() }}'"
  q3 4 "'{{ joiner.__init__.__globals__.os.popen('id').read() }}'"; }
bash "$L" up "$P" "$R1" > "$W/up.log" 2>&1 || { tail -20 "$W/up.log" >&2; die "the throwaway lake did not start"; }
STATE=$(bash "$L" state "$P")
cp -a "$R1F" "$R2" "$R3" "$STATE/releases/"
mkdir -p "$STATE/w118"
cp "$Q3"/q3-* "$STATE/w118/"
S=$STATE/secrets/k2
for f in principals/dbt_access_canary.password principals/pub_access_canary.password principals/svc.runs.password tls/ca.pem; do
  [ -f "$S/$f" ] || die "the lake made no $f"
done

# The tools side: every read of the lake, and the claims (/work is $STATE).
cat > "$W/probe.py" <<'PY'
import base64, json, sys, time, urllib.request
from collections import Counter
from pathlib import Path
sys.path[:0] = ["/src/scripts", "/src/scripts/verify"]
import access_verify as av
import catalog_drift as cd
from k2gen import model
from k2gen.model import quote_ident, quote_relation, sql_literal

trino, env = av.connect(Path("/work/env.json"))
LK = f"{env['lakekeeper']}/catalog"
OUT, VIEW, READER, BUILDER = "governance_build.access_canary__base.events", "governance.access_canary.events", "p.canary-full", "dbt_access_canary"
PRODUCT = "governance.access-canary"
RECORDS = "governance_build.run_records.run_record"
READER_BASE = "governance.access_canary__base.events"  # the base as the view reads it, through the reader catalog
D = Path("/work/w118")
cmd, args = sys.argv[1], sys.argv[2:]


def records(run=None):
    rows = trino.ok("svc.runs", f"SELECT run_id, event, table_name, snapshot_before, snapshot_after FROM {RECORDS} "
                                "WHERE product_id = 'governance.access-canary' ORDER BY recorded_at")
    return [dict(zip(("run", "event", "table", "before", "after"), r)) for r in rows if run is None or r[0] == f"access_canary/{run}"]


def state():
    c = cd.Catalog(LK, "governance")
    meta = c.table("access_canary__base", "events") or {}
    refs = meta.get("refs") or {}
    main, tag = (refs.get("main") or {}).get("snapshot-id"), (refs.get("audited") or {}).get("snapshot-id")
    vmeta = c.view("access_canary", "events")
    reader = trino.run(READER, f"SELECT * FROM {quote_relation(VIEW)} ORDER BY id, region")
    names = [c for c, _ in reader.columns]
    versions = {v["version-id"]: v["representations"][0]["sql"] for v in (vmeta or {}).get("versions", [])}
    spec = next((s for s in meta.get("partition-specs", []) if s["spec-id"] == meta.get("default-spec-id")), {})
    rows = lambda at: trino.ok(BUILDER, f"SELECT id, region, note FROM {quote_relation(OUT)}{at} ORDER BY id, region")
    return {"main": main, "tag": tag, "last_updated": meta.get("last-updated-ms"),
            "schema": cd.columns_at(meta, main) if main else [],
            "current_schema": [f["name"] for f in next((s for s in meta.get("schemas", []) if s["schema-id"] == meta.get("current-schema-id")), {"fields": []})["fields"]],
            "tag_cols": cd.columns_at(meta, tag) if tag else [],
            "partitioning": [(f["name"], f["transform"]) for f in spec.get("fields", [])],
            # Time order: Lakekeeper lists the log by version, and Iceberg reuses an identical version.
            "view_log": sorted(({"ts": e["timestamp-ms"], "sql": versions[e["version-id"]]} for e in (vmeta or {}).get("version-log", [])),
                               key=lambda e: e["ts"]),
            "reader_cols": names, "reader_types": dict(reader.columns), "reader_error": reader.error,
            "reader_rows": [[r[names.index(c)] for c in ("id", "region", "note")] for r in reader.rows] if not reader.error else [],
            "main_rows": rows("") if main else [], "tag_rows": rows(" FOR VERSION AS OF 'audited'") if tag else []}


def newest_audited(table):
    r = trino.ok("svc.runs", f"SELECT snapshot_after FROM {RECORDS} WHERE table_name = {sql_literal(table)} "
                             "AND event = 'audited' AND snapshot_after IS NOT NULL ORDER BY recorded_at DESC LIMIT 1")
    return int(r[0][0]) if r else None


if cmd == "setup":  # what deploy makes before a product's first run: the namespaces, the run-record table
    release = Path("/work/releases") / args[0]
    for w in json.loads((release / model.STORAGE_PLAN).read_text())["warehouses"]:
        base = cd.Catalog(LK, w["name"]).base
        for ns in w["namespaces"]:
            st, _ = cd.call("POST", f"{base}/namespaces", {"namespace": [ns]})
            assert st in (200, 409), f"namespace {w['name']}.{ns}: HTTP {st}"
    for ddl in sorted((release / "compiled/lake/ddl/trino").glob("*.sql")):
        trino.ok("svc.platform", "\n".join(x for x in ddl.read_text().splitlines() if not x.startswith("--")).strip().rstrip(";"))
    print("dbt-wrapper: namespaces and the run-record table made")
elif cmd == "state":
    (D / f"state-{args[0]}.json").write_text(json.dumps(state(), indent=1, default=str))
elif cmd == "drift":  # the whole release, each tag held to the run records (release-verify's scope)
    found = cd.drift(LK, cd.expected(Path("/work/releases") / args[0]), newest_audited=newest_audited)
    (D / f"drift-{args[1]}.json").write_text(json.dumps(found, indent=1))
elif cmd == "wait-started":
    t0 = time.monotonic()
    while not any(r["event"] == "started" for r in records(args[0])):
        if time.monotonic() - t0 > 300:
            sys.exit("no started row in 300 s")
        time.sleep(0.2)
elif cmd == "poll":  # a reader polls the view every 50 ms until the stop file appears
    polls, errors, bad, stop = 0, [], [], D / f"stop-{args[0]}"
    while not stop.exists():
        a = trino.run(READER, f"SELECT id, region, note FROM {quote_relation(VIEW)}")
        polls += 1
        if a.error:
            errors.append(a.error)
        elif len(a.rows) != 5 or any(r[2] == "w118-failed" or r[0] not in range(1, 6) for r in a.rows):
            bad.append(a.rows)
        time.sleep(0.05)
    (D / f"poll-{args[0]}.json").write_text(json.dumps({"polls": polls, "errors": errors[:5], "n_errors": len(errors),
                                                        "bad": bad[:3], "n_bad": len(bad)}))
elif cmd == "facts":  # what the fakes assumed, as the real lake answers it
    base = trino.run(BUILDER, f"SELECT * FROM {quote_relation(OUT)} LIMIT 0")
    view = trino.run(READER, f"SELECT * FROM {quote_relation(VIEW)} LIMIT 0")
    missing = trino.run(BUILDER, 'SELECT * FROM "governance_build"."access_canary__base"."w118_absent$refs"')
    refs = trino.run(BUILDER, f"SELECT name, type, snapshot_id FROM {quote_relation(OUT + '$refs')}")
    (D / "facts.json").write_text(json.dumps({"base types (the wrapper's columns())": dict(base.columns),
                                              "reader view types": dict(view.columns),
                                              "a missing table's $refs": f"{missing.error}: {missing.message[:120]}",
                                              "$refs rows": refs.rows, "$refs types": dict(refs.columns)}, indent=1))
elif cmd == "orphan":  # orphan <name> <k>: a slow INSERT into the output as the builder, its client gone after 2 s (Q1)
    name, k = args[0], int(args[1])
    sql = (f"INSERT INTO {quote_relation(OUT)} (id, region, note, event_ts) SELECT count_if((x + y + z) % 7 = 0), 'north', "
           f"{sql_literal('w118-' + name)}, current_timestamp FROM UNNEST(sequence(1, 10000)) a(x) "
           f"CROSS JOIN UNNEST(sequence(1, 10000)) b(y) CROSS JOIN UNNEST(sequence(1, {k})) c(z)")
    auth = "Basic " + base64.b64encode(f"{BUILDER}:{trino.password(BUILDER)}".encode()).decode()
    req = urllib.request.Request(f"{trino.url}/v1/statement", sql.encode(), {"Authorization": auth, "X-Trino-User": BUILDER})
    qid, t0 = None, time.monotonic()
    while req is not None and time.monotonic() - t0 < 2:  # it polls (Trino dispatches on a poll), then goes: no cancel is sent
        with urllib.request.urlopen(req, context=trino.ctx, timeout=60) as r:
            doc = json.load(r)
        qid = doc.get("id", qid)
        if "error" in doc:
            sys.exit(f"the orphan {name} failed at once: {doc['error'].get('message')}")
        req = urllib.request.Request(doc["nextUri"], headers={"Authorization": auth}) if doc.get("nextUri") else None
    (D / f"orphan-{name}.json").write_text(json.dumps({"query_id": qid, "k": k, "running_when_left": req is not None}))
    print(f"dbt-wrapper: orphan {name} is {qid}; its client left after 2 s", flush=True)
elif cmd == "orphan-end":  # orphan-end <name>: as svc.platform, the orphan's end (420 s at most), then any snapshot it committed
    f = D / f"orphan-{args[0]}.json"
    o = json.loads(f.read_text())
    t0, row = time.monotonic(), None
    while time.monotonic() - t0 < 420:
        rows = trino.ok("svc.platform", "SELECT state, error_code, date_diff('millisecond', created, \"end\") FROM system.runtime.queries "
                                        f"WHERE query_id = {sql_literal(o['query_id'])}")
        row = rows[0] if rows else None
        if row and row[0] in ("FINISHED", "FAILED"):
            break
        time.sleep(2)
    snaps = trino.ok(BUILDER, f"SELECT snapshot_id, CAST(committed_at AS varchar) FROM {quote_relation(OUT + '$snapshots')} "
                              f"WHERE element_at(summary, 'trino_query_id') = {sql_literal(o['query_id'])}")
    o.update(state=row and row[0], error=row and row[1], ms=row and row[2], snapshots=snaps)
    f.write_text(json.dumps(o))
    print(f"dbt-wrapper: orphan {args[0]} ended {o['state']} {o['error'] or ''} after {o['ms']} ms; its snapshots {snaps}", flush=True)
elif cmd == "q2-poll":  # q2-poll <n>: n polls of each read of the tag while the shell changes main (Q2a)
    tag = int(trino.ok("pub_access_canary", f"SELECT snapshot_id FROM {quote_relation(READER_BASE + '$refs')} WHERE name = 'audited'")[0][0])
    forms = {"base by tag name, publisher (the view's read)": ("pub_access_canary", f"SELECT * FROM {quote_relation(READER_BASE)} FOR VERSION AS OF 'audited' LIMIT 0"),
             "base by snapshot id, publisher": ("pub_access_canary", f"SELECT * FROM {quote_relation(READER_BASE)} FOR VERSION AS OF BIGINT '{tag}' LIMIT 0"),
             "base by tag name, builder (build catalog)": (BUILDER, f"SELECT * FROM {quote_relation(OUT)} FOR VERSION AS OF 'audited' LIMIT 0"),
             "reader view, p.canary-full": (READER, f"SELECT * FROM {quote_relation(VIEW)} LIMIT 0")}
    polls = []
    for i in range(int(args[0])):
        for form, (user, sql) in forms.items():
            a = trino.run(user, sql)
            polls.append({"i": i, "t": time.time(), "form": form,
                          "got": f"{a.error}: {a.message[:160]}" if a.error else ",".join(c for c, _ in a.columns)})
        time.sleep(0.05)
    (D / "q2-polls.json").write_text(json.dumps({"tag": tag, "polls": polls}))
elif cmd == "q2-change":  # as the builder, dbt's contract table twice: CREATE OR REPLACE with the new columns, then INSERT (Q2a)
    q, marks = quote_relation(OUT), []
    for step, extra, value in (("drop note", "varchar", "extra"), ("retype extra", "bigint", "7")):
        for what, sql in (("create or replace", f"CREATE OR REPLACE TABLE {q} (id bigint, region varchar, event_ts timestamp(6) with time zone, "
                                                f"extra {extra}) WITH (partitioning = ARRAY['region'])"),
                          ("insert", f"INSERT INTO {q} SELECT id, region, event_ts, {value} FROM {q} FOR VERSION AS OF 'audited'")):
            a = trino.run(BUILDER, sql)
            marks.append({"t": time.time(), "step": f"{step}: {what}", "got": a.says()})
        time.sleep(3)
    (D / "q2-change.json").write_text(json.dumps(marks))
elif cmd == "q2-swap":  # main back to the tag's rows and schema, then region and note swapped by renames as the builder (Q2b)
    q = quote_relation(OUT)
    tag = int(trino.ok(BUILDER, f"SELECT snapshot_id FROM {quote_relation(OUT + '$refs')} WHERE name = 'audited'")[0][0])
    restore = trino.run(BUILDER, f"CREATE OR REPLACE TABLE {q} WITH (partitioning = ARRAY['region']) AS SELECT * FROM {q} FOR VERSION AS OF BIGINT '{tag}'")

    def reads():
        out = {}
        for u in ("p.canary-delayed", "p.canary-scoped", "p.canary-full"):
            a = trino.run(u, f"SELECT id, region, note FROM {quote_relation(VIEW)} ORDER BY id, region")
            out[u] = a.says() if a.error else a.rows
        for form, at in (("tag name", "'audited'"), ("snapshot id", f"BIGINT '{tag}'")):
            a = trino.run("pub_access_canary", f"SELECT * FROM {quote_relation(READER_BASE)} FOR VERSION AS OF {at} ORDER BY 1 LIMIT 2")
            out[f"publisher, base by {form}"] = a.says() if a.error else [[c for c, _ in a.columns], *a.rows]
        return out
    before = reads()
    renames = [trino.run(BUILDER, f"ALTER TABLE {q} RENAME COLUMN {quote_ident(a)} TO {quote_ident(b)}").says()
               for a, b in (("region", "w118_tmp"), ("note", "region"), ("w118_tmp", "note"))]
    (D / "q2-swap.json").write_text(json.dumps({"restore": restore.says(), "renames": renames, "before": before, "after": reads()}, default=str))
elif cmd == "claims":
    s = {p.stem.removeprefix("state-"): json.loads(p.read_text()) for p in D.glob("state-*.json")}
    drift = {p.stem.removeprefix("drift-"): json.loads(p.read_text()) for p in D.glob("drift-*.json")}
    poll = {p.stem.removeprefix("poll-"): json.loads(p.read_text()) for p in D.glob("poll-*.json")}
    rc = {p.stem: int(p.read_text()) for p in D.glob("run*.rc")}
    err = {p.stem: p.read_text() for p in D.glob("run*.err")}
    rec = records()
    for k, v in json.loads((D / "facts.json").read_text()).items():
        print(f"INFO {k}: {v}")
    dq = dict(trino.ok(BUILDER, "SELECT run_id, count(*) FROM governance_build.access_canary__base.dq_test_results GROUP BY run_id"))
    fails = 0

    def claim(name, ok, detail):
        global fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    def ev(run):
        return [r["event"] for r in rec if r["run"] == f"access_canary/{run}"]

    C1, C2 = ["id", "region", "note", "event_ts"], ["id", "region", "note", "event_ts", "extra"]
    tested = [[i, r, f"canary-note-{i}"] for i, r in ((1, "north"), (2, "north"), (3, "north"), (4, "south"), (5, "south"))]
    a = s["run1"]
    claim("a first pass moves audited onto main and the view serves the tested rows",
          rc.get("run1") == 0 and a["tag"] == a["main"] and a["reader_rows"] == tested and a["reader_cols"] == C1,
          f"exit {rc.get('run1')}, tag {a['tag']}, main {a['main']}, {len(a['reader_rows'])} rows, {a['reader_cols']}")
    b, p2 = s["run2"], poll.get("run2", {})
    claim("a failed test leaves main at the tag, and a reader polling every 50 ms never sees its rows",
          rc.get("run2") == 1 and b["tag"] == a["tag"] == b["main"] and b["reader_rows"] == tested
          and p2.get("polls", 0) >= 20 and p2.get("n_bad") == 0 and p2.get("n_errors") == 0,
          f"exit {rc.get('run2')}, tag {b['tag']} (was {a['tag']}), main {b['main']}; {p2.get('polls')} polls, "
          f"{p2.get('n_bad')} with an untested row, {p2.get('n_errors')} errors {p2.get('errors')}")
    c = s["run4"]
    claim("a run killed after started is reconciled by the next, which passes",
          "started" in ev("run3") and ev("run3")[-1] in ("reconciled", "audited", "rolled_back")
          and rc.get("run4") == 0 and c["tag"] == c["main"],
          f"run3's records {ev('run3')}; run4 exit {rc.get('run4')}, tag {c['tag']} main {c['main']}")
    d = s["run5"]
    claim("the commit's two requirements with a non-null previous tag pass on the real catalog",
          rc.get("run5") == 0 and c["tag"] is not None and d["tag"] == d["main"] != c["tag"] and d["reader_cols"] == C2,
          f"the tag moved from {c['tag']} to {d['tag']} (main {d['main']}), view {d['reader_cols']}")
    r, p5 = s["run5b"], poll.get("run5b", {})
    log = r["view_log"]
    narrow, widen = (log[-2], log[-1]) if len(log) >= 2 else ({}, {})
    claim("a retyped column narrows the real view, moves the tag, then widens it, every poll answered",
          rc.get("run5b") == 0 and r["reader_cols"] == C2 and r["reader_types"].get("extra") == "bigint"
          and '"extra"' not in narrow.get("sql", '"extra"') and '"extra"' in widen.get("sql", "")
          and narrow.get("ts", 0) <= r["last_updated"] <= widen.get("ts", 0)
          and p5.get("n_errors") == 0 and p5.get("n_bad") == 0 and p5.get("polls", 0) >= 20,
          f"exit {rc.get('run5b')}; narrow at {narrow.get('ts')}, tag commit at {r['last_updated']}, widen at {widen.get('ts')}; "
          f"view {r['reader_types']}; {p5.get('polls')} polls, {p5.get('n_errors')} errors {p5.get('errors')}, {p5.get('n_bad')} wrong")
    d = r
    e = s["run6"]
    rolled = [r for r in rec if r["run"] == "access_canary/run6" and r["event"] == "rolled_back"]
    claim("a failed build after a schema change is restored by create-or-replace: the tag's rows and schema",
          rc.get("run6") == 1 and e["tag"] == d["tag"] and e["main"] not in (None, e["tag"]) and e["schema"] == e["tag_cols"] == C2
          and e["current_schema"] == C2 and e["main_rows"] == e["tag_rows"] and bool(rolled) and rolled[-1]["after"] == e["main"],
          f"exit {rc.get('run6')}, tag {e['tag']}, main {e['main']}, schema {e['current_schema']}, "
          f"rolled_back after {rolled[-1]['after'] if rolled else None}")
    claim("the create-or-replace restore keeps the table's partitioning",
          e["partitioning"] == d["partitioning"] == [["region", "identity"]], f"before {d['partitioning']}, after {e['partitioning']}")
    h, g = s["run8"], s["run7"]
    claim("a tag moved by another publisher before the commit gets a real 409: exit 3, the view never widened",
          rc.get("run8") == 3 and "HTTP 409" in err.get("run8", "") and h["tag"] != g["tag"] and h["reader_cols"] == C1
          and "audited" not in ev("run8")[:-1],
          f"exit {rc.get('run8')}, tag {g['tag']} to {h['tag']} by the other publisher, view {h['reader_cols']}")
    i = s["run9"]
    claim("the next run reconciles it and passes", rc.get("run9") == 0 and "audited" in ev("run8") and i["reader_cols"] == C2
          and i["tag"] == i["main"], f"run8's records {ev('run8')}; run9 exit {rc.get('run9')}, view {i['reader_cols']}")
    ran = ["run1", "run2", "run4", "run5", "run5b", "run6", "run7", "run8", "run9"]
    claim("every run that ran dbt wrote its dq_test_results rows", all(dq.get(f"access_canary/{r}", 0) > 0 for r in ran),
          {r: dq.get(f"access_canary/{r}", 0) for r in ran})
    claim("the drift check is clean after every run, its tags held to the run records",
          set(drift) == {"run1", "run2", "run4", "run5", "run5b", "run6", "run7", "run9"} and all(not f["differences"] and not f["skipped"] for f in drift.values()),
          {k: f["differences"][:2] or "clean" for k, f in sorted(drift.items())})
    xcom = {}
    for r in ran:
        lines = [x for x in (D / f"{r}.out").read_text().splitlines() if x.strip()]
        try:
            x = json.loads(lines[-1])
            xcom[r] = isinstance(x.get("expire"), list) and x.get("exit") == rc[r]
        except (ValueError, IndexError, KeyError):
            xcom[r] = False
    claim("the XCom is the last stdout line of every run, its exit the container's", all(xcom.values()), xcom)
    # F2: dbt parses every product's models for any --selector, and the wrapper passes k2_inputs {} for
    # a product with no input; run1 proves that case only while R1 also holds a model reading an input.
    r1 = Path("/work/releases") / args[0]
    readers = sorted({m.parent.name for m in (r1 / "compiled/dbt/models").rglob("*.sql")
                      if "k2_input(" in m.read_text() and m.parent.name != "access_canary"})
    own = json.loads((r1 / model.pipeline_path(PRODUCT)).read_text()).get("inputs")
    claim("a product with no input builds from a release in which another product reads one through k2_input",
          rc.get("run1") == 0 and own == [] and bool(readers), f"run1 exit {rc.get('run1')}, its inputs {own}, readers {readers}")
    # Q3: the image's dbt and classic Jinja sandbox escapes
    q3 = {p.stem: (int(p.with_suffix(".rc").read_text()), p.read_text()) for p in sorted(D.glob("q3-*.out"))}
    for k, (code, out) in q3.items():
        print(f"INFO {k}: exit {code}; {next((x.strip() for x in out.splitlines() if 'unsafe' in x or 'Security' in x or 'uid=' in x), out[-200:])}")
    claim("Q3 dbt's Jinja sandbox refuses each escape: an error, never the command's output",
          len(q3) == 4 and all(code != 0 and "uid=" not in out and ("unsafe" in out or "SecurityError" in out) for code, out in q3.values()),
          {k: code for k, (code, _) in q3.items()})
    # Q1: an orphaned build query, observed (q1a), then cancelled by the next run's step 1 (q1b)
    oa, ob = (json.loads((D / f"orphan-{n}.json").read_text()) for n in ("q1a", "q1b"))
    print(f"INFO Q1 q1a, observed: {oa}")
    print(f"INFO Q1 q1b, run11's kill: {ob}")
    claim("Q1 a builder's query runs on after its client is gone (q1a)", oa["running_when_left"] and oa["state"] in ("FINISHED", "FAILED"),
          f"{oa['query_id']} ended {oa['state']} {oa['error'] or ''} {oa['ms']} ms after it started; {len(oa['snapshots'])} snapshot(s) of it in events")
    claim("Q1 run10 reconciles whatever the orphan left and passes", rc.get("run10") == 0, f"run10 exit {rc.get('run10')}, its records {ev('run10')}")
    claim("Q1 run11's step 1 cancels the orphan before reconcile, and no snapshot of it exists after the run",
          ob["running_when_left"] and rc.get("run11") == 0 and "left by an earlier run: killed" in err.get("run11", "")
          and ob["state"] == "FAILED" and ob["snapshots"] == [],
          f"q1b {ob['query_id']} ended {ob['state']} {ob['error'] or ''} after {ob['ms']} ms; run11 exit {rc.get('run11')}; snapshots of it {ob['snapshots']}")
    # Q2a: reads of the tag across a create-or-replace that drops then retypes a column on main
    q2 = json.loads((D / "q2-polls.json").read_text())
    for m in json.loads((D / "q2-change.json").read_text()):
        print(f"INFO Q2a change at {m['t']:.2f}: {m['step']}: {m['got']}")
    tagged = ",".join(C2)
    by_form = {}
    for poll in q2["polls"]:
        by_form.setdefault(poll["form"], Counter())[poll["got"]] += 1
    for form, seen in by_form.items():
        first = {g: min(p["t"] for p in q2["polls"] if p["form"] == form and p["got"] == g) for g in seen}
        print(f"INFO Q2a {form}: " + "; ".join(f"{n} x {g!r} (first at {first[g]:.2f})" for g, n in seen.items()))
    claim("Q2a the tag read by name, the view's read, returns the tagged snapshot's columns on every poll",
          set(by_form.get("base by tag name, publisher (the view's read)", {})) == {tagged},
          dict(by_form.get("base by tag name, publisher (the view's read)", {})))
    claim("Q2a the reader view answers with the tagged columns on every poll", set(by_form.get("reader view, p.canary-full", {})) == {tagged},
          dict(by_form.get("reader view, p.canary-full", {})))
    # Q2b: region and note swapped on main by renames; the view reads the tag by name
    sw = json.loads((D / "q2-swap.json").read_text())
    print(f"INFO Q2b restore {sw['restore']}; renames {sw['renames']}")
    for k in sw["after"]:
        print(f"INFO Q2b {k}: before {sw['before'][k]}; after {sw['after'][k]}")
    delayed, scoped = sw["after"]["p.canary-delayed"], sw["after"]["p.canary-scoped"]
    claim("Q2b after the swap no reader sees data under a swapped name: the delayed reader no note, the scoped reader no south row",
          all(isinstance(x, list) for x in (delayed, scoped)) and sw["after"] == sw["before"]
          and not any("canary-note" in str(r[1]) or r[2] is not None for r in delayed) and all(r[1] == "north" for r in scoped),
          f"delayed {delayed}; scoped {scoped}")
    sys.exit(1 if fails else 0)
PY
probe() { bash "$L" py "$P" - "$@" < "$W/probe.py"; }

# The run as oneshot.py starts it, and the forced 409's harness: the wrapper's own code in its
# image, with one other publisher's tag move just before its commit.
cat > "$W/harness.py" <<'PY'
import sys
sys.path.insert(0, "/opt/k2/dbt-runner")
import catalog_drift
import k2_dbt

real = k2_dbt.Live.commit


def commit(self, warehouse, changes):
    for ch in changes:  # another publisher: the tag onto main, with its own current expectation
        ns, name = ch["identifier"]["namespace"][0], ch["identifier"]["name"]
        refs = self.cat().table(ns, name)["refs"]
        body = {"requirements": [{"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": refs["audited"]["snapshot-id"]}],
                "updates": [{"action": "set-snapshot-ref", "ref-name": "audited", "type": "tag", "snapshot-id": refs["main"]["snapshot-id"]}]}
        status, _ = catalog_drift.call("POST", f"{self.cat().base}/namespaces/{ns}/tables/{name}", body)
        print(f"dbt-wrapper harness: the other publisher's tag move: HTTP {status}", file=sys.stderr)
    return real(self, warehouse, changes)


k2_dbt.Live.commit = commit
sys.exit(k2_dbt.main(sys.argv[1:]))
PY
export K2_TRINO_HOST=trino K2_TRINO_INTERNAL_PORT=8443 K2_TRINO_USER=dbt_access_canary K2_PUB_USER=pub_access_canary \
  K2_RUNS_USER=svc.runs K2_CATALOG_URI=http://lakekeeper:8181/catalog
oneshot() {  # oneshot <release id> <run> [extra flags for the container ...] -- <command ...>
  local id=$1 run=$2; shift 2
  local flags=(); while [ "$1" != -- ]; do flags+=("$1"); shift; done; shift
  K2_RELEASE=$id timeout 900 docker run --rm "${flags[@]}" --name "$P-dbt-$run" --label "k2.throwaway=$P" --network "$P-net" --pull never \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
    --user "$(id -u):$(id -g)" --cpus 0.5 --memory 768M \
    -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_TRINO_USER -e K2_PUB_USER -e K2_RUNS_USER -e K2_CATALOG_URI -e K2_RELEASE \
    --mount "type=bind,source=$STATE/releases/$id,target=/k2/releases/$id,readonly" \
    --mount "type=bind,source=$S/principals/dbt_access_canary.password,target=/run/k2-secrets/builder.password,readonly" \
    --mount "type=bind,source=$S/principals/pub_access_canary.password,target=/run/k2-secrets/publisher.password,readonly" \
    --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
    --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
    "$IMAGE" "$@"
}
build_args() { echo build --release "$1" --product "$PRODUCT" --run-id "access_canary/$2"; }
run() {  # run <release id> <run> [poll]: one wrapper run, its stdout, stderr and exit kept
  local poller=
  if [ "${3:-}" = poll ]; then probe poll "$2" & poller=$!; sleep 3; fi
  # shellcheck disable=SC2046
  oneshot "$1" "$2" -- k2-dbt $(build_args "$1" "$2") > "$STATE/w118/$2.out" 2> "$STATE/w118/$2.err"
  echo $? > "$STATE/w118/$2.rc"
  if [ -n "$poller" ]; then touch "$STATE/w118/stop-$2"; wait "$poller"; fi
  echo "dbt-wrapper: $2 ($1) exit $(cat "$STATE/w118/$2.rc")"
  probe state "$2"
}

probe setup "$ID1" || die "the lake's namespaces or run-record table could not be made"
run "$ID1" run1;            probe drift "$ID1" run1
run "$ID1F" run2 poll;      probe drift "$ID1F" run2
# run3: killed once its started row is in the records, inside its dbt build
# shellcheck disable=SC2046
oneshot "$ID1" run3 -d -- k2-dbt $(build_args "$ID1" run3) > /dev/null
probe wait-started run3 && docker kill "$P-dbt-run3" > /dev/null && echo "dbt-wrapper: run3 killed after started"
run "$ID1" run4;            probe drift "$ID1" run4
run "$ID2" run5;            probe drift "$ID2" run5
run "$ID3" run5b poll;      probe drift "$ID3" run5b
run "$ID1F" run6;           probe drift "$ID1F" run6
run "$ID1" run7;            probe drift "$ID1" run7
# shellcheck disable=SC2046
oneshot "$ID2" run8 -i -- python - $(build_args "$ID2" run8) < "$W/harness.py" > "$STATE/w118/run8.out" 2> "$STATE/w118/run8.err"
echo $? > "$STATE/w118/run8.rc"; echo "dbt-wrapper: run8 ($ID2, another publisher's move) exit $(cat "$STATE/w118/run8.rc")"
probe state run8
run "$ID2" run9;            probe drift "$ID2" run9

probe facts

# Q1: an orphaned build query. q1a observed; run10 reconciles what it left; q1b sized from q1a to
# run about 90 s, and run11 started at once, whose step 1 must cancel it.
probe orphan q1a 20 && probe orphan-end q1a
run "$ID2" run10
kb=$(python3 -c 'import json, sys; o = json.load(open(sys.argv[1])); print(max(5, min(400, round(o["k"] * 90000 / max(o["ms"] or 1, 1)))))' \
  "$STATE/w118/orphan-q1a.json")
probe orphan q1b "$kb" && run "$ID2" run11
probe orphan-end q1b

# Q2: reads of the tag across dbt's create-or-replace on main, then the rename swap
probe q2-poll 100 & poller=$!
sleep 4; probe q2-change; wait "$poller"
probe q2-swap
probe claims "$ID1" | tee "$W/claims"
fail=$(grep -c '^FAIL' "$W/claims"); pass=$(grep -c '^PASS' "$W/claims")
echo "dbt-wrapper: $pass PASS, $fail FAIL in $((SECONDS - T0)) s"
[ "$fail" = 0 ] && [ "$pass" -gt 0 ]
