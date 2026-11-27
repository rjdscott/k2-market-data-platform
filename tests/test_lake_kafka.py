"""docker/lake/kafka.py: the Kafka connector's plan, land and stage, their pure cores and their flows
over fakes.

No Spark and no broker: `Topic` fakes the product's topic (log start, end, records), `Lake` the
landing table and the bronze table's main and `audit`. The filter's predicate and the stage's range
predicate run in DuckDB, so the SQL Spark runs is the SQL tested. The shells over Spark are for the
throwaway-lake proof. Values are synthetic.
"""

import json

import connector as C
import duckdb
import kafka as K
import pytest
from test_lake_connector import model  # k2gen's names, executed from model.py

import offsets as O

TOPIC = "test.raw.sample"
LANDING = "lake.sample_trades.landing"
BRONZE = "lake.sample_trades.bronze_trade"
RELEASE = "v3.0.0-alpha.6-rc.1-0123456789ab"
PIPELINE = {
    "product": "market-capture.sample-trades", "fence": "sample_trades", "domain": "market-capture",
    "status": "active", "dagId": "sample_trades", "schedule": {"cron": "*/5 * * * *"}, "inputs": [],
    "maintain": {"cron": "17 3 * * *"},
    "connector": {"kind": "kafka", "brokers": "redpanda:9092", "registry": "http://redpanda:8081", "topic": TOPIC,
                  "envelope": {"format": "avro-confluent", "contract": "market-capture.raw-message.raw_message"},
                  "filter": {"field": "stream", "in": ["trade"]}, "payload": "json",
                  "startFrom": "earliest-retained", "maxOffsetsPerPartition": 100},
    "landing": {"table": LANDING},
    "bronze": [{"table": BRONZE, "key": ["kafka_partition", "kafka_offset"], "values": [], "audits": [],
                "branch": "audit", "checks": "lake.sample_trades.audit_checks"}],
}


def run_id(n):
    return f"sample_trades/scheduled__2026-11-28T00:{n:02d}:00+00:00"


def ctx(n=0, accept=False, pipeline=PIPELINE):
    return C.Run(release=RELEASE, product=pipeline["product"], run_id=run_id(n), pipeline=pipeline,
                 accept_data_loss=accept)


def frame(stream):
    """A record's value: a synthetic stand-in for a framed envelope, the stream readable by the fake
    lake; None makes bytes that are not framed at all."""
    if stream is None:
        return b"not framed"
    return b"\x00\x00\x00\x00\x01" + json.dumps({"stream": stream}).encode()


def envelope(value):
    return json.loads(value[5:]) if value.startswith(b"\x00") else None


