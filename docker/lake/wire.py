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

# int64 at 1e-8 -> DECIMAL(28,10). Exact: the divisor is a power of ten, so no
# rounding step exists to get wrong. The cast pins the result type, because
# Spark's decimal division otherwise picks its own precision and scale and the
# writer then has to coerce it at the table boundary.
FIXED_POINT_SQL = "CAST(CAST({col} AS DECIMAL(28,10)) / {divisor} AS DECIMAL(28,10))".format(
    col="{col}", divisor=10**FIXED_POINT_SCALE
)


def schema_id_expr(col: str = "value") -> str:
    return SCHEMA_ID_SQL.format(col=col)


def body_expr(col: str = "value") -> str:
    return BODY_SQL.format(col=col)


def fixed_point_expr(col: str) -> str:
    return FIXED_POINT_SQL.format(col=col)
