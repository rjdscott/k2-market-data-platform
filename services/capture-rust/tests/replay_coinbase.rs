//! Replay a recorded Coinbase session through the live adapter.
//!
//! Same shape as `tests/replay.rs`: the same `handle_frame(bytes, recv_ts_ns)`
//! the socket loop calls, fed from a JSONL fixture, the clock supplied by the
//! recording. Nothing is mocked.
//!
//! Re-record the fixture with `k2-capture record --exchange coinbase
//! --symbols ATOM-USD --seconds 20`. ATOM-USD because it had the smallest `level2` snapshot of
//! every product in the registry on 2026-08-26 (356 KB / 3,440 levels; XRP-USD
//! was 1.66 MB / 15,331). One line of the committed fixture is not verbatim:
//! the `snapshot` event keeps all 450 bids and the best 800 of 2,990 offers -
//! 1,250 levels - which takes the file from 582 KB to 320 KB. Nothing else,
//! including every `sequence_num`, is touched.

use std::path::{Path, PathBuf};

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, CoinbaseAdapter};
use k2_capture::record::{BookSnapshotRecord, OutRecord};
use k2_capture::replay;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const CONN_ID: &str = "00000000-0000-4000-8000-0000000000fx";
const SNAPSHOT_INTERVAL_NS: i64 = 1_000_000_000;
const SYMBOL: &str = "ATOM-USD";

#[derive(Deserialize)]
struct FixtureLine {
    recv_ts_ns: i64,
    payload: String,
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// The real registry: Coinbase natives are spelled the same on the wire, so
/// unlike Kraken no test-only copy is needed.
fn adapter() -> CoinbaseAdapter {
    let registry = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml");
    let mut instruments = Instruments::load(&registry, Exchange::Coinbase).expect("registry");
    instruments
        .retain_native(&[SYMBOL.to_string()])
        .expect("ATOM-USD is in the registry");
    CoinbaseAdapter::new(instruments)
}

struct Replay {
    records: Vec<OutRecord>,
    /// One `Action::Reconnect` per sequence gap, so 0 is "no gap in the
    /// recording".
    reconnects: usize,
    frames: usize,
}

fn replay() -> Replay {
    let text = std::fs::read_to_string(fixtures().join("coinbase-20s.jsonl")).expect("fixture");
    let mut adapter = adapter();
    adapter.begin_connection(CONN_ID);

    let symbols = adapter.symbols();
    let mut out = Replay {
        records: Vec::new(),
        reconnects: 0,
        frames: 0,
    };
    let mut next_sample_ns: Option<i64> = None;

    for line in text.lines() {
        let frame: FixtureLine = serde_json::from_str(line).expect("fixture line");
        let handled = adapter.handle_frame(frame.payload.as_bytes(), frame.recv_ts_ns);
        out.frames += 1;
        out.reconnects += handled.actions.len();
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
        "bids not strictly descending"
    );
    assert!(
        b.ask_px.windows(2).all(|w| w[0] < w[1]),
        "asks not strictly ascending"
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
fn fixture_replays_with_no_sequence_gaps() {
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
            .all(|t| t.price > 0 && t.qty > 0 && t.symbol == SYMBOL),
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
        assert_eq!(b.depth, 20, "a full-depth book always fills top-20");
        assert_eq!(b.checksum_ok, None, "coinbase publishes no checksum");
        assert!(
            b.exchange_ts.is_some(),
            "event_time was not carried into the snapshot"
        );
    }
    assert!(
        books.windows(2).all(|w| w[0].seq <= w[1].seq),
        "snapshot seq must not go backwards"
    );

    assert_eq!(r.reconnects, 0, "sequence gaps during replay");

    eprintln!(
        "replayed {} frames -> {} raw, {} trades, {} snapshots, 0 sequence gaps",
        r.frames,
        raw,
        trades.len(),
        books.len()
    );
}

/// Same input, same bytes out. The golden hash is committed so drift fails CI.
#[test]
fn replay_is_deterministic() {
    // Through the shared driver `k2-capture replay` runs, hashed over the same
    // JSONL bytes it writes: `replay --fixture coinbase-20s.jsonl | sha256sum` must
    // print the committed digest.
    let pass = || {
        let mut adapter = Adapter::Coinbase(adapter());
        let input = std::io::BufReader::new(
            std::fs::File::open(fixtures().join("coinbase-20s.jsonl")).expect("fixture"),
        );
        let mut bytes = Vec::new();
        let stats = replay::run(&mut adapter, input, &replay::Options::default(), |r| {
            replay::write_jsonl(&mut bytes, r)
        })
        .expect("replay");
        (bytes, stats)
    };
    let (first, stats) = pass();
    let (second, _) = pass();
    assert_eq!(first, second, "two passes over one fixture disagreed");
    assert_eq!(
        stats.records,
        first.iter().filter(|&&b| b == b'\n').count(),
        "one record per line"
    );

    let digest = format!("{:x}", Sha256::digest(&first));
    let golden = std::fs::read_to_string(fixtures().join("coinbase-20s.sha256"))
        .expect("golden hash")
        .split_whitespace()
        .next()
        .expect("golden hash is empty")
        .to_string();
    assert_eq!(
        digest, golden,
        "replay output changed; update tests/fixtures/coinbase-20s.sha256 only if the change was intended"
    );
}
