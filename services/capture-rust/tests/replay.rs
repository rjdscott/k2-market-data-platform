//! Replay a recorded Kraken session through the live adapter.
//!
//! This is the Phase G parity property in miniature: the same
//! `handle_frame(bytes, recv_ts_ns)` the socket loop calls, fed from a JSONL
//! fixture, with the clock supplied by the recording rather than read from the
//! host. Nothing here mocks anything - a mock adapter would prove the mock
//! works.
//!
//! Re-record the fixture with:
//!
//! ```text
//! cargo run -- record --exchange kraken --seconds 20 --symbols BTC/USD \
//!     --instruments-file ../../config/instruments.yaml \
//!     > tests/fixtures/kraken-20s.jsonl
//!
//! (`--instruments-file` because the default is the container path
//! `/app/config/instruments.yaml`; it used to point at a Kraken-only fixture
//! registry, which is gone — see `adapter()` below.)
//! ```
//!
//! One line of the committed fixture is not verbatim: the `instrument`
//! snapshot's `pairs` array is filtered to `BTC/USD` and its `assets` array
//! emptied, which takes the frame from 639 KB to under 1 KB. The frame keeps
//! its shape and the fields the adapter reads; nothing else is touched.

use std::path::{Path, PathBuf};

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, KrakenAdapter};
use k2_capture::record::{BookSnapshotRecord, OutRecord};
use serde::Deserialize;
use sha2::{Digest, Sha256};

/// Fixed so the output depends only on the fixture. Live, this is a v4 uuid
/// per connection; replay supplies the one the archive recorded.
const CONN_ID: &str = "00000000-0000-4000-8000-0000000000fx";
/// The sampler cadence `run` uses by default, applied to recorded time.
const SNAPSHOT_INTERVAL_NS: i64 = 1_000_000_000;

