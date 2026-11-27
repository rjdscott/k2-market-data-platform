"""docker/lake/kafka.py: the Kafka connector's plan, land and stage, their pure cores and their flows
over fakes.

No Spark and no broker: `Topic` fakes the product's topic (log start, end, records), `Lake` the
landing table and the bronze table's main and `audit`. The filter's predicate and the stage's range
predicate run in DuckDB, so the SQL Spark runs is the SQL tested. The shells over Spark are for the
throwaway-lake proof. Values are synthetic.
"""

import json
from pathlib import Path

import connector as C
import duckdb
import kafka as K
import pytest
from test_lake_connector import model  # k2gen's names, executed from model.py

import offsets as O
import wire

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


def frame(stream, sid=1, **fields):
    """A record's value: a synthetic stand-in for a framed envelope under writer schema `sid`, its
    fields readable by the fake lake; None makes bytes that are not framed at all."""
    if stream is None:
        return b"not framed"
    return b"\x00" + sid.to_bytes(4, "big") + json.dumps({"stream": stream, **fields}).encode()


# The writer schemas the fake registry serves as RawMessage, by id, as Spark types their decoded
# fields: 1 the current one, 2 one without symbol, 3 one with symbol retyped, 4 one with a field added.
WRITERS = {1: {"stream": "string", "symbol": "string"}, 2: {"stream": "string"},
           3: {"stream": "string", "symbol": "bigint"}, 4: {"stream": "string", "symbol": "string", "seq": "bigint"}}
ENVELOPE_COLUMNS = {"stream": "string", "symbol": "string"}  # the fake bronze's, as its contract declares them
BRONZE_COLUMNS = [*K.BRONZE_LEAD, *ENVELOPE_COLUMNS, *K.LANDING_RUN]


def decode(value):
    """`(schema id, envelope)`: the id None when unframed; the envelope None as Spark's PERMISSIVE
    read and decoded_sql have it, for an id the registry does not serve as RawMessage or a body
    that does not read."""
    try:
        sid, body = wire.parse_confluent(value)
    except ValueError:
        return None, None
    try:
        return sid, (json.loads(body) if sid in WRITERS else None)
    except ValueError:
        return sid, None


