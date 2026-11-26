"""
Unit tests for the v2/v3 trade parity comparator, scripts/parity/compare_trades.py.

Pure python: hand-built records, no Kafka, no schema registry, no running stack.
Everything Kafka-shaped in that script is a thin shell around the functions
tested here, and those functions are what the retirement evidence rests on — a
tolerance that is off by one, or a Decimal that goes through a float, produces a
green table that means nothing.

What each test is actually guarding:

  - the tolerance boundary, both sides of it. The number in the PR is only
    trustworthy if `<=` is not `<`.
  - the Kraken path. Its whole point is that it does NOT join on trade_id; a
    regression that quietly reinstated the ID join would report a total mismatch
    on real data and a tester might "fix" it by loosening the tolerance.
  - a px/qty/side mismatch forcing FAIL regardless of counts. This is the finding
    the tolerance must never absorb — `side` included, because an inverted
    aggressor is invisible in every other column.
  - the string -> fixed-point conversion, exactly, on a value with trailing
    zeros. float('128.14') * 1e8 == 12813999999.999998, so a float
    implementation would fail this test — which is why it is here.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "parity"))

from compare_trades import (  # noqa: E402
    MAX_TS_DELTA_US,
    MIN_WINDOW_END_AGE_S,
    TRUNCATION_NOTE,
    Norm,
    compare,
    normalise_v2,
    normalise_v3,
    parse_args,
    render_markdown,
    to_fixed_1e8,
    to_micros,
    to_side,
    tolerance_for,
)


def trade(
    symbol="BTC/USD",
    tid="1",
    price=100_00000000,
    qty=1_00000000,
    side="buy",
    ts_us=1_700_000_000_000_000,
):
    return Norm(
        canonical_symbol=symbol,
        trade_id=tid,
        price=price,
        qty=qty,
        side=side,
        exchange_ts_us=ts_us,
    )


def run(v2, v3, exchange="binance"):
    """compare() for one symbol, returning that symbol's stats."""
    stats = compare(v2, v3, exchange=exchange)
    assert len(stats) == 1, "these fixtures are single-symbol on purpose"
    return stats[0]


# ─────────────────────────────────────────────────────────────────────────────
# Fixed-point conversion
# ─────────────────────────────────────────────────────────────────────────────


def test_v2_price_string_converts_exactly():
    """The headline case: trailing zeros, and a value a float cannot round-trip."""
    assert to_fixed_1e8("128.14000000") == 12814000000


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", 0),
        ("1", 100_000_000),
        ("0.00000001", 1),
        ("0.015", 1_500_000),
        ("42150.50", 4_215_050_000_000),
        ("-0.5", -50_000_000),
    ],
)
def test_fixed_point_table(text, expected):
    assert to_fixed_1e8(text) == expected


def test_more_than_eight_decimals_is_an_error_not_a_rounding():
    """v3 rejects and counts >8dp at capture; silently rounding here would hide it."""
    with pytest.raises(ValueError, match="more than 8 decimal places"):
        to_fixed_1e8("1.123456789")


def test_non_numeric_price_is_rejected():
    with pytest.raises(ValueError, match="not a decimal number"):
        to_fixed_1e8("nope")


# ─────────────────────────────────────────────────────────────────────────────
# Timestamps
# ─────────────────────────────────────────────────────────────────────────────


def test_v2_millis_become_micros():
    assert to_micros(1_700_000_000_123, unit="ms") == 1_700_000_000_123_000


def test_v3_datetime_becomes_micros():
    """fastavro decodes timestamp-micros to a tz-aware datetime, not an int."""
    dt = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    assert to_micros(dt, unit="us") == 1_700_000_000_123_456
    # and the int path agrees with the datetime path for the same instant
    assert to_micros(1_700_000_000_123_456, unit="us") == to_micros(dt, unit="us")


def test_naive_datetime_is_read_as_utc():
    """
    Pinned to an absolute value, not to `f(naive) == f(aware)`: that form holds
    for any implementation that ignores tzinfo, including a constant.
    """
    naive = datetime(2023, 11, 14, 22, 13, 20)
    assert to_micros(naive, unit="us") == 1_700_000_000_000_000
    assert to_micros(naive.replace(tzinfo=timezone.utc), unit="us") == 1_700_000_000_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Record normalisation
# ─────────────────────────────────────────────────────────────────────────────


def test_normalise_v2_record():
    n = normalise_v2(
        {
            "canonical_symbol": "BTC/USDT",
            "trade_id": "5551212",
            "price": "128.14000000",
            "quantity": "0.01500000",
            "quote_volume": "1.9221",
            "side": "BUY",
            "exchange_timestamp": 1_700_000_000_123,
        }
    )
    assert n == Norm("BTC/USDT", "5551212", 12814000000, 1_500_000, "buy", 1_700_000_000_123_000)


def test_normalise_v3_record():
    n = normalise_v3(
        {
            "canonical_symbol": "BTC/USDT",
            "trade_id": 5551212,  # venues emit ints; the contract stringifies
            "price": 12814000000,
            "qty": 1_500_000,
            "side": "buy",
            "exchange_ts": 1_700_000_000_123_456,
        }
    )
    assert n == Norm("BTC/USDT", "5551212", 12814000000, 1_500_000, "buy", 1_700_000_000_123_456)


def test_the_two_contracts_meet_on_the_same_tuple():
    """
    The reason a comparison is possible at all: a v2 string price and a v3 int
    price for the same trade must land on the same integer.
    """
    v2 = normalise_v2(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": "128.14000000",
            "quantity": "0.01500000",
            "side": "BUY",
            "exchange_timestamp": 1_700_000_000_123,
        }
    )
    v3 = normalise_v3(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": 12814000000,
            "qty": 1_500_000,
            "side": "buy",
            "exchange_ts": 1_700_000_000_123_000,
        }
    )
    assert v2 == v3


# ─────────────────────────────────────────────────────────────────────────────
# Taker side
# ─────────────────────────────────────────────────────────────────────────────


def test_an_inverted_binance_side_does_not_pass():
    """
    The hazard trade.avsc names in `side`'s own doc: "Binance is_buyer_maker
    inverted". Both tiers derive the taker side from the same boolean —
    TradeNormalizer.kt:27 `if (event.isBuyerMaker) SELL else BUY`, binance.rs:313
    `aggressor_side`. They agree today. If either flips, every Binance trade in
    the lake is labelled with the wrong aggressor, and it is the same price, the
    same quantity, the same id and the same timestamp — so counts, ID join, and
    px/qty all stay perfectly green. Nothing else in this suite can see it.
    """
    raw_v2 = {
        "canonical_symbol": "BTC/USDT",
        "trade_id": "5551212",
        "price": "128.14000000",
        "quantity": "0.01500000",
        "side": "BUY",
        "exchange_timestamp": 1_700_000_000_123,
    }
    raw_v3 = {
        "canonical_symbol": "BTC/USDT",
        "trade_id": "5551212",
        "price": 12814000000,
        "qty": 1_500_000,
        "side": "sell",  # inverted
        "exchange_ts": 1_700_000_000_123_000,
    }
    s = run([normalise_v2(raw_v2)], [normalise_v3(raw_v3)])
    assert (s.count_v2, s.count_v3, s.count_delta) == (1, 1, 0)
    assert (s.only_v2, s.only_v3) == (0, 0), "the ID join matches: same trade"
    assert s.mismatched == 1, "same trade, opposite aggressor, must be a mismatch"
    assert not s.passed


def test_the_two_enums_meet_on_one_vocabulary():
    """v2 emits `BUY`/`SELL`, v3 emits `buy`/`sell` (the two .avsc enums)."""
    v2 = normalise_v2(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": "1.00000000",
            "quantity": "1.00000000",
            "side": "SELL",
            "exchange_timestamp": 1_700_000_000_000,
        }
    )
    v3 = normalise_v3(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": 100_000_000,
            "qty": 100_000_000,
            "side": "sell",
            "exchange_ts": 1_700_000_000_000_000,
        }
    )
    assert v2.side == v3.side == "sell"
    assert v2 == v3


@pytest.mark.parametrize("raw,expected", [("BUY", "buy"), ("SELL", "sell"),
                                          ("buy", "buy"), ("sell", "sell")])
def test_both_enum_spellings_fold_onto_one_vocabulary(raw, expected):
    assert to_side(raw) == expected


def test_an_unrecognised_side_is_rejected_not_passed_through():
    """A third spelling would compare unequal to both tiers and read as a mismatch
    everywhere; failing loudly names the contract change instead."""
    with pytest.raises(ValueError, match="not a taker side"):
        to_side("MAKER")


