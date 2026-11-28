"""scripts/product_verify.py (make product-verify, W1-14): every check on fakes of the catalog's
table metadata and the run records, each made to fail by its own fixture. Synthetic offsets only."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import kafka as K  # noqa: E402  (docker/lake, on the path by conftest)
import product_verify as V  # noqa: E402

import offsets as O  # noqa: E402

T = "topic.synthetic"
SILVER, DOWN = "d_build.p__base.silver", "e_build.q__base.out"
S = {"product": "d.p", "topic": T, "landing": "lake.p.landing", "bronze": "lake.p.bronze_x",
     "outputs": [DOWN, SILVER], "warehouses": {"lake": "k2", "d_build": "d", "e_build": "e"}}


class Table:
    """A table's REST metadata, built one commit at a time on main."""
    ids = iter(range(1000, 10**6))

    def __init__(self, parent=None):
        self.snaps, self.head, self.total, self.refs = [], parent, 0, {}

    def commit(self, added=0, total=None, **props):
        sid = next(Table.ids)
        self.total = self.total + added if total is None else total
        self.snaps.append({"snapshot-id": sid, "parent-snapshot-id": self.head, "timestamp-ms": sid,
                           "summary": {"operation": "append", "added-records": str(added),
                                       "total-records": str(self.total), **props}})
        self.head = sid
        return sid

    def meta(self):
        return {"snapshots": list(self.snaps), "refs": {"main": {"snapshot-id": self.head, "type": "branch"}, **self.refs}}


def land(t, starts, ends, gaps=None, added=None):
    props = {O.JOB: K.JOB_CONNECTOR, O.KAFKA_OFFSETS: O.encode({T: ends}), K.KAFKA_STARTS: O.encode({T: starts})}
    if gaps:
        props[K.KAFKA_GAPS] = K.gaps_json({T: gaps})
    span = sum(ends[p] - s for p, s in starts.items())
    return t.commit(span if added is None else added, **props)


def stage(t, src, through, skipped, added):
    return t.commit(added, **{O.JOB: K.JOB_CONNECTOR, O.SRC_SNAPSHOT_ID: str(src),
                              O.KAFKA_OFFSETS: O.encode({T: through}), K.KAFKA_SKIPPED: O.encode({T: skipped})})


def healthy():
    """Two partitions; a compaction; an accepted loss on partition 1 (offsets 8..11); bronze level."""
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 0, 1: 5}, {0: 10, 1: 8})
    c2 = land(landing, {0: 10, 1: 8}, {0: 15, 1: 8})
    landing.commit(0, total=landing.total, **{O.JOB: "maintenance", "operation": "replace"})
    c3 = land(landing, {0: 15, 1: 12}, {0: 20, 1: 14}, gaps={1: [(8, 12)]})
    stage(bronze, c1, {0: 10, 1: 8}, {0: 2, 1: 1}, 10)  # 10 + 3 read; 2 + 1 skipped
    stage(bronze, c2, {0: 15, 1: 8}, {}, 5)
    stage(bronze, c3, {0: 20, 1: 14}, {0: 1}, 6)  # partition 1 reads 12..13 only: 8..11 lost
    return landing, bronze


class Reader:
    """The fake behind Live: each table's metadata, the run records, and every read in order."""

    def __init__(self, landing, bronze, tags=None, records=None):
        self.metas = {S["landing"]: landing.meta(), S["bronze"]: bronze.meta()}
        for o, snap in (tags if tags is not None else {SILVER: 7, DOWN: 9}).items():
            self.metas[o] = {"snapshots": [], "refs": {"audited": {"snapshot-id": snap, "type": "tag"}}}
        self.records = records if records is not None else {SILVER: 7, DOWN: 9}
        self.log = []

    def table(self, relation):
        self.log.append(relation)
        return self.metas.get(relation)

    def newest_audited(self, relations):
        self.log.append("records")
        return {r: s for r, s in self.records.items() if r in relations}


def verdicts(reader):
    return {(check, rel): (v, d) for v, check, rel, d in V.verify(S, reader)}


def failing(reader):
    return {k: d for k, (v, d) in verdicts(reader).items() if v != "PASS"}


