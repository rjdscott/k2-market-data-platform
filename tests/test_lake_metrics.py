"""
metrics.py's table enumeration.

The exporter used to carry a hand-maintained dict of 15 tables while the lake
held 26. The eleven it omitted had no freshness gauge, no row count and so no
alert that could ever fire on them — which is indistinguishable from healthy.
These tests pin the replacement: the catalog's own listing is the list.

`prometheus_client` is not a test dependency (metrics.py runs in the Spark image,
the tests run under bare `uv`), so it comes in as a stub the same way
tests/test_lake_bronze.py stubs pyspark. The stub is enough because nothing here
reads a gauge back — `list_tables` is pure over the catalog's HTTP responses.
"""

import sys
import types

import pytest

_LISTING = {
    "namespaces": [["raw"], ["bronze"], ["silver"], ["gold"], ["audit"]],
    "raw": ["messages"],
    "bronze": ["binance_trade", "kraken_instrument"],
    "silver": ["trades_binance", "book_binance"],
    "gold": ["trades", "bbo_1s", "dim_venue"],
    "audit": ["checks"],
}


@pytest.fixture(scope="module")
def metrics():
    if "prometheus_client" not in sys.modules:
        stub = types.ModuleType("prometheus_client")

        class Gauge:
            def __init__(self, *a, **kw):
                pass

            def labels(self, **kw):
                return self

            def set(self, value):
                pass

        stub.Gauge = Gauge
        stub.start_http_server = lambda *a, **kw: None
        sys.modules["prometheus_client"] = stub
    import metrics as module

    return module


@pytest.fixture
def fake_catalog(metrics, monkeypatch):
    """`_get` answering the two REST shapes Lakekeeper serves, and recording calls."""
    calls = []

    def _get(url):
        calls.append(url)
        if url.endswith("/namespaces"):
            return {"namespaces": _LISTING["namespaces"]}
        namespace = url.rsplit("/", 2)[-2]
        return {
            "identifiers": [{"namespace": [namespace], "name": t} for t in _LISTING[namespace]]
        }

    monkeypatch.setattr(metrics, "_get", _get)
    return calls


def test_every_table_in_the_catalog_is_enumerated(metrics, fake_catalog):
    assert metrics.list_tables("PREFIX") == [
        ("audit", "checks"),
        ("bronze", "binance_trade"),
        ("bronze", "kraken_instrument"),
        ("gold", "bbo_1s"),
        ("gold", "dim_venue"),
        ("gold", "trades"),
        ("raw", "messages"),
        ("silver", "book_binance"),
        ("silver", "trades_binance"),
    ]


def test_a_table_nobody_listed_here_is_picked_up_anyway(metrics, fake_catalog):
    """The whole point of the change: a new table needs no edit to this file."""
    _LISTING["gold"].append("ohlcv_1d")
    try:
        assert ("gold", "ohlcv_1d") in metrics.list_tables("PREFIX")
    finally:
        _LISTING["gold"].pop()


def test_the_labels_refresh_uses_are_namespace_dot_table(metrics, fake_catalog):
    """`k2_lake_*{table="gold.bbo_1s"}` — the shape every alert expression matches."""
    labels = [f"{ns}.{t}" for ns, t in metrics.list_tables("PREFIX")]
    assert "gold.bbo_1s" in labels
    assert metrics.INGEST_TABLE in labels
    assert metrics.CHECKS_TABLE in labels


def test_the_listing_is_rooted_at_the_warehouse_prefix(metrics, fake_catalog):
    """Using the warehouse NAME answers 400 WarehouseIdIsNotUUID (catalog_prefix's docstring)."""
    metrics.list_tables("PREFIX")
    assert fake_catalog[0].endswith("/v1/PREFIX/namespaces")
    assert all("/v1/PREFIX/namespaces" in url for url in fake_catalog)
