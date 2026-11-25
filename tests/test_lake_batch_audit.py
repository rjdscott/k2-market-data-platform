"""
Stage 1's batch audit (ADR-047): the batch just committed to raw.messages,
checked against the offset range the run planned, without blocking the commit.

The comparison is `offsets.batch_audit`, pure, and most of this file tests it
directly. The last tests drive `ingest.stage_raw` and `ingest.main` through a
fake Spark (ingest.py imports pyspark at module level, so the module is loaded
over MagicMock stubs, as tests/test_lake_lock.py does) to pin the one property
the phase exit names: a batch with a missing offset files an audit.checks row
and the run still commits and carries on to stage 2.
"""

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import offsets as O

ROOT = Path(__file__).resolve().parent.parent

RAW = "market.crypto.v3.raw.kraken"
BOOK = "market.crypto.v3.book.kraken"


def plan(**ranges):
    """`starts, ends` for partitions given as `p<N>=(start, end)` on RAW."""
    starts = {RAW: {int(k[1:]): s for k, (s, _) in ranges.items()}}
    ends = {RAW: {int(k[1:]): e for k, (_, e) in ranges.items()}}
    return starts, ends


# ── the comparison ──────────────────────────────────────────────────────────


def test_a_clean_batch_files_nothing():
    starts, ends = plan(p0=(100, 200), p1=(5, 6), p2=(40, 40))
    found = [(RAW, 0, 100, 100, 199), (RAW, 1, 1, 5, 5)]
    assert O.batch_audit(starts, ends, found) == []


def test_a_missing_offset_in_the_middle_is_a_short_batch():
    starts, ends = plan(p0=(100, 200))
    # First and last are right, one row is not there: only the count can see it.
    [failure] = O.batch_audit(starts, ends, [(RAW, 0, 99, 100, 199)])
    assert failure == {
        "scope": f"{RAW}/0",
        "observed": 1,
        "detail": "planned 100 at 100..199, found 99 at 100..199",
    }


@pytest.mark.parametrize(
    "found, first, last",
    [
        ((RAW, 0, 99, 101, 199), 101, 199),  # the first offset never landed
        ((RAW, 0, 99, 100, 198), 100, 198),  # the last one never landed
    ],
)
def test_a_missing_first_or_last_offset_fails_and_says_where(found, first, last):
    starts, ends = plan(p0=(100, 200))
    [failure] = O.batch_audit(starts, ends, [found])
    assert failure["observed"] == 1
    assert failure["detail"] == f"planned 100 at 100..199, found 99 at {first}..{last}"


def test_a_shifted_batch_with_the_right_count_still_fails():
    """Count alone would pass this: 100 rows, but one offset late on both ends."""
    starts, ends = plan(p0=(100, 200))
    [failure] = O.batch_audit(starts, ends, [(RAW, 0, 100, 101, 200)])
    assert failure["observed"] == 0
    assert failure["detail"] == "planned 100 at 100..199, found 100 at 101..200"


def test_a_planned_partition_that_wrote_nothing_fails():
    starts, ends = plan(p0=(100, 200), p1=(0, 50))
    [failure] = O.batch_audit(starts, ends, [(RAW, 0, 100, 100, 199)])
    assert failure == {"scope": f"{RAW}/1", "observed": 50, "detail": "planned 50 at 0..49, found none"}


def test_rows_nobody_planned_fail_with_a_negative_count():
    starts, ends = plan(p0=(100, 200), p1=(7, 7))
    found = [(RAW, 0, 100, 100, 199), (RAW, 1, 3, 7, 9), (BOOK, 4, 2, 0, 1)]
    failures = O.batch_audit(starts, ends, found)
    assert [(f["scope"], f["observed"]) for f in failures] == [(f"{RAW}/1", -3), (f"{BOOK}/4", -2)]
    assert failures[1]["detail"] == "planned none, found 2 at 0..1"