def duck_keep(keep, envelopes):
    """The filter's verdict per envelope, by DuckDB running keep_sql's predicate (Spark's backtick
    quoting as DuckDB's double quotes)."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t (i INTEGER, envelope STRUCT(stream VARCHAR, symbol VARCHAR))")
    for i, e in enumerate(envelopes):
        con.execute("INSERT INTO t VALUES (?, ?)", [i, None if e is None else {"stream": None, "symbol": None, **e}])
    return [r[0] for r in con.execute(f"SELECT {keep.replace('`', chr(34))} FROM t ORDER BY i").fetchall()]


class Topic:
    """One topic: per partition a log start and records at dense offsets, each (stream, key, value)."""

    def __init__(self, partitions=2):
        self.records = {p: {} for p in range(partitions)}
        self.log_start = {p: 0 for p in range(partitions)}
        self.reads = []

    def produce(self, partition, *streams):
        for s in streams:
            offset = self.end(partition)
            self.records[partition][offset] = (s, f"k{offset}".encode(), frame(s))

    def evict(self, partition, below):
        self.log_start[partition] = below
        self.records[partition] = {o: r for o, r in self.records[partition].items() if o >= below}

    def end(self, partition):
        return max(self.records[partition], default=self.log_start[partition] - 1) + 1

    def offsets(self, topic):
        return ({topic: dict(self.log_start)}, {topic: {p: self.end(p) for p in self.records}})

    def frames(self, topic, starts, ends):
        self.reads.append((starts, ends))
        return [{"partition": p, "offset": o, "timestamp": f"2026-11-28T00:00:{o % 60:02d}+00:00",
                 "key": r[1], "value": r[2]}
                for p in sorted(ends[topic]) for o, r in sorted(self.records[p].items())
                if starts[topic][p] <= o < ends[topic][p]]

    @staticmethod
    def counts(frames):
        out = {}
        for f in frames:
            n, lo, hi = out.get(f["partition"], (0, f["offset"], f["offset"]))
            out[f["partition"]] = (n + 1, min(lo, f["offset"]), max(hi, f["offset"]))
        return [(p, *v) for p, v in sorted(out.items())]


class Lake:
    """The landing (main only) and bronze (main and audit): snapshots with parent, summary and the
    table's rows at each."""

    def __init__(self):
        self.next_id, self.snaps = 0, {}
        self.refs_ = {LANDING: {}, BRONZE: {}}

    def _commit(self, table, parent, rows, summary):
        self.next_id += 1
        self.snaps[self.next_id] = {"table": table, "parent": parent, "summary": summary,
                                    "rows": (self.snaps[parent]["rows"] if parent else []) + rows}
        return self.next_id

    def lineage(self, head):
        out = []
        while head:
            out.append(head)
            head = self.snaps[head]["parent"]
        return out

    def rows(self, table, ref="main"):
        head = self.refs_[table].get(ref)
        return self.snaps[head]["rows"] if head else []

    def history(self, table):
        head = self.refs_[table].get("main")
        return head, [(s, self.snaps[s]["summary"]) for s in self.lineage(head)]

    def land(self, table, frames, values, props):
        rows = [{**{dst: f[src] for dst, src in K.LANDING_SOURCE}, **values} for f in frames]
        self.refs_[table]["main"] = self._commit(table, self.refs_[table].get("main"), rows, props)

    def maintain(self, table, summary=None):
        """A compaction or expiry snapshot: the same rows, no k2 properties."""
        main = self.refs_[table]["main"]
        self.refs_[table]["main"] = self._commit(table, main, [], summary or {"operation": "replace"})
        if self.refs_[table].get("audit") == main:
            self.refs_[table]["audit"] = self.refs_[table]["main"]

    # bronze, as connector.SparkLake and kafka.SparkLanding have it
    def refs(self, table):
        return dict(self.refs_[table])

    def published(self, table, snapshot):
        return snapshot in self.lineage(self.refs_[table].get("main"))

    def batch_of(self, table, snapshot):
        return self.snaps[snapshot]["summary"].get("k2.batch-id")

    def create_branch(self, table):
        self.refs_[table]["audit"] = self.refs_[table].get("main")

    def reset_branch(self, table, snapshot):
        self.refs_[table]["audit"] = snapshot

    def landed(self, landing, snapshot, ranges, keep):
        rows = [r for r in self.snaps[snapshot]["rows"]
                if r["kafka_partition"] in ranges
                and ranges[r["kafka_partition"]][0] <= r["kafka_offset"] < ranges[r["kafka_partition"]][1]]
        return [{**r, K.KEPT: k} for r, k in zip(rows, duck_keep(keep, [envelope(r["record_value"]) for r in rows]))]

    @staticmethod
    def stage_counts(frames):
        out = {}
        for f in frames:
            n, kept, skipped, lo, hi = out.get(f["kafka_partition"], (0, 0, 0, f["kafka_offset"], f["kafka_offset"]))
            out[f["kafka_partition"]] = (n + 1, kept + (f[K.KEPT] is True), skipped + (f[K.KEPT] is False),
                                         min(lo, f["kafka_offset"]), max(hi, f["kafka_offset"]))
        return [(p, *v) for p, v in sorted(out.items())]

    def stage_rows(self, table, frames, props):
        rows = [{k: v for k, v in f.items() if k != K.KEPT} for f in frames if f[K.KEPT]]
        self.refs_[table]["audit"] = self._commit(table, self.refs_[table].get("audit"), rows, props)
        return len(rows)

    def publish(self, table=BRONZE):  # what connector.publish's fast-forward does, once audited
        self.refs_[table]["main"] = self.refs_[table]["audit"]


