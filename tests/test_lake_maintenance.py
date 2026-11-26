"""
maintenance.py's compaction fallback.

`lake-maintenance-daily` crashed on every run because `gold.bbo_1s` shipped
without a `WRITE ORDERED BY` clause and the nightly loop compacts every derived
table with `strategy => 'sort'`, which raises on a table with no declared sort
order. The DDL fix (ddl/lake.sql) is the real repair; this fallback is what stops
one table's missing clause taking expiry, orphan removal and all seven audits
down with it, silently, forever.

maintenance.py imports pyspark at module level, so the module comes in through
the same stub tests/test_lake_bronze.py uses — the function under test only ever
calls `spark.sql(...)`, which is what the fake here records.
"""

import sys
import types

import pytest

_UNSORTED_MESSAGE = (
    "Cannot sort data without a valid sort order, table 'lake.gold.bbo_1s' is "
    "unsorted and no sort order is provided"
)


@pytest.fixture(scope="module")
def maintenance():
    for name in (
        "pyspark",
        "pyspark.sql",
        "pyspark.sql.avro",
        "pyspark.sql.avro.functions",
        "pyspark.sql.types",
        "pyspark.sql.window",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))
    sql = sys.modules["pyspark.sql"]
    for attr in ("DataFrame", "Row", "Window", "functions", "SparkSession"):
        setattr(sql, attr, object())
    sys.modules["pyspark.sql.avro.functions"].from_avro = object()
    sys.modules["pyspark.sql.window"].Window = object()
    import maintenance as module

    return module


class FakeSpark:
    """Records every statement; raises the unsorted-table error for chosen tables."""

    def __init__(self, unsorted=()):
        self.unsorted = set(unsorted)
        self.statements = []

    def sql(self, statement):
        self.statements.append(statement)
        if "'sort'" in statement and any(t in statement for t in self.unsorted):
            raise Exception(_UNSORTED_MESSAGE)
        return types.SimpleNamespace(collect=lambda: [])


def _strategies(spark):
    return ["sort" if "'sort'" in s else "binpack" for s in spark.statements]


def test_a_sorted_table_is_rewritten_once_with_sort(maintenance, sample_moment):
    spark = FakeSpark()
    maintenance._rewrite(spark, "lake.gold.trades", "exchange_ts", sample_moment)
    assert _strategies(spark) == ["sort"]


def test_an_unsorted_table_falls_back_to_binpack(maintenance, sample_moment):
    """The whole point: the loop continues instead of the run dying on this table."""
    spark = FakeSpark(unsorted={"gold.bbo_1s"})
    maintenance._rewrite(spark, "lake.gold.bbo_1s", "second", sample_moment)
    assert _strategies(spark) == ["sort", "binpack"]
    # Same table, same predicate — only the strategy differs.
    assert "gold.bbo_1s" in spark.statements[1] and "second >=" in spark.statements[1]


def test_the_fallback_says_the_ddl_is_what_needs_fixing(maintenance, sample_moment, capsys):
    """Binpacking an unsorted table quietly would leave the missing clause forever."""
    maintenance._rewrite(FakeSpark(unsorted={"gold.bbo_1s"}), "lake.gold.bbo_1s", "second", sample_moment)
    out = capsys.readouterr().out
    assert "lake.gold.bbo_1s: no sort order declared" in out
    assert "ddl/lake.sql" in out


def test_any_other_failure_still_propagates(maintenance, sample_moment):
    """An OOM or a lock timeout is not a sort-order problem and must not be swallowed."""

    class Boom(FakeSpark):
        def sql(self, statement):
            raise Exception("Job aborted due to stage failure: OutOfMemoryError")

    with pytest.raises(Exception, match="OutOfMemoryError"):
        maintenance._rewrite(Boom(), "lake.gold.trades", "exchange_ts", sample_moment)


@pytest.fixture
def sample_moment():
    from datetime import datetime, timezone

    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


# ── bronze_schema_drift on a sample with no frame of the table's stream ─────
#
# It filed passed = true having sampled nothing, which the venue quality
# scorecard counts as a passing check of that day (bronze.kraken_instrument most
# nights; every book table once ADR-050 pauses them). No frame, no row.


def _table(maintenance, name):
    (t,) = [t for t in maintenance.bronze.VENUE_TABLES if t.name == name]
    return t


def test_a_drift_sample_with_no_frame_of_the_stream_files_no_row(maintenance, capsys):
    t = _table(maintenance, "kraken_instrument")
    assert maintenance.drift_result(t, 0, {}) == []
    assert "no instrument frames in the sample, no row filed" in capsys.readouterr().out


def test_a_drift_sample_with_frames_still_passes_and_fails(maintenance):
    t = _table(maintenance, "kraken_book")
    (ok,) = maintenance.drift_result(t, 13, {})
    assert (ok["check_name"], ok["passed"], ok["observed"]) == ("bronze_schema_drift", True, 0)
    assert "13 book frames" in ok["detail"]
    (bad,) = maintenance.drift_result(t, 13, {"$.data[0]": {"new_key"}})
    assert (bad["passed"], bad["observed"]) == (False, 1)


# ── the book audits pause with the switch (ADR-050) ─────────────────────────

ON = {"binance": True, "kraken": True, "coinbase": True}


def _skipped(maintenance, switch):
    kept, skipped = maintenance.paused_audits(maintenance.AUDITS, switch)
    assert len(kept) + len(skipped) == len(maintenance.AUDITS)
    return sorted((name, scope) for name, scope, _ in skipped)


def test_every_switch_on_skips_nothing(maintenance):
    assert _skipped(maintenance, ON) == []


def test_one_venue_off_skips_exactly_its_book_audits_and_keeps_the_shared_gold_ones(maintenance):
    assert _skipped(maintenance, {**ON, "kraken": False}) == sorted([
        ("duplicate_identifiers", "lake.bronze.kraken_book"),
        ("bronze_unparseable", "lake.bronze.kraken_book"),
        ("bronze_schema_drift", "lake.bronze.kraken_book"),
        ("duplicate_identifiers", "lake.silver.book_kraken"),
        ("silver_parity", "lake.silver.book_kraken"),
        ("kraken_checksum", "lake.silver.book_kraken"),
    ])


def test_every_venue_off_skips_all_eighteen_and_nothing_of_trades(maintenance):
    skipped = _skipped(maintenance, {v: False for v in ON})
    assert len(skipped) == 18
    scopes = {scope for _, scope in skipped}
    assert {"lake.gold.book_top20", "lake.gold.bbo_1s"} <= scopes
    assert not scopes & {"lake.bronze.kraken_instrument", "lake.bronze.kraken_trade", "lake.gold.trades", "lake.raw.messages"}


def test_two_venues_off_still_audit_the_shared_gold_tables(maintenance):
    kept, _ = maintenance.paused_audits(maintenance.AUDITS, {**ON, "binance": False, "coinbase": False})
    assert {"lake.gold.book_top20", "lake.gold.bbo_1s"} <= {scope for _, scope, _ in kept}
