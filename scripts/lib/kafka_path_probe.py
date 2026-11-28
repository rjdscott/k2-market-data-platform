#!/usr/bin/env python3
"""scripts/verify/kafka-path.sh's steps. Three run in the throwaway's Spark container on the
release's own code, fed on stdin (`python3 - <command> /k2/releases/<id> ...`); `rebuild` and
`claim` run on the host, `claim` over what the others printed.

    facts   <release> <product>                 the topic, the landing, bronze and audit_checks as
                                                JSON: offsets and counts, every snapshot's summary,
                                                the data files' paths, the key variables' NAMES
    corrupt <release> <product>                 one frame onto the topic: the Confluent header of the
                                                registered RawMessage id and a body that is no Avro
    kill    <before|after> <release> <argv ...> connector.py <argv> with its driver SIGKILLed at
                                                SparkLanding.land (before the landing commit) or at
                                                kafka.stage (after it, before staging): no finally,
                                                no result line, as a container OOM-kill leaves it
    cycles  <release> <product> <dag> <n>       group c's small files: n runs in one Spark application, each
                                                three of the topic's own trade frames produced again
                                                and kafka.py's load, audit and publish as the DAG's
                                                tasks call them, so each commit adds small files
    expire  <release> <product> <days>          maintain.expire_snapshots(), the default pass's expiry
                                                step, at now + days: every snapshot past retention, so
                                                only retain_last keeps one (the pass under the DAG
                                                expires none of a lake minutes old). Its orphan step is
                                                not run so: Iceberg's remove_orphan_files refuses an
                                                older_than under 24 hours before now
    rebuild <scenario> <rp dir> <release> <product>  on the host: scripts/rebuild.py's rebuild() against
                                                this lake (rebuild_run says what is real and what is faked);
                                                its JSON line
    claim   <name> <rp dir>                     one PASS or FAIL line (and INFO lines) for a claim
Synthetic data only; never a key's value.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def lake_code(release: str) -> None:
    sys.path.insert(0, release + "/platform/docker/lake")


def pipeline(release: str, product: str) -> dict:
    return json.loads((Path(release) / "compiled/products" / product / "pipeline.json").read_text())


def facts(release: str, product: str) -> dict:
    import urllib.request

    lake_code(release)
    from catalog import broker_offsets, fetch_schema
    from pyspark.sql import functions as F
    from pyspark.sql.avro.functions import from_avro

    import wire
    from spark_conf import SPARK_KEY, lake_session

    p = pipeline(release, product)
    c, landing, bronze, checks = p["connector"], p["landing"]["table"], p["bronze"][0]["table"], p["bronze"][0]["checks"]
    spark = lake_session("kafka-path-facts", driver_memory="768m")
    try:
        def q(sql):
            return [list(r) for r in spark.sql(sql).collect()]

        def table(t, at="main"):
            refs = dict(q(f"SELECT name, snapshot_id FROM {t}.refs"))
            out = {"refs": refs, "snapshots": q(f"SELECT snapshot_id, parent_id, committed_at, operation, summary "
                                                f"FROM {t}.snapshots ORDER BY committed_at"),
                   "files": [r[0] for r in q(f"SELECT file_path FROM {t}.all_data_files")],
                   "live": [r[0] for r in q(f"SELECT file_path FROM {t}.data_files")]}
            for ref, sid in refs.items():
                out[ref] = q(f"SELECT kafka_partition, kafka_offset FROM {t} VERSION AS OF {int(sid)} "
                             "ORDER BY kafka_partition, kafka_offset")
            return out

        envelope = ("schema_id", "exchange", "stream", "symbol", "recv_ts_ns", "conn_id", "conn_msg_seq")
        rows = {ref: q(f"SELECT kafka_partition, kafka_offset, {', '.join(envelope)}, hex(payload) FROM {bronze} "
                       f"VERSION AS OF {int(sid)} ORDER BY kafka_partition, kafka_offset")
                for ref, sid in dict(q(f"SELECT name, snapshot_id FROM {bronze}.refs")).items()}
        with urllib.request.urlopen(f"{c['registry']}/subjects/{c['topic']}-value/versions/latest", timeout=10) as r:
            registered = json.load(r)["id"]
        earliest, latest, _ = broker_offsets(spark, [c["topic"]], brokers=c["brokers"])
        topic = (spark.read.format("kafka").option("kafka.bootstrap.servers", c["brokers"])
                 .option("subscribe", c["topic"]).option("startingOffsets", "earliest")
                 .option("endingOffsets", "latest").load()
                 .withColumn("sid", F.expr(wire.schema_id_guarded_expr("value"))))
        trades = []
        for (sid,) in topic.select("sid").where("sid IS NOT NULL").distinct().collect():
            schema = fetch_schema(sid, c["registry"])
            decoded = (topic.where(F.col("sid") == sid)
                       .withColumn("e", from_avro(F.expr(wire.body_expr("value")), schema, {"mode": "PERMISSIVE"})))
            trades += [list(r) for r in decoded.where(F.col("e.stream") == "trade").select("partition", "offset").collect()]
        names = sorted(n for n in os.environ if n.startswith(("K2_", "AWS_", "MINIO_")) and "KEY" in n)
        return {
            "topic": {"earliest": earliest, "latest": latest, "frames": topic.count(), "trades": sorted(trades)},
            "landing": table(landing), "bronze": {**table(bronze), "rows": rows, "registered": registered,
                                                  "columns": [[x.name, x.dataType.simpleString()]
                                                              for x in spark.table(bronze).schema]},
            "checks": q(f"SELECT batch_id, snapshot_id, check_name, passed, detail FROM {checks} ORDER BY checked_at"),
            # names only: which key Spark signs with, and which pairs the container holds
            "keys": {"signs": SPARK_KEY[0], "held": names,
                     "aws_is_spark": os.environ.get("AWS_ACCESS_KEY_ID") == os.environ.get(SPARK_KEY[0]),
                     "conf_is_spark": spark.conf.get("spark.sql.catalog.lake.s3.access-key-id") == os.environ[SPARK_KEY[0]],
                     "warehouse": spark.conf.get("spark.sql.catalog.lake.warehouse")},
        }
    finally:
        spark.stop()


def corrupt(release: str, product: str) -> None:
    import urllib.request

    lake_code(release)
    from pyspark.sql import SparkSession

    c = pipeline(release, product)["connector"]
    with urllib.request.urlopen(f"{c['registry']}/subjects/{c['topic']}-value/versions/latest", timeout=10) as r:
        sid = json.load(r)["id"]
    spark = (SparkSession.builder.appName("kafka-path-corrupt").master("local[1]")
             .config("spark.driver.memory", "512m").config("spark.ui.enabled", "false").getOrCreate())
    # magic 0, the registered id, then varint bytes that never end: no Avro reader takes it
    value = bytes([0]) + sid.to_bytes(4, "big") + b"\xff" * 12
    (spark.createDataFrame([(b"BTC/USD", value)], "key binary, value binary").write.format("kafka")
     .option("kafka.bootstrap.servers", c["brokers"]).option("kafka.acks", "all").option("topic", c["topic"]).save())
    print(f"kafka-path: one corrupt frame (schema id {sid}, a body that is no Avro) onto {c['topic']}")
    spark.stop()


def cycles(release: str, product: str, dag: str, n: int) -> None:
    lake_code(release)
    import connector as C
    from catalog import fetch_schema
    from pyspark.sql import functions as F
    from pyspark.sql.avro.functions import from_avro

    import wire
    from spark_conf import lake_session

    p = pipeline(release, product)
    c = p["connector"]
    spark = lake_session("kafka-path-cycles", driver_memory="768m")
    try:
        topic = (spark.read.format("kafka").option("kafka.bootstrap.servers", c["brokers"]).option("subscribe", c["topic"])
                 .option("startingOffsets", "earliest").option("endingOffsets", "latest").load()
                 .withColumn("sid", F.expr(wire.schema_id_guarded_expr("value"))))
        [(sid,)] = topic.select("sid").where("sid IS NOT NULL").distinct().collect()
        trades = [tuple(r) for r in topic.where(F.col("sid") == sid).withColumn(
            "e", from_avro(F.expr(wire.body_expr("value")), fetch_schema(sid, c["registry"]), {"mode": "PERMISSIVE"}))
            .where("e.stream = 'trade'").select("key", "value").limit(3).collect()]
        source, lake = C.shells(spark, p)
        for i in range(n):
            (spark.createDataFrame(trades, "key binary, value binary").write.format("kafka")
             .option("kafka.bootstrap.servers", c["brokers"]).option("kafka.acks", "all").option("topic", c["topic"]).save())
            run = C.Run(release=Path(release).name, product=product, run_id=f"{dag}/manual__cycle-{i:02d}", pipeline=p,
                        accept_data_loss=False)
            for task in ("load", "audit", "publish"):
                for t in (p["landing"]["table"], p["bronze"][0]["table"], p["bronze"][0]["checks"]):
                    spark.sql(f"REFRESH TABLE {t}")  # publish commits through the catalog's REST API, past Spark's table cache
                code, _ = C.task_of(task, "kafka")(run, source, lake)
                if code != C.EXIT_OK:
                    raise SystemExit(f"kafka-path: cycle {i} {task} exit {code}")
        print(f"kafka-path: {n} cycles of {len(trades)} trade frames, each landed, staged, audited and published")
    finally:
        spark.stop()


def expire(release: str, product: str, days: int) -> int:
    lake_code(release)
    from datetime import timedelta

    import connector as C
    import maintain as M
    from spark_conf import lake_session

    p = pipeline(release, product)
    retention = json.loads((Path(release) / "compiled/lake/retention.json").read_text())
    spark = lake_session("kafka-path-expire", key=M.PASS_KEYS["maintain"], catalogs=["lake"])
    try:
        code, expired, unbuilt = M.expire_snapshots(p, retention, M.MaintainLake(spark), C.now() + timedelta(days=days))
    finally:
        spark.stop()
    print(json.dumps({"exit": code, "expired": expired, "notBuilt": unbuilt}))
    return code


def kill(when: str, release: str, argv: list[str]) -> int:
    lake_code(release)
    import connector
    import kafka

    def die(*_args, **_kw):
        print(f"kafka-path: SIGKILL {when} the landing commit", file=sys.stderr, flush=True)
        os.kill(os.getpid(), signal.SIGKILL)

    if when == "before":
        kafka.SparkLanding.land = die
    else:
        kafka.stage = die
    return connector.main(argv)


# --- group d: scripts/rebuild.py's own rebuild() and unlock(), on the host, against this lake ----

REPO = Path(__file__).resolve().parents[2]
# k2runs' connection on the lake's network: the release's own k2runs.connect as svc.operator, as
# rebuild.main connects on the host; one JSON line in ([sql, params]) and one out per statement.
HOLDS_CLIENT = """\
import json, sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import k2runs
conn = k2runs.connect(host="trino", port=8443, user="svc.operator", password_file="/run/k2-secrets/operator.password",
                      ca="/run/k2-secrets/tls/ca.pem")