def offsets_of(rows):
    return sorted((r["kafka_partition"], r["kafka_offset"]) for r in rows)


def newest_summary(lake, table, ref="main"):
    return lake.snaps[lake.refs_[table][ref]]["summary"]


# --- plan ------------------------------------------------------------------------------------


def connector_snapshot(ends):
    return {"k2.job": K.JOB_CONNECTOR, "k2.kafka-offsets": O.encode({TOPIC: ends})}


def test_resume_is_the_newest_connector_snapshot_walking_back_past_offsetless_ones():
    history = [
        (1, connector_snapshot({0: 10, 1: 20})),
        (2, connector_snapshot({0: 30, 1: 40})),
        (3, {"operation": "replace"}),  # compaction
        (4, {"operation": "delete"}),  # expiry
        (5, {"k2.job": K.JOB_CONNECTOR}),  # a connector commit without offsets
        (6, {"k2.job": O.JOB_MAINTENANCE, "k2.kafka-offsets": O.encode({TOPIC: {0: 99, 1: 99}})}),
        (7, None),
    ]
    p = K.plan(history, {TOPIC: {0: 0, 1: 0}}, {TOPIC: {0: 50, 1: 50}}, 100)
    assert p.starts == {TOPIC: {0: 30, 1: 40}} and p.ends == {TOPIC: {0: 50, 1: 50}}
    assert p.offsets == {TOPIC: {0: 50, 1: 50}} and p.gaps == []


def test_no_connector_snapshot_starts_at_the_log_start():
    p = K.plan([(1, {"operation": "replace"})], {TOPIC: {0: 7, 1: 0}}, {TOPIC: {0: 9, 1: 3}}, 100)
    assert p.starts == {TOPIC: {0: 7, 1: 0}} and p.ends == {TOPIC: {0: 9, 1: 3}}


def test_each_partition_is_bounded_and_the_rest_is_backlog():
    p = K.plan([(1, connector_snapshot({0: 10, 1: 10}))], {TOPIC: {0: 0, 1: 0}}, {TOPIC: {0: 500, 1: 15}}, 100)
    assert p.ends == {TOPIC: {0: 110, 1: 15}} and p.backlog == {TOPIC: 390}


def test_below_the_log_start_is_refused_naming_each_partition():
    history = [(1, connector_snapshot({0: 5, 1: 50, 2: 3}))]
    with pytest.raises(C.Refused) as e:
        K.plan(history, {TOPIC: {0: 10, 1: 0, 2: 8}}, {TOPIC: {0: 60, 1: 60, 2: 60}}, 100)
    text = str(e.value)
    assert f"{TOPIC}/0 committed 5, log start 10, 5 records gone" in text
    assert f"{TOPIC}/2 committed 3, log start 8, 5 records gone" in text
    assert f"{TOPIC}/1 " not in text


def test_accept_data_loss_resumes_at_the_log_start_and_returns_the_gap():
    history = [(1, connector_snapshot({0: 5, 1: 50, 2: 3}))]
    p = K.plan(history, {TOPIC: {0: 10, 1: 0, 2: 8}}, {TOPIC: {0: 60, 1: 60, 2: 60}}, 100, accept_data_loss=True)
    assert p.starts == {TOPIC: {0: 10, 1: 50, 2: 8}}
    assert p.gaps == [(TOPIC, 0, 5, 10, 5), (TOPIC, 2, 3, 8, 5)]
    assert json.loads(K.gaps_json(p.gaps)) == {TOPIC: {"0": [5, 10], "2": [3, 8]}}


