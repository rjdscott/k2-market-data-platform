//! An L2 order book held as two `BTreeMap<price, qty>` in 1e-8 fixed point.
//!
//! `BTreeMap` and not `HashMap` for two reasons that are both requirements
//! rather than preferences: the book has to be walked in price order to produce
//! a top-N snapshot or a checksum, and replay has to produce byte-identical
//! output, which rules out any iteration order that depends on a hasher seed.
//!
//! Updates are **absolute quantities**, not deltas - the venues K2 captures all
//! publish "this level is now N", and a zero means the level is gone. Nothing in
//! here is exchange-specific; Kraken's checksum lives in the adapter that knows
//! what a checksum is.

use std::collections::BTreeMap;

/// One side's top-N levels as parallel arrays, in the order
/// `BookSnapshotL2.bid_px` / `bid_qty` want them.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TopN {
    pub bid_px: Vec<i64>,
    pub bid_qty: Vec<i64>,
    pub ask_px: Vec<i64>,
    pub ask_qty: Vec<i64>,
}

impl TopN {
    /// `BookSnapshotL2.depth`: the longer of the two sides, because a thin book
    /// can legitimately hold fewer levels on one side and a consumer must be
    /// able to tell that from "we dropped some".
    pub fn depth(&self) -> i32 {
        self.bid_px.len().max(self.ask_px.len()) as i32
    }
}

/// Which side of the book a level sits on. Coinbase spells the ask side
/// `offer`; adapters normalise to this enum so the book never learns a venue's
/// vocabulary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Bid,
    Ask,
}

#[derive(Debug, Default)]
pub struct Book {
    /// price -> qty, both 1e-8 fixed point. Ascending by price; bids are read
    /// back in reverse.
    bids: BTreeMap<i64, i64>,
    asks: BTreeMap<i64, i64>,
}

impl Book {
    pub fn new() -> Self {
        Self::default()
    }

    /// Drop every level. Called when a venue sends a fresh snapshot, or when a
    /// checksum mismatch means the local book can no longer be trusted.
    pub fn clear(&mut self) {
        self.bids.clear();
        self.asks.clear();
    }

    /// Apply one absolute-quantity level update. `qty == 0` removes the level.
    ///
    /// Negative quantities are not representable here as anything meaningful,
    /// so they are treated as a removal in release builds and trip an assertion
    /// in debug ones - a negative resting size is a parse bug upstream.
    pub fn apply(&mut self, side: Side, px: i64, qty: i64) {
        debug_assert!(qty >= 0, "negative resting quantity {qty} at {px}");
        let levels = match side {
            Side::Bid => &mut self.bids,
            Side::Ask => &mut self.asks,
        };
        if qty <= 0 {
            levels.remove(&px);
        } else {
            levels.insert(px, qty);
        }
        self.debug_assert_invariants();
    }

    /// Drop everything past the best `depth` levels on each side.
    ///
    /// **This is not an optimisation, it is correctness.** A depth-limited
    /// subscription (Kraken `book depth=25`) only publishes updates for levels
    /// inside that window: when a level falls out of the window the venue sends
    /// no deletion for it, because as far as the subscription is concerned it
    /// no longer exists. Keeping it means a stale price sits in the book until
    /// the market moves back and it silently re-enters the top 10 - at which
    /// point the checksum starts failing and the resync loop never converges.
    pub fn truncate(&mut self, depth: usize) {
        while self.bids.len() > depth {
            self.bids.pop_first(); // the worst bid is the lowest price
        }
        while self.asks.len() > depth {
            self.asks.pop_last(); // the worst ask is the highest price
        }
    }

    pub fn is_empty(&self) -> bool {
        self.bids.is_empty() && self.asks.is_empty()
    }

    /// Total resting levels across both sides - the `k2_capture_book_depth`
    /// gauge, and the number Coinbase's 44k-level snapshot is sized against.
    pub fn len(&self) -> usize {
        self.bids.len() + self.asks.len()
    }

    pub fn best_bid(&self) -> Option<i64> {
        self.bids.keys().next_back().copied()
    }

    pub fn best_ask(&self) -> Option<i64> {
        self.asks.keys().next().copied()
    }

    /// Best `n` levels per side: bids descending, asks ascending.
    pub fn top_n(&self, n: usize) -> TopN {
        let mut out = TopN::default();
        for (&px, &qty) in self.bids.iter().rev().take(n) {
            out.bid_px.push(px);
            out.bid_qty.push(qty);
        }
        for (&px, &qty) in self.asks.iter().take(n) {
            out.ask_px.push(px);
            out.ask_qty.push(qty);
        }
        out
    }

    /// Best `n` levels per side as `(price, qty)` pairs, bids descending and
    /// asks ascending - the shape Kraken's checksum walks.
    pub fn top_pairs(&self, side: Side, n: usize) -> Vec<(i64, i64)> {
        match side {
            Side::Bid => self
                .bids
                .iter()
                .rev()
                .take(n)
                .map(|(&p, &q)| (p, q))
                .collect(),
            Side::Ask => self.asks.iter().take(n).map(|(&p, &q)| (p, q)).collect(),
        }
    }

