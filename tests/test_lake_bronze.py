"""
bronze.py's pure parts: routing, drift detection, and the claim that the
declared key sets match frames the venues actually sent.

The fixtures under tests/fixtures/bronze/ are frames captured on 2026-08-27
(docker/lake/README.md, "Bronze per venue"), trimmed to a few levels/trades but
with every key the venue sent. A venue adding a field is caught here the day
the fixture is refreshed, and nightly by maintenance.py's schema-drift audit
until then.

bronze.py imports pyspark at module level, so the tests import the pure pieces
through a stub: the dataclass, the tables, route() and drift() do not touch
Spark, and the stub keeps this runnable under the root `make test-python`.
"""

import json
import sys
import types
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "bronze"


@pytest.fixture(scope="module")
def bronze():
    for name in ("pyspark", "pyspark.sql", "pyspark.sql.avro", "pyspark.sql.avro.functions", "catalog"):
        mod = types.ModuleType(name)
        sys.modules.setdefault(name, mod)
    sql = sys.modules["pyspark.sql"]
    for attr in ("DataFrame", "Row", "functions", "SparkSession"):
        setattr(sql, attr, object())
    sys.modules["pyspark.sql.avro.functions"].from_avro = object()
    for attr in ("UnresolvableSchema", "added_records", "fetch_schema", "snapshot_history", "write_audit_rows"):
        setattr(sys.modules["catalog"], attr, object())
    import bronze as module

    return module


def _keys_at(doc, path):
    """`json_object_keys(get_json_object(doc, path))` in pure Python, for the paths bronze.py uses."""
    node = doc
    for step in path.split(".")[1:]:
        name, _, idx = step.partition("[")
        node = node[name]
        if idx:
            node = node[int(idx.rstrip("]"))]
    return set(node.keys())


def test_route_covers_every_declared_pair_and_nothing_else(bronze):
    assert bronze.route("binance", "trade").name == "binance_trade"
    assert bronze.route("coinbase", "l2_data").name == "coinbase_level2"
    assert bronze.route("kraken", "heartbeat") is None
    assert bronze.route("coinbase", "trade") is None
    names = [t.name for t in bronze.VENUE_TABLES]
    assert len(names) == len(set(names)) == 6


def test_drift_reports_only_undeclared_keys(bronze):
    expected = {"$": {"a", "b"}, "$.a": {"x"}}
    assert bronze.drift({"$": {"a", "b"}, "$.a": {"x"}}, expected) == {}
    assert bronze.drift({"$": {"a"}, "$.a": {"x"}}, expected) == {}  # a key the venue stopped sending is not drift
    assert bronze.drift({"$": {"a", "b", "c"}, "$.a": {"x", "y"}}, expected) == {"$": ["c"], "$.a": ["y"]}


@pytest.mark.parametrize("name", ["binance_trade", "binance_depth20", "kraken_trade", "kraken_book", "coinbase_market_trades", "coinbase_level2"])
def test_declared_keys_match_a_captured_frame(bronze, name):
    t = next(t for t in bronze.VENUE_TABLES if t.name == name)
    doc = json.loads((FIXTURES / f"{name}.json").read_text())
    seen = {path: _keys_at(doc, path) for path in t.keys}
    assert bronze.drift(seen, t.keys) == {}, "the venue sends keys the table does not declare"
    for path, keys in t.keys.items():
        assert seen[path] == keys, f"{path}: fixture {seen[path]} != declared {keys}"


@pytest.mark.parametrize("name", ["binance_trade", "binance_depth20", "kraken_trade", "kraken_book", "coinbase_market_trades", "coinbase_level2"])
def test_every_declared_key_is_a_column_in_the_schema(bronze, name):
    t = next(t for t in bronze.VENUE_TABLES if t.name == name)
    for keys in t.keys.values():
        for key in keys:
            assert f"{key}:" in t.schema or t.schema.startswith(f"{key} ") or f", {key} " in t.schema, key