def test_one_row_per_partition_however_much_is_wrong():
    """The rows share run_ts and check_name, so scope must be unique per run."""
    starts, ends = plan(p0=(100, 200), p1=(0, 10), p2=(0, 10))
    # p0 wrong on all three numbers; p1 empty; p2 short.
    found = [(RAW, 0, 50, 120, 180), (RAW, 2, 9, 0, 9)]
    failures = O.batch_audit(starts, ends, found)
    scopes = [f["scope"] for f in failures]
    assert scopes == [f"{RAW}/0", f"{RAW}/1", f"{RAW}/2"]
    assert len(set(scopes)) == len(scopes)


def test_an_evicted_range_is_not_reported_again():
    """--accept-data-loss already filed the hole as offset_gap; the batch audit must not.

    The real repair sequence from ingest.stage_raw: evicted, skip_evicted, then
    bounded_offsets again. The batch is planned from the log start, so the
    records Redpanda evicted were never part of it.
    """
    committed = {RAW: {0: 1000, 1: 500}}
    earliest = {RAW: {0: 4000, 1: 0}}  # partition 0 lost 1000..3999
    latest = {RAW: {0: 4100, 1: 600}}
    starts, _, _ = O.bounded_offsets(committed, earliest, latest, 0)
    losses = O.evicted(starts, earliest)
    assert losses == [(RAW, 0, 1000, 4000, 3000)]
    starts, ends, _ = O.bounded_offsets(O.skip_evicted(starts, losses), earliest, latest, 0)

    found = [(RAW, 0, 100, 4000, 4099), (RAW, 1, 100, 500, 599)]
    assert O.batch_audit(starts, ends, found) == []
    # The pre-repair starts WOULD report it: the test is not passing by accident.
    pre_repair = {RAW: {0: 1000, 1: 500}}
    assert [f["scope"] for f in O.batch_audit(pre_repair, ends, found)] == [f"{RAW}/0"]


# ── the wiring: the commit stands, the run continues ────────────────────────


class Writer:
    def __init__(self, events):
        self.events, self.props = events, {}

    def option(self, key, value):
        self.props[key] = value
        return self

    def append(self):
        self.events.append(("commit", dict(self.props)))


class Chain:
    """Every Spark builder call returns itself; the Kafka read yields one max(kafka_ts)."""

    def __init__(self, events):
        self.events = events

    def __getattr__(self, name):
        return lambda *a, **kw: self

    def collect(self):
        return [{"m": datetime(2026, 1, 1, tzinfo=timezone.utc)}]

    def writeTo(self, table):
        return Writer(self.events)


class FakeSpark:
    def __init__(self, events):
        self.read = Chain(events)
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture
def ingest(monkeypatch):
    """ingest.py over stubbed Spark, catalog and stages; the offsets and wire modules are real."""
    for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions", "books", "bronze", "gold", "silver", "catalog"):
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    spark_conf = mock.MagicMock(CATALOG="lake", KAFKA_BROKERS="redpanda:9092")
    monkeypatch.setitem(sys.modules, "spark_conf", spark_conf)
    monkeypatch.delitem(sys.modules, "ingest", raising=False)
    module = importlib.import_module("ingest")
    yield module
    sys.modules.pop("ingest", None)


@pytest.fixture
def run(ingest, monkeypatch):
    """One stage-1 batch on RAW/0, planned 0..9; returns the event log."""
    events = []
    zero = {t: {0: 0} for t in ingest.ALL_TOPICS}
    latest = {**zero, RAW: {0: 10}}
    monkeypatch.setattr(ingest, "Row", lambda **kw: kw)
    monkeypatch.setattr(ingest, "snapshot_history", lambda spark, table: [])
    monkeypatch.setattr(ingest, "broker_offsets", lambda spark, topics, end_ms: (zero, latest, None))
    monkeypatch.setattr(ingest, "added_records", lambda spark, table: 9)
    monkeypatch.setattr(ingest, "current_snapshot_id", lambda spark, table: 7)

    def write_audit_rows(spark, rows, properties):
        events.append(("audit.checks", rows, properties))
        return True

    monkeypatch.setattr(ingest, "write_audit_rows", write_audit_rows)
    return events