#[derive(Deserialize)]
struct FixtureLine {
    recv_ts_ns: i64,
    payload: String,
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// The repo registry, like the Binance and Coinbase replay tests. This test used
/// to need its own copy with the WS v2 spellings, because
/// `config/instruments.yaml` had to keep Kraken's v1 spellings while the Kotlin
/// v1 handlers read the same file. Those retired (ADR-019), the registry moved
/// to the v2 spellings, and the fixture copy went with the alias table it
/// existed for. The recorded frames and the file that produced them are still
/// spelled identically - that is now true by default rather than by a second
/// file.
fn adapter() -> Adapter {
    let registry = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml");
    let instruments = Instruments::load(&registry, Exchange::Kraken).expect("repo registry");
    Adapter::Kraken(KrakenAdapter::new(instruments))
}

/// Everything one pass over the fixture produced.
struct Replay {
    records: Vec<OutRecord>,
    /// Resubscribe requests. Kraken raises one per checksum mismatch, so an
    /// empty vec is "every book update verified".
    resubscribes: usize,
    frames: usize,
}

/// Drive the adapter over the fixture with a virtual clock.
///
/// The 1 Hz sampler is driven by recorded `recv_ts_ns` rather than by
/// `tokio::time`, which is what makes two passes produce identical bytes.
fn replay() -> Replay {
    let text = std::fs::read_to_string(fixtures().join("kraken-20s.jsonl")).expect("fixture");
    let mut adapter = adapter();
    adapter.begin_connection(CONN_ID);

    let symbols = adapter.symbols();
    let mut out = Replay {
        records: Vec::new(),
        resubscribes: 0,
        frames: 0,
    };
    let mut next_sample_ns: Option<i64> = None;

    for line in text.lines() {
        let frame: FixtureLine = serde_json::from_str(line).expect("fixture line");
        let handled = adapter.handle_frame(frame.payload.as_bytes(), frame.recv_ts_ns);
        out.frames += 1;
        out.resubscribes += handled.actions.len();
        out.records.extend(handled.records);

        let due = *next_sample_ns.get_or_insert(frame.recv_ts_ns + SNAPSHOT_INTERVAL_NS);
        if frame.recv_ts_ns >= due {
            for symbol in &symbols {
                if let Some(snapshot) = adapter.snapshot(symbol, frame.recv_ts_ns) {
                    out.records.push(OutRecord::Book(snapshot));
                }
            }
            next_sample_ns = Some(frame.recv_ts_ns + SNAPSHOT_INTERVAL_NS);
        }
    }
    out
}

fn assert_book_invariants(b: &BookSnapshotRecord) {
    assert!(
        b.bid_px.len() <= 20 && b.ask_px.len() <= 20,
        "deeper than top-20"
    );
    assert_eq!(b.bid_px.len(), b.bid_qty.len(), "bid arrays out of step");
    assert_eq!(b.ask_px.len(), b.ask_qty.len(), "ask arrays out of step");
    assert_eq!(
        b.depth as usize,
        b.bid_px.len().max(b.ask_px.len()),
        "depth does not describe the arrays"
    );
    assert!(b.bid_qty.iter().all(|&q| q > 0), "zero-quantity bid level");
    assert!(b.ask_qty.iter().all(|&q| q > 0), "zero-quantity ask level");
    assert!(
        b.bid_px.windows(2).all(|w| w[0] > w[1]),
        "bids are not strictly descending"
    );
    assert!(
        b.ask_px.windows(2).all(|w| w[0] < w[1]),
        "asks are not strictly ascending"
    );
    if let (Some(&bid), Some(&ask)) = (b.bid_px.first(), b.ask_px.first()) {
        assert!(bid < ask, "crossed book: {bid} >= {ask}");
    }
    assert!(
        b.snapshot_ts_ns >= b.recv_ts_ns,
        "sampled before the update it contains"
    );
}

#[test]
fn fixture_replays_with_no_checksum_failures() {
    let r = replay();

    let raw = r
        .records
        .iter()
        .filter(|x| matches!(x, OutRecord::Raw(_)))
        .count();
    assert_eq!(raw, r.frames, "every frame must be archived, verbatim");

    let trades: Vec<_> = r
        .records
        .iter()
        .filter_map(|x| match x {
            OutRecord::Trade(t) => Some(t),
            _ => None,
        })
        .collect();
    assert!(!trades.is_empty(), "the fixture carries no trades");
    assert!(
        trades
            .iter()
            .all(|t| t.price > 0 && t.qty > 0 && t.seq == 0),
        "a trade came out with a non-positive price or quantity"
    );

    let books: Vec<_> = r
        .records
        .iter()
        .filter_map(|x| match x {
            OutRecord::Book(b) => Some(b),
            _ => None,
        })
        .collect();
    assert!(
        books.len() >= 15,
        "20 s at 1 Hz should sample ~19 snapshots, got {}",
        books.len()
    );
    for b in &books {
        assert_book_invariants(b);
        assert_eq!(
            b.checksum_ok,
            Some(true),
            "a snapshot was emitted from an unverified book"
        );
    }

    // Kraken raises exactly one resubscribe per checksum mismatch, so this is
    // the assertion that every one of the fixture's book updates reproduced the
    // venue's own CRC32 over our locally maintained book.
    assert_eq!(r.resubscribes, 0, "checksum mismatches during replay");

    eprintln!(
        "replayed {} frames -> {} raw, {} trades, {} snapshots, 0 checksum failures",
        r.frames,
        raw,
        trades.len(),
        books.len()
    );
}

/// Same input, same bytes out - the property Phase G's `k2-replay` rests on.
///
/// The golden hash is committed so drift fails CI rather than being noticed
/// later. If this fails and the change was intentional, the fixture or the
/// record shape moved, and the new hash goes in the file with a note saying
/// what moved.
#[test]
fn replay_is_deterministic() {
    let first = serde_json::to_vec(&replay().records).expect("serialise");
    let second = serde_json::to_vec(&replay().records).expect("serialise");
    assert_eq!(first, second, "two passes over one fixture disagreed");

    let digest = format!("{:x}", Sha256::digest(&first));
    let golden = std::fs::read_to_string(fixtures().join("kraken-20s.sha256"))
        .expect("golden hash")
        .split_whitespace()
        .next()
        .expect("golden hash is empty")
        .to_string();
    assert_eq!(
        digest, golden,
        "replay output changed; update tests/fixtures/kraken-20s.sha256 only if the change was intended"
    );
}