def envelope(value):
    return decode(value)[1]


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
        """Each a stream name, None (unframed), or the value's bytes as given."""
        for s in streams:
            offset = self.end(partition)
            self.records[partition][offset] = (s, f"k{offset}".encode(), s if isinstance(s, bytes) else frame(s))

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
        self.next_id, self.snaps, self.checks = 0, {}, []
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

    def landed(self, landing, snapshot, ranges, keep, bronze):
        """As SparkLanding.landed: schema_id, and each envelope column by name where the writer
        schema's field reads as it (kafka.compatible), else null."""
        assert bronze == BRONZE
        rows = [r for r in self.snaps[snapshot]["rows"]
                if r["kafka_partition"] in ranges
                and ranges[r["kafka_partition"]][0] <= r["kafka_offset"] < ranges[r["kafka_partition"]][1]]
        out = []
        for r, k in zip(rows, duck_keep(keep, [envelope(r["record_value"]) for r in rows])):
            sid, e = decode(r["record_value"])
            fields = WRITERS[sid] if e is not None else {}
            out.append({**r, "schema_id": sid, K.KEPT: k,
                        **{n: e.get(n) if K.compatible(fields.get(n), t) else None for n, t in ENVELOPE_COLUMNS.items()}})
        return out

    @staticmethod
    def stage_counts(frames):
        out = {}
        for f in frames:
            n, kept, skipped, lo, hi = out.get(f["kafka_partition"], (0, 0, 0, f["kafka_offset"], f["kafka_offset"]))
            out[f["kafka_partition"]] = (n + 1, kept + (f[K.KEPT] is True), skipped + (f[K.KEPT] is False),
                                         min(lo, f["kafka_offset"]), max(hi, f["kafka_offset"]))
        return [(p, *v) for p, v in sorted(out.items())]

    def stage_rows(self, table, frames, props):
        rows = [{k: f[k] for k in BRONZE_COLUMNS} for f in frames if f[K.KEPT]]  # by name, as Spark's select
        self.refs_[table]["audit"] = self._commit(table, self.refs_[table].get("audit"), rows,
                                                  {**props, "added-records": str(len(rows))})  # as Iceberg adds it
        return len(rows)

    def publish(self, table=BRONZE):  # what connector.publish's fast-forward does, once audited
        self.refs_[table]["main"] = self.refs_[table]["audit"]

    # the audit, as kafka.SparkLanding has it
    def summary_of(self, table, snapshot):
        return dict(self.snaps[snapshot]["summary"])

    def _in(self, snapshot, ranges):
        return [r for r in (self.snaps[snapshot]["rows"] if snapshot else [])
                if r["kafka_partition"] in ranges
                and ranges[r["kafka_partition"]][0] <= r["kafka_offset"] < ranges[r["kafka_partition"]][1]]

    def firsts(self, table, snapshot, partitions):
        found = {}
        for r in self.snaps[snapshot]["rows"]:
            if r["kafka_partition"] in partitions:
                found[r["kafka_partition"]] = min(found.get(r["kafka_partition"], r["kafka_offset"]), r["kafka_offset"])
        return found

    def offset_counts(self, table, snapshot, ranges):
        found = {}
        for r in self._in(snapshot, ranges):
            found.setdefault(r["kafka_partition"], []).append(r["kafka_offset"])
        return [(p, len(o), len(set(o)), min(o), max(o)) for p, o in sorted(found.items())]

    def unparsed(self, landing, src, table, snapshot, ranges, keep):
        """As SparkLanding.unparsed: bronze's frames, their bytes read again from the landing at `src`."""
        assert landing == LANDING
        staged = {(r["kafka_partition"], r["kafka_offset"]) for r in self._in(snapshot, ranges)}
        rows = sorted((r for r in self._in(src, ranges) if (r["kafka_partition"], r["kafka_offset"]) in staged),
                      key=lambda r: (r["kafka_partition"], r["kafka_offset"]))
        envelopes = [envelope(r["record_value"]) for r in rows]
        bad = []
        for r, e, kept in zip(rows, envelopes, duck_keep(keep, envelopes)):
            sid = decode(r["record_value"])[0]
            problem = ", and ".join(K.problems(WRITERS[sid], ENVELOPE_COLUMNS)) or None if e is not None else None
            if e is None or not kept or problem:
                bad.append((r["kafka_partition"], r["kafka_offset"], sid, sid in WRITERS, e is not None, problem))
        return len(bad), bad[:K.LISTED]

    def append(self, table, records, props):  # audit_checks
        assert table == PIPELINE["bronze"][0]["checks"] and props["k2.job"] == K.JOB_CONNECTOR
        self.checks.extend(records)


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
    assert json.loads(K.gaps_json(K.carried_gaps({}, p.gaps))) == {TOPIC: {"0": [[5, 10]], "2": [[3, 8]]}}


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
    assert lake.refs_[LANDING]["main"] != head and json.loads(props[K.KAFKA_GAPS]) == {TOPIC: {"0": [[2, 5]]}}
    assert O.decode(props["k2.kafka-offsets"]) == {TOPIC: {0: 5}}
    lake.publish()
    topic.produce(0, "trade")
    assert K.load(ctx(4), topic, lake)[0] == C.EXIT_OK  # no flag needed past a recorded gap
    assert offsets_of(lake.rows(LANDING)) == [(0, 0), (0, 1), (0, 5)]


def gaps_now(lake):
    return json.loads(newest_summary(lake, LANDING).get(K.KAFKA_GAPS, "null"))


