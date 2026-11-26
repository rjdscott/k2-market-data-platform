"""
scripts/replay_export.py: the pure part — Confluent-framed RawMessage rows in,
one connection's fixture lines out, in the socket's order.
"""

import io
import json
import sys
from pathlib import Path

import pytest
from fastavro import schemaless_writer

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import replay_export as rx  # noqa: E402


def framed(conn_id: str, seq: int, recv_ts_ns: int, payload: bytes, schema_id: int = 7) -> tuple:
    body = io.BytesIO()
    schemaless_writer(
        body,
        rx.RAW_SCHEMA,
        {
            "exchange": "kraken",
            "stream": "book",
            "symbol": "BTC/USD",
            "recv_ts_ns": recv_ts_ns,
            "conn_id": conn_id,
            "conn_msg_seq": seq,
            "payload": payload,
        },
    )
    return schema_id, b"\x00" + schema_id.to_bytes(4, "big") + body.getvalue()


def test_one_connection_in_conn_msg_seq_order():
    # Delivered out of order across partitions, mixed with another connection
    # and an un-framed row; only conn A comes back, by its own counter.
    rows = [
        framed("A", 3, 300, b'{"n":3}'),
        framed("B", 1, 50, b'{"other":true}'),
        framed("A", 1, 100, b'{"n":1}'),
        (None, b"\x00\x00\x01"),
        framed("A", 2, 200, b'{"n":2}'),
    ]
    lines = rx.frames(rows, "A")
    assert [json.loads(line["payload"])["n"] for line in lines] == [1, 2, 3]
    assert [line["recv_ts_ns"] for line in lines] == [100, 200, 300]


def test_until_trims_the_tail_only():
    rows = [framed("A", i, i * 100, b"{}") for i in (1, 2, 3, 4)]
    assert [line["recv_ts_ns"] for line in rx.frames(rows, "A", until_ns=250)] == [100, 200]


@pytest.mark.parametrize("payload", [b"", b"\x00\x00\x01", b"\x01" + b"x" * 10])
def test_unframed_rows_are_skipped_not_fatal(payload):
    assert rx.frames([(1, payload)], "A") == []


def test_a_gap_in_conn_msg_seq_is_fatal():
    # The scan window missed frame 3: a replay of 1, 2, 4 would fold deltas
    # into a book that never saw one of them and look perfectly normal.
    rows = [framed("A", i, i * 100, b"{}") for i in (1, 2, 4)]
    with pytest.raises(SystemExit, match="not 1..n unbroken"):
        rx.frames(rows, "A")


def test_a_duplicated_conn_msg_seq_is_fatal():
    rows = [framed("A", i, i * 100, b"{}") for i in (1, 2, 2, 3)]
    with pytest.raises(SystemExit, match="4 frames"):
        rx.frames(rows, "A")


class _FakeResult:
    """Just enough of a duckdb cursor for export(): fetchall() for connections(),
    fetchmany() for the raw.messages scan."""

    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchmany(self, n):
        rows, self.rows = self.rows[:n], self.rows[n:]
        return rows


class _FakeConn:
    """A conn_id that's a real connection (bronze bounds know it) but the
    raw.messages scan comes back empty — wrong snapshot, --until before the
    first frame, or a spelling that only collides with the bounds lookup."""

    def __init__(self, bounds_row):
        self.bounds_row = bounds_row

    def execute(self, sql):
        if "GROUP BY" in sql:  # connections()
            return _FakeResult([self.bounds_row])
        return _FakeResult([])  # the raw.messages scan in export()


def test_zero_frames_for_a_known_conn_id_is_fatal_not_a_silent_empty_export():
    # This is the case a bare `if seqs and ...` guard inside frames() misses:
    # the export is empty before frames() ever runs, so there's no seqs list
    # to be non-contiguous. A silent empty export here means `k2-capture
    # replay` emits 0 records and replay-lake.sh files observed=0, passed=true.
    c = _FakeConn(("A", 3, "2026-01-01 00:00:00", "2026-01-01 00:05:00"))
    with pytest.raises(SystemExit, match="A: 0 frames at raw.messages snapshot 42"):
        rx.export(c, "kraken", 42, "A", None, io.StringIO())