def test_kraken_multiset_carries_side_too():
    """
    Kraken has no ID join, so side has to ride in the multiset key or the venue
    with the weakest join is also the one with no side coverage at all.
    """
    v2 = [trade(tid="KRAKEN-x", side="buy")]
    flipped = [trade(tid="99", side="sell")]
    s = run(v2, flipped, exchange="kraken")
    assert (s.only_v2, s.only_v3) == (1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Tolerance
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "count,expected",
    [(0, 2), (1, 2), (100, 2), (2000, 2), (3000, 3), (150_000, 150)],
)
def test_tolerance_floor_and_slope(count, expected):
    assert tolerance_for(count) == expected


def test_count_delta_of_two_passes_at_the_boundary():
    """100 trades -> tolerance 2. Δ=2 is inside it."""
    v2 = [trade(tid=str(i)) for i in range(100)]
    v3 = [trade(tid=str(i)) for i in range(98)]
    s = run(v2, v3)
    assert (s.count_v2, s.count_v3, s.tolerance) == (100, 98, 2)
    assert s.passed


def test_count_delta_of_three_fails_just_past_the_boundary():
    v2 = [trade(tid=str(i)) for i in range(100)]
    v3 = [trade(tid=str(i)) for i in range(97)]
    s = run(v2, v3)
    assert s.count_delta == 3 and s.tolerance == 2
    assert not s.passed


def test_large_symbol_gets_a_proportional_allowance():
    """0.1% of 10k is 10, so a 9-trade edge effect is not a failure at that volume."""
    v2 = [trade(tid=str(i)) for i in range(10_000)]
    v3 = [trade(tid=str(i)) for i in range(9_991)]
    s = run(v2, v3)
    assert s.tolerance == 10 and s.passed


# ─────────────────────────────────────────────────────────────────────────────
# ID join (binance / coinbase)
# ─────────────────────────────────────────────────────────────────────────────


def test_a_px_qty_mismatch_forces_fail_even_with_identical_counts():
    """
    The finding the tolerance must never absorb: both tiers saw the same trade
    and disagree about its price. Counts match perfectly, verdict is still FAIL.
    """
    v2 = [trade(tid="1"), trade(tid="2", price=200_00000000)]
    v3 = [trade(tid="1"), trade(tid="2", price=200_00000001)]
    s = run(v2, v3)
    assert (s.count_v2, s.count_v3, s.count_delta) == (2, 2, 0)
    assert (s.only_v2, s.only_v3) == (0, 0)
    assert s.mismatched == 1
    assert not s.passed


def test_a_qty_mismatch_counts_too():
    v2 = [trade(tid="1", qty=1_00000000)]
    v3 = [trade(tid="1", qty=2_00000000)]
    assert run(v2, v3).mismatched == 1


def test_ids_present_on_only_one_side_are_counted_per_side():
    v2 = [trade(tid=str(i)) for i in range(5)]
    v3 = [trade(tid=str(i)) for i in range(3, 9)]
    s = run(v2, v3)
    assert (s.only_v2, s.only_v3) == (3, 4)  # 0,1,2 vs 5,6,7,8
    assert not s.passed  # 3 and 4 both exceed the floor of 2


def test_a_duplicate_record_does_not_move_the_delta():
    """
    Venues re-send trades. Measured 2026-08-26: Coinbase re-sent trade_id
    69662829 (`time` 15:31:09.261488Z) on the SAME connection at 15:52:58.890Z,
    raw frame `sequence_num` 1017853 — both tiers wrote it twice. Counting
    records makes that look like a producer inventing a trade; counting unique
    IDs is the honest comparison, and the copies get their own column.
    """
    v2 = [trade(tid="1"), trade(tid="1"), trade(tid="2")]
    v3 = [trade(tid="1"), trade(tid="2")]
    s = run(v2, v3)
    assert (s.count_v2, s.count_v3, s.count_delta) == (2, 2, 0), "counts are unique IDs"
    assert (s.dup_v2, s.dup_v3) == (1, 0)
    assert (s.only_v2, s.only_v3) == (0, 0)
    assert s.passed


def test_a_duplicate_that_disagrees_with_its_first_copy_fails():
    """
    A re-sent trade is the venue repeating itself; a re-sent trade with a
    different price is a capture that mangled one of the copies. Deduplicating
    on ID alone would silently keep whichever arrived first, so the disagreement
    is counted and sinks the symbol.
    """
    v2 = [trade(tid="1"), trade(tid="1", price=999_00000000)]
    s = run(v2, [trade(tid="1")])
    assert s.dup_v2 == 1
    assert s.dup_inconsistent == 1
    assert (s.count_delta, s.only_v2, s.only_v3, s.mismatched) == (0, 0, 0, 0)
    assert not s.passed, "everything else agrees; the two copies of trade 1 do not"


def test_a_duplicate_never_counts_as_a_one_sided_trade():
    """The failure mode this replaces: 300 v2 copies read as 300 missing v3 trades."""
    v2 = [trade(tid="1")] * 300 + [trade(tid="2")]
    s = run(v2, [trade(tid="1"), trade(tid="2")])
    assert (s.count_v2, s.count_v3) == (2, 2)
    assert s.dup_v2 == 299
    assert (s.only_v2, s.only_v3) == (0, 0) and s.passed


def test_ts_delta_is_measured_over_matched_ids_only():
    v2 = [trade(tid="1", ts_us=1_000_000), trade(tid="2", ts_us=9_000_000)]
    v3 = [trade(tid="1", ts_us=1_000_750)]
    s = run(v2, v3)
    assert s.max_ts_delta_us == 750
    assert s.passed, "under a millisecond is v2's stored resolution, not a disagreement"


def test_a_ts_delta_of_a_full_millisecond_fails():
    """
    The number used to be printed and gate nothing, so a v3 tier stamping recv_ts
    into exchange_ts rendered a large delta next to a PASS. v2 floors to ms
    (Instant.toEpochMilli), so a matched pair cannot legitimately reach 1000 µs.
    """
    v2 = [trade(tid="1", ts_us=1_000_000)]
    v3 = [trade(tid="1", ts_us=1_001_000)]
    s = run(v2, v3)
    assert s.max_ts_delta_us == MAX_TS_DELTA_US
    assert (s.count_delta, s.only_v2, s.only_v3, s.mismatched) == (0, 0, 0, 0)
    assert not s.passed, "everything else agrees; only the clock disagrees"


def test_one_side_seeing_nothing_never_passes_on_the_tolerance():
    """
    The vacuous-PASS hazard. A symbol with two trades in the window sits on the
    floor of 2, so without an explicit guard it "passes" against a v3 tier that
    produced nothing at all — and a table of PASS rows gets pasted into the
    retirement PR as evidence of nothing. An absent side is not a window edge.
    """
    v2 = [trade(tid="1"), trade(tid="2")]
    s = run(v2, [])
    assert s.tolerance == 2 and abs(s.count_delta) <= s.tolerance
    assert not s.passed
    # symmetric: a v3-only symbol is just as much a divergence
    assert not run([], v2).passed


def test_kraken_one_side_seeing_nothing_also_fails():
    """The guard sits above the join, so the multiset path inherits it."""
    assert not run([trade(tid="KRAKEN-1")], [], exchange="kraken").passed


def test_both_sides_empty_is_not_reachable():
    """A symbol only exists in the output if some record carried it."""
    assert compare([], [], exchange="binance") == []


def test_symbols_are_compared_independently():
    v2 = [trade(symbol="BTC/USD", tid="1"), trade(symbol="ETH/USD", tid="2")]
    v3 = [trade(symbol="BTC/USD", tid="1")]
    stats = {s.symbol: s for s in compare(v2, v3, exchange="coinbase")}
    assert stats["BTC/USD"].passed
    assert stats["ETH/USD"].count_v3 == 0
    assert stats["ETH/USD"].only_v2 == 1


# ─────────────────────────────────────────────────────────────────────────────
# Kraken: no usable v2 trade_id (ADR-018 gap 5)
# ─────────────────────────────────────────────────────────────────────────────


def test_kraken_ignores_trade_ids_entirely():
    """
    v2 synthesises `KRAKEN-<ms>-<hash>`; v3 carries the venue's real integer id.
    No id is shared, so an id join would report 100% divergence. The multiset
    path sees two identical sets of trades and passes.
    """
    v2 = [
        trade(tid="KRAKEN-1700000000123-8891", price=100_00000000, ts_us=1_700_000_000_123_000),
        trade(tid="KRAKEN-1700000000456-8891", price=101_00000000, ts_us=1_700_000_000_456_000),
    ]
    v3 = [
        trade(tid="61150482", price=100_00000000, ts_us=1_700_000_000_123_900),
        trade(tid="61150483", price=101_00000000, ts_us=1_700_000_000_456_100),
    ]
    s = run(v2, v3, exchange="kraken")
    assert s.mismatched is None, "id join must be skipped for kraken"
    assert (s.only_v2, s.only_v3) == (0, 0)
    assert s.passed


