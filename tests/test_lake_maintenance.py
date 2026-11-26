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


class CatalogSpark:
    """SHOW NAMESPACES / SHOW TABLES from a dict; the JVM, which loads a table, is unreachable."""

    def __init__(self, tables):
        self.tables, self.statements = tables, []

    def sql(self, statement):
        self.statements.append(statement)
        if statement.startswith("SHOW NAMESPACES"):
            rows = [(ns,) for ns in self.tables]
        else:
            rows = [{"tableName": t} for t in self.tables[statement.rsplit(".", 1)[1]]]
        return types.SimpleNamespace(collect=lambda: rows)

    @property
    def _jvm(self):
        raise RuntimeError("py4j gateway closed")


class Frame:
    """What run_audits writes to audit.checks: the rows, and the append."""

    def __init__(self, rows):
        self.rows, self.appended = rows, False

    def writeTo(self, table):
        return self

    def option(self, key, value):
        return self

    def append(self):
        self.appended = True


class CatalogDown:
    """Every statement raises, as when the catalog is unreachable; the audit.checks write works."""

    def sql(self, statement):
        raise RuntimeError("catalog down")

    def createDataFrame(self, rows):
        self.frame = Frame(rows)
        return self.frame


def test_contract_schema_runs_first_and_a_whole_audit_failure_files_against_the_catalog(maintenance, monkeypatch, sample_moment):
    import offsets as O

    assert maintenance.AUDITS[0] == ("contract_schema", "lake", maintenance.audit_contract_schema)
    monkeypatch.setattr(maintenance, "Row", dict)  # the module-level pyspark stub has no Row
    spark = CatalogDown()
    results = maintenance.run_audits(spark, sample_moment)
    assert results[0] == {"check_name": "contract_schema", "scope": "lake", "passed": False, "observed": -1,
                          "detail": f"{O.CHECK_RAISED} RuntimeError: catalog down"}
    assert spark.frame.appended and spark.frame.rows[0]["check_name"] == "contract_schema"


def test_iceberg_json_is_the_three_parsers_output_for_the_table_the_session_loads(maintenance):
    """The happy path of the JVM read: one load by the session, catalog-qualified, three parsers."""
    import json

    loaded = []
    table = types.SimpleNamespace(schema=lambda: "schema", spec=lambda: "spec", sortOrder=lambda: "sort")
    as_json = {"schema": {"fields": []}, "spec": {"spec-id": 0, "fields": []}, "sort": {"order-id": 0, "fields": []}}
    parser = types.SimpleNamespace(toJson=lambda obj: json.dumps(as_json[obj]))
    iceberg = types.SimpleNamespace(
        spark=types.SimpleNamespace(Spark3Util=types.SimpleNamespace(loadIcebergTable=lambda session, name: loaded.append((session, name)) or table)),
        SchemaParser=parser, PartitionSpecParser=parser, SortOrderParser=parser,
    )
    spark = types.SimpleNamespace(_jsparkSession="the session", _jvm=types.SimpleNamespace(org=types.SimpleNamespace(apache=types.SimpleNamespace(iceberg=iceberg))))
    assert maintenance._iceberg_json(spark, "gold.trades") == {
        "schema": as_json["schema"], "partition-spec": as_json["spec"], "sort-order": as_json["sort"],
    }
    assert loaded == [("the session", "lake.gold.trades")]


def test_contract_schema_never_raises_a_table_it_cannot_load_is_a_check_raised_row(maintenance):
    """The JVM failing on one table is that table's row; a missing one and a stray one get theirs."""
    import contract_schema

    import offsets as O

    spark = CatalogSpark({"gold": ["trades", "handmade"], "spike": ["tmp"], "scratch_p4": ["wap_bronze"]})
    rows = {r["scope"]: r for r in maintenance.audit_contract_schema(spark)}
    assert rows["lake.gold.trades"]["passed"] is False and rows["lake.gold.trades"]["observed"] == -1
    assert rows["lake.gold.trades"]["detail"] == f"{O.CHECK_RAISED} RuntimeError: py4j gateway closed"
    assert rows["lake.raw.messages"]["detail"].startswith("table missing")
    assert rows["lake.gold.handmade"]["detail"].startswith("no contract")
    assert rows["lake.spike.tmp"]["passed"] is False and "which no contract names" in rows["lake.spike.tmp"]["detail"]
    assert "lake.scratch_p4.wap_bronze" not in rows
    assert "SHOW TABLES IN lake.scratch_p4" not in spark.statements  # a scratch namespace is not even listed
    assert len(rows) == len(contract_schema.load()) + 2
    assert {r["check_name"] for r in rows.values()} == {"contract_schema"}


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