def test_an_accepted_gap_is_carried_on_every_later_landing_commit_and_none_ever_means_no_key():
    topic, lake = Topic(partitions=2), Lake()
    for n in range(1, 4):  # no loss yet: no key on any commit
        topic.produce(0, "trade")
        K.load(ctx(n), topic, lake)
        lake.publish()
        assert gaps_now(lake) is None
    topic.produce(0, "trade", "trade", "trade")
    topic.evict(0, 5)  # offsets 3 and 4 gone
    K.load(ctx(4, accept=True), topic, lake)  # run N
    lake.publish()
    for n in range(5, 8):  # runs N+1 to N+3, clean, the loss's commit no longer the newest
        topic.produce(1, "trade")
        assert K.load(ctx(n), topic, lake)[0] == C.EXIT_OK
        lake.publish()
        assert gaps_now(lake) == {TOPIC: {"0": [[3, 5]]}}
    assert K.accepted_losses(lake.history(LANDING)[1], TOPIC) == {0: [(3, 4)]}  # once, though seven commits read


def test_two_gaps_in_one_partition_are_both_carried_and_adjacent_ones_merge():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    for n, below in ((2, 4), (3, 7)):  # a loss, a clean landing of what survived, then another loss
        topic.produce(0, "trade", "trade", "trade")
        topic.evict(0, below)
        K.load(ctx(n, accept=True), topic, lake)
        lake.publish()
    assert gaps_now(lake) == {TOPIC: {"0": [[2, 4], [5, 7]]}}
    assert K.accepted_losses(lake.history(LANDING)[1], TOPIC) == {0: [(2, 3), (5, 6)]}
    assert K.carried_gaps({TOPIC: {0: [(2, 4)]}}, [(TOPIC, 0, 4, 7, 3), (TOPIC, 1, 0, 2, 2)]) == {
        TOPIC: {0: [(2, 7)], 1: [(0, 2)]}}  # one that starts where the last ended is one gap


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
        K.stage_ranges({TOPIC: {0: 3, 1: 9}}, {TOPIC: {0: 5, 1: 4}}, {})
    assert K.stage_ranges({TOPIC: {0: 3}}, {TOPIC: {0: 5, 1: 4}}, {1: 0}) == {0: (3, 5), 1: (0, 4)}


def test_a_partition_bronze_never_staged_starts_at_the_landings_first_offset_never_0():
    assert K.stage_ranges({TOPIC: {0: 3}}, {TOPIC: {0: 5, 1: 9, 2: 7}}, {1: 6}) == {0: (3, 5), 1: (6, 9)}
    assert K.stage_ranges({}, {TOPIC: {0: 5}}, {0: 2}) == {0: (2, 5)}  # 2 holds no landing row: nothing


def trimmed(partitions, produced, log_start):
    """A topic whose every partition holds `produced` records, those below `log_start` evicted."""
    topic = Topic(partitions)
    for p in range(partitions):
        topic.produce(p, *["trade", "heartbeat"] * (produced // 2))
        topic.evict(p, log_start)
    return topic


def test_a_topic_trimmed_before_the_first_run_lands_audits_and_publishes_from_its_log_start():
    topic, lake = trimmed(2, 8, 5), Lake()
    code, result = K.load(ctx(1), topic, lake)
    assert code == C.EXIT_OK and result["starts"] == {TOPIC: {0: 5, 1: 5}} and result["landed"] == 6
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 6), (1, 6)]  # the trades, from the log start
    code, checks = audited(lake, 1)
    assert code == C.EXIT_OK and checks["accounting"] == (True, "2 staged + 4 skipped = 6 landed over 2 partitions")
    lake.publish()
    topic.produce(0, "trade")
    K.load(ctx(2), topic, lake)  # the next resumes at the through-offsets, as every later run
    assert audited(lake, 2)[0] == C.EXIT_OK and offsets_of(lake.rows(BRONZE, "audit")) == [(0, 6), (0, 8), (1, 6)]