# --- land ------------------------------------------------------------------------------------


def test_every_record_lands_byte_for_byte_with_its_end_offsets_in_the_same_commit():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "heartbeat", "trade", None)
    topic.produce(1, "subscriptions", "trade")
    code, result = K.land(ctx(), topic, lake)
    assert code == C.EXIT_OK and result["landed"] == 6
    rows, props = lake.rows(LANDING), newest_summary(lake, LANDING)
    assert offsets_of(rows) == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1)]  # control frames too
    assert all(r["record_value"] == topic.records[r["kafka_partition"]][r["kafka_offset"]][2] for r in rows)
    assert all(r["record_key"] == topic.records[r["kafka_partition"]][r["kafka_offset"]][1] for r in rows)
    assert O.decode(props["k2.kafka-offsets"]) == {TOPIC: {0: 4, 1: 2}}
    assert O.decode(props[K.KAFKA_STARTS]) == {TOPIC: {0: 0, 1: 0}}
    assert (props["k2.job"], props["k2.batch-id"], props["k2.run-id"], props["k2.release"]) == (
        K.JOB_CONNECTOR, run_id(0), run_id(0), RELEASE)
    assert {r["batch_id"] for r in rows} == {run_id(0)} and K.KAFKA_GAPS not in props


def test_a_read_short_of_its_span_commits_nothing():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "trade", "trade")
    del topic.records[0][1]  # a hole the broker's offsets do not show
    topic.records[0][3] = topic.records[0][2]
    assert K.land(ctx(), topic, lake)[0] == C.EXIT_REFUSED
    assert lake.refs_[LANDING] == {}


def test_a_load_reads_at_most_the_pipelines_bound_per_partition():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, *["trade"] * 5)
    bounded = {**PIPELINE, "connector": {**PIPELINE["connector"], "maxOffsetsPerPartition": 2}}
    assert K.land(ctx(1, pipeline=bounded), topic, lake)[1]["landed"] == 2
    assert K.land(ctx(2, pipeline=bounded), topic, lake)[1]["backlog"] == {TOPIC: 1}
    assert offsets_of(lake.rows(LANDING)) == [(0, 0), (0, 1), (0, 2), (0, 3)]


def test_a_retry_lands_only_what_is_new():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "trade")
    K.land(ctx(1), topic, lake)
    assert K.land(ctx(1), topic, lake)[1]["landed"] == 0  # the same run again: nothing new
    topic.produce(0, "trade")
    assert K.land(ctx(1), topic, lake)[1]["landed"] == 1
    assert offsets_of(lake.rows(LANDING)) == [(0, 0), (0, 1), (0, 2)]


def test_below_the_log_start_load_lands_nothing_and_accept_data_loss_commits_the_gap_with_the_skip():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    topic.produce(0, "trade", "trade", "trade")
    topic.evict(0, 5)  # everything past the landing's end, 2, evicted: nothing new survives
    code, _ = K.load(ctx(2), topic, lake)
    head = lake.refs_[LANDING]["main"]
    assert code == C.EXIT_REFUSED and lake.refs_[LANDING]["main"] == head
    code, result = K.load(ctx(3, accept=True), topic, lake)
    assert code == C.EXIT_OK and result["offsetGaps"] == [
        {"topic": TOPIC, "partition": 0, "committed": 2, "logStart": 5, "lost": 3}]
    props = newest_summary(lake, LANDING)  # a commit of no rows: the skip and its record in one
    assert lake.refs_[LANDING]["main"] != head and json.loads(props[K.KAFKA_GAPS]) == {TOPIC: {"0": [2, 5]}}
    assert O.decode(props["k2.kafka-offsets"]) == {TOPIC: {0: 5}}
    lake.publish()
    topic.produce(0, "trade")
    assert K.load(ctx(4), topic, lake)[0] == C.EXIT_OK  # no flag needed past a recorded gap
    assert offsets_of(lake.rows(LANDING)) == [(0, 0), (0, 1), (0, 5)]