    /// Invariants that hold for every well-formed book, checked only in debug
    /// and test builds. In release the cost is not worth paying on every level
    /// update; a crossed book shows up in the data as a negative spread, and a
    /// venue that crosses its own book briefly (Kraken does, between two frames
    /// of a large sweep) must not take the capture process down.
    fn debug_assert_invariants(&self) {
        #[cfg(debug_assertions)]
        {
            debug_assert!(
                self.bids.values().all(|&q| q > 0) && self.asks.values().all(|&q| q > 0),
                "a zero-quantity level survived an update"
            );
            if let (Some(b), Some(a)) = (self.best_bid(), self.best_ask()) {
                debug_assert!(b < a, "crossed book: best bid {b} >= best ask {a}");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn book_of(bids: &[(i64, i64)], asks: &[(i64, i64)]) -> Book {
        let mut b = Book::new();
        for &(px, qty) in bids {
            b.apply(Side::Bid, px, qty);
        }
        for &(px, qty) in asks {
            b.apply(Side::Ask, px, qty);
        }
        b
    }

    #[test]
    fn apply_insert_update_remove() {
        let mut b = book_of(&[(100, 5), (99, 7)], &[(101, 3)]);
        assert_eq!(b.len(), 3);
        assert_eq!(b.best_bid(), Some(100));
        assert_eq!(b.best_ask(), Some(101));

        b.apply(Side::Bid, 100, 9); // absolute replace, not an add
        assert_eq!(b.top_n(1).bid_qty, vec![9]);

        b.apply(Side::Bid, 100, 0); // zero removes
        assert_eq!(b.best_bid(), Some(99));
        assert_eq!(b.len(), 2);
    }

    #[test]
    fn top_n_is_ordered_and_truncated() {
        let b = book_of(
            &[(100, 1), (98, 1), (99, 1)],
            &[(103, 1), (101, 1), (102, 1)],
        );
        let t = b.top_n(2);
        assert_eq!(t.bid_px, vec![100, 99], "bids best first = descending");
        assert_eq!(t.ask_px, vec![101, 102], "asks best first = ascending");
        assert_eq!(t.depth(), 2);
    }

    #[test]
    fn truncate_keeps_the_best_levels_on_each_side() {
        let mut b = book_of(
            &[(100, 1), (99, 1), (98, 1)],
            &[(101, 1), (102, 1), (103, 1)],
        );
        b.truncate(2);
        assert_eq!(b.top_n(9).bid_px, vec![100, 99], "worst bid dropped");
        assert_eq!(b.top_n(9).ask_px, vec![101, 102], "worst ask dropped");
        b.truncate(9);
        assert_eq!(b.len(), 4, "truncating deeper than the book is a no-op");
    }

    #[test]
    fn depth_reports_the_longer_side() {
        let b = book_of(&[(100, 1), (99, 1)], &[(101, 1)]);
        let t = b.top_n(20);
        assert_eq!(t.depth(), 2);
        assert_eq!(
            t.ask_px.len(),
            1,
            "a thin side stays short, it is not padded"
        );
    }

    // Bids and asks are generated from disjoint price ranges so that a random
    // apply sequence can never legitimately cross the book; anything crossed is
    // then a real bug rather than a badly chosen input.
    proptest! {
        #[test]
        fn invariants_survive_random_updates(
            ops in prop::collection::vec(
                (any::<bool>(), 1i64..1000, 0i64..5),
                0..300,
            )
        ) {
            let mut b = Book::new();
            for (is_bid, px, qty) in ops {
                if is_bid {
                    b.apply(Side::Bid, px, qty);
                } else {
                    b.apply(Side::Ask, px + 1000, qty);
                }
            }
            let t = b.top_n(20);
            prop_assert!(t.bid_qty.iter().all(|&q| q > 0), "zero qty in snapshot");
            prop_assert!(t.ask_qty.iter().all(|&q| q > 0), "zero qty in snapshot");
            prop_assert!(t.bid_px.windows(2).all(|w| w[0] > w[1]), "bids not strictly descending");
            prop_assert!(t.ask_px.windows(2).all(|w| w[0] < w[1]), "asks not strictly ascending");
            if let (Some(&bb), Some(&ba)) = (t.bid_px.first(), t.ask_px.first()) {
                prop_assert!(bb < ba, "crossed book");
            }
            prop_assert!(t.bid_px.len() <= 20 && t.ask_px.len() <= 20);
        }

        /// A deeper `top_n` is a prefix-preserving extension of a shallower one.
        #[test]
        fn top_n_is_monotonic(
            levels in prop::collection::vec((1i64..1000, 1i64..5), 0..100)
        ) {
            let mut b = Book::new();
            for (px, qty) in levels {
                b.apply(Side::Bid, px, qty);
                b.apply(Side::Ask, px + 1000, qty);
            }
            let small = b.top_n(5);
            let large = b.top_n(20);
            prop_assert!(large.bid_px.len() >= small.bid_px.len());
            prop_assert_eq!(&large.bid_px[..small.bid_px.len()], &small.bid_px[..]);
            prop_assert_eq!(&large.ask_px[..small.ask_px.len()], &small.ask_px[..]);
        }
    }
}
