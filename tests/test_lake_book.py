"""book.py against Kraken's published worked example and the capture's invariants."""

import book as B
import pytest

# docs.kraken.com/api/docs/guides/spot-ws-book-v2, BTC/USD, price precision 1, qty precision 8.
ASKS = [
    ("45285.2", "0.00100000"), ("45286.4", "1.54571953"), ("45286.6", "1.54571109"), ("45289.6", "1.54560911"),
    ("45290.2", "0.15890660"), ("45291.8", "1.54553491"), ("45294.7", "0.04454749"), ("45296.1", "0.35380000"),
    ("45297.5", "0.09945542"), ("45299.5", "0.18772827"),
]
BIDS = [
    ("45283.5", "0.10000000"), ("45283.4", "1.54582015"), ("45282.1", "0.10000000"), ("45281.0", "0.10000000"),
    ("45280.3", "1.54592586"), ("45279.0", "0.07990000"), ("45277.6", "0.03310103"), ("45277.5", "0.30000000"),
    ("45277.3", "1.54602737"), ("45276.6", "0.15445238"),
]


def levels(pairs):
    return [(B.fixed(p), B.fixed(q)) for p, q in pairs]


def test_fixed_is_exact_and_refuses_nine_decimals():
    assert B.fixed("45285.2") == 4_528_520_000_000
    assert B.fixed("0.00184") == 184_000
    assert B.fixed("0") == 0 and B.fixed("100") == 10_000_000_000
    with pytest.raises(ValueError):
        B.fixed("0.123456789")


def test_checksum_digits_match_the_capture():
    assert B.checksum_digits(4_528_520_000_000, 1) == "452852"
    assert B.checksum_digits(100_000, 8) == "100000"
    assert B.checksum_digits(18_772_827, 8) == "18772827"
    assert B.checksum_digits(0, 8) == "0"


def test_kraken_doc_example_checksum():
    assert B.kraken_checksum(levels(ASKS), levels(BIDS), 1, 8) == 3310070434
    # Sides swapped is a plausible-looking wrong number, never the right one.
    assert B.kraken_checksum(levels(BIDS), levels(ASKS), 1, 8) != 3310070434


def test_book_replay_reproduces_the_snapshot_checksum_and_truncates():
    b = B.Book()
    B.kraken_apply(b, "snapshot", levels(BIDS), levels(ASKS), depth=25)
    bids, asks = b.top(10)
    assert B.kraken_checksum(asks, bids, 1, 8) == 3310070434
    # an update: remove the best ask (qty 0), add a worse bid, then a zero-qty on an unknown level is a no-op
    B.kraken_apply(b, "update", [(B.fixed("45270.0"), B.fixed("1"))], [(B.fixed("45285.2"), 0), (B.fixed("1.0"), 0)], depth=25)
    bids, asks = b.top(10)
    assert asks[0][0] == B.fixed("45286.4") and bids[-1][0] == B.fixed("45276.6") and len(asks) == 9
    assert b.depth() == 11
    # depth-limited subscription: an 11th bid beyond depth 10 is dropped at truncate
    B.kraken_apply(b, "update", [], [], depth=10)
    assert len(b.bids) == 10 and min(b.bids) == B.fixed("45276.6")