def test_kraken_multiset_matches_at_millisecond_granularity():
    """
    v2 stores milliseconds and Kraken publishes microseconds, so the multiset key
    truncates to ms. Sub-millisecond difference: same trade. Different ms: not.
    """
    v2 = [trade(tid="KRAKEN-x", ts_us=1_700_000_000_123_000)]
    same_ms = [trade(tid="99", ts_us=1_700_000_000_123_999)]
    next_ms = [trade(tid="99", ts_us=1_700_000_000_124_000)]
    assert run(v2, same_ms, exchange="kraken").only_v2 == 0
    assert run(v2, next_ms, exchange="kraken").only_v2 == 1


def _kraken_pair(n, diverging=0):
    """n matching trades on both sides, with the first `diverging` v3 prices wrong."""
    v2 = [trade(tid=f"KRAKEN-{i}", price=100_00000000 + i, ts_us=1_700_000_000_000_000 + i * 1000)
          for i in range(n)]
    v3 = [trade(tid=str(i), price=100_00000000 + i, ts_us=1_700_000_000_000_000 + i * 1000)
          for i in range(n)]
    for i in range(diverging):
        v3[i] = trade(tid=str(i), price=999_00000000 + i, ts_us=1_700_000_000_000_000 + i * 1000)
    return v2, v3


def test_a_single_kraken_price_divergence_is_indistinguishable_from_a_window_edge():
    """
    The carve-out, asserted rather than assumed. With no id join, one diverging
    price is one only-v2 plus one only-v3 — arithmetically identical to a trade
    that fell off one side of the window and another that fell off the other. It
    passes, and the rendered header says so. This is the guarantee Kraken does
    NOT get, and the test name has to say that, because a name promising the
    opposite is worse than no test.
    """
    v2, v3 = _kraken_pair(10, diverging=1)
    s = run(v2, v3, exchange="kraken")
    assert (s.count_v2, s.count_v3, s.count_delta) == (10, 10, 0)
    assert (s.only_v2, s.only_v3) == (1, 1)
    assert s.passed


def test_kraken_price_divergence_past_the_edge_allowance_fails():
    """
    What the carve-out must not become: 150 wrong prices on a 150k-trade symbol
    sitting inside 0.1%. The multiset difference is capped at the constant edge
    allowance, never at the proportional slope, so volume does not buy silence.
    """
    v2, v3 = _kraken_pair(10, diverging=3)
    s = run(v2, v3, exchange="kraken")
    assert (s.only_v2, s.only_v3) == (3, 3)
    assert not s.passed

    # The case the proportional slope used to swallow whole.
    big2, big3 = _kraken_pair(150_000, diverging=150)
    big = run(big2, big3, exchange="kraken")
    assert big.tolerance == 150, "0.1% of 150k would have absorbed all 150"
    assert big.side_allowance == 2
    assert not big.passed


def test_kraken_counts_still_gate():
    v2 = [trade(tid=f"KRAKEN-{i}", price=i) for i in range(100)]
    v3 = [trade(tid=str(i), price=i) for i in range(96)]
    assert not run(v2, v3, exchange="kraken").passed


def test_kraken_v3_is_deduped_on_its_real_trade_id():
    """
    v3's Kraken id is the venue's own integer, so v3 CAN be reduced to unique
    trades even where v2 cannot. Measured 2026-08-26: 0 of 29,167 v3 records
    repeated an id across two involuntary reconnects, so this is currently a
    no-op — which is exactly why it needs a test. A future replay on resubscribe
    would otherwise land in the multiset as a v3 surplus and read as v2 dropping.
    """
    v2 = [trade(tid="KRAKEN-a", price=100), trade(tid="KRAKEN-b", price=101)]
    v3 = [trade(tid="7", price=100), trade(tid="7", price=100), trade(tid="8", price=101)]
    s = run(v2, v3, exchange="kraken")
    assert (s.count_v2, s.count_v3) == (2, 2), "v2 counts records, v3 counts unique real ids"
    assert (s.dup_v2, s.dup_v3) == (0, 1)
    assert (s.only_v2, s.only_v3) == (0, 0)
    assert s.passed


def test_kraken_v2_id_collisions_are_reported_not_deduplicated():
    """
    `KRAKEN-<ms>-<hash>` collides by construction, so its repeats are DIFFERENT
    trades and must stay in the count. Measured 2026-08-26 over 2 h: BTC/USD
    9,093 v2 records carried only 3,923 distinct synthesised ids — deduplicating
    them would have deleted 57% of the symbol's trades. The column reports the
    collision; the comparison ignores it.
    """
    v2 = [trade(tid="KRAKEN-1", price=100), trade(tid="KRAKEN-1", price=101),
          trade(tid="KRAKEN-1", price=102)]
    v3 = [trade(tid="7", price=100), trade(tid="8", price=101), trade(tid="9", price=102)]
    s = run(v2, v3, exchange="kraken")
    assert (s.count_v2, s.count_v3) == (3, 3), "all three v2 trades survive"
    assert s.dup_v2 == 2, "reported as ID collisions"
    assert s.dup_inconsistent == 0, "colliding synthesised ids are not a capture defect"
    assert (s.only_v2, s.only_v3) == (0, 0) and s.passed


# ─────────────────────────────────────────────────────────────────────────────
# Rendering — the artefact that is pasted into the PR
# ─────────────────────────────────────────────────────────────────────────────


def _header(exchange="binance", **over):
    base = {
        "exchange": exchange,
        "window_start": "2026-08-26T10:00:00+00:00",
        "window_end": "2026-08-26T12:00:00+00:00",
        "window_hours": 2.0,
        "v2_topic": f"market.crypto.trades.{exchange}",
        "v3_topic": f"market.crypto.v3.trades.{exchange}",
        "v2_consumed": 2,
        "v3_consumed": 2,
        "join_on_id": exchange != "kraken",
        "notes": [],
        "truncated": False,
    }
    return {**base, **over}


def test_markdown_table_has_the_agreed_columns_and_an_overall_verdict():
    stats = compare([trade(tid="1")], [trade(tid="1")], exchange="binance")
    out = render_markdown(stats, _header())
    assert (
        "| symbol | v2 | v3 | Δ | dup-v2 | dup-v3 | only-v2 | only-v3 |"
        " px/qty/side mismatch | verdict |"
    ) in out
    assert "**OVERALL: PASS** — 1/1 symbols" in out
    assert "labelled sample, not a soak" in out


def test_markdown_says_the_counts_are_unique_ids_and_duplicates_are_separate():
    """
    The header is the only place a reader learns that Δ is not a record delta.
    Without it the dup column looks like decoration and the table over-claims.
    """
    v2 = [trade(tid="1"), trade(tid="1")]
    out = render_markdown(compare(v2, [trade(tid="1")], exchange="coinbase"), _header("coinbase"))
    assert "unique `trade_id`" in out
    assert "never folded into Δ" in out
    assert "| 1 | 1 | +0 | 1 | 0 | 0 | 0 | 0 | PASS |" in out


def test_markdown_marks_failures_and_the_overall_verdict_follows():
    stats = compare([trade(tid="1")], [trade(tid="1", price=1)], exchange="binance")
    out = render_markdown(stats, _header())
    assert "**FAIL**" in out
    assert "**OVERALL: FAIL** — 0/1 symbols" in out


def test_markdown_explains_the_kraken_join_and_prints_na():
    stats = compare([trade(tid="KRAKEN-1")], [trade(tid="7")], exchange="kraken")
    out = render_markdown(stats, _header("kraken"))
    assert "ID comparison **skipped**" in out and "ADR-018 gap 5" in out
    assert "| n/a |" in out


def test_markdown_surfaces_an_empty_v3_topic_as_a_note_not_a_crash():
    stats = compare([trade(tid="1")], [], exchange="binance")
    out = render_markdown(
        stats, _header(v3_consumed=0, notes=["topic `market.crypto.v3.trades.binance` empty in window"])
    )
    assert "empty in window" in out
    assert "**OVERALL: FAIL**" in out


def test_a_truncated_read_cannot_render_a_pass():
    """
    Every per-symbol row can be green and the artefact still be worthless: the
    early-stop path is a short read, so its note has to reach the table AND sink
    the verdict. A caveat printed next to a PASS is how a fake PASS gets pasted.
    """
    stats = compare([trade(tid="1")], [trade(tid="1")], exchange="binance")
    assert all(s.passed for s in stats)
    out = render_markdown(
        stats,
        _header(
            truncated=True,
            notes=[f"{TRUNCATION_NOTE} — `market.crypto.v3.trades.binance` returned no "
                   "records for 15s with 2 of 8 partition(s) still short of the window end."],
        ),
    )
    assert TRUNCATION_NOTE in out
    assert "2 of 8 partition(s)" in out, "the note must name the scope of the shortfall"
    assert "**OVERALL: FAIL**" in out
    assert "read truncated — not evidence" in out


