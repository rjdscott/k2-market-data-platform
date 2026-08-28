"""
docker/lake/bars.py: the threshold loader and the Python reference — the
arbiter the SQL in two engines is compared against (scripts/parity_bars.py).
"""

from datetime import datetime, timezone
from pathlib import Path

import bars
import pytest
import yaml

REPO = Path(__file__).parent.parent
E8 = 100_000_000


def trade(i, price, qty, ts="2026-08-27T10:00:00", exchange="kraken", symbol="BTC/USD"):
    return {
        "exchange": exchange,
        "canonical_symbol": symbol,
        "exchange_ts": datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        "recv_ts_ns": i,
        "trade_seq": i,
        "price_e8": int(round(price * E8)),
        "qty_e8": int(round(qty * E8)),
    }


def test_repo_bars_yaml_covers_every_registry_symbol():
    reg = yaml.safe_load((REPO / "config" / "instruments.yaml").read_text())["instruments"]
    canon = {e["canonical"] for entries in reg.values() for e in entries}
    rows = bars.load(REPO / "config" / "bars.yaml", canonical_symbols=canon)
    assert len(rows) == len(canon) * len(bars.KINDS)


def test_missing_symbol_is_an_error(tmp_path):
    p = tmp_path / "bars.yaml"
    p.write_text("version: 1\nthresholds:\n  BTC/USD: {dollar: 1, volume: 1, tick: 1}\n")
    with pytest.raises(ValueError, match="ETH/USD"):
        bars.load(p, canonical_symbols={"BTC/USD", "ETH/USD"})


def test_tick_bars_are_cumulative_buckets():
    # 7 trades at threshold 3: bars of 3, 3, 1 — the last one open-ended.
    t = [trade(i, 100 + i, 1) for i in range(7)]
    out = bars.reference(t, "tick", 3)
    assert [b["trade_count"] for b in out] == [3, 3, 1]
    assert [b["bar_seq"] for b in out] == [0, 1, 2]
    assert out[0]["open_e8"] == 100 * E8 and out[0]["close_e8"] == 102 * E8
    assert out[1]["high_e8"] == 105 * E8 and out[1]["low_e8"] == 103 * E8


def test_volume_bucket_is_decided_by_the_total_before_the_trade():
    # Threshold 2.0 base units. Cumulative before: 0, 1.5, 3.0, 3.5 -> buckets 0, 0, 1, 1.
    # The 1.5-unit trade lands in bar 0 even though it carries the total to 3.0:
    # the bar is a bucket on the cumulative grid, not a reset after crossing.
    t = [trade(0, 10, 1.5), trade(1, 11, 1.5), trade(2, 12, 0.5), trade(3, 13, 2.0)]
    out = bars.reference(t, "volume", 2.0)
    assert [(b["bar_seq"], b["trade_count"], b["volume_e8"]) for b in out] == [(0, 2, 3 * E8), (1, 2, int(2.5 * E8))]


def test_dollar_bars_use_exact_notional():
    # 3 trades of 0.1 BTC at 50,000 = 5,000 USD each; threshold 10,000 -> [2, 1].
    t = [trade(i, 50_000, 0.1) for i in range(3)]
    out = bars.reference(t, "dollar", 10_000)
    assert [b["trade_count"] for b in out] == [2, 1]
    assert out[0]["quote_volume_e16"] == 2 * 50_000 * E8 * int(0.1 * E8)
    assert out[0]["quote_volume_e8"] == 2 * 5_000 * E8


def test_bars_restart_at_the_utc_day_boundary():
    t = [trade(0, 1, 1, "2026-08-27T23:59:59"), trade(1, 1, 1, "2026-08-28T00:00:00")]
    out = bars.reference(t, "tick", 10)
    assert [(str(b["day"]), b["bar_seq"], b["trade_count"]) for b in out] == [("2026-08-27", 0, 1), ("2026-08-28", 0, 1)]


def test_sql_is_generated_for_both_dialects():
    spark = bars.bars_sql("lake.gold.trades", "__th", "spark")
    duck = bars.bars_sql("lake.gold.trades", "__th", "duckdb")
    assert " DIV " in spark and "struct(" in spark and "DECIMAL(38,0)" in spark
    assert " // " in duck and "arg_min(" in duck and "HUGEINT" in duck
    assert "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW" in spark
