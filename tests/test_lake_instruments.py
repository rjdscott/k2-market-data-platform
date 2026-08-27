"""instruments.py against the real registry: every venue maps, unknowns raise."""

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
