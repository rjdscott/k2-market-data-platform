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
    zeros. float('78600.44') * 1e8 == 7860043999999.999, so a float
    implementation would fail this test — which is why it is here.
"""

import sys
from datetime import datetime, timedelta, timezone
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


@pytest.fixture(autouse=True)
def mock_prefect_run_logger():
    """
    Neutralise the autouse fixture of the same name in tests/conftest.py, which
    patches `prefect.get_run_logger` for the offload-flow tests and makes Prefect
    a hard import for everything in this directory. Nothing here touches Prefect.
    A same-named fixture in a test module overrides the conftest one, autouse
    included — same trick as tests/test_contracts.py.
    """
    yield


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
    assert to_fixed_1e8("78600.44000000") == 7860044000000


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
            "price": "78600.44000000",
            "quantity": "0.01500000",
            "quote_volume": "1179.0066",
            "side": "BUY",
            "exchange_timestamp": 1_700_000_000_123,
        }
    )
    assert n == Norm("BTC/USDT", "5551212", 7860044000000, 1_500_000, "buy", 1_700_000_000_123_000)


def test_normalise_v3_record():
    n = normalise_v3(
        {
            "canonical_symbol": "BTC/USDT",
            "trade_id": 5551212,  # venues emit ints; the contract stringifies
            "price": 7860044000000,
            "qty": 1_500_000,
            "side": "buy",
            "exchange_ts": 1_700_000_000_123_456,
        }
    )
    assert n == Norm("BTC/USDT", "5551212", 7860044000000, 1_500_000, "buy", 1_700_000_000_123_456)


def test_the_two_contracts_meet_on_the_same_tuple():
    """
    The reason a comparison is possible at all: a v2 string price and a v3 int
    price for the same trade must land on the same integer.
    """
    v2 = normalise_v2(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": "78600.44000000",
            "quantity": "0.01500000",
            "side": "BUY",
            "exchange_timestamp": 1_700_000_000_123,
        }
    )
    v3 = normalise_v3(
        {
            "canonical_symbol": "BTC/USD",
            "trade_id": "77",
            "price": 7860044000000,
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
        "price": "78600.44000000",
        "quantity": "0.01500000",
        "side": "BUY",
        "exchange_timestamp": 1_700_000_000_123,
    }
    raw_v3 = {
        "canonical_symbol": "BTC/USDT",
        "trade_id": "5551212",
        "price": 7860044000000,
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
    assert "| symbol | v2 | v3 | Δ | only-v2 | only-v3 | px/qty/side mismatch | verdict |" in out
    assert "**OVERALL: PASS** — 1/1 symbols" in out
    assert "labelled sample, not a soak" in out


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
