//! The adapter contract: bytes in, records out, nothing else.
//!
//! # Why an enum and not a trait
//!
//! There are exactly three venues and there will not be a fourth this year. A
//! `trait Adapter` would buy dynamic dispatch nobody needs, invite a
//! `Box<dyn Adapter>` in the hot loop, and - the real cost - make it possible
//! to write a mock adapter, at which point the tests stop exercising the code
//! that runs in production. An enum keeps every implementation visible from one
//! `match`, and adding Binance means the compiler lists every place that has to
//! learn about it.
//!
//! # The contract an adapter must honour
//!
//! `handle_frame` is **pure**: no I/O, no clock reads, no randomness, no
//! iteration over a `HashMap`. Everything time-dependent is passed in
//! (`recv_ts_ns`) or asked for explicitly (`snapshot(symbol, now_ns)`). This is
//! not style - it is what makes `k2-replay` (Phase G) able to feed the archived
//! frames back through *this* code and assert the output is byte-identical
//! (`docs/research/2026-08-26-v3-requirements-clarification.md`, Q1). An adapter
//! that reads the clock produces a different answer on every replay and the
//! parity test becomes decorative.
//!
//! Concretely, an adapter must:
//!
//! 1. Emit a [`OutRecord::Raw`] for **every** frame, first, before any record
//!    derived from it, with the payload byte-for-byte as received - including
//!    frames it cannot parse. The archive is the system of record; a frame we
//!    failed to understand is precisely the one worth keeping.
//! 2. Own `conn_msg_seq`, incrementing it once per frame, reset by
//!    [`Adapter::begin_connection`]. `conn_id` comes from the caller.
//! 3. Keep book state internally and expose it only through
//!    [`Adapter::snapshot`], which the sampler in `main.rs` drives. The adapter
//!    never decides *when* to emit a snapshot.
//! 4. Return an [`Action`] rather than performing it. Sending a resubscribe is
//!    I/O and belongs to the caller.
//!
//! Counters (`metrics::counter!`) are permitted inside an adapter: they are
//! write-only, never read back, and cannot influence the records produced, so
//! they do not compromise determinism.

pub mod binance;
pub mod coinbase;
pub mod kraken;

use crate::config::Exchange;
use crate::decimal::DecimalError;
use crate::record::{BookSnapshotRecord, OutRecord};

pub use binance::BinanceAdapter;
pub use coinbase::CoinbaseAdapter;
pub use kraken::KrakenAdapter;

/// Something the adapter needs the connection to do, which the adapter cannot
/// do itself because it performs no I/O.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// The local book for this native symbol can no longer be trusted - drop
    /// the subscription and take a fresh snapshot. Kraken raises this on a
    /// checksum mismatch; the messages to send come from
    /// [`Adapter::resubscribe_messages`].
    Resubscribe(String),
    /// Connection-wide sequencing broke; only a fresh socket repairs it -
    /// Coinbase. The caller drops the connection and takes the reconnect
    /// path; the adapter has already dropped every book.
    Reconnect,
}

/// The result of one frame.
#[derive(Debug, Default)]
pub struct Handled {
    /// Which subscription the frame arrived on, as the venue names it
    /// (`book`, `trade`, `instrument`, `heartbeat`, `status`, `control`). This
    /// is the `stream` label on `k2_capture_messages_total`.
    pub stream: String,
    /// Raw first, then anything derived from it, in emission order.
    pub records: Vec<OutRecord>,
    pub actions: Vec<Action>,
    /// The frame is the venue replaying history on subscribe — Coinbase's
    /// `market_trades` `snapshot` event carries the last hour or two of trades.
    /// They are archived and emitted like any other (they arrived), but their
    /// `exchange_ts` is hours old, so they must not be observed as receive
    /// latency: measured 2026-08-27, 24,706 such trades put Coinbase's p99 at
    /// the histogram's 30 s ceiling while its live p99 was 0.47 s
    /// (docs/benchmarks/2026-08-27.md). Silver flags them as `venue_replay`.
    pub history: bool,
}

/// One venue's frame decoder and book state machine.
#[derive(Debug)]
pub enum Adapter {
    Kraken(KrakenAdapter),
    Binance(BinanceAdapter),
    Coinbase(CoinbaseAdapter),
}

impl Adapter {
    pub fn exchange(&self) -> Exchange {
        match self {
            Adapter::Kraken(_) => Exchange::Kraken,
            Adapter::Binance(_) => Exchange::Binance,
            Adapter::Coinbase(_) => Exchange::Coinbase,
        }
    }

    /// Reset every per-connection fact: book state, sequence counters, and the
    /// `conn_id` that stamps each record. Called once per (re)connect, before
    /// the first frame.
    pub fn begin_connection(&mut self, conn_id: &str) {
        match self {
            Adapter::Kraken(a) => a.begin_connection(conn_id),
            Adapter::Binance(a) => a.begin_connection(conn_id),
            Adapter::Coinbase(a) => a.begin_connection(conn_id),
        }
    }

    /// The endpoint to dial. Binance carries its subscription in the URL
    /// (`/stream?streams=a/b/c`); the others return `base` unchanged.
    pub fn ws_url(&self, base: &str) -> String {
        match self {
            Adapter::Binance(a) => format!("{base}?streams={}", a.stream_query()),
            Adapter::Kraken(_) | Adapter::Coinbase(_) => base.to_string(),
        }
    }