def test_markdown_states_the_kraken_carve_out_in_the_header():
    """README prose is not the artefact; the pasted table has to carry it."""
    stats = compare([trade(tid="KRAKEN-1")], [trade(tid="7")], exchange="kraken")
    out = render_markdown(stats, _header("kraken"))
    assert "carve-out" in out
    assert "never at the 0.1%" in out


def test_markdown_says_only_the_v3_side_of_kraken_can_be_deduplicated():
    """
    The asymmetry is the whole Kraken story: v3 counts unique venue ids, v2
    counts records because its ids collide. A table that printed both columns
    without saying so would invite the reader to subtract them.
    """
    stats = compare([trade(tid="KRAKEN-1")], [trade(tid="7")], exchange="kraken")
    out = render_markdown(stats, _header("kraken"))
    assert "v2 side cannot be deduplicated" in out
    assert "collision" in out


def test_markdown_calls_out_an_inconsistent_duplicate():
    """A dup column of 3 and a dup column of 3-with-one-mangled must not look alike."""
    v2 = [trade(tid="1"), trade(tid="1", price=9)]
    out = render_markdown(compare(v2, [trade(tid="1")], exchange="binance"), _header())
    assert "**Inconsistent duplicates**" in out
    assert "BTC/USD (1)" in out, "the note must name the symbol and the count"
    assert "**OVERALL: FAIL**" in out


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument validation
# ─────────────────────────────────────────────────────────────────────────────


def _args(start, end):
    return parse_args(["--exchange", "binance", "--window-start", start, "--window-end", end])


def test_a_window_ending_now_is_refused():
    """
    v3 is read only after v2 has fully drained, so a window still being written
    into hands v3 the records produced during the v2 read and v2 none of them.
    """
    now = datetime.now(timezone.utc)
    with pytest.raises(SystemExit):
        _args((now - timedelta(hours=2)).isoformat(), now.isoformat())


def test_a_window_ending_safely_in_the_past_is_accepted():
    now = datetime.now(timezone.utc)
    end = now - timedelta(seconds=MIN_WINDOW_END_AGE_S + 30)
    args = _args((end - timedelta(hours=2)).isoformat(), end.isoformat())
    assert args.window_end == end


# ─────────────────────────────────────────────────────────────────────────────
# --v2-only: the smoke path has the same truncation gate as the verdict path
# ─────────────────────────────────────────────────────────────────────────────


def _v2_only(monkeypatch, note):
    """
    Run `main(--v2-only)` with the Kafka half replaced: one decoded record and
    whatever `note` the read is supposed to have produced. Everything Kafka-shaped
    in that path is a shell around `consume_window`, which is what this stubs.
    """
    import compare_trades as ct

    row = Norm(
        canonical_symbol="BTC/USD",
        trade_id="1",
        price=to_fixed_1e8("128.14"),
        qty=to_fixed_1e8("0.001"),
        side="buy",
        exchange_ts_us=1_787_730_846_075_373,
    )
    monkeypatch.setattr(ct, "build_deserializer", lambda registry: object())
    monkeypatch.setattr(
        ct, "make_consumer", lambda brokers: type("C", (), {"close": lambda self: None})()
    )
    monkeypatch.setattr(ct, "consume_window", lambda *a, **k: ([row], 1, note))

    end = datetime.now(timezone.utc) - timedelta(seconds=MIN_WINDOW_END_AGE_S + 30)
    return ct.main(
        [
            "--exchange", "binance",
            "--window-start", (end - timedelta(hours=2)).isoformat(),
            "--window-end", end.isoformat(),
            "--v2-only",
            "--json",
        ]
    )


def test_v2_only_exits_zero_on_a_complete_read(monkeypatch):
    assert _v2_only(monkeypatch, None) == 0


def test_v2_only_fails_on_a_truncated_read(monkeypatch):
    """
    The counts a truncated read prints are a lower bound on the topic. Exiting 0
    on them reports "decoding and offsets_for_times work over this window" for a
    window the tool never finished reading — the same reason the full comparison
    forces FAIL on `header["truncated"]`.
    """
    note = f"{TRUNCATION_NOTE} — `t` returned no records for 30s with 2 of 20 partition(s) short."
    assert _v2_only(monkeypatch, note) == 1


def test_v2_only_json_carries_the_truncation_the_exit_code_reports(monkeypatch, capsys):
    """
    An exit code is not in the document. A pipeline that reads the JSON and not
    `$?` — which is the whole point of `--json` — would take `counts` for the
    topic's totals with nothing in the payload saying they are a lower bound.
    Same two keys as the full comparison's header.
    """
    note = f"{TRUNCATION_NOTE} — `t` returned no records for 30s with 2 of 20 partition(s) short."
    assert _v2_only(monkeypatch, note) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["truncated"] is True
    assert doc["notes"] == [note]

    assert _v2_only(monkeypatch, None) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["truncated"] is False
    assert doc["notes"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Pin resolution: scripts/parity_ohlcv.py resolve_snapshot
#
# A pin is read only if it is on main's lineage (the walk from main's head along
# parent links) or a tag names it; anything else stops the run. From 2026-08-27 to
# 2026-09-03 a pin written on another host crashed the gate; the fix that followed
# fell back to main's current snapshot with one printed line. The walk stops at the
# first parent expiry removed, so a week after pinning that fallback compared the
# engines on live data and called it the pin (audit, 2026-11-26). These hold the
# rule that replaced both: no crash, no fallback, and the tag keeps a pin valid.
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from parity_ohlcv import pin_tag, resolve_snapshot  # noqa: E402

# main's lineage, current first (notebooks/k2lake.py `lineage`). 444 is a commit on
# a branch, in the table but never on main, and someone's scratch tag points at it.
# 100 is kept by the pin's own tag, parity_2001_01_01, and expiry removed the
# snapshot after it, so the walk from main no longer reaches it.
_LINEAGE = [999, 222, 111]
_BRANCH = 444
_TAGGED = 100
_TAG = "parity_2001_01_01"
_TAGS = {_TAG: _TAGGED, "scratch": _BRANCH}
_STALE = 1622213366608023449  # the 2026-08-27 pin: in no table today


def test_the_pins_own_tag_is_named_for_its_day():
    assert pin_tag("2001-01-01") == _TAG
    assert pin_tag("2001-01-01", "_dbt") == "parity_2001_01_01_dbt"


def test_a_pin_on_mains_lineage_is_used_as_is(capsys):
    assert resolve_snapshot(_LINEAGE, _TAGS, "lake.gold.ohlcv_1m", 111, _TAG) == 111
    assert capsys.readouterr().out == "", "an intact pin must say nothing"


def test_a_tagged_pin_off_mains_walk_is_used_and_says_why(capsys):
    """The 2026-12-03 case: the tag kept the pin, expiry cut the walk above it."""
    assert resolve_snapshot(_LINEAGE, _TAGS, "lake.gold.ohlcv_1m", _TAGGED, _TAG) == _TAGGED
    assert f"held by its tag {_TAG}" in capsys.readouterr().out


@pytest.mark.parametrize("tag", ["parity_2001_01_02", "parity_2001_01_01_dbt"])  # another day's; the dbt side's
def test_a_pin_held_only_by_some_other_tag_stops_the_run(tag):
    with pytest.raises(SystemExit, match=f"100 is not on main's lineage and its tag {tag} does not point at it"):
        resolve_snapshot(_LINEAGE, _TAGS, "lake.gold.ohlcv_1m", _TAGGED, tag)


def test_a_pin_only_a_branch_reaches_stops_the_run_even_under_a_scratch_tag():
    """A branch's snapshot is in the table (`iceberg_snapshots` lists it) but was never
    published: parity over it would compare rows main never served (spike, 2026-11-25).
    The scratch tag on it validates nothing: only the pin's own tag counts."""
    with pytest.raises(SystemExit, match=f"444 is not on main's lineage and its tag {_TAG} does not point at it"):
        resolve_snapshot(_LINEAGE, _TAGS, "lake.gold.ohlcv_1m", _BRANCH, _TAG)


def test_an_unresolvable_pin_stops_the_run_instead_of_falling_back(capsys):
    """No other snapshot is returned, and the message carries the table, the id and both ways out."""
    with pytest.raises(SystemExit) as stop:
        resolve_snapshot(_LINEAGE, _TAGS, "lake.gold.ohlcv_1m", _STALE, _TAG)
    message = str(stop.value.code)
    assert f"lake.gold.ohlcv_1m: pinned snapshot {_STALE}" in message
    assert f"restore the tag {_TAG}" in message and "re-pin with --pin-current" in message
    assert capsys.readouterr().out == ""


def test_a_table_with_no_snapshots_stops_the_run():
    with pytest.raises(SystemExit):
        resolve_snapshot([], {}, "lake.gold.ohlcv_1m", 111, _TAG)


# The entry points: main() of both parity scripts resolve every pin through main's
# lineage and the pin's own tag, and nothing else. Lakekeeper answers every table with
# main at 3, a live branch's commit 4 (under a scratch tag) and a dropped branch's 5 (the
# newest), and 7, kept by parity_2026_01_01, whose parent 6 expiry removed. The DuckDB stand-in answers
# `iceberg_snapshots` as DuckDB does: every snapshot in the metadata.
_REST = {
    "current-snapshot-id": 3,
    "refs": {"main": {"snapshot-id": 3, "type": "branch"}, "audit_1": {"snapshot-id": 4, "type": "branch"},
             "parity_2026_01_01": {"snapshot-id": 7, "type": "tag"}, "scratch": {"snapshot-id": 4, "type": "tag"}},
    "snapshots": [{"snapshot-id": 1}] + [{"snapshot-id": s, "parent-snapshot-id": p} for s, p in ((2, 1), (3, 2), (4, 3), (5, 2), (7, 6))],
}


class _Duck:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "iceberg_snapshots" in sql:
            rows = [(n, n) for n in (1, 2, 3, 4, 5, 7)]  # (sequence_number, snapshot_id)
        elif "lake.gold.ohlcv_1m AT" in sql:
            rows = [("kraken", "BTC/USD", datetime(2026, 1, 1), 1, 1, 1, 1, 1, Decimal(1), Decimal(1))]
        else:
            rows = []  # every other side is empty, so main() stops after reading it
        return type("Result", (), {"fetchall": lambda self: rows})()

    def executemany(self, sql, rows):
        self.sql.append(sql)


@pytest.fixture
def lake(monkeypatch):
    """Stubbed Lakekeeper REST and DuckDB; returns the DuckDB stand-in."""
    import k2lake  # on sys.path once parity_ohlcv is imported

    k2lake._prefix.cache_clear()
    monkeypatch.setattr(k2lake, "_get", lambda url: {"defaults": {"prefix": "P"}} if "/config" in url else {"metadata": _REST})
    monkeypatch.setenv("MINIO_ROOT_USER", "u")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "p")
    yield _Duck()
    k2lake._prefix.cache_clear()