def batch(ingest, monkeypatch, events, found=None, raises=None):
    def batch_offsets(spark):
        events.append(("audit read",))
        if raises:
            raise raises
        return found

    monkeypatch.setattr(ingest, "_batch_offsets", batch_offsets)


def test_a_short_batch_files_a_row_after_the_commit_and_the_commit_stands(ingest, run, monkeypatch):
    batch(ingest, monkeypatch, run, found=[(RAW, 0, 9, 0, 9)])  # offset 0..9 planned, one missing
    written = ingest.stage_raw(FakeSpark(run), datetime(2026, 1, 1, tzinfo=timezone.utc), "", 600000)

    assert written == 9
    assert [e[0] for e in run] == ["commit", "audit read", "audit.checks"]
    # The commit carries the planned end, whatever the audit later says about it.
    assert O.decode(run[0][1][f"snapshot-property.{O.KAFKA_OFFSETS}"])[RAW] == {0: 10}
    _, rows, properties = run[2]
    assert properties == {O.BATCH_AUDIT_FAILURES: "1"}
    [row] = rows
    assert row["job"] == "ingest" and row["check_name"] == O.BATCH_AUDIT == "batch_offsets"
    assert (row["scope"], row["passed"], row["observed"]) == (f"{RAW}/0", False, 1)
    assert row["detail"] == "planned 10 at 0..9, found 9 at 0..9"


def test_a_clean_batch_writes_no_audit_row(ingest, run, monkeypatch):
    batch(ingest, monkeypatch, run, found=[(RAW, 0, 10, 0, 9)])
    ingest.stage_raw(FakeSpark(run), datetime(2026, 1, 1, tzinfo=timezone.utc), "", 600000)
    assert [e[0] for e in run] == ["commit", "audit read"]


def test_an_audit_that_raises_is_a_finding_not_a_failed_run(ingest, run, monkeypatch):
    batch(ingest, monkeypatch, run, raises=RuntimeError("catalog went away"))
    assert ingest.stage_raw(FakeSpark(run), datetime(2026, 1, 1, tzinfo=timezone.utc), "", 600000) == 9
    [row] = run[-1][1]
    assert (row["scope"], row["observed"]) == ("lake.raw.messages", -1)
    assert row["detail"] == "check raised RuntimeError: catalog went away"


def test_the_could_not_run_detail_is_the_one_the_governance_view_reads_as_error():
    """dq_results maps observed -1 plus this prefix to `error`; anything else is `fail`."""
    sql = (ROOT / "dbt/models/governance/marts/quality_results/dq_results.sql").read_text()
    assert f"when observed = -1 and detail like '{O.CHECK_RAISED} %' then 'error'" in sql
    assert O.check_raised(RuntimeError("x")) == f"{O.CHECK_RAISED} RuntimeError: x"


def test_a_failed_audit_lets_the_run_commit_and_continue_to_stage_2(ingest, run, monkeypatch):
    """The phase exit: a missing offset records a row and does not block the commit."""
    batch(ingest, monkeypatch, run, found=[(RAW, 0, 9, 1, 9)])
    monkeypatch.setattr(ingest, "write_audit_rows", lambda *a: run.append(("audit.checks unwritable",)) or False)
    spark = FakeSpark(run)
    monkeypatch.setattr(ingest, "lake_session", lambda name: spark)
    monkeypatch.setattr(ingest, "acquire_lock", lambda path: mock.MagicMock())
    monkeypatch.setattr(sys, "argv", ["ingest.py"])

    assert ingest.main() == 0
    assert [e[0] for e in run] == ["commit", "audit read", "audit.checks unwritable"]
    ingest.bronze.stage.assert_called_once()
    for later in (ingest.silver, ingest.gold, ingest.books):
        later.stage.assert_called_once()
    assert spark.stopped