def test_a_paused_product_lands_nothing():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade")
    assert K.load(ctx(pipeline={**PIPELINE, "status": "paused"}), topic, lake)[0] == C.EXIT_OK
    assert lake.refs_[LANDING] == {} and topic.reads == []


# --- stage -----------------------------------------------------------------------------------


def test_bronze_takes_the_kept_records_and_staged_plus_skipped_is_the_range_read():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "heartbeat", "trade", None)
    topic.produce(1, "subscriptions", "trade")
    code, result = K.load(ctx(), topic, lake)
    assert code == C.EXIT_OK and (result["rows"], result["skipped"]) == (4, 2)
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 0), (0, 2), (0, 3), (1, 1)]  # trades, and the unframed
    props = newest_summary(lake, BRONZE, "audit")
    assert O.decode(props[K.KAFKA_SKIPPED]) == {TOPIC: {0: 1, 1: 1}}
    assert props["k2.src-snapshot-id"] == str(lake.refs_[LANDING]["main"]) and props["k2.batch-id"] == run_id(0)
    assert O.decode(props["k2.kafka-offsets"]) == O.decode(newest_summary(lake, LANDING)["k2.kafka-offsets"])
    assert lake.rows(BRONZE) == []  # staged, not published


def test_a_kill_after_the_landing_commit_is_staged_by_the_next_run():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "trade")
    topic.produce(1, "trade")
    K.land(ctx(1), topic, lake)  # killed here: landed, never staged
    topic.produce(0, "trade")
    code, result = K.load(ctx(2), topic, lake)
    assert code == C.EXIT_OK and result["landed"] == 1
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 0), (0, 1), (0, 2), (1, 0)]  # no hole, nothing twice


def test_a_tail_of_control_frames_advances_the_through_offset():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "heartbeat", "heartbeat")
    K.load(ctx(1), topic, lake)
    lake.publish()
    assert O.decode(newest_summary(lake, BRONZE)["k2.kafka-offsets"]) == {TOPIC: {0: 3}}
    code, result = K.load(ctx(2), topic, lake)  # nothing new: the two heartbeats are not read again
    assert code == C.EXIT_OK and result["rows"] == 0 and lake.refs_[BRONZE]["audit"] == lake.refs_[BRONZE]["main"]


def test_two_landed_ranges_one_staged_the_second_stages_alone():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    lake.maintain(LANDING)  # a compaction between: no offsets on the landing's newest snapshot
    topic.produce(0, "trade", "trade", "trade")
    K.land(ctx(2), topic, lake)
    lake.maintain(BRONZE)
    code, result = K.stage(ctx(2), lake)
    assert code == C.EXIT_OK and result["rows"] == 3
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]
    lake.publish()  # after a clean run the through-offsets are the landing's end offsets
    assert O.decode(newest_summary(lake, BRONZE)["k2.kafka-offsets"]) == {TOPIC: {0: 5}}


def test_a_failed_bronze_blocks_bronze_never_the_landing():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)  # its audit fails: the batch waits on `audit`, unpublished
    topic.produce(0, "trade", "trade")
    code, result = K.load(ctx(2), topic, lake)
    assert code == C.EXIT_REFUSED and result["landed"] == 2
    assert topic.reads[-1] == ({TOPIC: {0: 2}}, {TOPIC: {0: 4}})  # after the landed end, never again from 0
    assert offsets_of(lake.rows(LANDING)) == [(0, 0), (0, 1), (0, 2), (0, 3)]
    lake.reset_branch(BRONZE, lake.refs_[BRONZE].get("main"))  # the failed batch discarded
    topic.evict(0, 4)  # the topic no longer holds any of it; the landing does
    code, result = K.load(ctx(3), topic, lake)
    assert code == C.EXIT_OK and result["landed"] == 0 and result["rows"] == 4
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 0), (0, 1), (0, 2), (0, 3)]