def _ohlcv(lake, monkeypatch, pin):
    import parity_ohlcv

    monkeypatch.setattr(parity_ohlcv, "duck", lambda user, password: lake)
    monkeypatch.setattr(sys, "argv", ["parity_ohlcv.py", "--day", "2026-01-01", "--ohlcv-snapshot", str(pin),
                                      "--silver-snapshots", json.dumps({"kraken": pin}), "--skip-clickhouse"])
    return parity_ohlcv.main()


def _bars(lake, monkeypatch, pin):
    import parity_bars

    monkeypatch.setattr(parity_bars, "duck", lambda user, password: lake)
    monkeypatch.setattr(sys, "argv", ["parity_bars.py", "--day", "2026-01-01", "--bars-snapshot", str(pin),
                                      "--trades-snapshot", str(pin), "--reference-symbols", ""])
    return parity_bars.main()


@pytest.mark.parametrize("pin", [3, 7])  # main's head, as --pin-current passes it; the tagged one off the walk
def test_parity_ohlcv_main_reads_each_pin_at_the_pin(lake, monkeypatch, pin):
    assert _ohlcv(lake, monkeypatch, pin) == 1  # silver is empty in the stand-in
    assert any(f"lake.gold.ohlcv_1m AT (VERSION => {pin})" in q for q in lake.sql)
    assert any(f"lake.silver.trades_kraken AT (VERSION => {pin})" in q for q in lake.sql)


@pytest.mark.parametrize("pin", [3, 7])
def test_parity_bars_main_reads_each_pin_at_the_pin(lake, monkeypatch, pin):
    assert _bars(lake, monkeypatch, pin) == 1  # every side is empty in the stand-in
    assert any(f"lake.gold.bars AT (VERSION => {pin})" in q for q in lake.sql)
    assert any(f"lake.gold.trades AT (VERSION => {pin})" in q for q in lake.sql)


@pytest.mark.parametrize("run", [_ohlcv, _bars])
@pytest.mark.parametrize("pin", [999, 4, 5])  # in no table; a live branch's commit; a dropped branch's
def test_both_scripts_stop_on_a_pin_they_cannot_resolve_before_reading_anything(lake, monkeypatch, run, pin):
    with pytest.raises(SystemExit, match=f"pinned snapshot {pin} is not on main's lineage"):
        run(lake, monkeypatch, pin)
    assert not any("AT (VERSION" in q for q in lake.sql), "nothing may be read at a snapshot nobody pinned"


# ─────────────────────────────────────────────────────────────────────────────
# The dbt side, D: scripts/parity_ohlcv.py --dbt
#
# D runs dbt's compiled ohlcv_1m SQL through Trino over gold.trades at the
# snapshot the lake's candles were computed from. These hold the two pure halves
# without a stack: which snapshot, and the SQL that reads nothing at `latest`.
# ─────────────────────────────────────────────────────────────────────────────

from parity_ohlcv import (  # noqa: E402
    confirm_dbt_snapshot,
    lake_vs_dbt,
    newest_source_snapshot,
    normalise,
    pinned_sql,
)

_TRADES = '"lake"."gold"."trades"'
_STAGING = '"market_analytics"."staging"."stg_gold__trades"'


def _manifest():
    """The shape of dbt's target/manifest.json after `dbt compile`, two models and a source."""
    return {
        "sources": {"source.k2.gold.trades": {"relation_name": _TRADES}},
        "nodes": {
            "model.k2.stg_gold__trades": {
                "relation_name": _STAGING,
                "compiled_code": f"select exchange, price_e8 from {_TRADES}",
                "depends_on": {"nodes": ["source.k2.gold.trades"]},
            },
            "model.k2.ohlcv_1m": {
                "relation_name": '"market_analytics"."candles"."ohlcv_1m"',
                "compiled_code": f"select exchange, max(price_e8) as high_e8 from {_STAGING} group by 1",
                "depends_on": {"nodes": ["model.k2.stg_gold__trades"]},
            },
        },
    }


_DBT_TAG = "parity_2001_01_01_dbt"


def test_the_newest_source_snapshot_is_the_highest_sequence_number_not_the_highest_id():
    # What --dbt --write-pin records. 999 is the highest id; 222 has the highest sequence number among the ids asked for.
    assert newest_source_snapshot([111, 222], [(13, 111), (44, 999), (27, 222)]) == 222


@pytest.mark.parametrize("src", [[111, 333], [111, None], []])  # one expired; a NULL; none
def test_nothing_is_pinned_for_d_unless_every_source_snapshot_is_held(src):
    with pytest.raises(SystemExit):
        newest_source_snapshot(src, [(13, 111)])


def test_a_week_on_the_recorded_dbt_snapshot_is_read_under_its_own_tag(capsys):
    # 2026-12-03 for 2026-11-25: of the day's source snapshots only the pinned one is left, off the walk, tagged.
    assert confirm_dbt_snapshot(555, [111, 333, 555], [(50, 555), (90, 999)], [999], {_DBT_TAG: 555}, _DBT_TAG) == 555
    assert f"held by its tag {_DBT_TAG}" in capsys.readouterr().out


def test_an_older_tagged_snapshot_never_stands_in_for_the_recorded_one():
    # The pin recorded 2, the newest; 2 has expired and 1, older, is tagged by mistake. The old
    # "newest still held" rule read 1 and could pass a candle that missed a late trade.
    with pytest.raises(SystemExit, match="pinned snapshot 2 is not on main's lineage"):
        confirm_dbt_snapshot(2, [1, 2], [(10, 1), (90, 9)], [9], {_DBT_TAG: 1}, _DBT_TAG)


def test_a_recorded_dbt_snapshot_with_a_newer_source_snapshot_held_stops_the_run():
    with pytest.raises(SystemExit, match=r"not the newest source snapshot: lake.gold.trades holds newer ones \[222\]"):
        confirm_dbt_snapshot(111, [111, 222], [(13, 111), (27, 222)], [222, 111], {}, _DBT_TAG)


def test_a_recorded_dbt_snapshot_the_days_candles_do_not_name_stops_the_run():
    with pytest.raises(SystemExit, match="999 is not one of the gold.trades snapshots"):
        confirm_dbt_snapshot(999, [111, 222], [(13, 111), (27, 222), (44, 999)], [999, 222, 111], {}, _DBT_TAG)


def test_a_recorded_dbt_snapshot_only_a_branch_reaches_stops_the_run():
    with pytest.raises(SystemExit, match="444 is not on main's lineage"):
        confirm_dbt_snapshot(444, [111, 444], [(13, 111), (60, 444)], [111], {"scratch": 444}, _DBT_TAG)


def test_a_null_source_snapshot_stops_the_dbt_side():
    # The old code stopped on it; skipping it as "expired" would hide a broken lake row.
    with pytest.raises(SystemExit, match="NULL"):
        confirm_dbt_snapshot(111, [111, None], [(13, 111)], [111], {}, _DBT_TAG)