def test_a_partition_added_later_with_its_log_start_above_0_stages_from_it():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade")
    K.load(ctx(1), topic, lake)
    K.audit(ctx(1), None, lake)
    lake.publish()
    topic.records[1], topic.log_start[1] = {}, 0  # partition 1 added, and trimmed before any run read it
    topic.produce(1, *["trade"] * 6)
    topic.evict(1, 4)
    K.load(ctx(2), topic, lake)
    code, checks = audited(lake, 2)
    assert code == C.EXIT_OK and checks["accounting"] == (True, "2 staged + 0 skipped = 2 landed over 1 partitions")
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 0), (1, 4), (1, 5)]


def test_an_accept_data_loss_run_that_is_bronzes_first_stage_plans_from_the_landings_first_offset():
    topic, lake = trimmed(1, 4, 2), Lake()  # log start 2
    K.land(ctx(1, accept=True), topic, lake)  # the first run, the flag a no-op on it; killed before its stage
    topic.produce(0, "trade", "trade", "trade")
    topic.evict(0, 6)  # 4 and 5 gone before they landed
    code, result = K.load(ctx(2, accept=True), topic, lake)
    assert code == C.EXIT_OK and result["offsetGaps"][0]["committed"] == 4
    code, checks = audited(lake, 2)
    assert code == C.EXIT_OK and checks["accounting"] == (True, "2 staged + 1 skipped = 3 landed over 1 partitions")
    assert offsets_of(lake.rows(BRONZE, "audit")) == [(0, 2), (0, 6)]


def test_the_stage_refuses_a_row_neither_kept_nor_skipped_or_outside_its_range():
    with pytest.raises(C.Refused, match="3 read, 2 kept, 0 skipped"):
        K.stage_account(TOPIC, {0: (0, 3)}, [(0, 3, 2, 0, 0, 2)])
    with pytest.raises(C.Refused, match="offsets 0..3"):
        K.stage_account(TOPIC, {0: (0, 3)}, [(0, 4, 4, 0, 0, 3)])
    assert K.stage_account(TOPIC, {0: (0, 3)}, [(0, 3, 2, 1, 0, 2)]) == {TOPIC: {0: 1}}


# --- audit -----------------------------------------------------------------------------------


def audited(lake, n):
    """The audit's exit and its newest audit_checks rows, `{check: (passed, detail)}`."""
    code, _ = K.audit(ctx(n), None, lake)
    return code, {r["check_name"]: (r["passed"], r["detail"]) for r in lake.checks[-len(K.CHECKS):]}


def bronze_head(lake):
    return lake.snaps[lake.refs_[BRONZE]["audit"]]


def test_a_clean_batch_passes_every_check_one_row_each_at_the_audit_head():
    topic, lake = Topic(), Lake()
    topic.produce(0, "trade", "heartbeat", "trade")
    topic.produce(1, "subscriptions", "trade")
    K.load(ctx(1), topic, lake)
    code, checks = audited(lake, 1)
    assert code == C.EXIT_OK and [r["check_name"] for r in lake.checks] == list(K.CHECKS)
    assert checks == {"accounting": (True, "3 staged + 2 skipped = 5 landed over 2 partitions"),
                      "parses": (True, "3 frames parse"), "keys_unique": (True, "3 keys, each once, none on main")}
    row = lake.checks[0]
    assert tuple(row) == C.CHECK_COLUMNS
    assert (row["table_name"], row["batch_id"], row["snapshot_id"], row["release_id"]) == (
        BRONZE, run_id(1), lake.refs_[BRONZE]["audit"], RELEASE)
    found = [(r["check_name"], r["passed"], r["checked_at"]) for r in lake.checks]
    assert C.unaudited(found, list(K.CHECKS)) is None  # what publish needs of a Kafka batch


def test_a_batch_of_only_control_frames_stages_nothing_and_passes_accounting():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "heartbeat", "subscriptions", "heartbeat")
    K.load(ctx(1), topic, lake)
    assert lake.rows(BRONZE, "audit") == []
    code, checks = audited(lake, 1)
    assert code == C.EXIT_OK and checks["accounting"] == (True, "0 staged + 3 skipped = 3 landed over 1 partitions")