for line in sys.stdin:
    sql, params = json.loads(line)
    try:
        cur = conn.cursor()
        cur.execute(sql, [datetime.fromisoformat(p["at"]) if isinstance(p, dict) else p for p in params])
        print(json.dumps({"rows": cur.fetchall()}, default=str), flush=True)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
"""
HOLD_ROWS = ("SELECT kind, held_by, held_at, released_at, released_by "  # times as k2runs.held reads them
             "FROM governance.run_records.holds WHERE product_id = ? ORDER BY held_at, released_at NULLS FIRST")


class Holds:
    """The DB-API surface k2runs uses (cursor, execute with ? parameters, fetchall), each statement
    sent to HOLDS_CLIENT in the dbt-runner image on the lake's network: the throwaway publishes no
    Trino port, so the host's rebuild.py reaches the real holds table through it."""

    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.rows: list = []

    def cursor(self):
        return self

    def execute(self, sql: str, params=()) -> None:
        self.p.stdin.write(json.dumps([sql, [{"at": v.isoformat()} if isinstance(v, datetime) else v for v in params]]) + "\n")
        self.p.stdin.flush()
        reply = json.loads(self.p.stdout.readline() or '{"error": "the holds client ended"}')
        if "error" in reply:
            raise RuntimeError(reply["error"])
        self.rows = reply["rows"]

    def fetchall(self) -> list:
        return self.rows


def rebuild_run(scenario: str, rp: Path, rid: str, product: str) -> dict:
    """One `make rebuild` as scripts/rebuild.py's rebuild() runs it, on this lake (env P, NET, IMAGE,
    STATE, S: run-path-lib.sh's). Real: the holds (svc.operator, the release's k2runs), bronze's
    replace and the restage (Live's own docker exec into this lake's Spark container), and k2-dbt
    build --full-refresh in the dbt-runner image. Faked, having no counterpart here: Airflow (a dict:
    the DAG starts as the scenario says, and no task runs, so the drain finds none), and the
    scheduler's oneshot.py, whose argv tests/test_rebuild.py proves: the build runs the image
    straight, with run-path-lib.sh build's flags, BUILD_PY's command and the -rebuild name under this
    lake's prefix. Scenarios: ct and kt, a rebuild of an unpaused DAG; fail, the same over an
    operator's hold with the DAG paused before; locked, a rebuild lock left open (a killed rebuild),
    then UNLOCK=1."""
    sys.path.insert(0, str(REPO / "scripts"))
    import rebuild as R
    from hold import k2runs

    env, by, now = os.environ, "kafka-path", datetime.now(timezone.utc)
    p, state, secrets = env["P"], Path(env["STATE"]), Path(env["S"])
    R.SPARK = f"{p}-spark-iceberg"
    rel = state / "releases" / rid
    dag = pipeline(str(rel), product)["dagId"]

    def mount(src, dst):
        return ["--mount", f"type=bind,source={src},target={dst},readonly"]

    base = ["docker", "run", "--rm", "--label", f"k2.throwaway={p}", "--network", env["NET"], "--pull", "never", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}"]
    conn = Holds([*base, "-i", "--name", f"{p}-holds-{scenario}", "--memory", "256m", *mount(rel, f"/k2/releases/{rid}"),
                  *mount(secrets / "principals/svc.operator.password", "/run/k2-secrets/operator.password"),
                  *mount(secrets / "tls/ca.pem", "/run/k2-secrets/tls/ca.pem"),
                  env["IMAGE"], "python", "-c", HOLDS_CLIENT, f"/k2/releases/{rid}/platform/platform/runs"])

    class Throwaway(R.Live):
        def __init__(self):
            self.dags, self.log, self.during = {dag: scenario == "fail"}, [], []

        def paused(self, dag):
            return self.dags[dag]

        def pause(self, dag):
            self.log.append(f"pause {dag}")
            self.dags[dag] = True

        def unpause(self, dag):
            self.log.append(f"unpause {dag}")
            self.dags[dag] = False

        def busy(self, dag):
            return []

        def running(self, name):
            return super().running(name.replace("k2-", f"{p}-", 1))

        def replace(self, release_path, statements):
            super().replace(release_path, statements)  # then bronze and the landing as the replace left them
            done = subprocess.run(["docker", "exec", "-i", R.SPARK, "python3", "-", "facts", release_path, product],
                                  input=Path(__file__).read_text(), stdout=subprocess.PIPE, text=True)
            (rp / f"facts-{scenario}-replaced.json").write_text(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "{}")

        def build(self, release_path, dag, product, run):
            b, k = pipeline(str(rel), product)["build"], json.loads((rel / "compiled/oneshots.json").read_text())["oneshots"]["dbt-runner"]
            held = k2runs.held(conn, product)  # what the DAG's resolve task reads: it fails on anything but None
            names = ("K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER", "K2_PUB_USER", "K2_RUNS_USER", "K2_CATALOG_URI", "K2_RELEASE")
            argv = [*base, "--name", R.oneshot_name(dag).replace("k2-", f"{p}-", 1), "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                    "--pids-limit", "256", "--cpus", k["cpus"], "--memory", k["memory"], *(x for n in names for x in ("-e", n)),
                    *mount(rel, release_path), *mount(secrets / f"principals/{b['writer']}.password", "/run/k2-secrets/builder.password"),
                    *mount(secrets / f"principals/{b['publisher']}.password", "/run/k2-secrets/publisher.password"),
                    *mount(secrets / "principals/svc.runs.password", "/run/k2-secrets/runs.password"),
                    *mount(secrets / "tls/ca.pem", "/run/k2-secrets/tls/ca.pem"),
                    env["IMAGE"], "k2-dbt", "build", "--release", rid, "--product", product, "--run-id", run, "--full-refresh"]
            done = subprocess.run(argv, env={**env, "K2_RELEASE": rid, "K2_TRINO_USER": b["writer"], "K2_PUB_USER": b["publisher"]},
                                  stdout=subprocess.PIPE, text=True, errors="replace")
            print(done.stdout, end="")
            self.during.append({"held": held, "exit": done.returncode, "xcom": R.last_json(done.stdout)})
            return done.returncode, R.last_json(done.stdout)

    io = Throwaway()
    if scenario == "fail":  # an operator's hold, as make hold writes it
        k2runs.hold(conn, product, "kafka-path: an operator's hold across a rebuild", by)
    if scenario == "locked":  # the lock a killed rebuild leaves
        k2runs.hold(conn, product, f"make rebuild by {by}", R.BY, kind=R.LOCK)
    out = {"run": R.run_id(dag, now), "dag": dag, "release": rid, "planted": k2runs.held(conn, product)}
    try:
        out["exit"] = R.rebuild(conn, rel, product, dag, by, io, now)
    except R.Refused as e:  # refused before anything changed: rebuild.main's exit 2
        print(f"rebuild: REFUSED: {e}", file=sys.stderr)
        out["exit"] = 2
    out |= {"airflow": io.log, "during": io.during, "open": k2runs.held(conn, product)}
    if scenario == "locked":
        out["unlock"] = R.unlock(conn, product, dag, by, io)
        out["open_after_unlock"] = k2runs.held(conn, product)
    if scenario == "fail":  # make unhold
        k2runs.release(conn, product, by)
    conn.execute(HOLD_ROWS, [product])
    out["rows"] = conn.fetchall()
    conn.p.stdin.close()
    conn.p.wait()
    return out