def test_pinned_sql_inlines_the_staging_view_and_reads_the_source_at_the_snapshot():
    sql = pinned_sql(_manifest(), "model.k2.ohlcv_1m", {"source.k2.gold.trades": 42})
    assert sql == (
        f"select exchange, max(price_e8) as high_e8 from (select exchange, price_e8 from {_TRADES} FOR VERSION AS OF 42) group by 1"
    )
    assert _STAGING not in sql  # the view reads the lake at latest; the parity must not


def test_pinned_sql_refuses_a_source_with_no_snapshot():
    with pytest.raises(SystemExit, match="no snapshot"):
        pinned_sql(_manifest(), "model.k2.ohlcv_1m", {})


def test_pinned_sql_refuses_a_relation_the_compiled_sql_does_not_name():
    # A changed quoting style would otherwise leave the source unpinned, silently.
    manifest = _manifest()
    manifest["nodes"]["model.k2.stg_gold__trades"]["compiled_code"] = "select exchange, price_e8 from lake.gold.trades"
    with pytest.raises(SystemExit, match="does not appear"):
        pinned_sql(manifest, "model.k2.ohlcv_1m", {"source.k2.gold.trades": 42})


def test_pinned_sql_needs_a_compiled_manifest():
    manifest = _manifest()
    del manifest["nodes"]["model.k2.ohlcv_1m"]["compiled_code"]  # what `dbt parse` alone leaves
    with pytest.raises(SystemExit, match="dbt compile"):
        pinned_sql(manifest, "model.k2.ohlcv_1m", {"source.k2.gold.trades": 42})


_WS = datetime(2001, 1, 1)
# One made-up bucket, as each side returns it: B's ten columns, D's eleventh is Trino's typeof.
_LAKE = ("x", "AAA/BBB", _WS, 1, 2, 1, 2, 3, Decimal("4.0000000000"), Decimal("403.0000010000"))
_DBT = _LAKE + ("decimal(38,10)",)


def test_the_three_way_sides_still_compare_volume_rounded_to_8_decimals():
    # Sides A, B and C: unchanged by --dbt.
    assert normalise([_LAKE]) == {("x", "AAA/BBB", _WS): (1, 2, 1, 2, 3, 4.0)}
    assert normalise([_LAKE]) == normalise([_LAKE[:8] + (Decimal("4.0000000001"), _LAKE[9])])


def test_lake_and_dbt_agree_when_every_column_and_the_type_match(capsys):
    assert lake_vs_dbt([_LAKE], [_DBT], "decimal(38,10)") == 0
    assert "1 buckets, 0 differ" in capsys.readouterr().out


@pytest.mark.parametrize("column", [8, 9])  # volume, quote_volume
def test_lake_and_dbt_differ_on_one_unit_in_the_tenth_decimal(column):
    # No tolerance (ADR-029): the rounding to 8 decimals the three-way sides use would pass this.
    changed = list(_DBT)
    changed[column] += Decimal("0.0000000001")
    assert lake_vs_dbt([_LAKE], [tuple(changed)], "decimal(38,10)") == 1


def test_quote_volume_typed_differently_is_a_failure_even_when_the_values_match(capsys):
    assert lake_vs_dbt([_LAKE], [_DBT[:10] + ("decimal(38,6)",)], "decimal(38,10)") == 1
    assert "FAIL, the engines type it differently" in capsys.readouterr().out


def test_a_run_with_no_side_to_compare_the_lake_with_is_refused(monkeypatch):
    # Before: --skip-silver --skip-clickhouse without --dbt compared B with nothing and printed PASS.
    import parity_ohlcv

    monkeypatch.setattr(sys, "argv", ["parity_ohlcv.py", "--day", "2001-01-01", "--ohlcv-snapshot", "1", "--skip-silver", "--skip-clickhouse"])
    monkeypatch.setattr(parity_ohlcv, "duck", lambda *a: pytest.fail("ran without a side to compare"))
    with pytest.raises(SystemExit) as stop:
        parity_ohlcv.main()
    assert stop.value.code == 2


# ─────────────────────────────────────────────────────────────────────────────
# scripts/parity-ohlcv.sh --pin-current: the day is optional and comes first.
# `--pin-current --dbt` took `--dbt` as the day. Run with curl and uv stubbed:
# nothing reaches a catalog and nothing is written.
# ─────────────────────────────────────────────────────────────────────────────

# main is at 7; 8 is a newer commit on a branch, which --pin-current must not take.
_CURL_TABLE = json.dumps({"metadata": {
    "current-snapshot-id": 7,
    "refs": {"main": {"snapshot-id": 7, "type": "branch"}, "audit_1": {"snapshot-id": 8, "type": "branch"}},
    "snapshots": [{"snapshot-id": 7, "summary": {"k2.src-snapshot-id.x": "5"}},
                  {"snapshot-id": 8, "parent-snapshot-id": 7, "summary": {"k2.src-snapshot-id.x": "6"}}],
}})
_CURL = """#!/usr/bin/env bash
case "$2" in
  *config*) echo '{"defaults": {"prefix": "p"}}' ;;
  *) echo '""" + _CURL_TABLE + """' ;;
esac
"""


def _wrapper(tmp_path, *args, pin=None):
    import subprocess

    (tmp_path / "scripts").mkdir()
    if pin is not None:
        (tmp_path / "tests/parity").mkdir(parents=True)
        (tmp_path / "tests/parity/pinned.json").write_text(json.dumps(pin))
    (tmp_path / "scripts/parity-ohlcv.sh").write_text((Path(__file__).parent.parent / "scripts/parity-ohlcv.sh").read_text())
    (tmp_path / ".env").write_text("")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "curl").write_text(_CURL)
    (stubs / "uv").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')  # what parity_ohlcv.py would be given
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    done = subprocess.run(["bash", str(tmp_path / "scripts/parity-ohlcv.sh"), *args], capture_output=True, text=True,
                          env={"PATH": f"{stubs}:/usr/bin:/bin"}, timeout=30)
    assert done.returncode == 0, done.stderr
    given = done.stdout.splitlines()
    return given[given.index("scripts/parity_ohlcv.py") + 1:]


def test_pin_current_passes_dbt_through_and_pins_yesterday(tmp_path):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert _wrapper(tmp_path, "--pin-current", "--dbt") == [
        "--day", yesterday, "--ohlcv-snapshot", "7", "--silver-snapshots", '{"x": 5}', "--known-gaps", "[]", "--write-pin", "--dbt"
    ]


def test_pin_current_pins_mains_head_which_the_strict_resolver_then_reads(tmp_path):
    """--pin-current is the one way to run at "current": it passes main's head, never a newer branch commit."""
    import k2lake

    given = _wrapper(tmp_path, "--pin-current", "2001-01-02")
    pin = int(given[given.index("--ohlcv-snapshot") + 1])
    assert pin == 7
    meta = json.loads(_CURL_TABLE)["metadata"]
    assert resolve_snapshot(k2lake.lineage(meta), k2lake.tags(meta), "lake.gold.ohlcv_1m", pin, pin_tag("2001-01-02")) == 7


def test_pin_current_takes_a_day_then_the_rest(tmp_path):
    assert _wrapper(tmp_path, "--pin-current", "2001-01-02", "--dbt", "--skip-clickhouse")[:2] == ["--day", "2001-01-02"]
    (tmp_path / "again").mkdir()
    assert _wrapper(tmp_path / "again", "--pin-current", "2001-01-02", "--dbt")[-2:] == ["--write-pin", "--dbt"]


