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


class Recorder:
    def __init__(self):
        self.value = None

    def labels(self, **kw):
        return self

    def set(self, value):
        self.value = value


def test_each_ingest_finding_sets_only_its_own_gauge(metrics, monkeypatch):
    """A batch-audit commit on audit.checks moves k2_lake_batch_audit_failures_total and
    nothing else; read off the wrong key, it would page as an offset gap or a schema id."""
    now = 1_800_000_000.0
    snapshot = {
        "snapshot-id": 1,
        "timestamp-ms": int(now * 1000) - 60_000,
        "summary": {"operation": "append", metrics.JOB: metrics.JOB_INGEST,
                    # Different values, so a gauge read off the wrong key cannot pass.
                    metrics.AUDIT_FAILURES: "5", metrics.BATCH_AUDIT_FAILURES: "2"},
    }
    monkeypatch.setattr(metrics, "list_tables", lambda prefix: [("audit", "checks")])
    monkeypatch.setattr(metrics, "load_metadata", lambda prefix, ns, table: {
        "current-snapshot-id": 1, "snapshots": [snapshot]})
    monkeypatch.setattr(metrics, "_refresh_disk", lambda: None)
    gauges = {}
    for name in ("batch_audit_failures", "offset_gaps", "unresolvable_schema_ids", "audit_failures"):
        gauges[name] = Recorder()
        monkeypatch.setattr(metrics, name, gauges[name])

    assert metrics.refresh("PREFIX", now) == 0
    assert gauges["batch_audit_failures"].value == 2.0
    assert gauges["offset_gaps"].value == 0.0
    assert gauges["unresolvable_schema_ids"].value == 0.0
    assert gauges["audit_failures"].value == 0.0  # the nightly audit's gauge, untouched by the ingest

    # A pulse: 15 minutes after the failing batch, with none since, it reads 0.
    assert metrics.refresh("PREFIX", now + metrics.INGEST_SUMMARY_MAX_AGE) == 0
    assert gauges["batch_audit_failures"].value == 0.0


def test_every_finding_key_the_exporter_reads_has_a_writer():
    """A gauge read off a commit property nobody sets is an alert that cannot fire.

    LakeUnresolvableSchemaId was one from its first day until 2026-11-25: bronze.py
    wrote `k2.bronze-findings` and the exporter read `k2.unresolvable-schema-ids`.
    """
    import pathlib
    import re

    lake = pathlib.Path(__file__).resolve().parents[1] / "docker" / "lake"
    writers = "\n".join(p.read_text() for p in lake.glob("*.py") if p.name not in ("metrics.py", "offsets.py"))
    for name in ("UNRESOLVABLE_IDS", "OFFSET_GAPS", "BATCH_AUDIT_FAILURES", "AUDIT_FAILURES"):
        assert re.search(rf"\bO\.{name}\b", writers), f"no lake job writes O.{name}"


def test_a_branch_commit_does_not_set_a_gauge(metrics, monkeypatch):
    """`main` is at 1. 2 is a newer commit on a branch, 3 one on a branch since dropped:
    `metadata["snapshots"]` lists all three, and the newest by timestamp-ms is not
    a snapshot any reader of the table can see."""
    now = 1_800_000_000.0

    def snap(sid, parent, ago_ms, failures):
        return {"snapshot-id": sid, "parent-snapshot-id": parent, "timestamp-ms": int(now * 1000) - ago_ms,
                "summary": {"operation": "append", metrics.JOB: metrics.JOB_INGEST,
                            metrics.BATCH_AUDIT_FAILURES: failures}}

    monkeypatch.setattr(metrics, "list_tables", lambda prefix: [("audit", "checks")])
    monkeypatch.setattr(metrics, "load_metadata", lambda prefix, ns, table: {
        "current-snapshot-id": 1,
        "snapshots": [snap(1, None, 60_000, "2"), snap(2, 1, 30_000, "7"), snap(3, None, 10_000, "9")]})
    monkeypatch.setattr(metrics, "_refresh_disk", lambda: None)
    for name in ("offset_gaps", "unresolvable_schema_ids", "audit_failures"):
        monkeypatch.setattr(metrics, name, Recorder())
    gauge = Recorder()
    monkeypatch.setattr(metrics, "batch_audit_failures", gauge)

    assert metrics.refresh("PREFIX", now) == 0
    assert gauge.value == 2.0