# --- claims, on the host --------------------------------------------------------------------


def load_json(rp: Path, name: str):
    return json.loads((rp / name).read_text())


def result(rp: Path, label: str) -> dict:
    lines = [x for x in (rp / f"{label}.out").read_text().splitlines() if x.startswith("{")]
    return json.loads(lines[-1]) if lines else {}


def rc(rp: Path, label: str) -> str:
    return (rp / f"{label}.rc").read_text().strip()


def connector_snapshots(t: dict) -> list:
    return [s for s in t["snapshots"] if (s[4] or {}).get("k2.job") == "connector"]


def offsets_of(summary: dict) -> dict:
    return {t: {int(p): o for p, o in v.items()} for t, v in json.loads(summary["k2.kafka-offsets"]).items()}


def topic_ends(f: dict) -> dict:
    return {t: {int(p): o for p, o in v.items()} for t, v in f["topic"]["latest"].items()}


def every_frame_once(f: dict) -> list:
    """Problems with the landing at main against the topic: every offset of every partition from
    the log start to the end, once each."""
    bad, seen = [], {}
    for p, o in f["landing"]["main"]:
        seen.setdefault(p, []).append(o)
    for t, ends in topic_ends(f).items():
        for p, end in ends.items():
            start = f["topic"]["earliest"][t][str(p)]
            want, got = list(range(start, end)), sorted(seen.get(p, []))
            if got != want:
                bad.append(f"partition {p}: landing holds {len(got)} offsets, {len(set(got))} distinct, "
                           f"topic {start}..{end - 1}")
    return bad


def bronze_is_trades(f: dict, ref: str = "main") -> list:
    got, want = sorted(map(list, f["bronze"].get(ref, []))), f["topic"]["trades"]
    if got == want:
        return []
    return [f"bronze {ref} holds {len(got)} keys ({len({tuple(k) for k in got})} distinct), the topic {len(want)} "
            f"trade frames; missing {[k for k in want if k not in got][:5]}, extra {[k for k in got if k not in want][:5]}"]


def through_is_landing(f: dict) -> list:
    b, la = connector_snapshots(f["bronze"]), connector_snapshots(f["landing"])
    main = f["bronze"]["refs"].get("main")
    on_main = [s for s in b if s[0] == main]
    if not on_main or not la:
        return [f"no connector snapshot at bronze main {main} or on the landing"]
    through, ends = offsets_of(on_main[0][4]), offsets_of(la[-1][4])
    return [] if through == ends == topic_ends(f) else [f"through {through}, landing ends {ends}, topic {topic_ends(f)}"]


def say(n: int, name: str, bad: list, ok: str, info: list = ()) -> bool:
    for line in info:
        print(f"INFO {n} {line}")
    print(f"{'FAIL' if bad else 'PASS'} {n} {name}: {'; '.join(bad) if bad else ok}")
    return not bad


BRONZE_COLUMNS = [["kafka_partition", "int"], ["kafka_offset", "bigint"], ["kafka_timestamp", "timestamp"],
                  ["schema_id", "int"], ["exchange", "string"], ["stream", "string"], ["symbol", "string"],
                  ["recv_ts_ns", "bigint"], ["conn_id", "string"], ["conn_msg_seq", "bigint"], ["payload", "binary"],
                  ["batch_id", "string"], ["landed_at", "timestamp"], ["run_id", "string"], ["release_id", "string"]]
SEEDED = Path(__file__).resolve().parents[2] / "services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl"


def envelope_problems(f: dict, ref: str = "main") -> tuple[list, list]:
    """(problems, info) of bronze's rows at `ref` against the seeded file itself: each row's stream
    trade, its schema id the registered one, its payload the seeded line's byte for byte, and its
    other envelope fields as kafka_seed.py built them (symbol by the adapter's rule, null when the
    frame names no one instrument)."""
    from kafka_seed import EXCHANGE, FIXTURE_CONN_ID, route

    lines = [json.loads(x) for x in SEEDED.read_text().splitlines()]
    seeded = {d["recv_ts_ns"]: (seq, d["payload"]) for seq, d in enumerate(lines, 1) if route(d["payload"])[0] == "trade"}
    b, bad = f["bronze"], []
    if b["columns"] != BRONZE_COLUMNS:
        bad.append(f"bronze columns {b['columns']}")
    rows = b["rows"].get(ref, [])
    for p, o, sid, exchange, stream, symbol, recv, conn, seq, payload in rows:
        want = seeded.get(recv)
        got = (sid, exchange, stream, conn, seq, symbol, payload)
        exp = (b["registered"], EXCHANGE, "trade", FIXTURE_CONN_ID, want and want[0], want and route(want[1])[1],
               want and want[1].encode().hex().upper())
        if got != exp:
            bad.append(f"{p}/{o}: {got[:6]} payload {len(payload or '') // 2} bytes; want {exp[:6]} "
                       f"{len(exp[6] or '') // 2} bytes")
    info = [f"bronze {ref}: {len(rows)} rows, stream {sorted({r[4] for r in rows})}, schema id "
            f"{sorted({r[2] for r in rows})} (registered {b['registered']}), {sum(r[5] is None for r in rows)} symbol "
            f"null, {len({r[9] for r in rows})} distinct payloads, each the seeded line's bytes"]
    return bad[:5] + ([f"{len(bad) - 5} more"] if len(bad) > 5 else []), info


LANDING_KEYS = {"k2.job", "k2.run-id", "k2.release", "k2.batch-id", "k2.kafka-offsets", "k2.kafka-starts"}
BRONZE_KEYS = {"k2.job", "k2.run-id", "k2.release", "k2.batch-id", "k2.src-snapshot-id", "k2.kafka-offsets",
               "k2.kafka-skipped"}


def k2_keys(summary: dict) -> set:
    return {k for k in summary if k.startswith("k2.")}


