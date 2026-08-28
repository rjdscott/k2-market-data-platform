//! `k2-capture` - the v3 market-data capture tier.
//!
//! One process per venue, one WebSocket per process, three topics out:
//! `market.crypto.v3.{raw,trades,book}.<exchange>`. The raw topic is the system
//! of record (ADR-018); trades and book snapshots are derived from it, so a bug
//! in normalisation is repairable by reprocessing rather than by losing the day.
//!
//! The library half exists so `tests/replay.rs` can drive the same adapter code
//! the binary runs. Everything that touches the network lives in [`ws`] and
//! [`sink`]; everything an adapter does is pure, which is what makes replay
//! byte-for-byte reproducible.

pub mod book;
pub mod config;
pub mod decimal;
pub mod exchanges;
pub mod metrics;
pub mod record;
pub mod replay;
pub mod resync;
pub mod sink;
pub mod ws;
