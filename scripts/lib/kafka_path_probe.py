#!/usr/bin/env python3
"""scripts/verify/kafka-path.sh's steps. Three run in the throwaway's Spark container on the
release's own code, fed on stdin (`python3 - <command> /k2/releases/<id> ...`); `claim` runs on the
host over what they printed.

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
    claim   <name> <rp dir>                     one PASS or FAIL line (and INFO lines) for a claim
Synthetic data only; never a key's value.
"""

from __future__ import annotations

import json
import os
import signal
import sys
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
        f8, f = load_json(rp, "facts-k8.json"), load_json(rp, "facts-k9.json")
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
        f8, f = load_json(rp, "facts-k8.json"), load_json(rp, "facts-k9.json")
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
                   "every later stage refused behind it until an operator resets audit")
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
        seen = {}
        for p, o in f["landing"]["main"]:
            seen.setdefault(p, []).append(o)
        for p, end in topic_ends(f)[topic].items():
            lo = trims.get(str(p))
            want_offsets = list(range(ends.get(p, 0))) + list(range(lo if lo is not None else ends.get(p, 0), end))
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
    raise SystemExit(f"kafka_path_probe.py: no claim {name}")


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
    elif cmd == "claim":
        sys.exit(0 if claim(args[0], Path(args[1])) else 1)
    else:
        sys.exit(f"kafka_path_probe.py: no command {cmd}")
