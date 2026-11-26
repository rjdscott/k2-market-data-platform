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


# ─────────────────────────────────────────────────────────────────────────────
# book_capture (ADR-050): the capture's rules, read the lake's way
# ─────────────────────────────────────────────────────────────────────────────


def test_the_shipped_switch_names_every_venue():
    switch = instruments.book_capture(REGISTRY)
    assert set(switch) == set(instruments.load(REGISTRY))
    assert all(isinstance(v, bool) for v in switch.values())


@pytest.mark.parametrize("section, why", [
    ("", "required"),
    ("book_capture: {binance: false, kraken: false}\n", "registry's venues"),
    ("book_capture: {binance: false, kraken: false, coinbase: false, okx: false}\n", "registry's venues"),
    # PyYAML reads `on` as True; the capture's serde_yaml reads it as a string and refuses to start.
    ("book_capture: {binance: on, kraken: false, coinbase: false}\n", "not true or false"),
    ("book_capture: {binance: 'false', kraken: false, coinbase: false}\n", "not true or false"),
])
def test_a_wrong_switch_raises_rather_than_defaulting(tmp_path, section, why):
    text = REGISTRY.read_text()
    start = text.index("\nbook_capture:")
    end = text.index("\ninstruments:")
    path = tmp_path / "instruments.yaml"
    path.write_text(text[:start + 1] + section + text[end:])
    with pytest.raises(ValueError, match=why):
        instruments.book_capture(path)


def test_a_switch_is_read_per_venue(tmp_path):
    text = REGISTRY.read_text()
    start, end = text.index("\nbook_capture:"), text.index("\ninstruments:")
    path = tmp_path / "instruments.yaml"
    path.write_text(text[:start + 1] + "book_capture:\n  binance: true\n  kraken: false\n  coinbase: True\n" + text[end:])
    assert instruments.book_capture(path) == {"binance": True, "kraken": False, "coinbase": True}
