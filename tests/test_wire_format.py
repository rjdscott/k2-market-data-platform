"""
Unit tests for the wire format the lake reads: docker/lake/wire.py, and the
contract between schemas/avro/*.avsc and docker/lake/ddl/lake.sql.

Pure python — the .avsc files and the .sql file are parsed as text. No Spark, no
catalog, no Avro library.

The second half is the one that earns its place. CLAUDE.md's schema-change rule
says the Avro schema, the DDL and the docs move in one PR or not at all, and the
failure mode it warns about is silence: a field added to trade.avsc that never
reaches a bronze table does not break a build, it produces a column of nothing at
the ingest boundary hours later. These tests are that rule, executable.
"""

import json
import re
from pathlib import Path

import pytest

import wire

_ROOT = Path(__file__).resolve().parent.parent
_AVRO = _ROOT / "schemas" / "avro"
_DDL = _ROOT / "docker" / "lake" / "ddl" / "lake.sql"


def _avro_fields(name):
    schema = json.loads((_AVRO / name).read_text())
    return [field["name"] for field in schema["fields"]]


def _ddl_columns(table):
    """Column names of one CREATE TABLE block in lake.sql.

    A line-oriented parse rather than a SQL parser: every column in that file is
    declared on its own line as `<name> <TYPE> ...`, and a real parser here would
    be a dependency in service of a test.
    """
    body = re.search(
        rf"CREATE TABLE IF NOT EXISTS lake\.{re.escape(table)} \((.*?)\n\)\nUSING iceberg",
        _DDL.read_text(),
        re.DOTALL,
    )
    assert body, f"no CREATE TABLE block for lake.{table} in {_DDL}"
    columns = []
    for line in body.group(1).splitlines():
        match = re.match(r"\s{4}(\w+)\s+[A-Z]", line)
        if match:
            columns.append(match.group(1))
    return columns


# ── the Confluent framing ───────────────────────────────────────────────────


def _frame(schema_id, body=b"payload", magic=0):
    return bytes([magic]) + schema_id.to_bytes(4, "big") + body


class TestParseConfluent:
    def test_reads_the_schema_id_big_endian(self):
        # 3 is the registered id of market.crypto.v3.trades.<ex>-value on this
        # stack. Little-endian would read it as 50331648.
        assert wire.parse_confluent(_frame(3))[0] == 3

    def test_returns_the_body_after_the_five_byte_header(self):
        assert wire.parse_confluent(_frame(3, b"avro-bytes"))[1] == b"avro-bytes"

    def test_handles_a_four_byte_id(self):
        assert wire.parse_confluent(_frame(2**30))[0] == 2**30

    def test_an_empty_body_is_a_valid_frame(self):
        assert wire.parse_confluent(_frame(3, b"")) == (3, b"")

    def test_rejects_a_non_confluent_magic_byte(self):
        # 0x7b is '{' — raw JSON on a v3 topic, the realistic wrong producer.
        with pytest.raises(wire.BadFrame, match="magic byte"):
            wire.parse_confluent(b'{"a":1}')

    def test_rejects_a_frame_shorter_than_the_header(self):
        with pytest.raises(wire.BadFrame, match="shorter than"):
            wire.parse_confluent(b"\x00\x00\x00")

    def test_rejects_an_unregistered_schema_id(self):
        # Registry ids start at 1; 0 or negative means the four bytes are not an
        # id, which is a corrupt frame rather than a schema we have not fetched.
        with pytest.raises(wire.BadFrame, match="schema id"):
            wire.parse_confluent(_frame(0))