def test_a_healthy_product_passes_every_check():
    found = verdicts(Reader(*healthy()))
    assert {k: v for k, (v, _) in found.items()} == {
        ("landing", S["landing"]): "PASS", ("bronze", S["bronze"]): "PASS", ("level", S["bronze"]): "PASS",
        ("tag", SILVER): "PASS", ("tag", DOWN): "PASS"}
    assert "3 connector commit(s) over 2 partition(s)" in found["landing", S["landing"]][1]
    assert "1 jump(s)" in found["landing", S["landing"]][1]
    assert "21 staged + 4 skipped = 25 landing rows read" in found["bronze", S["bronze"]][1]


def test_bronze_is_read_before_the_landing_and_tags_before_the_run_records():
    reader = Reader(*healthy())
    V.verify(S, reader)
    assert reader.log == [S["bronze"], S["landing"], DOWN, SILVER, "records"]


# --- every offset accounted for: the landing ------------------------------------------------------


def test_a_hole_between_spans_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    land(landing, {0: 11}, {0: 15})  # offset 10 never landed, no loss records it
    assert "past the end before it at 10: a hole" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_an_overlap_between_spans_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    land(landing, {0: 9}, {0: 15})  # offset 9 landed twice
    assert "an overlap" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_jump_no_gap_records_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    land(landing, {0: 14}, {0: 20})  # the jump an accept-data-loss run would record, unrecorded
    assert "a hole no k2.kafka-gaps records" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_jump_past_its_recorded_gap_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    land(landing, {0: 15}, {0: 20}, gaps={0: [(10, 14)]})  # 14 lost too, but the gap ends at 13
    assert "a hole" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_recorded_gap_dropped_by_a_later_commit_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    land(landing, {0: 14}, {0: 20}, gaps={0: [(10, 14)]})
    land(landing, {0: 20}, {0: 25})  # carries no gaps: kafka.carried_gaps would have kept it
    assert "dropped the recorded loss of partition 0, offsets 10..13" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_commit_whose_rows_are_not_its_spans_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0, 1: 0}, {0: 10, 1: 4}, added=13)
    assert "added 13 rows; its spans hold 14 offsets" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_compaction_that_changes_the_rows_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0}, {0: 10})
    landing.commit(0, total=9, **{O.JOB: "maintenance", "operation": "replace"})
    assert "changed the rows from 10 to 9" in failing(Reader(landing, bronze))["landing", S["landing"]]


def test_a_partition_moved_with_no_start_fails():
    landing, bronze = Table(), Table()
    land(landing, {0: 0, 1: 0}, {0: 10, 1: 5})
    land(landing, {0: 10}, {0: 12, 1: 7})  # partition 1 advanced, never read
    assert "partition 1: moved from 5 to 7" in failing(Reader(landing, bronze))["landing", S["landing"]]


# --- every offset accounted for: bronze -----------------------------------------------------------


def test_staged_plus_skipped_unequal_to_the_span_fails():
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 0}, {0: 10})
    stage(bronze, c1, {0: 10}, {0: 2}, 7)  # 7 + 2 of 10: a record neither staged nor counted
    assert "7 staged + 2 skipped, but its range holds 10 landing rows" in failing(Reader(landing, bronze))["bronze", S["bronze"]]


def test_a_later_stage_unequal_to_its_span_fails():
    landing, bronze = healthy()
    c4 = land(landing, {0: 20, 1: 14}, {0: 26, 1: 14})
    stage(bronze, c4, {0: 26, 1: 14}, {}, 5)  # 5 of 6
    assert "5 staged + 0 skipped, but its range holds 6" in failing(Reader(landing, bronze))["bronze", S["bronze"]]


def test_a_stage_counting_a_lost_offset_fails():
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 0}, {0: 4})
    c2 = land(landing, {0: 8}, {0: 10}, gaps={0: [(4, 8)]})
    stage(bronze, c1, {0: 4}, {}, 4)
    stage(bronze, c2, {0: 10}, {}, 6)  # the 4 lost offsets counted as staged
    assert "6 staged + 0 skipped, but its range holds 2" in failing(Reader(landing, bronze))["bronze", S["bronze"]]


def test_bronze_through_other_than_the_landing_snapshot_it_read_fails():
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 0}, {0: 10})
    land(landing, {0: 10}, {0: 12})
    stage(bronze, c1, {0: 12}, {}, 12)  # says it read c1, which ends at 10
    assert "the landing at snapshot" in failing(Reader(landing, bronze))["bronze", S["bronze"]]


def test_a_first_stage_from_offset_zero_on_a_trimmed_topic_fails():
    """Review finding D1: the landing's first offset, never 0, starts a partition's first stage."""
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 100}, {0: 110})
    stage(bronze, c1, {0: 110}, {}, 110)
    assert "110 staged + 0 skipped, but its range holds 10" in failing(Reader(landing, bronze))["bronze", S["bronze"]]


