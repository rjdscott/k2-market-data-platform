#!/usr/bin/env python3
"""
An L2 order book replayed from venue frames, with Kraken's CRC32 verification —
the pure core of the silver/gold book layers. No Spark, no pandas; the Spark
side (books.py) streams frames through it one connection at a time.

Everything is 1e-8 fixed point (int), exactly as services/capture-rust/src/book.rs
and decimal.rs: a float round trip is lossy past 15 significant digits and would
desync the book while the checksum reported success (ADR-018 Appendix A, S1).

Kraken v2 checksum (docs.kraken.com/api/docs/guides/spot-ws-book-v2): the top 10
asks then the top 10 bids, each level as price then qty formatted to the pair's
precision with the decimal point removed and leading zeros stripped, all
concatenated, CRC32. Asks first — swapping the sides gives a plausible wrong
number, which is why the doc's worked example (3310070434) is the unit test.

tests/test_lake_book.py runs this file against that example and the capture's
own invariants.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

SCALE_DP = 8
SCALE = 10**SCALE_DP
CHECKSUM_LEVELS = 10


def fixed(text: str) -> int:
    """A venue decimal string -> 1e-8 units, exactly. Raises on more than 8 decimals."""
    neg = text.startswith("-")
    if neg:
        text = text[1:]
    whole, _, frac = text.partition(".")
    if len(frac) > SCALE_DP:
        raise ValueError(f"{text!r}: more than {SCALE_DP} decimals")
    units = int(whole or "0") * SCALE + int((frac + "0" * SCALE_DP)[:SCALE_DP])
    return -units if neg else units


def checksum_digits(units: int, precision: int) -> str:
    """`units` at the pair's precision, decimal point dropped, leading zeros stripped.

    Same arithmetic as decimal.rs::checksum_digits: integer division by
    10^(8 - precision), then str(). 4528520000000 @ precision 1 -> "452852".
    """
    return str(units // 10 ** (SCALE_DP - precision))


def kraken_checksum(asks: list, bids: list, price_precision: int, qty_precision: int) -> int:
    """CRC32 over the top-10 asks then top-10 bids, as `(px, qty)` pairs best-first."""
    parts = []
    for side in (asks, bids):
        for px, qty in side[:CHECKSUM_LEVELS]:
            parts.append(checksum_digits(px, price_precision))
            parts.append(checksum_digits(qty, qty_precision))
    return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF


@dataclass
class Book:
    """Absolute-quantity levels per side; qty 0 removes. `top()` is best-first."""

    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, side: str, px: int, qty: int) -> None:
        levels = self.bids if side == "bid" else self.asks
        if qty == 0:
            levels.pop(px, None)
        else:
            levels[px] = qty

    def truncate(self, depth: int) -> None:
        """Keep the best `depth` levels per side.

        A depth-limited subscription reports only levels inside its depth; a
        level that drifted past it will never be deleted by the venue, so it has
        to go now or it corrupts every later checksum (book.rs::truncate).
        """
        if len(self.bids) > depth:
            for px in sorted(self.bids)[: len(self.bids) - depth]:
                del self.bids[px]
        if len(self.asks) > depth:
            for px in sorted(self.asks, reverse=True)[: len(self.asks) - depth]:
                del self.asks[px]

    def top(self, n: int) -> tuple:
        """`(bids, asks)` as `[(px, qty), ...]`, best first, at most n each."""
        bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[:n]
        asks = sorted(self.asks.items())[:n]
        return bids, asks

    def depth(self) -> int:
        return max(len(self.bids), len(self.asks))


def kraken_apply(book: Book, frame_type: str, bids: list, asks: list, depth: int) -> None:
    """Fold one Kraken `book` frame in: a snapshot replaces, an update folds, then truncate to the subscription depth."""
    if frame_type == "snapshot":
        book.clear()
    for px, qty in bids:
        book.apply("bid", px, qty)
    for px, qty in asks:
        book.apply("ask", px, qty)
    book.truncate(depth)