def test_the_gate_stands_clickhouse_down_visibly_while_it_is_paused():
    """ADR-057: `make gates` reaches parity-ohlcv, which prints a SKIP line naming the ADR and
    passes --skip-clickhouse, with dbt as the third side. A dry run: nothing is executed."""
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}

    def dry(*args):
        return subprocess.run(["make", "-n", "-s", *args], cwd=Path(__file__).parent.parent, env=env,
                              capture_output=True, text=True, check=True, timeout=30).stdout.splitlines()

    assert ": parity-ohlcv" in dry("MAKE=:", "gates")
    assert dry("parity-ohlcv") == [
        'echo "SKIP parity-ohlcv side A, ClickHouse: the hot tier is paused (ADR-057)"',
        "scripts/parity-ohlcv.sh --skip-clickhouse --dbt",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Known gaps: `known_gaps` in tests/parity/pinned.json, scripts/parity_ohlcv.py
#
# A gap takes one listed window out of ClickHouse against the lake, and nothing else.
# These hold what it promises: the other comparisons still see the window, a minute
# either side of it still fails, a gap with no difference left in it fails, every rule
# on an entry stops the run, and a run with a gap applied never reads as a clean PASS.
# Made-up buckets on 2001-01-01, every side stubbed.
# ─────────────────────────────────────────────────────────────────────────────

from parity_ohlcv import GAP_FIELDS, load_gaps, verdict  # noqa: E402

_DAY = "2001-01-01"
# An outage from 00:00:30 to 00:04:10: rounded out to whole minutes, 00:00 to 00:05.
_REF = "lake.audit.checks job=operator check_name=capture_outage scope=stack/2001-01-01T00:00:30Z..2001-01-01T00:04:10Z"


def _gap(**over):
    """A valid entry: 00:01 to 00:02 on 2001-01-01, inside _REF's outage."""
    return {"start": "2001-01-01T00:01Z", "end": "2001-01-01T00:02Z", "sides": ["clickhouse"],
            "cause": "the host killed the VM", "recorded": "2001-01-02", "audit_ref": _REF, **over}


def _ref(scope):
    return f"lake.audit.checks job=operator check_name=capture_outage scope=stack/{scope}"


def _bucket(minute, close=2):
    return ("kraken", "AAA/BBB", datetime(2001, 1, 1, 0, minute), 1, 2, 1, close, 3, Decimal("4"), Decimal("5"))


_CLEAN = [_bucket(m) for m in range(3)]  # minutes 00:00, 00:01, 00:02


def _differs_at(*minutes):
    return [_bucket(m, close=9 if m in minutes else 2) for m in range(3)]


# The row _REF names, as `SELECT job, passed FROM lake.audit.checks ...` returns it.
_OUTAGE_ROW = [("operator", False)]


class _Q:
    """DuckDB stand-in for main()'s own lookups: the dbt side's, and the known gap's audit row.

    `audit` is what the audit query returns, or an exception it raises; `asked` keeps
    the parameters it was given.
    """

    def __init__(self, audit=_OUTAGE_ROW):
        self.audit, self.asked = audit, []

    def execute(self, sql, params=None):
        if "lake.audit.checks" in sql:
            self.asked.append(params)
            if isinstance(self.audit, Exception):
                raise self.audit
            rows = self.audit
        else:
            rows = [(7,)] if "src_snapshot_id" in sql else [(1, 7)] if "iceberg_snapshots" in sql else [("decimal(38,10)",)]
        return type("Result", (), {"fetchall": lambda self: rows})()


def _main(monkeypatch, capsys, gaps, *, clickhouse=_CLEAN, silver=_CLEAN, dbt=_CLEAN, flags=("--dbt", "--dbt-trades-snapshot", "7"),
          audit=_OUTAGE_ROW):
    """main() with all four sides stubbed, the lake's always _CLEAN. Returns (exit code, stdout)."""
    import parity_ohlcv as p

    for var in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "CLICKHOUSE_PASSWORD"):
        monkeypatch.setenv(var, "x")
    monkeypatch.setattr(p, "duck", lambda user, password: _Q(audit))
    monkeypatch.setattr(p, "table_refs", lambda table: ([7, 1], {}))  # 1 the pins, 7 the dbt side's source snapshot
    monkeypatch.setattr(p, "lake_candles", lambda c, day, snapshot: _CLEAN)
    monkeypatch.setattr(p, "silver_candles", lambda c, day, snapshots: silver)
    monkeypatch.setattr(p, "clickhouse_candles", lambda day, password: clickhouse)
    monkeypatch.setattr(p, "dbt_candles", lambda day, snapshot: [r + ("decimal(38,10)",) for r in dbt])
    monkeypatch.setattr(sys, "argv", ["parity_ohlcv.py", "--day", _DAY, "--ohlcv-snapshot", "1",
                                      "--silver-snapshots", '{"kraken": 1}', "--known-gaps", json.dumps(gaps), *flags])
    code = p.main()
    return code, capsys.readouterr().out


def test_a_difference_inside_a_known_gap_is_skipped(monkeypatch, capsys):
    code, out = _main(monkeypatch, capsys, [_gap()], clickhouse=_differs_at(1))
    assert code == 0
    assert f"known gap: audit row found, {_REF}" in out
    assert (f"known gap 2001-01-01T00:01Z to 2001-01-01T00:02Z (the host killed the VM; {_REF}): "
            "1 buckets removed, 1 of them differing") in out
    assert "clickhouse.ohlcv_live vs lake.gold.ohlcv_1m: 2 buckets, 0 differ" in out
    assert out.rstrip().endswith("PARITY: ok (1 known gap applied, see above)")


@pytest.mark.parametrize("side", ["silver", "dbt"])
def test_the_same_difference_against_silver_or_dbt_still_fails(monkeypatch, capsys, side):
    code, out = _main(monkeypatch, capsys, [_gap()], clickhouse=_differs_at(1), **{side: _differs_at(1)})
    assert code == 1
    assert "PARITY: FAIL (1 known gap applied, see above)" in out


@pytest.mark.parametrize("minute", [0, 2])  # the minute before start, and end itself: end is exclusive
def test_a_difference_one_minute_outside_the_gap_still_fails(monkeypatch, capsys, minute):
    # The gap's own minute differs too, so the gap is live and only the outside minute can fail the run.
    code, out = _main(monkeypatch, capsys, [_gap()], clickhouse=_differs_at(1, minute))
    assert code == 1
    assert "1 buckets removed, 1 of them differing" in out
    assert "clickhouse.ohlcv_live vs lake.gold.ohlcv_1m: 2 buckets, 1 differ" in out


def test_a_gap_that_matches_no_bucket_fails_the_run(monkeypatch, capsys):
    # Every side agrees; the gap names a minute with no bucket on either side: a typo, or stale.
    code, out = _main(monkeypatch, capsys, [_gap(start="2001-01-01T00:03Z", end="2001-01-01T00:04Z")])
    assert code == 1
    assert "0 buckets removed, 0 of them differing: FAIL, it matches no bucket" in out


def test_a_gap_whose_window_now_matches_fails_the_run(monkeypatch, capsys):
    # A dead exclusion must not stay in the pin: the next difference in that minute would hide behind it.
    code, out = _main(monkeypatch, capsys, [_gap()])
    assert code == 1
    assert "1 buckets removed, 0 of them differing: FAIL, its window now matches in full" in out


def test_a_gap_not_applied_is_printed_and_leaves_a_clean_pass(monkeypatch, capsys):
    code, out = _main(monkeypatch, capsys, [_gap()], flags=("--skip-clickhouse",))
    assert code == 0
    assert "not applied: clickhouse not compared in this run" in out
    assert out.rstrip().endswith("PARITY: PASS")


def test_the_summary_says_pass_only_when_nothing_was_left_out():
    assert verdict(0, 0) == "PARITY: PASS"
    assert verdict(3, 0) == "PARITY: FAIL"
    assert verdict(0, 1) == "PARITY: ok (1 known gap applied, see above)"
    assert verdict(1, 1) == "PARITY: FAIL (1 known gap applied, see above)"


def _without(field):
    g = _gap()
    del g[field]
    return g


@pytest.mark.parametrize(
    "gap,message",
    [
        (_gap(note="x"), r"unknown keys \['note'\]"),
        *[(_without(f), rf"missing keys \['{f}'\]") for f in sorted(GAP_FIELDS)],
        (_gap(end="2001-01-01T00:01Z"), "not after start"),
        (_gap(start="2001-01-01T00:02Z", end="2001-01-01T00:01Z"), "not after start"),
        (_gap(end="2001-01-01T01:02Z"), "longer than 60 minutes"),
        (_gap(start="2000-12-31T23:59Z"), "not inside the day compared, 2001-01-01"),
        (_gap(start="2001-01-01T23:59Z", end="2001-01-02T00:01Z"), "not inside the day compared"),
        # Time text: exactly YYYY-MM-DDTHH:MMZ.
        (_gap(start="2001-01-01T00:01"), "not written exactly like 2001-01-01T00:00Z"),  # no zone
        (_gap(start="2001-01-01T10:01+10:00"), "not written exactly"),  # the same instant, not in UTC
        (_gap(start="2001-01-01T00:00:30Z"), "not written exactly"),  # seconds
        (_gap(start="20010101T0001Z"), "not written exactly"),  # basic format
        (_gap(start="2001-W01-1T00:01Z"), "not written exactly"),  # week date
        (_gap(start="2001-01-01T00Z"), "not written exactly"),  # no minutes
        (_gap(start="2001-1-1T0:1Z"), "not written exactly"),  # unpadded
        (_gap(start="yesterday"), "not written exactly"),
        (_gap(recorded="20010102"), "not written exactly like 2001-01-01"),
        # Sides: ClickHouse against the lake, and only that.
        *[(_gap(sides=s), r"must be \['clickhouse'\]: lake against DuckDB over silver and lake against dbt")
          for s in ([], ["silver"], ["dbt"], ["clickhouse", "silver"], ["clickhouse", "clickhouse"], "clickhouse")],
        (_gap(cause=""), "cause is not a non-empty string"),
        # audit_ref: the exact form, and an outage the window sits inside.
        (_gap(audit_ref="  "), "is not 'lake.audit.checks job=<job> check_name=<name> scope=stack/"),
        (_gap(audit_ref=None), "is not 'lake.audit.checks"),
        (_gap(audit_ref="audit.checks job=operator check_name=capture_outage"), "is not 'lake.audit.checks"),
        (_gap(audit_ref=_ref("2001-01-01T00:00Z..2001-01-01T00:05Z")), "not written exactly like 2001-01-01T00:00:00Z"),
        (_gap(audit_ref=_ref("2001-01-01T00:04:10Z..2001-01-01T00:00:30Z")), "does not end after it starts"),
        (_gap(audit_ref=_ref("2001-01-01T00:02:10Z..2001-01-01T00:04:00Z")),
         "not inside the audit row's scope, rounded out to whole minutes: 2001-01-01T00:02Z to 2001-01-01T00:04Z"),
        (_gap(audit_ref=_REF + " extra"), "is not 'lake.audit.checks"),
        ("2001-01-01T00:01Z", "not an object"),
    ],
)
def test_a_gap_that_breaks_a_rule_stops_the_run(gap, message):
    with pytest.raises(SystemExit, match=message):
        load_gaps([gap], _DAY)


