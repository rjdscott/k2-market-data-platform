"""
Unit tests for the wire format the lake reads: docker/lake/wire.py, and the
contract between schemas/avro/*.avsc and docker/lake/ddl/lake.sql.

Pure python — the .avsc files and the .sql file are parsed as text. No Spark, no
catalog, no Avro library.

The second half is the one that earns its place. CLAUDE.md's schema-change rule
says the Avro schema, the DDL and the docs move in one PR or not at all, and the
failure mode it warns about is silence: a field added to trade.avsc that never
reaches bronze.trades does not break a build, it produces a column of nothing at
the offload boundary hours later. These tests are that rule, executable.
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


class TestFixedPoint:
    def test_divides_by_ten_to_the_scale(self):
        assert "/ 100000000" in wire.fixed_point_expr("price")

    def test_casts_to_the_declared_decimal_type(self):
        assert wire.fixed_point_expr("d.price").count("DECIMAL(28,10)") == 2

    def test_decimal_28_10_holds_the_whole_int64_range(self):
        # The wire is int64 at 1e-8 (schemas/avro/trade.avsc). Shrinking either
        # half of DECIMAL(28,10) silently truncates real prices, so the DDL's
        # numbers are asserted against the arithmetic rather than trusted.
        declared = re.search(r"price\s+DECIMAL\((\d+),(\d+)\)", _DDL.read_text())
        assert declared, "bronze.trades.price is no longer DECIMAL(p,s)"
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


class TestTradeContract:
    def test_every_avro_field_has_a_column(self):
        missing = set(_avro_fields("trade.avsc")) - set(_ddl_columns("bronze.trades"))
        assert not missing, f"trade.avsc fields with no bronze.trades column: {sorted(missing)}"

    def test_lineage_columns_are_present(self):
        columns = set(_ddl_columns("bronze.trades"))
        assert {"src_topic", "src_partition", "src_offset"} <= columns

    def test_the_only_extra_columns_are_lineage_and_ingest_time(self):
        # Catches the reverse drift: a column added to the table that no field
        # feeds, which decodes to null on every row forever.
        extra = set(_ddl_columns("bronze.trades")) - set(_avro_fields("trade.avsc"))
        assert extra == {"src_topic", "src_partition", "src_offset", "ingest_ts"}


class TestBookContract:
    # The wire carries four parallel arrays; the lake stores two arrays of
    # struct<px, qty>. That is the one deliberate reshaping between the two
    # files, so it is written down here as the mapping it is.
    ZIPPED = {"bid_px": "bids", "bid_qty": "bids", "ask_px": "asks", "ask_qty": "asks"}

    def test_every_avro_field_reaches_a_column(self):
        columns = set(_ddl_columns("bronze.book_snapshots_l2"))
        for field in _avro_fields("book-snapshot-l2.avsc"):
            target = self.ZIPPED.get(field, field)
            assert target in columns, f"book-snapshot-l2.avsc field {field} has no column"

    def test_snapshot_ts_is_derived_and_the_nanoseconds_are_kept(self):
        # snapshot_ts carries the partition; snapshot_ts_ns stays authoritative
        # because a microsecond TIMESTAMP cannot hold the sampler's clock.
        columns = set(_ddl_columns("bronze.book_snapshots_l2"))
        assert {"snapshot_ts", "snapshot_ts_ns"} <= columns

    def test_the_table_is_partitioned_on_a_clock_that_is_never_null(self):
        # exchange_ts is null for every binance row (partial-depth carries no
        # venue timestamp), so partitioning on it would put a third of the rows
        # in the null partition.
        assert "PARTITIONED BY (exchange, days(snapshot_ts))" in _DDL.read_text()

    def test_the_levels_are_decimal_pairs(self):
        assert "ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>>" in _DDL.read_text()


class TestTablePolicies:
    @pytest.mark.parametrize(
        "table", ["raw.messages", "bronze.trades", "bronze.book_snapshots_l2", "audit.checks"]
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
