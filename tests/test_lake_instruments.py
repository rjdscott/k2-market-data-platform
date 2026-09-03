"""instruments.py against the real registry: every venue maps, unknowns raise."""

import sys
import types
from pathlib import Path

import instruments
import pytest

REGISTRY = Path(__file__).parent.parent / "config" / "instruments.yaml"


def test_every_venue_maps_and_canonical_is_base_slash_quote():
    reg = instruments.load(REGISTRY)
    assert set(reg) == {"binance", "kraken", "coinbase"}
    assert instruments.canonical(reg, "binance", "BTCUSDT") == "BTC/USDT"
    assert instruments.canonical(reg, "kraken", "BTC/USD") == "BTC/USD"
    assert instruments.canonical(reg, "coinbase", "BTC-USD") == "BTC/USD"
    for venue, natives in reg.items():
        for native, canon in natives.items():
            base, quote = canon.split("/")
            assert base.isupper() and quote.isupper(), (venue, native, canon)


def test_unknown_native_raises_rather_than_guessing():
    reg = instruments.load(REGISTRY)
    with pytest.raises(instruments.UnknownInstrument):
        instruments.canonical(reg, "kraken", "XDG/USD")
    with pytest.raises(instruments.UnknownInstrument):
        instruments.canonical(reg, "okx", "BTC-USDT")


# ─────────────────────────────────────────────────────────────────────────────
# gold.py's book depth must cover the same venues
#
# `_VENUE_DEPTH` is a second registry keyed on the same venue names, with no
# fallback: a venue added to instruments.yaml and not there failed the dimension
# build with a bare `KeyError` from inside a Spark job — hours after the change,
# with nothing in the trace naming the file to edit. This is the check that would
# have caught it at `make test-python` instead, and
# docs/architecture/06-capture-venues.md no longer says the lake needs no change.
#
# gold.py imports pyspark, so it comes in through the same stub
# tests/test_lake_bronze.py uses; venue_depth() itself touches nothing Spark-side.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def gold():
    for name in ("pyspark", "pyspark.sql", "pyspark.sql.avro", "pyspark.sql.avro.functions",
                 "pyspark.sql.window", "catalog"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sql = sys.modules["pyspark.sql"]
    for attr in ("DataFrame", "Row", "Window", "functions", "SparkSession"):
        setattr(sql, attr, object())
    sys.modules["pyspark.sql.avro.functions"].from_avro = object()
    for attr in ("UnresolvableSchema", "added_records", "fetch_schema", "snapshot_history",
                 "write_audit_rows"):
        setattr(sys.modules["catalog"], attr, object())
    import gold as module

    return module


def test_every_registered_venue_has_a_book_depth(gold):
    """The registry decides which venues exist; gold.py must know a depth for each."""
    for venue in instruments.load(REGISTRY):
        assert isinstance(gold.venue_depth(venue), int)


def test_an_unregistered_venue_fails_by_naming_the_file_to_edit(gold):
    """A bare KeyError from a Spark job is a trace, not a fix."""
    with pytest.raises(KeyError) as exc:
        gold.venue_depth("bitstamp")
    message = str(exc.value)
    assert "docker/lake/gold.py" in message
    assert "_VENUE_DEPTH" in message
    assert "adding-new-exchanges.md" in message
