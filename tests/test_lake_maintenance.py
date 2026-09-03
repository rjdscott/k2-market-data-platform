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
