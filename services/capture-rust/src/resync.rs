//! Resubscribe a symbol's book once the producer queue drains after it dropped
//! one of that symbol's frames.
//!
//! Why: a raw frame dropped on a full producer queue is a frame the archive
//! never gets. The capture's own book is still right — it saw the frame — so
//! it never resubscribes, and the lake's replayed book stays wrong until the
//! connection ends: on 2026-08-26, 386,962 Kraken frames failed the replayed
//! checksum after the `capture-queue-full` / `redpanda-stop` chaos runs, every
//! one downstream of a drop (`docker/lake/README.md` § Books). A fresh snapshot
//! makes the archive's book verifiable again.
//!
//! Why not resubscribe on the drop itself: the queue is full *because* the
//! broker is unreachable or slow, and a Coinbase snapshot is up to 5 MB. Sending
//! it into a full queue drops it too and turns one hole into a storm. So the
//! symbol is remembered and the resubscribe goes out at the first tick after a
//! tick with no drops — when the queue has demonstrably drained.
//!
//! Pure; `main.rs` calls `dropped` from the send path and `drained` from the
//! 1 Hz ticker.

use std::collections::BTreeSet;

#[derive(Debug, Default)]
pub struct ResyncOnDrain {
    pending: BTreeSet<String>,
    dropped_since_tick: bool,
}

impl ResyncOnDrain {
    /// A book frame for `symbol` was dropped by the producer.
    pub fn dropped(&mut self, symbol: &str) {
        self.pending.insert(symbol.to_string());
        self.dropped_since_tick = true;
    }

    /// Called once per ticker tick. Returns the symbols to resubscribe when a
    /// whole tick has passed with no drop; otherwise nothing, and waits.
    pub fn drained(&mut self) -> Vec<String> {
        if self.pending.is_empty() {
            return Vec::new();
        }
        if self.dropped_since_tick {
            self.dropped_since_tick = false;
            return Vec::new();
        }
        std::mem::take(&mut self.pending).into_iter().collect()
    }

    pub fn pending(&self) -> usize {
        self.pending.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resubscribes_once_a_full_tick_passes_without_a_drop() {
        let mut r = ResyncOnDrain::default();
        assert!(r.drained().is_empty(), "nothing pending, nothing to do");
        r.dropped("BTC/USD");
        r.dropped("ETH/USD");
        r.dropped("BTC/USD");
        assert_eq!(r.pending(), 2, "a symbol is remembered once");
        assert!(
            r.drained().is_empty(),
            "the tick the drops happened in: still draining"
        );
        r.dropped("SOL/USD");
        assert!(
            r.drained().is_empty(),
            "a drop during the next tick: still draining"
        );
        assert_eq!(
            r.drained(),
            vec!["BTC/USD", "ETH/USD", "SOL/USD"],
            "a quiet tick: resubscribe all"
        );
        assert_eq!(r.pending(), 0);
        assert!(r.drained().is_empty());
    }
}