def test_a_retry_of_a_staged_batch_stages_nothing_twice():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade")
    K.load(ctx(1), topic, lake)
    head = lake.refs_[BRONZE]["audit"]
    assert K.load(ctx(1), topic, lake)[0] == C.EXIT_OK and lake.refs_[BRONZE]["audit"] == head


def test_bronze_past_the_landing_is_refused():
    with pytest.raises(C.Refused, match=r"partitions \[1\]"):
        K.stage_ranges({TOPIC: {0: 3, 1: 9}}, {TOPIC: {0: 5, 1: 4}})
    assert K.stage_ranges({TOPIC: {0: 3}}, {TOPIC: {0: 5, 1: 4}}) == {0: (3, 5), 1: (0, 4)}


def test_the_stage_refuses_a_row_neither_kept_nor_skipped_or_outside_its_range():
    with pytest.raises(C.Refused, match="3 read, 2 kept, 0 skipped"):
        K.stage_account(TOPIC, {0: (0, 3)}, [(0, 3, 2, 0, 0, 2)])
    with pytest.raises(C.Refused, match="offsets 0..3"):
        K.stage_account(TOPIC, {0: (0, 3)}, [(0, 4, 4, 0, 0, 3)])
    assert K.stage_account(TOPIC, {0: (0, 3)}, [(0, 3, 2, 1, 0, 2)]) == {TOPIC: {0: 1}}


# --- the SQL and the names -------------------------------------------------------------------


def test_the_filter_keeps_its_values_and_what_does_not_decode():
    keep = K.keep_sql({"field": "stream", "in": ["trade"]})
    assert duck_keep(keep, [{"stream": "trade"}, {"stream": "heartbeat"}, None, {"stream": None}]) == [
        True, False, True, False]
    assert duck_keep(K.keep_sql({"field": "symbol", "in": ["A/B", "C/D"]}),
                     [{"symbol": "C/D"}, {"symbol": "E/F"}]) == [True, False]
    assert K.keep_sql(None) == "true"
    with pytest.raises(ValueError):
        K.keep_sql({"field": "stream) OR (true", "in": ["trade"]})


def test_the_range_predicate_selects_each_partitions_half_open_range():
    where, args = K.range_sql({0: (2, 4), 1: (0, 1)})
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT p AS kafka_partition, o AS kafka_offset "
                "FROM range(3) a(p), range(6) b(o)")
    sql = f"SELECT kafka_partition, kafka_offset FROM t WHERE {where} ORDER BY 1, 2".replace(":", "$")
    assert con.execute(sql, args).fetchall() == [(0, 2), (0, 3), (1, 0)]


def test_the_landing_columns_are_k2gens():
    want = [c.name for c in model.PLATFORM_TABLES[model.KAFKA_LANDING].columns]
    assert [dst for dst, _ in K.LANDING_SOURCE] + list(K.LANDING_RUN) == want
    assert [src for _, src in K.LANDING_SOURCE] == ["partition", "offset", "timestamp", "key", "value"]


def test_connector_dispatches_a_kafka_pipeline_to_kafka_py():
    assert C.task_of("load", "kafka") is K.load and C.task_of("publish", "kafka") is C.publish
    assert C.task_of("load", "https-file") is C.load
    argv = ["load", "--release", RELEASE, "--product", PIPELINE["product"], "--run-id", run_id(0)]
    task, run = C.parse_args([*argv, "--accept-data-loss"], lambda path: PIPELINE)
    assert task == "load" and run.accept_data_loss is True
    assert C.parse_args(argv, lambda path: PIPELINE)[1].accept_data_loss is False
    with pytest.raises(SystemExit):
        C.parse_args(["audit", *argv[1:], "--accept-data-loss"], lambda path: PIPELINE)
    file_pipeline = {**PIPELINE, "connector": {"kind": "https-file"}}
    with pytest.raises(SystemExit):
        C.parse_args([*argv, "--accept-data-loss"], lambda path: file_pipeline)