def test_a_hole_or_a_repeat_in_the_landing_range_fails_accounting():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade", "trade")
    K.load(ctx(1), topic, lake)
    landing = lake.snaps[lake.refs_[LANDING]["main"]]["rows"]
    landing[1] = {**landing[2]}  # offset 1 gone, 2 twice: count, first and last still match
    code, checks = audited(lake, 1)
    assert code == C.EXIT_FAILED and checks["accounting"] == (
        False, f"{TOPIC}/0: the landing holds 3 rows, 2 offsets, at 0..2 of 0..2; planned 3")
    del landing[1]
    assert audited(lake, 1)[1]["accounting"][1] == f"{TOPIC}/0: the landing holds 2 rows, 2 offsets, at 0..2 of 0..2; planned 3"


def test_staged_plus_skipped_short_of_the_range_fails_accounting():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "heartbeat", "trade")
    K.load(ctx(1), topic, lake)
    bronze_head(lake)["rows"].pop()
    code, checks = audited(lake, 1)
    assert code == C.EXIT_FAILED and checks["accounting"] == (
        False, f"{TOPIC}/0: 1 staged + 1 skipped, planned 3; the batch's commit added 2 rows, 1 of them in its range")
    assert checks["parses"][0] and checks["keys_unique"][0]


def test_an_accepted_loss_is_accounted_and_a_hole_nobody_recorded_is_not():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    topic.produce(0, "trade", "trade", "trade")
    topic.evict(0, 4)  # offsets 2 and 3 gone before they landed
    K.load(ctx(2, accept=True), topic, lake)
    code, checks = audited(lake, 2)
    assert code == C.EXIT_OK and checks["accounting"] == (True, "1 staged + 0 skipped = 1 landed over 1 partitions")
    del lake.snaps[lake.refs_[LANDING]["main"]]["summary"][K.KAFKA_GAPS]
    assert audited(lake, 2)[1]["accounting"] == (
        False, f"{TOPIC}/0: the landing holds 1 rows, 1 offsets, at 4..4 of 2..4; planned 3; "
               f"{TOPIC}/0: 1 staged + 0 skipped, planned 3")


def test_planned_is_the_range_less_its_accepted_losses():
    assert K.planned(0, 3, []) == (3, 0, 2)
    assert K.planned(0, 10, [(3, 5)]) == (7, 0, 9)  # a loss inside the range
    assert K.planned(2, 5, [(2, 4)]) is None
    assert K.planned(0, 5, [(3, 9), (0, 0)]) == (2, 1, 2)
    assert K.planned(5, 8, [(0, 2)]) == (3, 5, 7)


def test_a_frame_that_does_not_parse_fails_naming_partition_and_offset_never_its_bytes():
    topic, lake = Topic(partitions=1), Lake()
    secret = b"SECRET-PAYLOAD"
    topic.produce(0, "trade", b"\x00\x00\x00\x00\x07" + secret, b"unframed " + secret, b"\x00\x00\x00\x00\x01" + secret)
    K.load(ctx(1), topic, lake)  # none of the three decodes, so the filter keeps each for this audit
    code, checks = audited(lake, 1)
    assert code == C.EXIT_FAILED and checks["parses"] == (False, (
        f"3 of 4 frames do not parse: {TOPIC}/0 offset 1: schema id 7 is not a registered {K.ENVELOPE}; "
        f"{TOPIC}/0 offset 2: not Confluent-framed; {TOPIC}/0 offset 3: its body does not read as {K.ENVELOPE}"))
    assert checks["accounting"][0] and checks["keys_unique"][0]
    assert not any("SECRET" in r["detail"] for r in lake.checks)
    assert K.unparsed_reason(1, True, True) == "the filter does not keep it"


