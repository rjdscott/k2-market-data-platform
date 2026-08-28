//! Replay a recorded Binance combined-stream session through the live adapter.
//!
//! Same shape as `tests/replay.rs`: the `handle_frame(bytes, recv_ts_ns)` the
//! socket loop calls, fed from a JSONL fixture with the clock supplied by the
//! recording. The fixture is 10 s of `btcusdt@trade/btcusdt@depth20@100ms`
//! recorded verbatim on 2026-08-26 (one symbol, 10 s: two symbols for 20 s is
//! 880 KB, and the fixture budget is 300 KB).
//!
//! Re-record with `k2-capture record --exchange binance --symbols BTCUSDT
//! --seconds 10 > tests/fixtures/binance-10s.jsonl`.

use std::path::{Path, PathBuf};

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, BinanceAdapter};
use k2_capture::record::{BookSnapshotRecord, OutRecord};
use k2_capture::replay;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const CONN_ID: &str = "00000000-0000-4000-8000-0000000000fx";
const SNAPSHOT_INTERVAL_NS: i64 = 1_000_000_000;

#[derive(Deserialize)]
struct FixtureLine {
    recv_ts_ns: i64,
    payload: String,
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// The repo registry, not a test copy: Binance natives (`BTCUSDT`) are the
/// same on the wire as in `config/instruments.yaml`, unlike Kraken's.
fn adapter() -> BinanceAdapter {
    let registry = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml");
    let mut instruments = Instruments::load(&registry, Exchange::Binance).expect("registry");
    instruments
        .retain_native(&["BTCUSDT".to_string()])
        .expect("BTCUSDT is in the registry");
    BinanceAdapter::new(instruments)
}

struct Replay {
    records: Vec<OutRecord>,
    /// One per `lastUpdateId` regression; zero on a clean recording.
    gaps: usize,
    frames: usize,
    /// Snapshots taken directly after each depth frame, independent of the
    /// 1 Hz sampler, so every book frame's shape is checked.
    after_depth: Vec<BookSnapshotRecord>,
}

fn replay() -> Replay {
    let text = std::fs::read_to_string(fixtures().join("binance-10s.jsonl")).expect("fixture");
    let mut adapter = adapter();
    adapter.begin_connection(CONN_ID);
    let symbols = adapter.symbols();
    let mut out = Replay {
        records: Vec::new(),
        gaps: 0,
        frames: 0,
        after_depth: Vec::new(),
    };
    let mut next_sample_ns: Option<i64> = None;

    for line in text.lines() {
        let frame: FixtureLine = serde_json::from_str(line).expect("fixture line");
        let handled = adapter.handle_frame(frame.payload.as_bytes(), frame.recv_ts_ns);
        out.frames += 1;
        out.gaps += handled.actions.len();
        if handled.stream == "depth20" {
            out.after_depth.push(
                adapter
                    .snapshot("BTCUSDT", frame.recv_ts_ns)
                    .expect("book after depth"),
            );
        }
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
    assert_eq!(
        (b.bid_px.len(), b.ask_px.len()),
        (20, 20),
        "partial stream is 20 deep"
    );
    assert_eq!(b.depth, 20);
    assert_eq!(b.bid_px.len(), b.bid_qty.len());
    assert_eq!(b.ask_px.len(), b.ask_qty.len());
    assert!(
        b.bid_qty.iter().chain(&b.ask_qty).all(|&q| q > 0),
        "zero-quantity level"
    );
    assert!(
        b.bid_px.windows(2).all(|w| w[0] > w[1]),
        "bids not descending"
    );
    assert!(
        b.ask_px.windows(2).all(|w| w[0] < w[1]),
        "asks not ascending"
    );
    assert!(
        b.bid_px[0] < b.ask_px[0],
        "crossed book: {} >= {}",
        b.bid_px[0],
        b.ask_px[0]
    );
    assert_eq!(
        (b.checksum_ok, b.exchange_ts),
        (None, None),
        "ADR-027: unanswerable, not invented"
    );
    assert!(b.seq > 0, "seq carries lastUpdateId");
    assert!(b.snapshot_ts_ns >= b.recv_ts_ns);
}

#[test]
fn fixture_replays_with_no_gaps() {
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
            .all(|t| t.price > 0 && t.qty > 0 && t.seq == 0 && t.exchange_ts > 0)
    );

    assert!(
        !r.after_depth.is_empty(),
        "the fixture carries no depth frames"
    );
    for b in &r.after_depth {
        assert_book_invariants(b);
    }
    assert!(
        r.after_depth.windows(2).all(|w| w[0].seq < w[1].seq),
        "lastUpdateId not strictly increasing through the fixture"
    );

    let sampled = r
        .records
        .iter()
        .filter(|x| matches!(x, OutRecord::Book(_)))
        .count();
    assert!(
        sampled >= 8,
        "10 s at 1 Hz should sample ~9 snapshots, got {sampled}"
    );

    assert_eq!(r.gaps, 0, "lastUpdateId regressions during replay");

    eprintln!(
        "replayed {} frames -> {} raw, {} trades, {} depth frames, {} sampled snapshots, 0 gaps",
        r.frames,
        raw,
        trades.len(),
        r.after_depth.len(),
        sampled
    );
}

/// Same input, same bytes out - golden hash in `binance-10s.sha256`.
#[test]
fn replay_is_deterministic() {
    // Through the shared driver `k2-capture replay` runs, hashed over the same
    // JSONL bytes it writes: `replay --fixture binance-10s.jsonl | sha256sum` must
    // print the committed digest.
    let pass = || {
        let mut adapter = Adapter::Binance(adapter());
        let input = std::io::BufReader::new(
            std::fs::File::open(fixtures().join("binance-10s.jsonl")).expect("fixture"),
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
    let golden = std::fs::read_to_string(fixtures().join("binance-10s.sha256"))
        .expect("golden hash")
        .split_whitespace()
        .next()
        .expect("golden hash is empty")
        .to_string();
    assert_eq!(
        digest, golden,
        "replay output changed; update tests/fixtures/binance-10s.sha256 only if the change was intended"
    );
}
