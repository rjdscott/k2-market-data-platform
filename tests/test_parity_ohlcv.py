"""scripts/parity_ohlcv.py (make parity-ohlcv, W1-18): every timeframe recomputed from synthetic
canonical trades against candles written by hand from the rules, on DuckDB tables standing for the
Iceberg ones; each class made to fail by its own plant. Instruments, prices and times are invented."""
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import parity_ohlcv as H  # noqa: E402
import parity_silver as P  # noqa: E402

TRADES = "market_analytics.canonical_trades__base.trades"
TABLES = {tf: f"market_analytics_build.candles__base.{tf}" for tf in H.WIDTHS}
S = {"tables": TABLES, "trades": TRADES, "warehouses": {"market_analytics": "market_analytics",
                                                       "market_analytics_build": "market_analytics",
                                                       "governance_build": "governance"}}
T0 = 1_767_600_000_000_000

# venue, partition, offset, ordinal, trade_ts, instrument, price, quantity, is_duplicate
ROWS = [
    ("kraken", 0, 1, 1, "2026-01-05 10:04:59.999999+00", "i-aaa", "10", "1", False),
    ("kraken", 0, 2, 1, "2026-01-05 10:05:00+00", "i-aaa", "12", "2", False),       # the same instant as the next:
    ("kraken", 0, 2, 2, "2026-01-05 10:05:00+00", "i-aaa", "11", "1", False),       # the key orders them
    ("kraken", 0, 3, 1, "2026-01-05 10:05:30+00", "i-aaa", "99", "9", True),        # a later delivery: not counted
    ("kraken", 0, 4, 1, "2026-01-05 23:59:59+00", "i-aaa", "1.0000000000005", "2", False),  # 13 decimals: quote at 12
    ("kraken", 1, 1, 1, "2026-01-06 00:00:00+00", "i-aaa", "5", "1", False),
    ("kraken", 2, 1, 1, "2026-01-05 10:00:00+00", "i-bbb", "100", "0.5", False),
]


def c_(start, end, o, h, lo, cl, vol, qv, n, first, last, inst="i-aaa"):
    return ("kraken", inst, start, end, o, h, lo, cl, vol, qv, n, first, last)


A1, A2, A2B, A5, A6 = ("2026-01-05 10:04:59.999999+00", "2026-01-05 10:05:00+00", "2026-01-05 10:05:00+00",
                       "2026-01-05 23:59:59+00", "2026-01-06 00:00:00+00")
P5 = "1.0000000000005"
EXPECTED = {
    "ohlcv_1m": [
        c_("2026-01-05 10:04:00+00", "2026-01-05 10:05:00+00", 10, 10, 10, 10, 1, 10, 1, A1, A1),
        c_("2026-01-05 10:05:00+00", "2026-01-05 10:06:00+00", 12, 12, 11, 11, 3, 35, 2, A2, A2B),
        c_("2026-01-05 23:59:00+00", "2026-01-06 00:00:00+00", P5, P5, P5, P5, 2, "2.000000000002", 1, A5, A5),
        c_("2026-01-06 00:00:00+00", "2026-01-06 00:01:00+00", 5, 5, 5, 5, 1, 5, 1, A6, A6),
        c_("2026-01-05 10:00:00+00", "2026-01-05 10:01:00+00", 100, 100, 100, 100, "0.5", 50, 1, "2026-01-05 10:00:00+00", "2026-01-05 10:00:00+00", "i-bbb"),
    ],
    "ohlcv_5m": [
        c_("2026-01-05 10:00:00+00", "2026-01-05 10:05:00+00", 10, 10, 10, 10, 1, 10, 1, A1, A1),
        c_("2026-01-05 10:05:00+00", "2026-01-05 10:10:00+00", 12, 12, 11, 11, 3, 35, 2, A2, A2B),
        c_("2026-01-05 23:55:00+00", "2026-01-06 00:00:00+00", P5, P5, P5, P5, 2, "2.000000000002", 1, A5, A5),
        c_("2026-01-06 00:00:00+00", "2026-01-06 00:05:00+00", 5, 5, 5, 5, 1, 5, 1, A6, A6),
        c_("2026-01-05 10:00:00+00", "2026-01-05 10:05:00+00", 100, 100, 100, 100, "0.5", 50, 1, "2026-01-05 10:00:00+00", "2026-01-05 10:00:00+00", "i-bbb"),
    ],
    "ohlcv_1h": [
        c_("2026-01-05 10:00:00+00", "2026-01-05 11:00:00+00", 10, 12, 10, 11, 4, 45, 3, A1, A2B),
        c_("2026-01-05 23:00:00+00", "2026-01-06 00:00:00+00", P5, P5, P5, P5, 2, "2.000000000002", 1, A5, A5),
        c_("2026-01-06 00:00:00+00", "2026-01-06 01:00:00+00", 5, 5, 5, 5, 1, 5, 1, A6, A6),
        c_("2026-01-05 10:00:00+00", "2026-01-05 11:00:00+00", 100, 100, 100, 100, "0.5", 50, 1, "2026-01-05 10:00:00+00", "2026-01-05 10:00:00+00", "i-bbb"),
    ],
    "ohlcv_1d": [
        c_("2026-01-05 00:00:00+00", "2026-01-06 00:00:00+00", 10, 12, P5, P5, 6, "47.000000000002", 4, A1, A5),
        c_("2026-01-06 00:00:00+00", "2026-01-07 00:00:00+00", 5, 5, 5, 5, 1, 5, 1, A6, A6),
        c_("2026-01-05 00:00:00+00", "2026-01-06 00:00:00+00", 100, 100, 100, 100, "0.5", 50, 1, "2026-01-05 10:00:00+00", "2026-01-05 10:00:00+00", "i-bbb"),
    ],
}
CANDLE_COLS = ("venue VARCHAR, instrument_id VARCHAR, window_start TIMESTAMPTZ, window_end TIMESTAMPTZ, open DECIMAL(38,18), "
               "high DECIMAL(38,18), low DECIMAL(38,18), close DECIMAL(38,18), volume DECIMAL(38,18), "
               "quote_volume DECIMAL(38,18), trade_count BIGINT, first_trade_at TIMESTAMPTZ, last_trade_at TIMESTAMPTZ, "
               "through_load_seq BIGINT")


