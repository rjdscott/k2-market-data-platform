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
                   "files": [r[0] for r in q(f"SELECT file_path FROM {t}.all_data_files")]}
            for ref, sid in refs.items():
                out[ref] = q(f"SELECT kafka_partition, kafka_offset FROM {t} VERSION AS OF {int(sid)} "
                             "ORDER BY kafka_partition, kafka_offset")
            return out

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
            "landing": table(landing), "bronze": table(bronze),
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
        want = {topic: {p: [ends[int(p)], o] for p, o in trims.items()}}
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
    raise SystemExit(f"kafka_path_probe.py: no claim {name}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "facts":
        print(json.dumps(facts(*args[:2]), default=str))
    elif cmd == "corrupt":
        corrupt(*args[:2])
    elif cmd == "kill":
        sys.exit(kill(args[0], args[1], args[2:]))
    elif cmd == "claim":
        sys.exit(0 if claim(args[0], Path(args[1])) else 1)
    else:
        sys.exit(f"kafka_path_probe.py: no command {cmd}")