def claim(name: str, rp: Path) -> bool:
    if name == "land":  # 1
        f, r = load_json(rp, "facts-k1.json"), result(rp, "k1.load")
        la = connector_snapshots(f["landing"])
        bad = every_frame_once(f) + bronze_is_trades(f)
        if len(la) != 1:
            bad.append(f"{len(la)} landing connector commits, not 1")
        else:
            s = la[0][4]
            if k2_keys(s) != LANDING_KEYS:
                bad.append(f"landing summary keys {sorted(k2_keys(s))}")
            if offsets_of(s) != topic_ends(f):
                bad.append(f"landing end offsets {offsets_of(s)}, topic's {topic_ends(f)}")
        b = connector_snapshots(f["bronze"])
        if len(b) != 1 or k2_keys(b[0][4]) != BRONZE_KEYS:
            bad.append(f"bronze connector commits {[sorted(k2_keys(x[4])) for x in b]}")
        if r.get("landed", 0) != f["topic"]["frames"] or r.get("rows", 0) + r.get("skipped", -1) != r.get("landed"):
            bad.append(f"load's result: landed {r.get('landed')}, staged {r.get('rows')}, skipped {r.get('skipped')}, "
                       f"topic {f['topic']['frames']}")
        return say(1, "one load lands every seeded frame and stages exactly the trades", bad,
                   f"{r.get('landed')} frames landed, every partition contiguous from 0 to its end offset; "
                   f"{r.get('rows')} trades staged + {r.get('skipped')} skipped = {r.get('landed')}; the landing "
                   f"commit carries {sorted(LANDING_KEYS)}, end offsets {offsets_of(la[0][4]) if la else '?'} = the topic's")
    if name == "publish":  # 2
        f = load_json(rp, "facts-k1.json")
        rows = [c for c in f["checks"] if c[0] == "kafka_fixture/k1"]
        bad = []
        if sorted((c[2], c[3]) for c in rows) != [("accounting", True), ("keys_unique", True), ("parses", True)]:
            bad.append(f"audit_checks rows {[(c[2], c[3], c[4]) for c in rows]}")
        if rc(rp, "k1.audit") != "0" or rc(rp, "k1.publish") != "0":
            bad.append(f"audit exit {rc(rp, 'k1.audit')}, publish exit {rc(rp, 'k1.publish')}")
        pub = result(rp, "k1.publish")
        if pub.get("snapshotAfter") != f["bronze"]["refs"].get("main") or pub.get("snapshotAfter") == pub.get("snapshotBefore"):
            bad.append(f"publish moved main {pub.get('snapshotBefore')} -> {pub.get('snapshotAfter')}, main now "
                       f"{f['bronze']['refs'].get('main')}")
        bad += bronze_is_trades(f)
        return say(2, "audit writes three passed rows and publish moves main by the compare-and-set commit", bad,
                   f"accounting, parses, keys_unique passed; main {pub.get('snapshotBefore')} -> "
                   f"{pub.get('snapshotAfter')}; bronze main holds the {len(f['topic']['trades'])} trade frames",
                   [f"k1 {c[2]}: {c[4]}" for c in rows])
    if name == "idle":  # 3
        f1, f = load_json(rp, "facts-k1.json"), load_json(rp, "facts-k2.json")
        bad = []
        if [s[0] for s in f["landing"]["snapshots"]] != [s[0] for s in f1["landing"]["snapshots"]]:
            bad.append("the landing gained a snapshot")
        if [s[0] for s in f["bronze"]["snapshots"]] != [s[0] for s in f1["bronze"]["snapshots"]]:
            bad.append("bronze gained a snapshot")
        if f["landing"]["main"] != f1["landing"]["main"] or f["bronze"]["main"] != f1["bronze"]["main"]:
            bad.append("rows changed")
        if any(rc(rp, f"k2.{t}") != "0" for t in ("load", "audit", "publish")):
            bad.append("a task exited non-zero")
        load_out = (rp / "k2.load.out").read_text()
        return say(3, "a second run with no new frames adds no landing row and no bronze row", bad,
                   "load, audit, publish exit 0; it commits nothing to the landing or bronze (no empty append): "
                   f"load says {'no new records' if 'no new records' in load_out else '?'}, "
                   f"{'level' if 'is level with' in load_out else '?'}; audit_checks gains its three rows only",
                   [f"k2 {c[2]}: {c[4]}" for c in f["checks"] if c[0] == "kafka_fixture/k2"])
    if name == "kill-after":  # 4
        f4, f = load_json(rp, "facts-k4.json"), load_json(rp, "facts-k5.json")
        f3 = load_json(rp, "facts-k3.json")
        bad = []
        if rc(rp, "k4.kill") != "137":
            bad.append(f"the killed load exited {rc(rp, 'k4.kill')}, not 137")
        if len(connector_snapshots(f4["landing"])) != len(connector_snapshots(f3["landing"])) + 1:
            bad.append("the killed load made no landing commit")
        if f4["bronze"]["snapshots"] != f3["bronze"]["snapshots"]:
            bad.append("the killed load staged something")
        bad += every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        r = result(rp, "k5.load")
        return say(4, "a load killed after its landing commit: the next run stages that range and the new one", bad,
                   f"k4 killed (exit 137) with its landing commit made and bronze unchanged; k5 landed {r.get('landed')} "
                   f"and staged {r.get('rows')} (skipped {r.get('skipped')}): bronze main = every trade frame seeded "
                   f"({len(f['topic']['trades'])}), each once; through-offsets = landing end offsets = topic's")
    if name == "kill-before":  # 5
        f5, f6, f = load_json(rp, "facts-k5.json"), load_json(rp, "facts-k6.json"), load_json(rp, "facts-k7.json")
        bad = []
        if rc(rp, "k6.kill") != "137":
            bad.append(f"the killed load exited {rc(rp, 'k6.kill')}, not 137")
        if f6["landing"]["snapshots"] != f5["landing"]["snapshots"] or f6["landing"]["main"] != f5["landing"]["main"]:
            bad.append("the landing changed under a load killed before its commit")
        bad += every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        if len(connector_snapshots(f["landing"])) != len(connector_snapshots(f5["landing"])) + 1:
            bad.append("the next run did not land in one commit")
        return say(5, "a load killed before its landing commit leaves the landing unchanged; the next lands the range once",
                   bad, f"k6 killed (exit 137), landing snapshots and rows as before; k7 landed "
                   f"{result(rp, 'k7.load').get('landed')} in one commit, every offset once")
    if name == "retry":  # 6
        f = load_json(rp, "facts-k8.json")
        r = result(rp, "k8.load.2")
        bad = every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        if rc(rp, "k8.kill") != "137" or rc(rp, "k8.load.2") != "0" or not r.get("rows"):
            bad.append(f"kill exit {rc(rp, 'k8.kill')}, retry exit {rc(rp, 'k8.load.2')}, staged {r.get('rows')}")
        if any(rc(rp, f"k8.{t}") != "0" for t in ("audit", "publish")):
            bad.append("the retry's audit or publish exited non-zero")
        return say(6, "a retry under the same run id after a landing commit stages the new range", bad,
                   f"k8 killed after its landing commit; its retry (same run id) landed {r.get('landed')} and "
                   f"staged {r.get('rows')}, then audit and publish exit 0: bronze main = every trade frame, once")
    if name == "key":  # 7
        f = load_json(rp, "facts-k8.json")
        k, bad = f["keys"], []
        files = f["landing"]["files"] + f["bronze"]["files"]
        outside = [x for x in files if not x.startswith("s3://k2-lake/warehouse/k2/")]
        if outside or not files:
            bad.append(f"{len(outside)} of {len(files)} data files outside s3://k2-lake/warehouse/k2/")
        if not (k["aws_is_spark"] and k["conf_is_spark"]) or k["warehouse"] != "k2" or any(n.startswith("MINIO_") for n in k["held"]):
            bad.append(f"keys {k}")
        return say(7, "the writes used Spark's own storage key and warehouse k2", bad,
                   f"signing key {k['signs']} (the AWS_* pair is the same one), catalog warehouse {k['warehouse']}; "
                   f"the container holds only {', '.join(k['held'])} (no MinIO root; the maintenance key may not "
                   f"PutObject); all {len(files)} data files of the landing and bronze under s3://k2-lake/warehouse/k2/")
    if name == "empty":  # 8
        f2, f = load_json(rp, "facts-k2.json"), load_json(rp, "facts-k3.json")
        r, bad = result(rp, "k3.load"), []
        [(t, starts)] = (r.get("starts") or {"": {}}).items()
        quiet = [p for p, s in starts.items() if r["ends"][t][p] == s]
        new = [s for s in f["bronze"]["snapshots"] if s[0] not in {x[0] for x in f2["bronze"]["snapshots"]}]
        added = [(s[3], (s[4] or {}).get("added-records"), (s[4] or {}).get("added-data-files")) for s in new]
        if any(rc(rp, f"k3.{t}") != "0" for t in ("load", "audit", "publish")):
            bad.append(f"exits {[rc(rp, f'k3.{t}') for t in ('load', 'audit', 'publish')]}")
        if r.get("rows") != 0 or not r.get("skipped"):
            bad.append(f"staged {r.get('rows')}, skipped {r.get('skipped')}")
        if not quiet:
            bad.append("no partition was quiet in this run")
        bad += through_is_landing(f) + bronze_is_trades(f)
        return say(8, "a batch of control frames only: an empty append, and a quiet partition read start = end", bad,
                   f"k3 landed {r.get('landed')}, staged 0, skipped {r.get('skipped')}; bronze commits {added} "
                   f"(operation, added-records, added-data-files) and publish moved main to it, so the through-offsets "
                   f"advance; partitions {quiet} had no new records (start = end, failOnDataLoss true) and read nothing",
                   [f"k3 {c[2]}: {c[4]}" for c in f["checks"] if c[0] == "kafka_fixture/k3"])
    if name == "corrupt":  # 9
        f8, f = load_json(rp, "facts-k11.json"), load_json(rp, "facts-k9.json")  # k11: the run before the corrupt frame
        ends8, ends = topic_ends(f8), topic_ends(f)
        [(topic, parts)] = ends.items()
        moved = [(p, o - 1) for p, o in parts.items() if o != ends8[topic][p]]
        parses = [c for c in f["checks"] if c[0] == "kafka_fixture/k9" and c[2] == "parses"]
        bad = []
        want = f"{topic}/{moved[0][0]} offset {moved[0][1]}: its body does not read" if len(moved) == 1 else None
        if want is None or not parses or parses[-1][3] or want not in (parses[-1][4] or ""):
            bad.append(f"frame at {moved}; parses rows {parses}")
        if rc(rp, "k9.audit") != "3" or rc(rp, "k9.publish") != "3" or f["bronze"]["refs"]["main"] != f8["bronze"]["refs"]["main"]:
            bad.append(f"audit exit {rc(rp, 'k9.audit')}, publish exit {rc(rp, 'k9.publish')}, main "
                       f"{f8['bronze']['refs']['main']} -> {f['bronze']['refs']['main']}")
        return say(9, "a corrupt Avro body is counted as not parsing and fails the batch naming partition and offset", bad,
                   f"parses FAIL: {parses[-1][4] if parses else '?'}; audit exit 3, publish exit 3, main not moved")
    if name == "envelope":  # a 10
        f8, f = load_json(rp, "facts-k11.json"), load_json(rp, "facts-k9.json")  # k11: the run before the corrupt frame
        bad, info = envelope_problems(f8)
        bad += [] if f8["bronze"]["rows"]["main"] else ["bronze main holds no rows"]
        ends8, ends = topic_ends(f8), topic_ends(f)
        [(topic, parts)] = ends.items()
        moved = [(p, o - 1) for p, o in parts.items() if o != ends8[topic][p]]
        corrupt = [r for r in f["bronze"]["rows"].get("audit", []) if [r[0], r[1]] in [list(m) for m in moved]]
        if len(corrupt) != 1 or corrupt[0][2] != f["bronze"]["registered"] or any(v is not None for v in corrupt[0][3:]):
            bad.append(f"the corrupt frame's bronze row {corrupt}")
        return say(10, "bronze carries the decoded envelope: stream trade only, the seeded payload byte for byte, "
                   "the registered schema id", bad,
                   f"{len(f8['bronze']['rows']['main'])} rows of bronze main as the seeded lines; the corrupt frame "
                   f"staged with schema id {corrupt[0][2] if corrupt else '?'} and every envelope column null, failing "
                   "parses (claim 9), never a value", info)
    if name == "heal":  # a 11
        f8, f10, f = load_json(rp, "facts-k8.json"), load_json(rp, "facts-k10.json"), load_json(rp, "facts-k11.json")
        r10, r, bad = result(rp, "k10.load"), result(rp, "k11.load"), []
        pending = f10["bronze"]["refs"].get("audit")
        if rc(rp, "k10.load") != "0" or not r10.get("rows") or pending == f10["bronze"]["refs"].get("main") \
                or f10["bronze"]["refs"].get("main") != f8["bronze"]["refs"].get("main"):
            bad.append(f"k10 load exit {rc(rp, 'k10.load')}, staged {r10.get('rows')}, refs {f10['bronze']['refs']}")
        if any(c[0] == "kafka_fixture/k10" for c in f["checks"]):
            bad.append("k10's batch has audit_checks rows")
        if any(rc(rp, f"k11.{t}") != "0" for t in ("load", "audit", "publish")) \
                or "never audited" not in (rp / "k11.load.out").read_text():
            bad.append(f"k11 exits {[rc(rp, f'k11.{t}') for t in ('load', 'audit', 'publish')]}: "
                       f"{(rp / 'k11.load.err').read_text().strip()[-300:]}")
        if r.get("rows", 0) <= r10.get("rows", 0) or r.get("snapshotBefore") != pending:
            bad.append(f"k11 staged {r.get('rows')} over audit {r.get('snapshotBefore')}, k10 {r10.get('rows')} at {pending}")
        bad += every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        return say(11, "a run killed between stage and audit is healed by the next scheduled run, no operator action", bad,
                   f"k10 staged {r10.get('rows')} on audit {pending} and was never audited; k11 found it, discarded it and "
                   f"staged {r.get('rows')} from main's through-offsets (k10's range and its own), audited and published: "
                   "every trade once, no hole, through-offsets = landing end offsets = the topic's",
                   [x for x in (rp / "k11.load.out").read_text().splitlines() if "never audited" in x])
    if name == "blocked":  # a 12
        f9, f, err = load_json(rp, "facts-k9.json"), load_json(rp, "facts-k12.json"), (rp / "k12.load.err").read_text()
        main = f9["bronze"]["refs"].get("main")
        bad = [] if rc(rp, "k12.load") == "2" else [f"k12 load exit {rc(rp, 'k12.load')}"]
        for want in ("holds batch kafka_fixture/k9", "failed [", "'parses'", f"REPLACE BRANCH audit AS OF VERSION {main}"):
            if want not in err:
                bad.append(f"the refusal does not say {want!r}: {err.strip()[-300:]}")
        if f["bronze"]["refs"] != f9["bronze"]["refs"]:
            bad.append(f"bronze refs {f9['bronze']['refs']} -> {f['bronze']['refs']}")
        return say(12, "a batch that failed its audit is never reset: the next load refuses, naming it and its clearing",
                   bad, f"k12 load exit 2, audit and main unmoved: "
                   f"{next((x for x in err.splitlines() if 'load refused' in x), '')[:400]}")
    if name == "envelope-b":  # b 5
        f = load_json(rp, "facts-b5.json")
        bad, info = envelope_problems(f)
        bad += [] if f["bronze"]["rows"]["main"] else ["bronze main holds no rows"]
        return say(5, "after the gap bronze still carries the decoded envelope as seeded", bad,
                   f"{len(f['bronze']['rows']['main'])} rows of bronze main as the seeded lines", info)
    if name == "compact-skip":  # c1
        f, fs, fp = load_json(rp, "facts-c3.json"), load_json(rp, "facts-c3s.json"), load_json(rp, "facts-c3p.json")
        r, pub, bad = result(rp, "c3.compact"), result(rp, "c3.publish"), []
        why = r.get("skipped", {})
        tables = sorted(f"lake.kafka_fixture.{t}" for t in ("landing", "bronze_frames"))
        if rc(rp, "c3.compact") != "0" or r.get("compacted") or sorted(why) != tables or not all(
                "holds batch kafka_fixture/c3, unpublished" in why[t] for t in tables):
            bad.append(f"compact exit {rc(rp, 'c3.compact')}, result {r}")
        if any(fs[t]["snapshots"] != f[t]["snapshots"] or fs[t]["refs"] != f[t]["refs"] for t in ("landing", "bronze")):
            bad.append("the skipped pass changed a table's snapshots or refs")
        if rc(rp, "c3.publish") != "0" or pub.get("snapshotAfter") != f["bronze"]["refs"].get("audit") \
                or fp["bronze"]["refs"].get("main") != f["bronze"]["refs"].get("audit"):
            bad.append(f"publish exit {rc(rp, 'c3.publish')}, {pub.get('snapshotBefore')} -> {pub.get('snapshotAfter')}")
        return say(1, "compaction is skipped while audit holds an unpublished batch, so that batch still publishes", bad,
                   f"compact exit 0, compacted nothing: {next(iter(why.values()), '?')[:160]}; no snapshot or ref moved; "
                   f"c3's publish then moved main {pub.get('snapshotBefore')} -> {pub.get('snapshotAfter')}. Without the "
                   "skip, main would move to a replace snapshot under the waiting batch: publish requires main to be the "
                   "snapshot the batch was staged on (connector.publish, staged_on), so it would be refused, exit 2, and "
                   "the audited batch discarded and staged again by the next run (kafka.restageable)")
    if name == "compact":  # c2
        f0, f, r, bad, info = load_json(rp, "facts-c3p.json"), load_json(rp, "facts-c4.json"), result(rp, "c4.compact"), [], []
        if rc(rp, "c4.compact") != "0":
            bad.append(f"compact exit {rc(rp, 'c4.compact')}: {(rp / 'c4.compact.err').read_text().strip()[-300:]}")
        for t in ("landing", "bronze"):
            new = [x for x in f[t]["snapshots"] if x[0] not in {y[0] for y in f0[t]["snapshots"]}]
            keys = {k: v for k, v in (new[0][4] or {}).items() if k.startswith("k2.")} if new else {}
            if len(new) != 1 or new[0][3] != "replace" or keys != {"k2.job": "maintenance"}:
                bad.append(f"{t}: new snapshots {[(x[0], x[3], x[4]) for x in new]}")
            if f[t]["main"] != f0[t]["main"] or (t == "bronze" and f[t]["rows"]["main"] != f0[t]["rows"]["main"]):
                bad.append(f"{t}: main's rows changed")
            if not len(f[t]["live"]) < len(f0[t]["live"]):
                bad.append(f"{t}: live data files {len(f0[t]['live'])} -> {len(f[t]['live'])}")
            info.append(f"{t}: {len(f0[t]['live'])} live data files -> {len(f[t]['live'])}, snapshot "
                        f"{new[0][0] if new else '?'} {new[0][3] if new else '?'} {keys}")
        return say(2, "compaction writes one replace snapshot per table, k2.job maintenance, no k2.kafka-offsets", bad,
                   f"compact exit 0, {r.get('compacted')}; every row and envelope value as before", info)
    if name == "maintain":  # c3
        f0, f, r, bad = load_json(rp, "facts-c4.json"), load_json(rp, "facts-c5.json"), result(rp, "c5.maintain"), []
        checks = r.get("checks") or [{}]
        if rc(rp, "c5.maintain") != "0" or not checks[0].get("passed"):
            bad.append(f"maintain exit {rc(rp, 'c5.maintain')}: {(rp / 'c5.maintain.err').read_text().strip()[-400:]}")
        removed = r.get("orphansRemoved", {})
        if sorted(removed) != sorted(["lake.kafka_fixture.landing", "lake.kafka_fixture.bronze_frames",
                                      "lake.kafka_fixture.audit_checks"]) or any(removed.values()):
            bad.append(f"orphansRemoved {removed}")
        for t in ("landing", "bronze"):
            if f[t]["live"] != f0[t]["live"] or f[t]["main"] != f0[t]["main"]:
                bad.append(f"{t}: live files or rows changed")
        listed = [x for x in (rp / "c5.maintain.out").read_text().splitlines() if "orphan files removed" in x]
        return say(3, "the default pass: continuity passes, expiry and orphan cleanup with the 24-hour floor run, no "
                   "live file deleted", bad, f"maintain exit 0; {checks[0].get('detail')}; expired {r.get('expired')}; "
                   f"orphans removed {removed}, every live file still there", listed)
    if name == "expire":  # c4
        f0, f, bad, info = load_json(rp, "facts-c5.json"), load_json(rp, "facts-c6.json"), [], []
        out = (rp / "c6.expire.out").read_text()
        r = json.loads([x for x in out.splitlines() if x.startswith("{")][-1]) if "{" in out else {}
        if rc(rp, "c6.expire") != "0":
            bad.append(f"expire exit {rc(rp, 'c6.expire')}: {(rp / 'c6.expire.err').read_text().strip()[-300:]}")
        for t in ("landing", "bronze"):
            snaps0 = {x[0]: x for x in f0[t]["snapshots"]}
            chain, head = [], f0[t]["refs"]["main"]
            while head is not None and (head not in snaps0 or (snaps0[head][4] or {}).get("k2.job") != "connector"
                                        or "k2.kafka-offsets" not in (snaps0[head][4] or {})):
                chain.append(head)
                head = snaps0[head][1] if head in snaps0 else None
            kept = {x[0] for x in f[t]["snapshots"]}
            if head is None or head not in kept or not set(chain) <= kept or f[t]["refs"] != f0[t]["refs"]:
                bad.append(f"{t}: the newest connector snapshot {head} or its chain {chain} expired, refs {f[t]['refs']}")
            if not len(kept) < len(snaps0):
                bad.append(f"{t}: {len(snaps0)} snapshots -> {len(kept)}, none expired")
            info.append(f"{t}: {len(snaps0)} snapshots -> {len(kept)}; kept main {f0[t]['refs']['main']} back to the newest "
                        f"connector snapshot {head} ({len(chain)} offset-less between)")
        info += [x for x in out.splitlines() if "snapshots expired" in x]
        return say(4, "expiry past retention keeps the newest connector snapshot of the landing and bronze and the chain to it",
                   bad, f"maintain.expire_snapshots at now + 30 days exit {r.get('exit')}: expired {r.get('expired')}", info)
    if name == "resume":  # c5
        f0, f, r, bad = load_json(rp, "facts-c6.json"), load_json(rp, "facts-c7.json"), result(rp, "c7.load"), []
        la0 = connector_snapshots(f0["landing"])
        ends0 = offsets_of(la0[-1][4]) if la0 else {}
        starts = {t: {int(k): v for k, v in x.items()} for t, x in (r.get("starts") or {}).items()}
        if any(rc(rp, f"c7.{t}") != "0" for t in ("load", "audit", "publish")) or starts != ends0:
            bad.append(f"exits {[rc(rp, f'c7.{t}') for t in ('load', 'audit', 'publish')]}, starts {starts}, "
                       f"the newest connector snapshot's ends {ends0}")
        new = sum(e - ends0[t][p] for t, x in topic_ends(f).items() for p, e in x.items())
        if r.get("landed") != new:
            bad.append(f"landed {r.get('landed')}, the topic had {new} new")
        bad += every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        return say(5, "after compaction and expiry the next run resumes from the newest connector snapshot", bad,
                   f"c7 started at {starts}, the newest connector snapshot's end offsets, landed {r.get('landed')} (all "
                   f"new) and staged {r.get('rows')}: every offset once, bronze main = every trade once, through-offsets = "
                   "landing end offsets = the topic's")
    if name == "continuity":  # c6
        r = result(rp, "c8.maintain")
        checks = r.get("checks") or [{}]
        bad = [] if rc(rp, "c8.maintain") == "0" and checks[0].get("passed") else [
            f"maintain exit {rc(rp, 'c8.maintain')}, {checks}"]
        return say(6, "the continuity check passes after the resume", bad, f"{checks[0].get('detail')}")
    if name == "below":  # b1
        f1, f = load_json(rp, "facts-b1.json"), load_json(rp, "facts-b3.json")
        trims, err = load_json(rp, "trims.json"), (rp / "b3.load.err").read_text()
        [(topic, ends)] = offsets_of(connector_snapshots(f1["landing"])[-1][4]).items()
        bad = [] if trims else ["nothing was trimmed"]
        named = [f"{topic}/{p} committed {ends[int(p)]}, log start {o}" for p, o in trims.items()]
        if rc(rp, "b3.load") != "2" or "below the log start" not in err or not all(n in err for n in named):
            bad.append(f"load exit {rc(rp, 'b3.load')}: {err.strip().splitlines()[-1:] if err.strip() else '(no stderr)'}")
        if f["landing"]["snapshots"] != f1["landing"]["snapshots"]:
            bad.append("the refused load changed the landing")
        return say(1, "a start below the log start fails loudly naming the partitions", bad,
                   f"load exit 2, nothing landed: {next((x for x in err.splitlines() if 'below the log start' in x), '')[:400]}")
    if name == "accepted":  # b2
        f1, f = load_json(rp, "facts-b1.json"), load_json(rp, "facts-b4.json")
        trims, r = load_json(rp, "trims.json"), result(rp, "b4.load")
        [(topic, ends)] = offsets_of(connector_snapshots(f1["landing"])[-1][4]).items()
        newest = connector_snapshots(f["landing"])[-1][4]
        want = {topic: {p: [[ends[int(p)], o]] for p, o in trims.items()}}
        gaps = sorted((g["partition"], g["committed"], g["logStart"], g["lost"]) for g in r.get("offsetGaps", []))
        bad = []
        if rc(rp, "b4.load") != "0" or json.loads(newest.get("k2.kafka-gaps", "{}")) != want:
            bad.append(f"load exit {rc(rp, 'b4.load')}, k2.kafka-gaps {newest.get('k2.kafka-gaps')}, want {want}")
        if gaps != sorted((int(p), ends[int(p)], o, o - ends[int(p)]) for p, o in trims.items()):
            bad.append(f"load's offsetGaps {gaps}")
        seen, first = {}, {int(p): o for p, o in f1["topic"]["earliest"][topic].items()}  # b1's: claim 6's trim
        for p, o in f["landing"]["main"]:
            seen.setdefault(p, []).append(o)
        for p, end in topic_ends(f)[topic].items():
            lo = trims.get(str(p))
            want_offsets = list(range(first.get(p, 0), ends.get(p, 0))) + list(range(lo if lo is not None else ends.get(p, 0), end))
            if sorted(seen.get(p, [])) != want_offsets:
                bad.append(f"partition {p}: landing {len(seen.get(p, []))} offsets, want {len(want_offsets)}")
        if offsets_of(newest) != topic_ends(f):
            bad.append(f"landing ends {offsets_of(newest)}, topic {topic_ends(f)}")
        return say(2, "with --accept-data-loss the run lands from the log start and records the gap in k2.kafka-gaps",
                   bad, f"load exit 0, k2.kafka-gaps {newest.get('k2.kafka-gaps')}, offsetGaps {r.get('offsetGaps')}; "
                   "the landing holds every offset but the gap, once each")
    if name == "gap-audit":  # b3
        f = load_json(rp, "facts-b4.json")
        rows = [c for c in f["checks"] if c[0] == "kafka_fixture/b4"]
        bad = [] if sorted((c[2], c[3]) for c in rows) == [("accounting", True), ("keys_unique", True), ("parses", True)] \
            else [f"audit_checks {[(c[2], c[3], c[4]) for c in rows]}"]
        if rc(rp, "b4.audit") != "0" or rc(rp, "b4.publish") != "0":
            bad.append(f"audit exit {rc(rp, 'b4.audit')}, publish exit {rc(rp, 'b4.publish')}")
        bad += through_is_landing(f)
        keys = [tuple(k) for k in f["bronze"]["main"]]
        if len(keys) != len(set(keys)) or not set(keys) <= {tuple(k) for k in f["landing"]["main"]}:
            bad.append("bronze keys repeat or are not the landing's")
        acc = next((c[4] for c in rows if c[2] == "accounting"), "")
        return say(3, "the audit's accounting passes with the gap excluded, and publish moves main", bad,
                   f"accounting: {acc}; through-offsets = landing ends = topic ends")
    if name == "carried":  # b4
        f4, f = load_json(rp, "facts-b4.json"), load_json(rp, "facts-b5.json")
        before, after = connector_snapshots(f4["landing"])[-1], connector_snapshots(f["landing"])[-1]
        gaps = after[4].get("k2.kafka-gaps")
        bad = [] if after[0] != before[0] and gaps == before[4].get("k2.kafka-gaps") and gaps else [
            f"b5's landing commit {after[0]} carries {gaps}, b4's {before[0]} {before[4].get('k2.kafka-gaps')}"]
        if any(rc(rp, f"b5.{t}") != "0" for t in ("load", "audit", "publish")):
            bad.append(f"b5 exits {[rc(rp, f'b5.{t}') for t in ('load', 'audit', 'publish')]}")
        return say(4, "after one further clean run the newest landing commit still carries the gap", bad,
                   f"b5 landed {result(rp, 'b5.load').get('landed')} in landing commit {after[0]}, which carries "
                   f"k2.kafka-gaps {gaps}, as b4's did; its audit and publish exit 0")
    if name == "first-trimmed":  # b6
        f, trims, r = load_json(rp, "facts-b1.json"), load_json(rp, "trims0.json"), result(rp, "b1.load")
        [(topic, earliest)] = f["topic"]["earliest"].items()
        bad = [] if trims else ["nothing was trimmed before the first run"]
        if any(earliest.get(p) != o for p, o in trims.items()):
            bad.append(f"log starts {earliest}, trimmed to {trims}")
        if any(rc(rp, f"b1.{t}") != "0" for t in ("load", "audit", "publish")):
            bad.append(f"b1 exits {[rc(rp, f'b1.{t}') for t in ('load', 'audit', 'publish')]}: "
                       f"{(rp / 'b1.audit.out').read_text().strip()[-300:]}")
        if {int(p): o for p, o in (r.get("starts") or {}).get(topic, {}).items()} != {int(p): o for p, o in earliest.items()}:
            bad.append(f"b1 started at {r.get('starts')}, the log starts {earliest}")
        bad += every_frame_once(f) + bronze_is_trades(f) + through_is_landing(f)
        rows = [c for c in f["checks"] if c[0] == "kafka_fixture/b1"]
        if sorted((c[2], c[3]) for c in rows) != [("accounting", True), ("keys_unique", True), ("parses", True)]:
            bad.append(f"audit_checks {[(c[2], c[3], c[4]) for c in rows]}")
        acc = next((c[4] for c in rows if c[2] == "accounting"), "")
        return say(6, "a topic trimmed before the product's first run loads, audits and publishes cleanly", bad,
                   f"log starts {earliest}; b1 landed {r.get('landed')} from them, staged {r.get('rows')}, accounting: "
                   f"{acc}; publish moved main; every offset from each log start once, bronze main = the trades")
    if name.startswith("rebuild-"):
        return rebuild_claim(name.removeprefix("rebuild-"), rp)
    raise SystemExit(f"kafka_path_probe.py: no claim {name}")


