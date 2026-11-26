"""silver.py's declarative parts: one spec per venue, every venue has a side rule,
the identifier is the bronze lineage plus the position in the frame, and each
spec's first venue column is the native symbol (project() reads it to resolve
the registry, so the order is load-bearing)."""

import sys
import types

import pytest


@pytest.fixture(scope="module")
def silver():
    for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions", "catalog", "instruments"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sql = sys.modules["pyspark.sql"]
    for attr in ("DataFrame", "Window", "SparkSession", "functions"):
        setattr(sql, attr, object())
    for attr in ("added_records", "snapshot_history"):
        setattr(sys.modules["catalog"], attr, object())
    import silver as module

    return module


def test_one_spec_per_venue_with_a_side_rule(silver):
    venues = [t.exchange for t in silver.TRADES]
    assert venues == ["binance", "kraken", "coinbase"]
    assert set(silver.SIDE_SQL) == set(venues)
    assert silver.IDENTIFIER_FIELDS == ("src_topic", "src_partition", "src_offset", "src_index")


@pytest.mark.parametrize("exchange", ["binance", "kraken", "coinbase"])
def test_spec_shape(silver, exchange):
    t = next(t for t in silver.TRADES if t.exchange == exchange)
    assert t.venue_columns[0].endswith(" AS symbol"), "project() resolves the registry from the first column"
    assert "src_index" in t.explode, "the position within the frame is the last identifier field"
    names = [c.rsplit(" AS ", 1)[-1] if " AS " in c else c for c in t.venue_columns]
    for required in ("symbol", "trade_id", "trade_seq", "price", "qty", "side", "side_native", "exchange_ts"):
        assert required in names, (exchange, required)
    assert t.table == f"lake.silver.trades_{exchange}"