class TestFramingExpressions:
    """The SQL the executors run must agree with the Python the driver runs."""

    def test_body_starts_one_past_the_header(self):
        # Spark's substring is 1-based, so the body begins at position 6.
        assert wire.body_expr("payload") == "substring(payload, 6)"

    def test_schema_id_reads_bytes_two_to_five(self):
        expr = wire.schema_id_expr("value")
        assert "substring(value, 2, 4)" in expr
        assert expr.startswith("CAST(") and expr.endswith("AS INT)")

    def test_magic_check_reads_the_first_byte(self):
        assert wire.MAGIC_OK_SQL.format(col="value") == "hex(substring(value, 1, 1)) = '00'"

    @pytest.mark.parametrize(
        "frame,why",
        [
            (b"\x00\x00\x01", "shorter than the 5-byte header"),
            (b"\x00\x00\x00\x00\x00body", "schema id 0"),
            (b"\x7b\x22\x61\x22\x3a", "magic 0x7b, not framed at all"),
        ],
    )
    def test_the_guard_rejects_everything_parse_confluent_rejects(self, frame, why):
        # The two readers have to agree, and this is the list they disagreed on.
        # The SQL guard used to check the magic byte alone, so `00 00 01` came
        # back with a FABRICATED schema id of 1 and an empty body — which
        # `parse_confluent` calls a BadFrame, `stage_bronze` sent to the
        # registry, and the registry answered 404 to. Permanently: the record is
        # in raw.messages, never expired, and re-read every cycle.
        with pytest.raises(wire.BadFrame):
            wire.parse_confluent(frame)

        guard = wire.schema_id_guarded_expr("value")
        # The SQL cannot be executed here (no Spark in this suite), so the
        # assertion is that the guard tests all three conditions the parser does.
        assert f"length(value) >= {wire.HEADER_BYTES}" in guard, why
        assert wire.MAGIC_OK_SQL.format(col="value") in guard, why
        assert "> 0" in guard, why

    def test_the_guard_yields_null_rather_than_a_value_for_a_bad_frame(self):
        # CASE WHEN with no ELSE is NULL, which is what raw.messages.schema_id
        # is nullable for and what stage 2's `schema_id IS NOT NULL` filters on.
        guard = wire.schema_id_guarded_expr("value")
        assert guard.startswith("CASE WHEN") and "ELSE" not in guard


class TestFixedPoint:
    def test_divides_by_ten_to_the_scale(self):
        assert "/ 100000000" in wire.fixed_point_expr("price")

    def test_casts_to_the_declared_decimal_type(self):
        assert wire.fixed_point_expr("d.price").endswith("AS DECIMAL(28,10))")

    def test_the_intermediate_cast_holds_the_whole_int64_range(self):
        # THE test this file got wrong. The old version asserted on the RESULT
        # type (~9.22e10, 11 integer digits, comfortably inside DECIMAL(28,10))
        # and never looked at the intermediate. The bug was in the intermediate:
        # `CAST(v AS DECIMAL(28,10))` before the divide has 18 integer digits and
        # int64 max needs 19, so with ansi off every |v| >= 1e18 became NULL —
        # into a NOT NULL DECIMAL(28,10) column. At 1e-8 that is 1e10 units,
        # an ordinary meme-coin quantity.
        #
        # Measured in k2-spark-iceberg on 2026-08-26:
        #   DECIMAL(28,10) intermediate: 9223372036854775807 -> NULL
        #   DECIMAL(38,0)  intermediate: -> 92233720368.5477580000  (2 digits lost)
        #   DECIMAL(38,10) intermediate: -> 92233720368.5477580700  (exact)
        expr = wire.fixed_point_expr("qty")
        inner = re.search(r"CAST\(qty AS DECIMAL\((\d+),(\d+)\)\)", expr)
        assert inner, f"the intermediate cast is no longer a plain DECIMAL: {expr}"
        precision, scale = int(inner.group(1)), int(inner.group(2))

        int64_digits = len(str(2**63 - 1))  # 19
        assert precision - scale >= int64_digits, (
            f"DECIMAL({precision},{scale}) holds {precision - scale} integer digits; "
            f"int64 needs {int64_digits}. |v| >= 10**{precision - scale} casts to NULL "
            "and lands in a NOT NULL column"
        )
        # And the quotient's scale must survive the divide: DECIMAL(38,0) is
        # wide enough for the input and still loses the bottom two digits,
        # because Spark caps the quotient's precision at 38 and takes it out of
        # the scale.
        assert scale >= wire.FIXED_POINT_SCALE, (
            f"an intermediate scale of {scale} truncates the fixed-point digits "
            "before the divide can use them"
        )

    def test_decimal_28_10_holds_the_whole_int64_range(self):
        # The wire is int64 at 1e-8 (schemas/avro/trade.avsc). Shrinking either
        # half of DECIMAL(28,10) silently truncates real prices, so the DDL's
        # numbers are asserted against the arithmetic rather than trusted.
        declared = re.search(r"price\s+DECIMAL\((\d+),(\d+)\)", _DDL.read_text())
        assert declared, "the lake price column is no longer DECIMAL(p,s)"
        precision, scale = int(declared.group(1)), int(declared.group(2))

        assert scale >= wire.FIXED_POINT_SCALE, "scale drops digits the wire carries"
        max_wire = (2**63 - 1) / 10**wire.FIXED_POINT_SCALE  # ~9.22e10
        integer_digits = len(str(int(max_wire)))
        assert precision - scale >= integer_digits, "precision cannot hold int64 at 1e-8"


# ── Avro schema to lake DDL ─────────────────────────────────────────────────