def ts(us):
    return datetime.fromtimestamp(us / 1e6, timezone.utc).isoformat()


def records(snapshots, canonical=70, other=None):
    """Run ca/1 built every timeframe at canonical@`canonical`; `other` gives one timeframe another input."""
    out = []
    for tf, snap in snapshots.items():
        read = (other or {}).get(tf, canonical)
        inputs = [{"contractId": H.INPUT, "relation": TRADES, "snapshot": read, "state": "active", "lagMinutes": 0.0}]
        out += [("candles/ca-1", "started", TABLES[tf], None, ts(T0), ts(T0 + 1), json.dumps(inputs)),
                ("candles/ca-1", "audited", TABLES[tf], snap, ts(T0), ts(T0 + 9), None)]
    return out


def lake(expected=EXPECTED, runs=None):
    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'")
    for w in ("market_analytics", "governance"):
        c.execute(f"ATTACH ':memory:' AS {w}")
    for ns in ("market_analytics.canonical_trades__base", "market_analytics.candles__base", "governance.run_records"):
        c.execute(f"CREATE SCHEMA {ns}")
    c.execute('CREATE TABLE "market_analytics"."canonical_trades__base"."trades@70" (venue VARCHAR, kafka_partition INTEGER, '
              "kafka_offset BIGINT, trade_ordinal INTEGER, trade_ts TIMESTAMPTZ, instrument_id VARCHAR, price DECIMAL(38,18), "
              "quantity DECIMAL(38,18), is_duplicate BOOLEAN, load_seq BIGINT)")
    c.executemany('INSERT INTO "market_analytics"."canonical_trades__base"."trades@70" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  [(*r, i) for i, r in enumerate(ROWS, start=1)])
    for i, (tf, rows) in enumerate(sorted(expected.items())):
        name = f'"market_analytics"."candles__base"."{tf}@{80 + i}"'
        c.execute(f"CREATE TABLE {name} ({CANDLE_COLS})")
        c.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * 14)})", [(*r, 7) for r in rows])
    c.execute('CREATE TABLE "governance"."run_records"."run_record" (run_id VARCHAR, event VARCHAR, table_name VARCHAR, '
              "snapshot_after BIGINT, started_at TIMESTAMPTZ, recorded_at TIMESTAMPTZ, inputs VARCHAR)")
    snaps = {tf: 80 + i for i, tf in enumerate(sorted(expected))}
    c.executemany('INSERT INTO "governance"."run_records"."run_record" VALUES (?, ?, ?, ?, ?, ?, ?)',
                  runs if runs is not None else records(snaps))
    return c


@pytest.fixture(autouse=True)
def at_snapshot(monkeypatch):
    monkeypatch.setattr(H, "at", lambda rel, snap: f'{rel[:-1]}@{int(snap)}"')


def parity(c, capsys):
    code = H.run(S, c, None)
    return code, capsys.readouterr().out


def failing(out):
    return sorted(line.split()[1] for line in out.splitlines() if line.startswith("FAIL"))