# group d's claims: chain-<label>.json (run_path_probe.py chain-state, each table as its builder),
# facts-<label>.json (facts above, kraken-trades), rebuild-<scenario>.out (rebuild_run's line)
TRADE_KEY = ("venue", "kafka_partition", "kafka_offset", "trade_ordinal")
WINDOW_KEY = ("venue", "instrument_id", "window_start")
WIDTHS = ("1m", "5m", "1h", "1d")


def keyed(rows: list, key: tuple, drop: tuple = ()) -> dict:
    return {tuple(r[c] for c in key): {c: v for c, v in r.items() if c not in drop} for r in rows}


def at(text: str) -> datetime:  # chain-state's timestamp in UTC (access_verify.norm's ISO 8601, or Trino's text)
    return datetime.fromisoformat(text[:19].replace("T", " "))


def window(ts: datetime, width: str) -> datetime:
    return {"1m": ts.replace(second=0), "5m": ts.replace(minute=ts.minute - ts.minute % 5, second=0),
            "1h": ts.replace(minute=0, second=0), "1d": ts.replace(hour=0, minute=0, second=0)}[width]


def built(rp: Path, run: str, want: int = 0) -> list:
    got = (rc(rp, f"{run}.build"), result(rp, f"{run}.build").get("exit"))
    return [] if got == (str(want), want) else [f"{run} build exit {got[0]}, XCom exit {got[1]}, want {want}"]