def test_bronze_carries_the_decoded_envelope_by_name_and_never_the_frames_bytes():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, frame("trade", symbol="A/B"), frame("trade", sid=4, symbol="C/D", seq=9), frame("trade"), None)
    K.load(ctx(1), topic, lake)
    rows = lake.rows(BRONZE, "audit")
    assert all(list(r) == BRONZE_COLUMNS for r in rows)  # no record_key, no record_value
    assert [(r["schema_id"], r["stream"], r["symbol"]) for r in rows] == [
        (1, "trade", "A/B"), (4, "trade", "C/D"), (1, "trade", None), (None, None, None)]
    landed = lake.rows(LANDING)
    assert [(r["batch_id"], r["landed_at"]) for r in rows] == [(r["batch_id"], r["landed_at"]) for r in landed]
    passed, detail = audited(lake, 1)[1]["parses"]  # a writer schema with a field added reads; the unframed fails
    assert not passed and detail == f"1 of 4 frames do not parse: {TOPIC}/0 offset 3: not Confluent-framed"


def test_a_writer_schema_lacking_or_retyping_a_bronze_column_fails_parses_naming_it_never_a_silent_null():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, frame("trade", symbol="A/B"), frame("trade", sid=2), frame("trade", sid=3, symbol=7))
    K.load(ctx(1), topic, lake)
    assert [r["symbol"] for r in lake.rows(BRONZE, "audit")] == ["A/B", None, None]  # staged, nulled
    code, checks = audited(lake, 1)
    assert code == C.EXIT_FAILED and checks["parses"] == (False, (
        f"2 of 3 frames do not parse: {TOPIC}/0 offset 1: its writer schema 2 has no field symbol, which bronze's "
        f"column symbol takes; {TOPIC}/0 offset 2: its writer schema 3 has field symbol as bigint, which bronze's "
        "column symbol (string) does not take"))
    assert checks["accounting"][0] and checks["keys_unique"][0]


def test_a_field_reads_as_its_column_when_equal_or_an_avro_promotion():
    assert K.problems({"a": "int", "b": "float", "c": "binary", "d": "string"},
                      {"a": "bigint", "b": "double", "c": "binary"}) == []
    assert K.problems({"a": "bigint", "c": "string"}, {"a": "int", "b": "string", "c": "binary"}) == [
        "has field a as bigint, which bronze's column a (int) does not take",
        "has no field b, which bronze's column b takes",
        "has field c as string, which bronze's column c (binary) does not take"]
    assert K.envelope_columns({n: "x" for n in BRONZE_COLUMNS}) == {"stream": "x", "symbol": "x"}


def test_parses_names_at_most_listed_frames():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, *[None] * (K.LISTED + 5))
    K.load(ctx(1), topic, lake)
    passed, detail = audited(lake, 1)[1]["parses"]
    assert not passed and detail.count(" offset ") == K.LISTED and detail.endswith("; 5 more")


def test_a_duplicate_offset_against_main_fails_keys_unique():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    main = lake.refs_[BRONZE]["main"]  # a stray row on main past its through-offset, 2
    stray = lake._commit(BRONZE, main, [{**lake.snaps[main]["rows"][0], "kafka_offset": 2}], {"operation": "append"})
    lake.refs_[BRONZE]["main"] = lake.refs_[BRONZE]["audit"] = stray
    topic.produce(0, "trade", "trade")
    K.load(ctx(2), topic, lake)
    code, checks = audited(lake, 2)
    assert code == C.EXIT_FAILED and checks["keys_unique"] == (
        False, "1 rows repeat a key of the batch; 1 rows of its range are on main already")


def test_a_key_twice_within_the_batch_fails_keys_unique():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade", "trade")
    K.load(ctx(1), topic, lake)
    rows = bronze_head(lake)["rows"]
    rows.append({**rows[0]})
    assert audited(lake, 1)[1]["keys_unique"] == (
        False, "1 rows repeat a key of the batch; 0 rows of its range are on main already")