    /// The frames to send immediately after the socket opens, in order.
    pub fn subscribe_messages(&self) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.subscribe_messages(),
            Adapter::Binance(a) => a.subscribe_messages(),
            Adapter::Coinbase(a) => a.subscribe_messages(),
        }
    }

    /// The frames that re-establish one symbol's book after an
    /// [`Action::Resubscribe`].
    pub fn resubscribe_messages(&self, native_symbol: &str) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.resubscribe_messages(native_symbol),
            Adapter::Binance(a) => a.resubscribe_messages(native_symbol),
            Adapter::Coinbase(a) => a.resubscribe_messages(native_symbol),
        }
    }

    /// Decode one frame. Pure - see the module docs.
    pub fn handle_frame(&mut self, bytes: &[u8], recv_ts_ns: i64) -> Handled {
        match self {
            Adapter::Kraken(a) => a.handle_frame(bytes, recv_ts_ns),
            Adapter::Binance(a) => a.handle_frame(bytes, recv_ts_ns),
            Adapter::Coinbase(a) => a.handle_frame(bytes, recv_ts_ns),
        }
    }

    /// Take a top-20 snapshot of one symbol's book at `now_ns`, or `None` if
    /// there is no book yet. Called by the sampler, never by the frame path.
    /// Levels per side the next snapshots carry. Live capture never calls
    /// this (20 is the product, ADR-027); `replay --depth` does.
    pub fn set_snapshot_depth(&mut self, depth: usize) {
        match self {
            Adapter::Kraken(a) => a.set_snapshot_depth(depth),
            Adapter::Binance(a) => a.set_snapshot_depth(depth),
            Adapter::Coinbase(a) => a.set_snapshot_depth(depth),
        }
    }

    pub fn snapshot(&self, native_symbol: &str, now_ns: i64) -> Option<BookSnapshotRecord> {
        match self {
            Adapter::Kraken(a) => a.snapshot(native_symbol, now_ns),
            Adapter::Binance(a) => a.snapshot(native_symbol, now_ns),
            Adapter::Coinbase(a) => a.snapshot(native_symbol, now_ns),
        }
    }

    /// Native symbols this adapter subscribes to, in registry order.
    pub fn symbols(&self) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.symbols(),
            Adapter::Binance(a) => a.symbols(),
            Adapter::Coinbase(a) => a.symbols(),
        }
    }

    /// Total resting book levels across all symbols - the
    /// `k2_capture_book_levels_total` gauge.
    pub fn total_levels(&self) -> usize {
        match self {
            Adapter::Kraken(a) => a.total_levels(),
            Adapter::Binance(a) => a.total_levels(),
            Adapter::Coinbase(a) => a.total_levels(),
        }
    }

    /// Resting levels for one symbol - the `k2_capture_book_depth` gauge.
    pub fn depth(&self, native_symbol: &str) -> Option<usize> {
        match self {
            Adapter::Kraken(a) => a.depth(native_symbol),
            Adapter::Binance(a) => a.depth(native_symbol),
            Adapter::Coinbase(a) => a.depth(native_symbol),
        }
    }
}

// ── helpers shared by every adapter ─────────────────────────────────────────

/// RFC 3339 to microseconds since the epoch. Kraken writes microseconds;
/// Coinbase writes nanoseconds on the frame `timestamp` and microseconds on
/// `event_time` / trade `time`. Everything truncates to micros, the Avro
/// logical type. `None` on anything else - a timestamp we cannot read is not
/// a timestamp to invent.
pub(crate) fn parse_micros(ts: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(ts)
        .ok()
        .map(|dt| dt.timestamp_micros())
}

/// `k2_capture_precision_loss_total` - a decimal the 1e-8 contract cannot hold
/// exactly. The record is dropped, never rounded: a rounded price is a wrong
/// price that looks right forever, a counter is a bug someone fixes.
///
/// No `symbol` label: five reasons times eleven instruments is fifty-five
/// series for something that should never tick, and the offending symbol and
/// text are already in the log line next to every increment.
pub(crate) fn count_decimal_error(exchange: &'static str, e: DecimalError) {
    let reason = match e {
        DecimalError::TooManyDecimals => "too_many_dp",
        DecimalError::Scientific => "scientific",
        DecimalError::Malformed => "malformed",
        DecimalError::Overflow => "overflow",
        DecimalError::Negative => "negative",
    };
    metrics::counter!(
        "k2_capture_precision_loss_total",
        "exchange" => exchange,
        "reason" => reason,
    )
    .increment(1);
}

/// `k2_capture_unknown_frames_total` - a frame we archived but did not
/// understand. Not a drop: the raw record is already emitted. A venue that
/// starts sending a new channel should show up on a graph before it shows up
/// in a reconciliation.
pub(crate) fn count_unknown(exchange: &'static str, stream: &str) {
    metrics::counter!(
        "k2_capture_unknown_frames_total",
        "exchange" => exchange,
        "stream" => stream.to_string(),
    )
    .increment(1);
}

#[cfg(test)]
mod tests {
    use super::parse_micros;

    #[test]
    fn parse_micros_truncates_nanos_and_rejects_junk() {
        assert_eq!(
            parse_micros("2026-08-26T12:13:45.754166Z"),
            Some(1_787_746_425_754_166)
        );
        assert_eq!(
            parse_micros("2026-08-26T12:13:45.818626985Z"),
            Some(1_787_746_425_818_626),
            "nanoseconds truncate, never round"
        );
        assert_eq!(parse_micros("2026-08-26 12:13:45"), None);
        assert_eq!(parse_micros(""), None);
    }
}
