"""k2lake.trades / completeness against synthetic tables — no stack needed.

Every test here fails if one of the four things that actually bite is broken:
the +1 SECOND rule, the LEFT-ness of the two ASOF joins, the gap counters, and
parameter binding. Run with `make test-notebooks` (or `cd notebooks && uv run pytest`).
"""

from __future__ import annotations

import duckdb
import pytest
from k2lake import SCALE, completeness, trades

# One symbol, one venue, one minute. Trades at :05.500 and :06.500 on 12:00.
TRADES = [
    # exchange, canonical_symbol, symbol, trade_id, trade_seq, price_e8, qty_e8, side,
    # exchange_ts, recv_ts_ns, conn_id, conn_msg_seq, seq_gap, missing_before
    ("kraken", "BTC/USD", "XBT/USD", "1", 1, 100_00000000, 1_00000000, "buy",
     "2026-09-01 12:00:05.500", 1, "c1", 1, False, 0),
    ("kraken", "BTC/USD", "XBT/USD", "9", 9, 101_00000000, 2_00000000, "sell",
     "2026-09-01 12:00:06.500", 2, "c1", 2, True, 7),
]
# The book at the END of each second: second 04 is the quote in force at :05.500.
BBO = [
    ("kraken", "BTC/USD", "2026-09-01 12:00:04", 90_00000000, 5_00000000, 92_00000000, 5_00000000),
    ("kraken", "BTC/USD", "2026-09-01 12:00:05", 190_00000000, 5_00000000, 192_00000000, 5_00000000),
    ("kraken", "BTC/USD", "2026-09-01 12:00:06", 290_00000000, 5_00000000, 292_00000000, 5_00000000),
]


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'; CREATE SCHEMA pinned")
    c.execute("""CREATE TABLE pinned.gold_trades (
        exchange TEXT, canonical_symbol TEXT, symbol TEXT, trade_id TEXT, trade_seq BIGINT,
        price_e8 BIGINT, qty_e8 BIGINT, side TEXT, exchange_ts TIMESTAMP, recv_ts_ns BIGINT,
        conn_id TEXT, conn_msg_seq BIGINT, seq_gap BOOLEAN, missing_before BIGINT)""")
    c.executemany("INSERT INTO pinned.gold_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", TRADES)
    c.execute("""CREATE TABLE pinned.gold_bbo_1s (
        exchange TEXT, canonical_symbol TEXT, second TIMESTAMP,
        bid_e8 BIGINT, bid_qty_e8 BIGINT, ask_e8 BIGINT, ask_qty_e8 BIGINT,
        mid DOUBLE, spread_bps DOUBLE)""")
    c.executemany(
        "INSERT INTO pinned.gold_bbo_1s VALUES (?,?,?,?,?,?,?, (?+?)/2.0/1e8, 0.0)",
        [row + (row[3], row[5]) for row in BBO],
    )
    # The dimension's first version starts AFTER the first trade: ADR-030 landed
    # mid-archive, so an early trade has no version and must not be dropped.
    c.execute("""CREATE TABLE pinned.gold_dim_instrument (
        exchange TEXT, canonical_symbol TEXT, symbol TEXT, tick_size DECIMAL(28,10),
        qty_increment DECIMAL(28,10), source TEXT, valid_from TIMESTAMP, valid_to TIMESTAMP,
        is_current BOOLEAN)""")
    c.execute("""INSERT INTO pinned.gold_dim_instrument VALUES
        ('kraken','BTC/USD','XBT/USD',0.1,0.00000001,'venue:kraken',
         TIMESTAMP '2026-09-01 12:00:06','9999-12-31 23:59:59',true)""")
    c.execute("""CREATE TABLE pinned.silver_book_kraken (
        canonical_symbol TEXT, checksum_ok BOOLEAN, recv_ts TIMESTAMP)""")
    c.execute("""INSERT INTO pinned.silver_book_kraken VALUES
        ('BTC/USD', false, TIMESTAMP '2026-09-01 12:00:05'),
        ('BTC/USD', true,  TIMESTAMP '2026-09-01 12:00:05'),
        ('BTC/USD', false, TIMESTAMP '2026-09-01 13:00:00')""")
    c.execute("""CREATE TABLE pinned.audit_checks (
        run_ts TIMESTAMP, job TEXT, check_name TEXT, scope TEXT, passed BOOLEAN,
        observed BIGINT, detail TEXT)""")
    c.execute("""INSERT INTO pinned.audit_checks VALUES
        (TIMESTAMP '2026-09-01 12:30:00','maintenance','sequence_gaps','lake.gold.trades',false,7,'x'),
        (TIMESTAMP '2026-09-01 12:31:00','maintenance','offset_continuity','t/0',true,0,'')""")
    return c


WINDOW = ("BTC/USD", "kraken", "2026-09-01 12:00:00", "2026-09-01 13:00:00")


def test_bbo_is_the_book_at_the_end_of_its_second(con):
    """A trade at :05.500 takes second :04's book, not :05's.

    Drop the `+ INTERVAL 1 SECOND` and this trade sees a quote from its own
    future (bid 190) — the 44.49% vs 53.41% trade-through difference.
    """
    rows = trades(con, *WINDOW).df()
    first = rows[rows.trade_id == "1"].iloc[0]
    assert first.bid == 90, f"expected second :04's bid, got {first.bid}"
    assert str(first.quote_second).endswith("12:00:04")
    assert first.ask == 92


def test_bid_ask_are_decimals_not_fixed_point(con):
    rows = trades(con, *WINDOW).df()
    assert float(rows.iloc[0].bid) * SCALE == 90 * SCALE


def test_trade_before_the_first_master_version_survives(con):
    """LEFT, not inner: the :05.500 trade predates the dimension's first row."""
    rows = trades(con, *WINDOW).df()
    assert len(rows) == 2, "a trade was dropped by one of the ASOF joins"
    early = rows[rows.trade_id == "1"].iloc[0]
    assert early.native_symbol is None
    late = rows[rows.trade_id == "9"].iloc[0]
    assert late.native_symbol == "XBT/USD"
    assert late.master_source == "venue:kraken"


def test_master_coverage_warning(con, capsys):
    trades(con, *WINDOW).df()
    assert "1 of 2 trades have no dim_instrument version" in capsys.readouterr().err


def test_completeness_counts_the_gap(con):
    row = completeness(con, *WINDOW).df().iloc[0]
    assert row.trades == 2
    assert row.seq_gaps == 1, "seq_gap not counted"
    assert row.ids_never_received == 7, "missing_before not summed"
    assert row.minutes_with_trades == 1
    assert row.minutes_expected == 60
    assert row.quote_coverage_pct == 100.0
    assert row.checksum_failed == 1, "only the in-window kraken checksum failure counts"
    assert row.audit_failures == 1


def test_completeness_quote_coverage_below_100(con):
    con.execute("DELETE FROM pinned.gold_bbo_1s")
    row = completeness(con, *WINDOW).df().iloc[0]
    assert row.quote_coverage_pct == 0.0


def test_a_symbol_with_a_quote_is_bound_not_interpolated(con):
    evil = "BTC/USD' OR '1'='1"
    assert len(trades(con, evil, "kraken", *WINDOW[2:]).df()) == 0
    assert completeness(con, evil, "kraken", *WINDOW[2:]).df().iloc[0].trades == 0


def test_bad_source_is_rejected(con):
    with pytest.raises(ValueError):
        trades(con, *WINDOW, source="pinned; DROP TABLE pinned.gold_trades")