def test_a_second_gap_or_not_a_list_stops_the_run():
    with pytest.raises(SystemExit, match="at most 1: a second incident .* ADR-029's 2026-11-26 Outcome"):
        load_gaps([_gap(), _gap(start="2001-01-01T00:02Z", end="2001-01-01T00:03Z")], _DAY)
    with pytest.raises(SystemExit, match="not a list"):
        load_gaps(_gap(), _DAY)


def test_the_audit_scope_is_rounded_out_to_whole_minutes():
    # The 2026-11-25 shape: capture went off at :43 seconds, so that whole minute is in scope; the next is not.
    ref = _ref("2001-01-01T00:01:43Z..2001-01-01T00:01:50Z")
    assert load_gaps([_gap(audit_ref=ref)], _DAY)[0]["hi"] == datetime(2001, 1, 1, 0, 2)
    with pytest.raises(SystemExit, match="not inside the audit row's scope"):
        load_gaps([_gap(end="2001-01-01T00:03Z", audit_ref=ref)], _DAY)


def test_the_widest_gap_is_sixty_minutes_and_it_may_end_at_midnight():
    ref = _ref("2001-01-01T22:59:59Z..2001-01-02T00:00:00Z")
    (gap,) = load_gaps([_gap(start="2001-01-01T23:00Z", end="2001-01-02T00:00Z", audit_ref=ref)], _DAY)
    assert (gap["lo"], gap["hi"]) == (datetime(2001, 1, 1, 23), datetime(2001, 1, 2))


def test_the_committed_pins_known_gaps_are_valid_for_its_day():
    # CI's half of the rules: a hand-edited pin that breaks one fails here, not first on the live gate.
    pin = json.loads((Path(__file__).parent / "parity" / "pinned.json").read_text())
    load_gaps(pin.get("known_gaps", []), pin["day"])


@pytest.mark.parametrize("pin_current", [False, True])
def test_the_wrapper_passes_the_pins_known_gaps(tmp_path, pin_current):
    # Both forms: a day with a listed gap can only be pinned if --pin-current forwards it.
    pin = {"day": _DAY, "ohlcv_1m_snapshot": 1, "silver_snapshots": {"x": 5}, "known_gaps": [_gap()]}
    given = _wrapper(tmp_path, *(["--pin-current", _DAY] if pin_current else []), pin=pin)
    assert json.loads(given[given.index("--known-gaps") + 1]) == [_gap()]


# ─────────────────────────────────────────────────────────────────────────────
# The audit row a known gap names: scripts/parity_ohlcv.py confirm_audit_row
#
# load_gaps checks `audit_ref` as text, which an invented reference of the right
# shape passes. Against the stack the run reads lake.audit.checks for exactly the
# job, check_name and scope the reference names, before anything is compared.
# "Could not read" and "no row" are different failures, and neither passes.
# ─────────────────────────────────────────────────────────────────────────────

from parity_ohlcv import confirm_audit_row  # noqa: E402


def test_a_present_failed_operator_row_lets_the_gap_through_and_is_asked_for_exactly(capsys):
    c = _Q()
    confirm_audit_row(c, _REF)
    assert c.asked == [["operator", "capture_outage", "stack/2001-01-01T00:00:30Z..2001-01-01T00:04:10Z"]]
    assert f"audit row found, {_REF}" in capsys.readouterr().out


def test_a_missing_audit_row_stops_the_run():
    with pytest.raises(SystemExit, match="lake.audit.checks has no row job=operator check_name=capture_outage scope=stack/"):
        confirm_audit_row(_Q(audit=[]), _REF)


@pytest.mark.parametrize(
    "ref,row",
    [
        (_REF, ("operator", True)),  # it passed
        (_REF, ("operator", None)),  # no verdict: NULL is not a failure
        (_REF.replace("job=operator", "job=decode"), ("decode", False)),  # a real failed row, but a job's, not an operator's
    ],
)
def test_a_row_that_is_not_a_failed_operator_row_stops_the_run(ref, row):
    with pytest.raises(SystemExit, match="is not a failed operator row"):
        confirm_audit_row(_Q(audit=[row]), ref)


def test_an_unreachable_lake_is_an_error_not_a_pass_and_not_a_missing_row():
    down = RuntimeError("IO Error: Connection refused, http://localhost:18181/catalog/v1/config")
    with pytest.raises(SystemExit, match="could not read lake.audit.checks .* Connection refused") as stop:
        confirm_audit_row(_Q(audit=down), _REF)
    assert "has no row" not in str(stop.value.code)


def test_main_stops_on_an_invented_audit_ref_before_comparing_anything(monkeypatch, capsys):
    # A reference of the right shape that names no row: load_gaps accepts it, the table does not.
    with pytest.raises(SystemExit, match="has no row"):
        _main(monkeypatch, capsys, [_gap()], clickhouse=_differs_at(1), audit=[])
    assert "vs" not in capsys.readouterr().out, "no comparison may run before the row is found"



# ─────────────────────────────────────────────────────────────────────────────
# The dbt side's pin: `dbt_trades_snapshot` in tests/parity/pinned.json
#
# Recorded by --dbt --write-pin while every source snapshot is held, passed back by
# the wrapper, and required, never re-derived, on every --dbt run.
# ─────────────────────────────────────────────────────────────────────────────


def test_a_dbt_run_whose_pin_lacks_the_dbt_snapshot_stops_and_says_to_re_pin(monkeypatch, capsys):
    with pytest.raises(SystemExit, match=r"the pin has no dbt_trades_snapshot.*--pin-current <day> --dbt"):
        _main(monkeypatch, capsys, [], flags=("--dbt",))


@pytest.mark.parametrize("dbt", [True, False])
def test_write_pin_records_the_dbt_snapshot_with_dbt_and_drops_a_stale_one_without(monkeypatch, capsys, tmp_path, dbt):
    pin = tmp_path / "tests/parity/pinned.json"
    pin.parent.mkdir(parents=True)
    pin.write_text(json.dumps({"dbt_trades_snapshot": 5, "bars": {"day": _DAY}}))
    monkeypatch.chdir(tmp_path)
    code, _ = _main(monkeypatch, capsys, [], flags=("--dbt", "--write-pin") if dbt else ("--write-pin",))
    assert code == 0
    doc = json.loads(pin.read_text())
    assert doc.get("dbt_trades_snapshot") == (7 if dbt else None)  # 7: the stand-in's only source snapshot
    assert doc["bars"] == {"day": _DAY}


@pytest.mark.parametrize("pin_current", [False, True])
def test_the_wrapper_passes_the_pins_dbt_snapshot_but_not_when_re_pinning(tmp_path, pin_current):
    pin = {"day": _DAY, "ohlcv_1m_snapshot": 1, "silver_snapshots": {"x": 5}, "dbt_trades_snapshot": 6911535457573800498}
    given = _wrapper(tmp_path, *(["--pin-current", _DAY] if pin_current else []), "--dbt", pin=pin)
    if pin_current:
        assert "--dbt-trades-snapshot" not in given  # a new day: --write-pin records a new one
    else:
        assert given[given.index("--dbt-trades-snapshot") + 1] == "6911535457573800498"
        assert given[-1] == "--dbt"


def test_the_committed_pin_records_the_dbt_snapshot():
    # Verified read-only 2026-11-26: the newest of the day's source snapshots, all held, and tagged parity_2026_11_25_dbt.
    pin = json.loads((Path(__file__).parent / "parity" / "pinned.json").read_text())
    assert isinstance(pin["dbt_trades_snapshot"], int)
