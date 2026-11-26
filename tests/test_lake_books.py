"""docker/lake/books.py, kept for reuse with its tables dropped: the pure parts.

merge_sql() is what made a Binance second split across two ingest runs one row of
gold.book_top20 instead of two: its statements run here in DuckDB (which takes
Spark's `UPDATE SET *` / `INSERT *`) against the two gold tables as the archived
DDL defined them, inlined below because that DDL left the live tree. The Iceberg
copy-on-write mechanics are Iceberg's; the key and the later-frame rule are
books.py's. replay_partition() is the per-connection book replay, plain Python.
Everything here is synthetic.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from conftest import import_lake_module

duckdb = pytest.importorskip("duckdb")
books = import_lake_module("books")
B = import_lake_module("book")

# The columns of gold.book_top20 and gold.bbo_1s, from the archived
# legacy/v3-lake-ingest/lake/ddl/lake.sql, in DuckDB types.
TABLES = {
    books.GOLD_BOOK: "exchange VARCHAR, canonical_symbol VARCHAR, symbol VARCHAR, second TIMESTAMP, depth INT, "
                     "seq BIGINT, checksum_ok BOOLEAN, bid_px_e8 BIGINT[], bid_qty_e8 BIGINT[], ask_px_e8 BIGINT[], "
                     "ask_qty_e8 BIGINT[], recv_ts_ns BIGINT, conn_id VARCHAR, conn_msg_seq BIGINT, src_topic VARCHAR, "
                     "src_partition INT, src_offset BIGINT, src_index INT, ingest_ts TIMESTAMP",
    books.GOLD_BBO: "exchange VARCHAR, canonical_symbol VARCHAR, second TIMESTAMP, bid_e8 BIGINT, bid_qty_e8 BIGINT, "
                    "ask_e8 BIGINT, ask_qty_e8 BIGINT, mid DOUBLE, spread_bps DOUBLE, imbalance DOUBLE, "
                    "microprice DOUBLE, checksum_ok BOOLEAN, src_snapshot_id BIGINT",
}
T0 = datetime(2026, 1, 1)
T0_NS = int(T0.replace(tzinfo=timezone.utc).timestamp()) * 1_000_000_000
NS, MS, E8 = 1_000_000_000, 1_000_000, 100_000_000


@pytest.fixture
def con():
    c = duckdb.connect()
    c.sql("ATTACH ':memory:' AS lake")
    c.sql("CREATE SCHEMA lake.gold")
    for table, cols in TABLES.items():
        c.sql(f"CREATE TABLE {table} ({cols})")
    return c


def frame(sec, ms, msg_seq):
    """A gold.book_top20 row: the frame received `ms` into second `sec`, its venue seq = msg_seq."""
    return {
        "exchange": "binance", "canonical_symbol": "TEST/USD", "symbol": "TESTUSD",
        "second": T0 + timedelta(seconds=sec), "depth": 1, "seq": msg_seq, "checksum_ok": None,
        "bid_px_e8": [100 * E8], "bid_qty_e8": [E8], "ask_px_e8": [101 * E8], "ask_qty_e8": [E8],
        "recv_ts_ns": T0_NS + sec * NS + ms * MS, "conn_id": "conn-1",
        "conn_msg_seq": msg_seq, "src_topic": "synthetic", "src_partition": 0, "src_offset": msg_seq,
        "src_index": 0, "ingest_ts": T0,
    }


def merge(con, target, rows, later):
    """One run's batch through the statement process() runs, `since` = the batch's first second."""
    con.sql("DROP TABLE IF EXISTS batch")
    con.sql(f"CREATE TEMP TABLE batch AS SELECT * FROM {target} LIMIT 0")
    cols = [r[0] for r in con.sql("DESCRIBE batch").fetchall()]
    con.executemany(f"INSERT INTO batch VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows])
    since = f"TIMESTAMP '{min(r['second'] for r in rows):%Y-%m-%d %H:%M:%S}'"
    con.sql(books.merge_sql(target, "batch", since, later))


def book(con):
    return con.sql(f"SELECT second, recv_ts_ns, conn_msg_seq, seq FROM {books.GOLD_BOOK} ORDER BY ALL").fetchall()


def test_merge_sql_matches_on_the_identity_and_prunes_by_since():
    sql = books.merge_sql("t_gold", "s_batch", "TIMESTAMP '2026-01-01 00:00:00'", later=False)
    assert books.GOLD_KEY == ("exchange", "canonical_symbol", "second")   # the archived tables' IDENTIFIER FIELDS
    assert "ON t.exchange = s.exchange AND t.canonical_symbol = s.canonical_symbol AND t.second = s.second" in sql
    assert "AND t.second >= TIMESTAMP '2026-01-01 00:00:00'" in sql
    assert "WHEN MATCHED THEN UPDATE SET *" in sql
    assert "recv_ts_ns" in books.merge_sql("t", "s", "x", later=True)


def test_a_second_split_across_two_runs_is_one_row_holding_the_later_frame(con):
    # Run 1 stops 200 ms into second 1; run 2 picks up 700 ms into it.
    merge(con, books.GOLD_BOOK, [frame(0, 900, 1), frame(1, 200, 2)], later=True)
    merge(con, books.GOLD_BOOK, [frame(1, 700, 3), frame(2, 100, 4)], later=True)
    rows = book(con)
    assert [r[0] for r in rows] == [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]
    assert rows[1][2:] == (3, 3), "second 1 holds run 2's frame, the later one"


def test_rerunning_either_batch_changes_nothing(con):
    run1, run2 = [frame(0, 900, 1), frame(1, 200, 2)], [frame(1, 700, 3), frame(2, 100, 4)]
    merge(con, books.GOLD_BOOK, run1, later=True)
    merge(con, books.GOLD_BOOK, run2, later=True)
    before = book(con)
    merge(con, books.GOLD_BOOK, run2, later=True)
    # Run 1 again is an EARLIER frame of second 1 arriving after a later one: it must not win.
    merge(con, books.GOLD_BOOK, run1, later=True)
    assert book(con) == before


def test_a_receive_time_tie_is_broken_by_conn_msg_seq(con):
    tie = frame(1, 500, 7)
    merge(con, books.GOLD_BOOK, [tie], later=True)
    merge(con, books.GOLD_BOOK, [{**tie, "conn_msg_seq": 6, "seq": 6}], later=True)
    assert book(con)[0][2] == 7
    merge(con, books.GOLD_BOOK, [{**tie, "conn_msg_seq": 8, "seq": 8}], later=True)
    assert book(con)[0][2] == 8


def test_bbo_1s_for_a_split_second_is_replaced_not_added(con):
    def bbo(bid_e8, snapshot):
        return {
            "exchange": "binance", "canonical_symbol": "TEST/USD", "second": T0 + timedelta(seconds=1),
            "bid_e8": bid_e8, "bid_qty_e8": E8, "ask_e8": 101 * E8, "ask_qty_e8": E8, "mid": 100.5,
            "spread_bps": 1.0, "imbalance": 0.5, "microprice": 100.5, "checksum_ok": None, "src_snapshot_id": snapshot,
        }

    merge(con, books.GOLD_BBO, [bbo(100 * E8, 1)], later=False)
    merge(con, books.GOLD_BBO, [bbo(99 * E8, 2)], later=False)
    assert con.sql(f"SELECT bid_e8, src_snapshot_id FROM {books.GOLD_BBO}").fetchall() == [(99 * E8, 2)]


# ── replay_partition: one connection's frames, in conn_msg_seq order ─────────

def cb(ms, seq, event, updates):
    """A typed Coinbase level2 event received `ms` after T0."""
    return {"symbol": "TEST-USD", "canonical_symbol": "TEST/USD", "conn_id": "c1", "src_topic": "synthetic",
            "src_partition": 0, "src_offset": seq, "src_index": 0, "recv_ts_ns": T0_NS + ms * MS,
            "conn_msg_seq": seq, "event_type": event, "sequence_num": seq,
            "updates": [{"side": s, "px": D(p), "qty": D(q)} for s, p, q in updates]}


def run(rows, exchange="coinbase", precisions=None, states=None):
    return list(books.replay_partition(rows, exchange, precisions or {}, states or {}, T0))


def test_a_book_is_sampled_at_every_second_end_and_the_open_second_is_carried():
    out = run([
        cb(100, 1, "update", [("bid", "1", "1")]),                      # before the snapshot: no book yet
        cb(200, 2, "snapshot", [("bid", "100", "1"), ("ask", "101", "2")]),
        cb(1500, 3, "update", [("bid", "100", "3")]),
        cb(3100, 4, "update", [("ask", "101", "0")]),                    # qty 0 removes the level
    ])
    snaps = [r for kind, r in out if kind == "book"]
    assert [(r["second"], r["seq"], r["bid_qty_e8"]) for r in snaps] == [
        (T0, 2, [E8]),                                  # second 0 ends on the snapshot
        (T0 + timedelta(seconds=1), 3, [3 * E8]),       # seconds 1 and 2 on the 1.5 s update
        (T0 + timedelta(seconds=2), 3, [3 * E8]),
    ]
    assert [p for p in snaps[0]["bid_px_e8"]] == [100 * E8] and snaps[0]["ask_qty_e8"] == [2 * E8]
    # Second 3 is still open: the state carries it to the next tick, with the last frame folded in.
    [(kind, state)] = [x for x in out if x[0] == "state"]
    assert (state["seq"], state["last_second"], state["ask_px_e8"]) == (4, T0 + timedelta(seconds=2), [])
    assert out[-1][0] == "state"


def test_the_next_tick_resumes_from_the_carried_state():
    first = run([cb(200, 2, "snapshot", [("bid", "100", "1")]), cb(1500, 3, "update", [("bid", "100", "3")])])
    state = next(r for kind, r in first if kind == "state")
    out = run([cb(2300, 4, "update", [("bid", "99", "5")])], states={("TEST-USD", "c1"): state})
    snaps = [r for kind, r in out if kind == "book"]
    # Second 1 ends on the carried book (the 1.5 s update), not on an empty one.
    assert [(r["second"], r["bid_qty_e8"]) for r in snaps] == [(T0 + timedelta(seconds=1), [3 * E8])]


def kr(ms, seq, frame_type, bids, asks, checksum):
    return {"symbol": "TEST/USD", "canonical_symbol": "TEST/USD", "conn_id": "k1", "src_topic": "synthetic",
            "src_partition": 0, "src_offset": seq, "src_index": 0, "recv_ts_ns": T0_NS + ms * MS,
            "conn_msg_seq": seq, "frame_type": frame_type, "checksum": checksum,
            "bids": [{"px": D(p), "qty": D(q)} for p, q in bids], "asks": [{"px": D(p), "qty": D(q)} for p, q in asks]}


def test_kraken_frames_carry_the_checksum_verdict_of_the_replayed_book():
    bids, asks = [("100.0", "1"), ("99.5", "2")], [("100.5", "1"), ("101.0", "3")]
    expected = B.kraken_checksum([(B.fixed(p), B.fixed(q)) for p, q in asks],
                                 [(B.fixed(p), B.fixed(q)) for p, q in bids], 1, 8)
    out = run([
        kr(100, 1, "update", [("100.0", "5")], [], 0),            # before any snapshot: no verdict
        kr(200, 2, "snapshot", bids, asks, expected),
        kr(300, 3, "update", [("100.0", "4")], [], expected),     # the book moved, the checksum did not
    ], exchange="kraken", precisions={"TEST/USD": (1, 8)})
    assert [v for kind, _, v in (x for x in out if x[0] == "verdict")] == [None, True, False]
    # No precision for the symbol: the verdict is unknown, not a pass.
    out = run([kr(200, 2, "snapshot", bids, asks, expected)], exchange="kraken")
    assert [x[2] for x in out if x[0] == "verdict"] == [None]