def test_a_first_stage_past_expired_snapshots_is_named_unproved_not_failed():
    landing, bronze = Table(parent=1), Table(parent=2)  # each lineage cut by expiry below its first commit
    c1 = land(landing, {0: 10}, {0: 20})
    c2 = land(landing, {0: 20}, {0: 25})
    stage(bronze, c1, {0: 20}, {}, 999)  # its start is in an expired snapshot: not counted
    stage(bronze, c2, {0: 25}, {}, 5)
    found = verdicts(Reader(landing, bronze))
    assert found["bronze", S["bronze"]][0] == "PASS"
    assert "1 commit(s) not counted" in found["bronze", S["bronze"]][1]
    assert "from the oldest commit main's lineage still holds" in found["landing", S["landing"]][1]


# --- layers level -------------------------------------------------------------------------------


def test_bronze_behind_the_landing_waits_then_fails():
    landing, bronze = healthy()
    land(landing, {0: 20, 1: 14}, {0: 22, 1: 14})  # landed, not staged yet
    bronze.refs = {"audit": {"snapshot-id": 5, "type": "branch"}}
    v, d = verdicts(Reader(landing, bronze))["level", S["bronze"]]
    assert v == "WAIT" and "behind the landing's end on partition(s) [0]" in d and "waits on its audit branch" in d
    assert V.run(S, Reader(landing, bronze), wait=0) == 1


def test_bronze_past_the_landing_fails_at_once():
    landing, bronze = Table(), Table()
    c1 = land(landing, {0: 0}, {0: 10})
    stage(bronze, c1, {0: 12}, {}, 12)
    v, _ = verdicts(Reader(landing, bronze))["level", S["bronze"]]
    sleeps = []
    assert v == "FAIL" and V.run(S, Reader(landing, bronze), wait=600, sleep=sleeps.append) == 1 and sleeps == []


def test_a_tag_no_run_record_names_fails():
    found = failing(Reader(*healthy(), records={DOWN: 9}))
    assert found == {("tag", SILVER): "audited tag 7, but no audited run record names the table"}
    assert V.run(S, Reader(*healthy(), records={DOWN: 9}), wait=0) == 1


def test_a_tag_other_than_the_newest_run_record_fails():
    assert failing(Reader(*healthy(), records={SILVER: 6, DOWN: 9})) == {
        ("tag", SILVER): "audited tag 7, but its newest audited run record says 6"}


def test_an_output_never_built_or_never_tagged_is_not_level():
    reader = Reader(*healthy(), tags={SILVER: 7})
    reader.metas[DOWN] = {"snapshots": [], "refs": {}}
    assert failing(reader) == {("tag", DOWN): "has no audited tag yet"}
    del reader.metas[DOWN]
    assert failing(reader) == {("tag", DOWN): "not built yet"}


def test_a_run_in_flight_is_read_again_until_level(capsys):
    landing, bronze = healthy()
    behind = Reader(landing, bronze, records={DOWN: 9})  # the silver publish between its tag and its record
    level_ = Reader(landing, bronze)
    reads = iter([behind, behind, level_])

    class Moving:
        def table(self, r):
            self.now = next(reads) if r == S["bronze"] else self.now
            return self.now.table(r)

        def newest_audited(self, rs):
            return self.now.newest_audited(rs)

    t = [0.0]
    assert V.run(S, Moving(), wait=600, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]) == 0
    out = capsys.readouterr().out
    assert "every offset accounted for, every layer level (read again for 40 s)" in out
    assert "FAIL" not in out


# --- the release, the readers, and writing nothing ---------------------------------------------


def pipeline(product, **body):
    return {"product": product, **body}