class TestRawMessages:
    def test_carries_the_nine_archive_columns(self):
        # The columns the plan names for raw.messages. This table is frozen by
        # policy (no schema evolution beyond nullable adds), so the list is
        # written out rather than derived.
        assert set(_ddl_columns("raw.messages")) == {
            "topic",
            "partition",
            "offset",
            "kafka_ts",
            "ingest_ts",
            "key",
            "schema_id",
            "payload",
            "headers",
        }

    def test_payload_is_binary_not_string(self):
        # A string forces UTF-8 validation and would corrupt a malformed frame —
        # which is precisely the frame worth keeping (raw-message.avsc).
        assert re.search(r"payload\s+BINARY\s+NOT NULL", _DDL.read_text())


_CH_KAFKA = Path(__file__).parent.parent / "docker" / "clickhouse" / "ddl" / "20-gold-kafka.sql"


def _ch_queue_columns(table):
    """Column names of one `CREATE TABLE IF NOT EXISTS gold.<table>` block in 20-gold-kafka.sql."""
    body = re.search(
        rf"CREATE TABLE IF NOT EXISTS gold\.{re.escape(table)}\n\((.*?)\n\)\nENGINE = Kafka",
        _CH_KAFKA.read_text(),
        re.DOTALL,
    )
    assert body, f"no Kafka-engine CREATE TABLE for gold.{table} in {_CH_KAFKA}"
    return [m.group(1) for m in re.finditer(r"^\s{4}(\w+)\s+[A-Z]", body.group(1), re.MULTILINE)]


class TestTradeContract:
    """trade.avsc is consumed by ClickHouse's AvroConfluent queue table, column for column.

    Since the Phase E cutover the lake decodes the venue JSON and never the Avro
    topics, so the Avro contract's reader is the served tier. AvroConfluent maps
    fields to columns BY NAME and rejects a record whose columns it cannot fill,
    so a field added to the schema and not to the queue table is a stalled feed
    (gold.feed_errors), and a column added to the queue table that no field
    feeds is a decode error on every record. Both directions, therefore.
    """

    def test_every_avro_field_is_a_queue_column(self):
        missing = set(_avro_fields("trade.avsc")) - set(_ch_queue_columns("q_trades"))
        assert not missing, f"trade.avsc fields with no gold.q_trades column: {sorted(missing)}"

    def test_no_queue_column_without_a_field(self):
        extra = set(_ch_queue_columns("q_trades")) - set(_avro_fields("trade.avsc"))
        assert not extra, f"gold.q_trades columns no trade.avsc field feeds: {sorted(extra)}"


class TestBookContract:
    def test_every_avro_field_is_a_queue_column_and_nothing_else(self):
        avro, cols = set(_avro_fields("book-snapshot-l2.avsc")), set(_ch_queue_columns("q_book"))
        assert avro == cols, f"book-snapshot-l2.avsc vs gold.q_book: missing {sorted(avro - cols)}, extra {sorted(cols - avro)}"

    def test_the_lake_levels_are_decimal_pairs(self):
        # silver.book_* keeps levels as struct<px, qty> decimals; gold.book_top20
        # keeps the wire's parallel int64 arrays so ClickHouse loads it as is.
        ddl = _DDL.read_text()
        assert "ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>>" in ddl
        assert "bid_px_e8        ARRAY<BIGINT> NOT NULL" in ddl


class TestTablePolicies:
    @pytest.mark.parametrize(
        "table",
        [
            "raw.messages",
            "bronze.binance_trade",
            "bronze.kraken_book",
            "bronze.coinbase_level2",
            "silver.trades_kraken",
            "silver.book_kraken",
            "gold.trades",
            "gold.ohlcv_1m",
            "gold.book_top20",
            "audit.checks",
        ],
    )
    def test_every_table_is_copy_on_write(self, table):
        # Merge-on-read would put positional delete files in front of readers
        # that have not been shown to handle them on this stack. Flipping one of
        # these is a spike, not an edit (the note at the head of lake.sql).
        block = re.search(
            rf"CREATE TABLE IF NOT EXISTS lake\.{re.escape(table)} \(.*?\n\);",
            _DDL.read_text(),
            re.DOTALL,
        )
        assert block, f"no CREATE TABLE block for lake.{table}"
        for mode in ("delete", "update", "merge"):
            assert f"'write.{mode}.mode'" in block.group(0)
            assert "copy-on-write" in block.group(0)

    def test_raw_messages_targets_larger_files_than_bronze(self):
        # 256 MB for the archive, 128 MB for bronze: raw is scanned in bulk by
        # replay, bronze is pruned by symbol and benefits from finer files.
        text = _DDL.read_text()
        assert "'write.target-file-size-bytes'              = '268435456'" in text
        assert "'write.target-file-size-bytes'                  = '134217728'" in text