def lock_cycle(r: dict, product: str) -> list:
    """A rebuild that placed its lock and ended: the lock open while the build ran, as `make rebuild`
    placed it, then released by this rebuild (its release row); nothing open after."""
    held = [d["held"] for d in r["during"]]
    if len(held) != 1 or not held[0] or (held[0]["kind"], held[0]["held_by"]) != ("rebuild", "make rebuild"):
        return [f"{product}: open during the build {held}, want the rebuild lock"]
    rows = [x for x in r["rows"] if x[0] == "rebuild" and x[2] == held[0]["held_at"]]
    bad = [] if [x[3] is None for x in rows] == [True, False] and rows[1][4] == "kafka-path" else [f"{product}: lock rows {rows}"]
    return bad + ([f"{product}: open after {r['open']}"] if r["open"] else [])


def rebuild_claim(name: str, rp: Path) -> bool:
    def chain(label):
        return load_json(rp, f"chain-{label}.json")

    if name == "ct":  # d1
        b, a, r = chain("d0"), chain("d1"), result(rp, "rebuild-ct")
        old, new = keyed(b["canonical"]["rows"], TRADE_KEY), keyed(a["canonical"]["rows"], TRADE_KEY)
        top = max(int(x["load_seq"]) for x in old.values())
        seqs = [int(new[k]["load_seq"]) for k in sorted(new)]
        bad = [] if r.get("exit") == 0 else [f"rebuild exit {r.get('exit')}"]
        if len(a["canonical"]["rows"]) != len(b["canonical"]["rows"]) or set(new) != set(old):
            bad.append(f"canonical {len(b['canonical']['rows'])} -> {len(a['canonical']['rows'])} rows, keys differ")
        elif seqs != list(range(top + 1, top + 1 + len(seqs))):
            bad.append(f"load_seq in key order {seqs}, the old maximum {top}")
        elif any({c: v for c, v in new[k].items() if c != "load_seq"} != {c: v for c, v in old[k].items() if c != "load_seq"} for k in old):
            bad.append("a column other than load_seq changed")
        refs = (b["canonical"]["refs"], a["canonical"]["refs"])
        if refs[1].get("audited") != refs[1].get("main") or refs[1].get("main") in (None, refs[0].get("main")):
            bad.append(f"canonical refs {refs[0]} -> {refs[1]}")
        return say(1, "through the wrapper, k2-dbt build --full-refresh renumbers canonical-trades above its old maximum",
                   bad, f"run {r.get('run')} exit 0; {len(seqs)} rows, the same keys and columns; load_seq "
                   f"{min(seqs, default=0)}..{max(seqs, default=0)} in key order, the old maximum {top}; main moved and "
                   f"audited on it ({refs[0].get('main')} -> {refs[1].get('main')})")
    if name == "candles":  # d2
        b, ct, a = chain("d0"), chain("d1"), chain("d2")
        top = max(int(x["load_seq"]) for x in ct["canonical"]["rows"])
        trades = [x for x in ct["canonical"]["rows"] if not x["is_duplicate"]]
        bad, info = built(rp, "ca-3"), []
        for w in WIDTHS:
            t = f"ohlcv_{w}"
            before, after = keyed(b[t]["rows"], WINDOW_KEY, ("through_load_seq",)), keyed(a[t]["rows"], WINDOW_KEY, ("through_load_seq",))
            mine = {}
            for x in trades:  # this probe's own count and volume per window, over the renumbered rows
                k = (x["venue"], x["instrument_id"], window(at(x["trade_ts"]), w))
                n, v = mine.get(k, (0, Decimal(0)))
                mine[k] = (n + 1, v + Decimal(x["quantity"]))
            theirs = {(k[0], k[1], at(k[2])): (int(x["trade_count"]), Decimal(x["volume"])) for k, x in after.items()}
            throughs = {int(x["through_load_seq"]) for x in a[t]["rows"]}
            if after != before:
                bad.append(f"{t}: {len(before)} -> {len(after)} rows, a value changed")
            if theirs != mine:
                bad.append(f"{t} (count, volume) {theirs}, the rows' own {mine}")
            if throughs != {top}:
                bad.append(f"{t} through_load_seq {sorted(throughs)}, canonical's maximum {top}")
            info.append(f"{t}: {len(after)} window(s), {sum(n for n, _ in theirs.values())} trades, through_load_seq {sorted(throughs)}")
        return say(2, "the next candles build recomputes every window from the renumbered rows: none skipped, none doubled",
                   bad, f"ca-3 exit 0; every window of 1m, 5m, 1h and 1d rewritten (through_load_seq {top}, past the old "
                        f"watermark), its values as before, count and volume this probe's own over {len(trades)} trades", info)
    if name == "fail":  # d3
        b, a, r = chain("d2"), chain("d3"), result(rp, "rebuild-fail")
        err = (rp / "rebuild-fail.err").read_text()
        builds = r.get("during", [])
        bad = [] if r.get("exit") == 1 and "k2-dbt build --full-refresh exited" in err else [f"rebuild exit {r.get('exit')}: {err.strip()[-300:]}"]
        if len(builds) != 1 or builds[0]["exit"] == 0 or builds[0]["xcom"].get("exit") in (None, 0):
            bad.append(f"the build {builds}")
        # the plant passes the unit tests, so the table was replaced and a data test failed on it
        failed = [x.split(" FAIL ")[1].split()[1] for x in err.splitlines() if " FAIL " in x and " of " in x]
        rolled = [x for x in err.splitlines() if x.startswith("k2-dbt: rollback ") and "canonical_trades__base.trades" in x]
        if not failed or not all(f.startswith("k2_unique_key_canonical_trades__trades") for f in failed) or not rolled:
            bad.append(f"failed nodes {failed}, rollback lines {rolled}: no data test failed after the replace")
        refs = (b["canonical"]["refs"], a["canonical"]["refs"])
        if refs[1].get("audited") != refs[0].get("audited") or refs[1].get("main") != refs[1].get("audited"):
            bad.append(f"canonical refs {refs[0]} -> {refs[1]}")
        if sorted(map(json.dumps, a["canonical"]["rows"])) != sorted(map(json.dumps, b["canonical"]["rows"])):
            bad.append("canonical's rows changed")
        planted, open_ = r.get("planted") or {}, r.get("open") or {}
        if (planted.get("kind"), open_.get("kind"), open_.get("held_at")) != ("operator", "operator", planted.get("held_at")):
            bad.append(f"the operator's hold {planted} -> {open_}")
        lock = [d["held"] for d in builds if d["held"]]
        rows = [x for x in r["rows"] if x[0] == "rebuild" and lock and x[2] == lock[0]["held_at"]]
        if not lock or lock[0]["kind"] != "rebuild" or [x[3] is None for x in rows] != [True, False]:
            bad.append(f"the lock {lock}, its rows {rows}")
        if r.get("airflow"):
            bad.append(f"Airflow {r['airflow']} on a DAG paused before")
        return say(3, "a full refresh whose data test fails after the replace leaves the tag where it was; rebuild releases its own lock only",
                   bad, f"exit 1 on release {r.get('release')}: the build exit "
                        f"{builds[0]['exit'] if builds else '?'}, XCom {builds[0]['xcom'] if builds else '?'}; canonical's audited "
                        f"{refs[1].get('audited')} unmoved, main back on it, every row as before; its lock released, the "
                        "operator's hold still open after it, the DAG (paused before) never unpaused",
                   [f"failed {', '.join(failed)}", *(x[:200] for x in rolled)])
    if name == "locked":  # d4
        r = result(rp, "rebuild-locked")
        planted = r.get("planted") or {}
        bad = [] if r.get("exit") == 2 else [f"rebuild exit {r.get('exit')}"]
        if (planted.get("kind"), planted.get("held_by")) != ("rebuild", "make rebuild") or r.get("open") != planted:
            bad.append(f"the open lock {planted} -> {r.get('open')}")
        if r.get("airflow") or r.get("during"):
            bad.append(f"it still acted: Airflow {r.get('airflow')}, builds {r.get('during')}")
        if r.get("unlock") != 0 or r.get("open_after_unlock") is not None:
            bad.append(f"UNLOCK=1 exit {r.get('unlock')}, open after {r.get('open_after_unlock')}")
        return say(4, "a rebuild lock already open refuses the rebuild and is left open; UNLOCK=1 ends it", bad,
                   "exit 2 before any pause or build, the lock as found; unlock() exit 0 with no -rebuild container running, "
                   "nothing open after")
    if name == "replace":  # d5
        f, g = load_json(rp, "facts-d0.json"), load_json(rp, "facts-kt-replaced.json")
        bad = [] if g.get("bronze") else ["no facts after the replace"]
        if bad:
            return say(5, "Spark's CREATE OR REPLACE TABLE leaves bronze with no main and no audit", bad, "")
        gb, fb = g["bronze"], f["bronze"]
        if gb["refs"]:
            bad.append(f"bronze refs after the replace {gb['refs']}")
        if not {s[0] for s in fb["snapshots"]} <= {s[0] for s in gb["snapshots"]}:
            bad.append("a snapshot of bronze's history went")
        if gb["columns"] != fb["columns"]:
            bad.append(f"columns {gb['columns']}")
        if (g["landing"]["refs"], g["landing"]["snapshots"]) != (f["landing"]["refs"], f["landing"]["snapshots"]):
            bad.append("the landing changed")
        return say(5, "Spark's CREATE OR REPLACE TABLE (no AS SELECT) leaves bronze with no main and no audit", bad,
                   f"refs {fb['refs']} -> {{}}; its {len(fb['snapshots'])} old snapshots kept ({len(gb['snapshots'])} listed), "
                   "its columns as before; the landing untouched: main's lineage is empty, so the stage starts at the "
                   "landing's first offset")
    if name == "restage":  # d6
        f, g, r = load_json(rp, "facts-d0.json"), load_json(rp, "facts-d4.json"), result(rp, "rebuild-kt")
        sb, sa = chain("d3"), chain("d4")
        bad = [] if r.get("exit") == 0 else [f"rebuild exit {r.get('exit')}"]
        if g["bronze"].get("main") != f["bronze"].get("main"):
            bad.append(f"bronze main keys {len(f['bronze'].get('main', []))} -> {len(g['bronze'].get('main', []))}")
        if g["bronze"]["rows"].get("main") != f["bronze"]["rows"].get("main"):
            bad.append("a bronze row's envelope or payload differs")
        checks = sorted((c[2], c[3]) for c in g["checks"] if c[0] == r.get("run"))
        if checks != [("accounting", True), ("keys_unique", True), ("parses", True)]:
            bad.append(f"the restage's audit_checks {checks}")
        bad += through_is_landing(g)
        if g["landing"]["snapshots"] != f["landing"]["snapshots"]:
            bad.append("the landing gained a snapshot")
        if sorted(map(json.dumps, sa["silver_trade"]["rows"])) != sorted(map(json.dumps, sb["silver_trade"]["rows"])):
            bad.append(f"silver_trade {len(sb['silver_trade']['rows'])} -> {len(sa['silver_trade']['rows'])} rows, or a value changed")
        refs = (sb["silver_trade"]["refs"], sa["silver_trade"]["refs"])
        if refs[1].get("audited") != refs[1].get("main") or refs[1].get("main") == refs[0].get("main"):
            bad.append(f"silver_trade refs {refs[0]} -> {refs[1]}")
        acc = next((c[4] for c in g["checks"] if c[0] == r.get("run") and c[2] == "accounting"), "")
        return say(6, "a Kafka product's restage from the whole landing gives bronze equal to before, audited", bad,
                   f"run {r.get('run')} exit 0; bronze main {len(g['bronze'].get('main', []))} rows, the same keys, envelope and "
                   f"payload; accounting: {acc}; through-offsets = landing ends, nothing re-landed; silver_trade rebuilt "
                   f"(--full-refresh) to the same {len(sa['silver_trade']['rows'])} rows")
    if name == "downstream":  # d7
        b, a = chain("d3"), chain("d5")
        moved = [t for t in ("canonical", *(f"ohlcv_{w}" for w in WIDTHS))
                 if sorted(map(json.dumps, a[t]["rows"])) != sorted(map(json.dumps, b[t]["rows"]))]
        bad = built(rp, "ct-4") + built(rp, "ca-4") + ([f"changed: {moved}"] if moved else [])
        return say(7, "after a venue's rebuild canonical-trades and candles build and change nothing", bad,
                   "ct-4 and ca-4 exit 0; canonical and every candle table row for row as before (load_seq included)")
    if name == "holds":  # d8
        bad = []
        for scenario, product in (("ct", "market-analytics.canonical-trades"), ("kt", "market-capture.kraken-trades")):
            r = result(rp, f"rebuild-{scenario}")
            bad += lock_cycle(r, product)
            if r.get("airflow") != [f"pause {r.get('dag')}", f"unpause {r.get('dag')}"]:
                bad.append(f"{product}: Airflow {r.get('airflow')}")
        return say(8, "a rebuild places its lock and releases it, and unpauses only the DAG it paused", bad,
                   "canonical-trades and kraken-trades: the rebuild lock (held_by make rebuild) open while each build ran, "
                   "where the DAG's resolve reads it, then its release row (released_by kafka-path), nothing open after; "
                   "the DAG paused and unpaused by the rebuild (claims 3 and 4 hold the paused and the refused cases)")
    raise SystemExit(f"kafka_path_probe.py: no claim rebuild-{name}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "facts":
        print(json.dumps(facts(*args[:2]), default=str))
    elif cmd == "corrupt":
        corrupt(*args[:2])
    elif cmd == "cycles":
        cycles(args[0], args[1], args[2], int(args[3]))
    elif cmd == "expire":
        sys.exit(expire(args[0], args[1], int(args[2])))
    elif cmd == "kill":
        sys.exit(kill(args[0], args[1], args[2:]))
    elif cmd == "rebuild":
        print(json.dumps(rebuild_run(args[0], Path(args[1]), args[2], args[3]), default=str))
    elif cmd == "claim":
        sys.exit(0 if claim(args[0], Path(args[1])) else 1)
    else:
        sys.exit(f"kafka_path_probe.py: no command {cmd}")
