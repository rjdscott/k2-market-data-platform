#!/usr/bin/env python3
"""
The Confluent wire framing, in the two forms the lake needs it: a Python parser
for the driver, and the Spark SQL expressions for the executors.

Both are here so the framing offsets are written down once. `services/capture-
rust/src/sink.rs` produces these bytes through `schema_registry_converter`;
everything below is the reader's half of that contract.

    byte 0        magic, always 0x00
    bytes 1..4    schema id, big-endian int32, registered under TopicNameStrategy
    bytes 5..     the Avro body, no schema attached

Pure — no Spark import, no network. tests/test_wire_format.py runs it directly.
"""

from __future__ import annotations

import struct

MAGIC = 0
HEADER_BYTES = 5

# The fixed-point scale shared by Trade.price / Trade.qty and every px/qty in
# BookSnapshotL2: the wire carries round(decimal * 10**FIXED_POINT_SCALE) as an
# int64. schemas/avro/trade.avsc is the contract; this is the divisor.
FIXED_POINT_SCALE = 8


class BadFrame(ValueError):
    """A payload that is not a Confluent-framed record."""


def parse_confluent(frame: bytes):
    """`(schema_id, body)` from a framed payload. Raises BadFrame otherwise.

    Used by `ingest.py --probe`, which pulls one record per topic to the driver
    and reports the schema id before a decode job is submitted. Failing here
    gives "magic byte 0x7b, not 0x00 — this looks like unframed JSON" instead of
    an Avro deserialiser error inside an executor, on a topic name nobody logged.
    """
    if len(frame) < HEADER_BYTES:
        raise BadFrame(f"frame is {len(frame)} bytes, shorter than the {HEADER_BYTES}-byte Confluent header")
    magic, schema_id = struct.unpack(">bi", frame[:HEADER_BYTES])
    if magic != MAGIC:
        raise BadFrame(f"magic byte is {magic & 0xFF:#04x}, expected {MAGIC:#04x}")
    if schema_id <= 0:
        raise BadFrame(f"schema id {schema_id} is not a registered id")
    return schema_id, frame[HEADER_BYTES:]


# ── the same framing, as Spark SQL ──────────────────────────────────────────
# Spark's `substring` is 1-based and works on BINARY, so byte 1 is position 1
# and the body starts at position 6. `conv(hex(...), 16, 10)` is how a 4-byte
# big-endian integer is read without a UDF: Spark has no `get_int_be`, and a
# Python UDF on every record would cost a serialisation round trip per row for
# four bytes of arithmetic.

SCHEMA_ID_SQL = "CAST(conv(hex(substring({col}, 2, 4)), 16, 10) AS INT)"
BODY_SQL = "substring({col}, {start})".format(col="{col}", start=HEADER_BYTES + 1)
MAGIC_OK_SQL = "hex(substring({col}, 1, 1)) = '00'"

# The SQL half of `parse_confluent`, and it has to agree with it on every case
# or a record the driver calls unframed becomes one the executors decode.
# Measured disagreement before this existed: a 3-byte `00 00 01` frame passed
# MAGIC_OK_SQL, produced a *fabricated* schema id of 1 and an empty body, and
# then killed stage 2 on `fetch_schema` — permanently, because the record is
# already archived and every following run re-reads the same range. Same for a
# frame declaring id 0, and for one whose high bit is set (schema_id NULL, but
# a NULL from `conv` overflow rather than from "not framed").
#
# All three rejections are the three `parse_confluent` makes: too short, wrong
# magic, id not a registered (positive) one. NULL is the single answer for all
# of them, which is exactly what lake.raw.messages.schema_id is nullable for.
SCHEMA_ID_GUARDED_SQL = (
    "CASE WHEN length({col}) >= {header} AND {magic} AND ({sid}) > 0"
    " THEN ({sid}) END"
).format(
    col="{col}",
    header=HEADER_BYTES,
    magic=MAGIC_OK_SQL,
    sid=SCHEMA_ID_SQL,
)

# int64 at 1e-8 -> DECIMAL(28,10). Exact: the divisor is a power of ten, so no
# rounding step exists to get wrong. The cast pins the result type, because
# Spark's decimal division otherwise picks its own precision and scale and the
# writer then has to coerce it at the table boundary.
#
# The INTERMEDIATE cast is DECIMAL(38,10), not DECIMAL(28,10), and that is the
# whole point of this line. int64 max is 9223372036854775807 — 19 digits — and
# a DECIMAL(28,10) holds 18 integer digits, so with `spark.sql.ansi.enabled`
# off the cast overflows to NULL *before* the divide and writes NULL into a
# NOT NULL column. Measured in k2-spark-iceberg: |v| >= 1e18 -> NULL, which at
# 1e-8 is 1e10 units, an ordinary SHIB/PEPE quantity. DECIMAL(38,0) does not
# fix it either — it divides exactly but Spark caps the quotient's scale and
# 9223372036854775807 comes back 92233720368.5477580000, two digits short.
# DECIMAL(38,10) is the one that round-trips: 92233720368.5477580700.
FIXED_POINT_SQL = "CAST(CAST({col} AS DECIMAL(38,10)) / {divisor} AS DECIMAL(28,10))".format(
    col="{col}", divisor=10**FIXED_POINT_SCALE
)


def schema_id_expr(col: str = "value") -> str:
    return SCHEMA_ID_SQL.format(col=col)


def schema_id_guarded_expr(col: str = "value") -> str:
    """The schema id, or NULL for anything `parse_confluent` would reject."""
    return SCHEMA_ID_GUARDED_SQL.format(col=col)


def body_expr(col: str = "value") -> str:
    return BODY_SQL.format(col=col)


def fixed_point_expr(col: str) -> str:
    return FIXED_POINT_SQL.format(col=col)