def test_a_detail_is_at_most_1000_characters():
    topic, lake = Topic(partitions=40), Lake()
    for p in range(40):
        topic.produce(p, "trade")
    K.load(ctx(1), topic, lake)
    bronze_head(lake)["rows"].clear()
    K.audit(ctx(1), None, lake)
    [row] = [r for r in lake.checks if r["check_name"] == "accounting"]
    assert not row["passed"] and len(row["detail"]) == K.DETAIL_MAX


def test_the_audit_refuses_a_batch_of_another_run_and_records_nothing():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade")
    K.load(ctx(1), topic, lake)
    assert K.audit(ctx(2), None, lake)[0] == C.EXIT_REFUSED and lake.checks == []


def test_a_run_that_staged_nothing_passes_each_check_saying_so():
    topic, lake = Topic(partitions=1), Lake()
    topic.produce(0, "trade")
    K.load(ctx(1), topic, lake)
    lake.publish()
    K.load(ctx(2), topic, lake)  # nothing new
    code, checks = audited(lake, 2)
    assert code == C.EXIT_OK and set(checks.values()) == {(True, f"batch {run_id(2)} staged nothing unpublished on audit")}
    assert lake.checks[-1]["snapshot_id"] == lake.refs_[BRONZE]["main"]


def test_the_envelope_is_raw_messages_full_name():
    avro = Path(__file__).parent.parent / "schemas" / "avro"
    assert K.is_envelope((avro / "raw-message.avsc").read_text())
    assert not K.is_envelope((avro / "trade.avsc").read_text())
    assert K.is_envelope(json.dumps({"type": "record", "name": K.ENVELOPE, "fields": []}))
    assert not K.is_envelope('"bytes"')


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


def test_a_body_that_does_not_read_is_kept_for_parses_never_skipped():
    """Spark's PERMISSIVE from_avro gives a corrupt body a struct of nulls, not a null (kafka-path.sh
    claim 9): decoded_sql makes it null, so the filter keeps it and parses fails it."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t (i INTEGER, envelope STRUCT(stream VARCHAR, symbol VARCHAR))")
    con.executemany("INSERT INTO t VALUES (?, ?)", [[0, {"stream": None, "symbol": None}],
                                                    [1, {"stream": None, "symbol": "A/B"}],
                                                    [2, {"stream": "trade", "symbol": "A/B"}]])
    decoded = K.decoded_sql(["stream", "symbol"]).replace("`", '"')
    keep = K.keep_sql({"field": "stream", "in": ["trade"]}).replace("`", '"')
    got = con.execute(f"SELECT envelope IS NULL, {keep} FROM (SELECT i, {decoded} AS envelope FROM t) ORDER BY i")
    assert got.fetchall() == [(True, True), (False, False), (False, True)]
    with pytest.raises(ValueError):
        K.decoded_sql(["stream) OR (true"])


def test_the_projection_casts_each_column_by_name_and_nulls_what_does_not_read():
    """project_sql in DuckDB (Spark's backticks as DuckDB's double quotes): a promoted field cast up,
    a missing or retyped one NULL of its column's type, and no reference to the struct at all for a
    frame that did not decode."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t (envelope STRUCT(stream VARCHAR, seq INTEGER, payload VARCHAR))")
    con.execute("INSERT INTO t VALUES ({'stream': 'trade', 'seq': 7, 'payload': 'x'})")
    columns = {"stream": "string", "seq": "bigint", "payload": "binary", "symbol": "string"}
    sql = K.project_sql({"stream": "string", "seq": "int", "payload": "string"}, columns)
    got = con.execute(f"SELECT {', '.join(sql).replace('`', chr(34))} FROM t")
    assert got.fetchall() == [("trade", 7, None, None)]
    assert [d[1] for d in got.description] == ["VARCHAR", "BIGINT", "BLOB", "VARCHAR"]
    assert all("envelope" not in x for x in K.project_sql({}, columns))
    with pytest.raises(ValueError):
        K.project_sql({}, {"a) OR (true": "string"})


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
    assert K.BRONZE_LEAD == (*want[:3], "schema_id")  # bronze's lead: the landing's offset key and timestamp
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