def release(tmp_path, kind="kafka"):
    pipes = {
        "d.p": pipeline("d.p", connector={"kind": kind, "topic": T}, landing={"table": S["landing"]},
                        bronze=[{"table": S["bronze"]}], build={"outputs": [SILVER]}),
        "e.q": pipeline("e.q", inputs=[{"contractId": "d.p.silver"}], build={"outputs": [DOWN]}),
        "e.r": pipeline("e.r", inputs=[{"contractId": "e.q.out"}], build={"outputs": ["e_build.r__base.bars"]}),
        "f.s": pipeline("f.s", inputs=[{"contractId": "f.t.other"}], build={"outputs": ["f_build.s__base.other"]}),
    }
    dags = []
    for pid, body in pipes.items():
        (tmp_path / "compiled/products" / pid).mkdir(parents=True)
        (tmp_path / "compiled/products" / pid / "pipeline.json").write_text(json.dumps(body))
        dags.append({"product": pid, "dagId": pid.split(".")[1], "pipeline": f"compiled/products/{pid}/pipeline.json"})
    (tmp_path / "compiled/airflow").mkdir(parents=True)
    (tmp_path / "compiled/airflow/dags.json").write_text(json.dumps({"dags": dags}))
    (tmp_path / "compiled/trino/etc/catalog").mkdir(parents=True)
    for catalog, w in {"lake": "k2", "d_build": "d", "e_build": "e", "f_build": "f"}.items():
        (tmp_path / f"compiled/trino/etc/catalog/{catalog}.properties").write_text(
            f"connector.name=iceberg\niceberg.rest-catalog.warehouse={w}\n")
    return tmp_path


def test_the_subject_is_the_product_and_every_product_downstream_of_it(tmp_path):
    s = V.subject(release(tmp_path), "d/p")
    assert s["outputs"] == [SILVER, DOWN, "e_build.r__base.bars"]
    assert (s["topic"], s["landing"], s["bronze"]) == (T, S["landing"], S["bronze"])
    assert s["warehouses"]["lake"] == "k2" and s["warehouses"]["e_build"] == "e"


def test_a_product_with_no_kafka_connector_is_refused(tmp_path):
    with pytest.raises(V.Refused, match="no Kafka product"):
        V.subject(release(tmp_path, kind="public-api"), "d.p")
    assert V.main(["--release", str(tmp_path), "--product", "d.p"]) == 2


class FakeCatalog:
    calls = []

    def __init__(self, uri, warehouse):
        self.warehouse = warehouse
        FakeCatalog.calls.append(("GET", f"{uri}/v1/config?warehouse={warehouse}"))

    def table(self, ns, t):
        FakeCatalog.calls.append(("GET", f"{self.warehouse}/{ns}/{t}"))
        return FakeCatalog.metas.get(f"{self.warehouse}.{ns}.{t}")


class FakeTrino:
    def __init__(self, records):
        self.records, self.statements = records, []

    def run(self, user, sql):
        self.statements.append((user, sql))
        from access_verify import Answer
        return Answer(rows=[[r, s] for r, s in self.records.items() if f"'{r}'" in sql])


def test_live_reads_only_and_twice_reads_the_same(tmp_path, monkeypatch, capsys):
    """Safe to run twice: catalog GETs and SELECTs as svc.catalog alone, and the same lines each time."""
    landing, bronze = healthy()
    FakeCatalog.calls = []
    FakeCatalog.metas = {"k2.p.landing": landing.meta(), "k2.p.bronze_x": bronze.meta(),
                         "d.p__base.silver": {"refs": {"audited": {"snapshot-id": 7, "type": "tag"}}},
                         "e.q__base.out": {"refs": {"audited": {"snapshot-id": 9, "type": "tag"}}},
                         "e.r__base.bars": {"refs": {"audited": {"snapshot-id": 11, "type": "tag"}}}}
    monkeypatch.setattr(V, "Catalog", FakeCatalog)
    trino = FakeTrino({SILVER: 7, DOWN: 9, "e_build.r__base.bars": 11})
    s = V.subject(release(tmp_path), "p")
    outs = []
    for _ in range(2):
        assert V.run(s, V.Live(s["warehouses"], "http://lakekeeper:8181/catalog", trino), wait=0) == 0
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1] and "every offset accounted for" in outs[0]
    assert {m for m, _ in FakeCatalog.calls} == {"GET"}
    assert {u for u, _ in trino.statements} == {"svc.catalog"}
    assert all(sql.startswith("SELECT ") and V.RUNS in sql for _, sql in trino.statements)


def test_a_failing_run_records_read_is_unreachable_not_a_pass():
    class Denied:
        def run(self, user, sql):
            from access_verify import Answer
            return Answer(error="PERMISSION_DENIED", message="Access Denied")
    with pytest.raises(V.Unreachable, match="PERMISSION_DENIED"):
        V.Live({"d_build": "d"}, "http://x", Denied()).newest_audited([SILVER])


def test_a_relation_that_is_no_identifier_triple_is_refused():
    with pytest.raises(V.Refused):
        V.split("d_build.p__base.silver'; DROP TABLE x --")