def test_candles_built_by_the_rules_pass_every_class(capsys):
    code, out = parity(lake(), capsys)
    assert code == 0, out
    assert "pin run candles/ca-1" in out and "trades@70" in out and "ohlcv_1m@82" in out and "ohlcv_1d@80" in out
    assert "7 canonical row(s), 1 flagged is_duplicate" in out and "every class empty over 4 timeframes" in out


def planted(tf, sql):
    c = lake()
    c.execute(sql.format(t=f'"market_analytics"."candles__base"."{tf}@{80 + sorted(H.WIDTHS).index(tf)}"'))
    return c


@pytest.mark.parametrize("tf, sql, classes", [
    ("ohlcv_5m", "UPDATE {t} SET open = 11 WHERE window_start = '2026-01-05 10:05:00+00'", ["ohlcv_5m.open"]),
    ("ohlcv_1h", "UPDATE {t} SET close = 12 WHERE instrument_id = 'i-aaa' AND window_start = '2026-01-05 10:00:00+00'", ["ohlcv_1h.close"]),
    ("ohlcv_1m", "UPDATE {t} SET quote_volume = 2.000000000000 WHERE window_start = '2026-01-05 23:59:00+00'", ["ohlcv_1m.quote_volume"]),
    ("ohlcv_1d", "UPDATE {t} SET trade_count = 5, volume = 15 WHERE instrument_id = 'i-aaa' AND window_start = '2026-01-05 00:00:00+00'",
     ["ohlcv_1d.trade_count", "ohlcv_1d.volume"]),
    ("ohlcv_1m", "UPDATE {t} SET last_trade_at = '2026-01-05 10:05:30+00' WHERE window_start = '2026-01-05 10:05:00+00'", ["ohlcv_1m.last_trade_at"]),
    ("ohlcv_1m", "DELETE FROM {t} WHERE instrument_id = 'i-bbb'", ["ohlcv_1m.missing"]),
    ("ohlcv_5m", "INSERT INTO {t} SELECT venue, instrument_id, window_start + INTERVAL 1 HOUR, window_end + INTERVAL 1 HOUR, open, high, "
                 "low, close, volume, quote_volume, trade_count, first_trade_at, last_trade_at, through_load_seq FROM {t} "
                 "WHERE instrument_id = 'i-bbb'", ["ohlcv_5m.extra"]),
    ("ohlcv_1h", "INSERT INTO {t} SELECT * FROM {t} WHERE instrument_id = 'i-bbb'", ["ohlcv_1h.duplicate_key_ohlcv_1h"]),
    ("ohlcv_1h", "UPDATE {t} SET through_load_seq = 99", []),  # bookkeeping, not a candle value
])
def test_each_difference_fails_its_own_class(capsys, tf, sql, classes):
    code, out = parity(planted(tf, sql), capsys)
    assert (code, failing(out)) == (1 if classes else 0, classes), out


def test_timeframes_that_read_different_inputs_are_refused():
    snaps = {tf: 80 + i for i, tf in enumerate(sorted(H.WIDTHS))}
    with pytest.raises(P.Refused, match="read different snapshots"):
        H.run(S, lake(runs=records(snaps, other={"ohlcv_1h": 69})), None)


def test_a_run_missing_a_timeframe_is_refused():
    snaps = {tf: 80 + i for i, tf in enumerate(sorted(H.WIDTHS)) if tf != "ohlcv_5m"}
    with pytest.raises(P.Refused, match="no audited run record of market_analytics_build.candles__base.ohlcv_5m for run candles/ca-1"):
        H.run(S, lake(runs=records(snaps)), None)


def test_the_quote_volume_rounds_price_at_twelve_decimals_half_up():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT 'kraken' AS venue, 'i' AS instrument_id, TIMESTAMPTZ '2026-01-05 10:00:00+00' AS trade_ts, "
              "0 AS kafka_partition, 0 AS kafka_offset, 1 AS trade_ordinal, x::DECIMAL(38,18) AS price, "
              "q::DECIMAL(38,18) AS quantity, false AS is_duplicate FROM (VALUES ('1.0000000000005', '2'), "
              "('1.0000000000004', '0.00000000005')) AS v(x, q)")
    H.candles(c, "t", "r", "1 minute")
    # 1.000000000001 * 2 + 1.000000000000 * 0.0000000001: the price at 12 decimals, the quantity at 10, each half up
    assert c.execute("SELECT quote_volume::VARCHAR FROM r").fetchone() == ("2.000000000102000000",)
    assert Decimal(c.execute("SELECT volume::VARCHAR FROM r").fetchone()[0]) == Decimal("2.00000000005")
