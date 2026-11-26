"""
books.py's Binance gold write: a second that straddles two ingest runs.

binance_books() keeps the last depth20 frame per (symbol, second) within ONE
run's batch. A run that stops mid-second and the next run each hold a frame of
that second, and appending both batches left two rows on gold.book_top20's
identity, so the nightly `duplicate_identifiers` audit failed on every Binance
day. process() now MERGEs them with merge_sql(), and gold.bbo_1s with it.

The statements below are merge_sql()'s own strings, executed by DuckDB (1.4.4,
the notebooks' engine, which takes Spark's `UPDATE SET *` / `INSERT *`) against
tables built from ddl/lake.sql. Spark and Iceberg are not in this run: the
copy-on-write mechanics are Iceberg's, the key and the later-frame rule are
ours, and those are what is tested. Everything here is synthetic.
"""

import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from k2gen import ddl

duckdb = pytest.importorskip("duckdb")

_DDL = Path(__file__).resolve().parent.parent / "docker" / "lake" / "ddl" / "lake.sql"
T0 = datetime(2026, 1, 1)
T0_NS = int(T0.replace(tzinfo=timezone.utc).timestamp()) * 1_000_000_000
E8 = 100_000_000


@pytest.fixture(scope="module")
def books():
    # books.py imports pyspark at module level; nothing tested here calls it.
    for name in ("pyspark", "pyspark.sql"):
        sys.modules.setdefault(name, types.ModuleType(name))
    for attr in ("DataFrame", "Row", "Window", "functions", "SparkSession"):
        if not hasattr(sys.modules["pyspark.sql"], attr):
            setattr(sys.modules["pyspark.sql"], attr, object())
    import books as module

    return module


@pytest.fixture
def con():
    c = duckdb.connect()
    c.sql("ATTACH ':memory:' AS lake")
    c.sql("CREATE SCHEMA lake.gold")
    for table in ("gold.book_top20", "gold.bbo_1s"):
        cols = ", ".join(
            f"{col.name} {col.type.replace('ARRAY<BIGINT>', 'BIGINT[]').replace('STRING', 'VARCHAR')}"
            for col in ddl.parse(_DDL)[table].schema
        )
        c.sql(f"CREATE TABLE lake.{table} ({cols})")
    return c


def frame(sec, ms, msg_seq):
    """A gold.book_top20 row: the frame received `ms` into second `sec`, its venue seq = msg_seq."""
    return {
        "exchange": "binance", "canonical_symbol": "TEST/USD", "symbol": "TESTUSD",
        "second": T0 + timedelta(seconds=sec), "depth": 1, "seq": msg_seq, "checksum_ok": None,
        "bid_px_e8": [100 * E8], "bid_qty_e8": [E8], "ask_px_e8": [101 * E8], "ask_qty_e8": [E8],
        "recv_ts_ns": T0_NS + sec * 1_000_000_000 + ms * 1_000_000, "conn_id": "conn-1",
        "conn_msg_seq": msg_seq, "src_topic": "synthetic", "src_partition": 0, "src_offset": msg_seq,
        "src_index": 0, "ingest_ts": T0,
    }


def merge(books, con, target, rows, later):
    """One run's batch through the statement process() runs, `since` = the batch's first second."""
    con.sql("DROP TABLE IF EXISTS batch")
    con.sql(f"CREATE TEMP TABLE batch AS SELECT * FROM {target} LIMIT 0")
    cols = [r[0] for r in con.sql("DESCRIBE batch").fetchall()]
    con.executemany(f"INSERT INTO batch VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows])
    since = f"TIMESTAMP '{min(r['second'] for r in rows):%Y-%m-%d %H:%M:%S}'"
    con.sql(books.merge_sql(target, "batch", since, later))


def book(books, con):
    return con.sql(f"SELECT second, recv_ts_ns, conn_msg_seq, seq FROM {books.GOLD_BOOK} ORDER BY ALL").fetchall()


def test_the_merge_key_is_the_identity_lake_sql_declares(books):
    text = _DDL.read_text()
    for table in (books.GOLD_BOOK, books.GOLD_BBO):
        declared = re.search(rf"^ALTER TABLE {re.escape(table)} SET IDENTIFIER FIELDS ([^;]+);", text, re.M)
        assert tuple(f.strip() for f in declared.group(1).split(",")) == books.GOLD_KEY, table


def test_a_second_split_across_two_runs_is_one_row_holding_the_later_frame(books, con):
    # Run 1 stops 200 ms into second 1; run 2 picks up 700 ms into it.
    merge(books, con, books.GOLD_BOOK, [frame(0, 900, 1), frame(1, 200, 2)], later=True)
    merge(books, con, books.GOLD_BOOK, [frame(1, 700, 3), frame(2, 100, 4)], later=True)
    rows = book(books, con)
    assert [r[0] for r in rows] == [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]
    assert rows[1][2:] == (3, 3), "second 1 holds run 2's frame, the later one"


def test_rerunning_either_batch_changes_nothing(books, con):
    run1, run2 = [frame(0, 900, 1), frame(1, 200, 2)], [frame(1, 700, 3), frame(2, 100, 4)]
    merge(books, con, books.GOLD_BOOK, run1, later=True)
    merge(books, con, books.GOLD_BOOK, run2, later=True)
    before = book(books, con)
    merge(books, con, books.GOLD_BOOK, run2, later=True)
    # Run 1 again is an EARLIER frame of second 1 arriving after a later one: it must not win.
    merge(books, con, books.GOLD_BOOK, run1, later=True)
    assert book(books, con) == before


def test_a_receive_time_tie_is_broken_by_conn_msg_seq(books, con):
    tie = frame(1, 500, 7)
    merge(books, con, books.GOLD_BOOK, [tie], later=True)
    merge(books, con, books.GOLD_BOOK, [{**tie, "conn_msg_seq": 6, "seq": 6}], later=True)
    assert book(books, con)[0][2] == 7
    merge(books, con, books.GOLD_BOOK, [{**tie, "conn_msg_seq": 8, "seq": 8}], later=True)
    assert book(books, con)[0][2] == 8


def test_bbo_1s_for_a_split_second_is_replaced_not_added(books, con):
    def bbo(bid_e8, snapshot):
        return {
            "exchange": "binance", "canonical_symbol": "TEST/USD", "second": T0 + timedelta(seconds=1),
            "bid_e8": bid_e8, "bid_qty_e8": E8, "ask_e8": 101 * E8, "ask_qty_e8": E8, "mid": 100.5,
            "spread_bps": 1.0, "imbalance": 0.5, "microprice": 100.5, "checksum_ok": None, "src_snapshot_id": snapshot,
        }

    merge(books, con, books.GOLD_BBO, [bbo(100 * E8, 1)], later=False)
    merge(books, con, books.GOLD_BBO, [bbo(99 * E8, 2)], later=False)
    assert con.sql(f"SELECT bid_e8, src_snapshot_id FROM {books.GOLD_BBO}").fetchall() == [(99 * E8, 2)]
