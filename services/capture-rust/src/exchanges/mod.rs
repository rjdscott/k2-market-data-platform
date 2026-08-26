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

pub mod kraken;

use crate::config::Exchange;
use crate::record::{BookSnapshotRecord, OutRecord};

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
}

/// One venue's frame decoder and book state machine.
#[derive(Debug)]
pub enum Adapter {
    Kraken(KrakenAdapter),
    // Binance(BinanceAdapter),   - Phase C, separate change
    // Coinbase(CoinbaseAdapter), - Phase C, separate change
}

impl Adapter {
    pub fn exchange(&self) -> Exchange {
        match self {
            Adapter::Kraken(_) => Exchange::Kraken,
        }
    }

    /// Reset every per-connection fact: book state, sequence counters, and the
    /// `conn_id` that stamps each record. Called once per (re)connect, before
    /// the first frame.
    pub fn begin_connection(&mut self, conn_id: &str) {
        match self {
            Adapter::Kraken(a) => a.begin_connection(conn_id),
        }
    }

    /// The frames to send immediately after the socket opens, in order.
    pub fn subscribe_messages(&self) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.subscribe_messages(),
        }
    }

    /// The frames that re-establish one symbol's book after an
    /// [`Action::Resubscribe`].
    pub fn resubscribe_messages(&self, native_symbol: &str) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.resubscribe_messages(native_symbol),
        }
    }

    /// Decode one frame. Pure - see the module docs.
    pub fn handle_frame(&mut self, bytes: &[u8], recv_ts_ns: i64) -> Handled {
        match self {
            Adapter::Kraken(a) => a.handle_frame(bytes, recv_ts_ns),
        }
    }

    /// Take a top-20 snapshot of one symbol's book at `now_ns`, or `None` if
    /// there is no book yet. Called by the sampler, never by the frame path.
    pub fn snapshot(&self, native_symbol: &str, now_ns: i64) -> Option<BookSnapshotRecord> {
        match self {
            Adapter::Kraken(a) => a.snapshot(native_symbol, now_ns),
        }
    }

    /// Native symbols this adapter subscribes to, in registry order.
    pub fn symbols(&self) -> Vec<String> {
        match self {
            Adapter::Kraken(a) => a.symbols(),
        }
    }

    /// Total resting book levels across all symbols - the
    /// `k2_capture_book_levels_total` gauge.
    pub fn total_levels(&self) -> usize {
        match self {
            Adapter::Kraken(a) => a.total_levels(),
        }
    }

    /// Resting levels for one symbol - the `k2_capture_book_depth` gauge.
    pub fn depth(&self, native_symbol: &str) -> Option<usize> {
        match self {
            Adapter::Kraken(a) => a.depth(native_symbol),
        }
    }
}
