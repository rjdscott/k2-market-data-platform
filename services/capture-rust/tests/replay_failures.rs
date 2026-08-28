//! One fixture per venue failure, replayed through the live adapter.
//!
//! The clean per-venue fixtures (`tests/replay.rs`, `replay_binance.rs`,
//! `replay_coinbase.rs`) prove the happy path; these three prove the resync
//! policy of ADR-027 by carrying the failure itself. Each is derived
//! deterministically from its clean sibling by one edit - no live venue, no
//! hand-written frames - so every byte except the one disturbance is real venue
//! output:
//!
//! | Fixture | Derived from | The one edit |
//! |---------|--------------|--------------|
//! | `kraken-20s-checksum-fail.jsonl` | `kraken-20s.jsonl` | line 600, a `book` `update` ~8 s after the snapshot: `"checksum":1925110775` -> `"checksum":1` |
//! | `coinbase-20s-seq-gap.jsonl` | `coinbase-20s.jsonl` | line 81 deleted (`l2_data` `update`, `sequence_num` 80), so 79 is followed by 81 |
//! | `binance-10s-regression.jsonl` | `binance-10s.jsonl` | the payloads of lines 67 and 68 swapped - `lastUpdateId` 99193217508 arrives before 99193217500 - each line keeping its own `recv_ts_ns` |
//!
//! Re-derive with `python3 scripts/make-failure-fixtures.py`; the committed
//! `.sha256` files are what `k2-capture replay --exchange <x> --fixture <f>
//! --instruments-file ../../config/instruments.yaml | sha256sum` prints.
//!
//! A fixture stops at the edge of the venue's reply: it can carry the failing
//! frame but not the answer to the resync that frame triggers. So each test also
//! pins what the adapter does for the *rest* of the session with its request
//! unanswered - the part these fixtures add over the unit tests in
//! `src/exchanges/*.rs`, which stop at the action. The Kraken one found a bug
//! that way: before #119 a single mismatch replayed to 573 `Resubscribe` actions
//! and 573 marked snapshots, one per `update` frame folded into the empty book
//! it had just cleared (ADR-027 Outcome, 2026-08-28). It is 1 and 1 now.

use std::io::BufReader;
use std::path::{Path, PathBuf};

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, BinanceAdapter, CoinbaseAdapter, KrakenAdapter};
use k2_capture::record::{BookSnapshotRecord, OutRecord};
use k2_capture::replay;
use sha2::{Digest, Sha256};

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn registry() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml")
}

fn instruments(exchange: Exchange, native: &str) -> Instruments {
    let mut i = Instruments::load(&registry(), exchange).expect("registry");
    i.retain_native(&[native.to_string()])
        .expect("symbol is in the registry");
    i
}

/// One pass over `name` through `replay::run`, the driver the `replay`
/// subcommand uses. The records come back parsed from the JSONL it writes, so
/// the test reads exactly the bytes the golden hash covers.
fn replay_fixture(mut adapter: Adapter, name: &str) -> (Vec<OutRecord>, replay::Stats, String) {
    let input = BufReader::new(std::fs::File::open(fixtures().join(name)).expect("fixture"));
    let mut bytes = Vec::new();
    let stats = replay::run(&mut adapter, input, &replay::Options::default(), |r| {
        replay::write_jsonl(&mut bytes, r)
    })
    .expect("replay");
    let records = bytes
        .split(|&b| b == b'\n')
        .filter(|l| !l.is_empty())
        .map(|l| serde_json::from_slice::<OutRecord>(l).expect("record"))
        .collect();
    (records, stats, format!("{:x}", Sha256::digest(&bytes)))
}

/// The committed digest of `k2-capture replay --fixture <f> | sha256sum`.
fn golden(name: &str) -> String {
    std::fs::read_to_string(fixtures().join(name))
        .expect("golden hash")
        .split_whitespace()
        .next()
        .expect("golden hash is empty")
        .to_string()
}

fn books(records: &[OutRecord]) -> Vec<&BookSnapshotRecord> {
    records
        .iter()
        .filter_map(|r| match r {
            OutRecord::Book(b) => Some(b),
            _ => None,
        })
        .collect()
}

/// Kraken: one wrong CRC32, one resubscribe, then a dark window.
///
/// Two properties, both from ADR-027's Outcome. First, `checksum_ok = false` has
/// to reach a consumer *before* the book is dropped, so the mismatch emits one
/// marked snapshot of the book as it actually stood. Second, and what this
/// fixture is the reason for: the round-trip window costs exactly **one**
/// action. An `update` for a symbol whose book is empty is ignored, because a
/// delta onto nothing produces a partial book that fails the checksum and asks
/// for the same resubscribe again — 572 more times here, which is what this
/// fixture measured before the guard landed (#119). The venue's `snapshot` is
/// the only thing that ends the window, this fixture cannot carry one, and so
/// the symbol emits **no further book record at all**: 10 snapshots against the
/// clean fixture's 19, the last of them the marked one. Every ignored frame is
/// still archived — the guard is about the book, not the record.
#[test]
fn kraken_checksum_mismatch_marks_once_then_goes_dark() {
    let adapter = Adapter::Kraken(KrakenAdapter::new(
        Instruments::load(&registry(), Exchange::Kraken).expect("registry"),
    ));
    let (records, stats, digest) = replay_fixture(adapter, "kraken-20s-checksum-fail.jsonl");
    let books = books(&records);

    let bad = books
        .iter()
        .filter(|b| b.checksum_ok == Some(false))
        .count();
    assert_eq!(
        (stats.actions, bad),
        (1, 1),
        "one incident is one Resubscribe and one marked snapshot, however many \
         update frames arrive before the resync lands"
    );
    assert!(
        books.iter().all(|b| b.symbol == "BTC/USD"),
        "the resync is scoped to the symbol that failed"
    );

    // 19 on the clean fixture, 10 here: the sampler stops producing the moment
    // the book is dropped and never resumes without a fresh venue snapshot.
    assert_eq!(books.len(), 10, "snapshots emitted at all");
    let marked = books.len() - 1;
    assert_eq!(
        books[marked].checksum_ok,
        Some(false),
        "the marked snapshot is the last book record of the session"
    );
    assert!(
        books[..marked].iter().all(|b| b.checksum_ok == Some(true)),
        "a snapshot claimed checksum_ok = true after the book had drifted"
    );
    // The marked snapshot is stamped by `handle_frame`, not by the sampler, so
    // it carries the failing frame's own clock.
    assert_eq!(
        books[marked].snapshot_ts_ns, books[marked].recv_ts_ns,
        "the marked snapshot was sampled off the 1 Hz clock"
    );
    // The 572 ignored `update` frames are ignored for book purposes only.
    let raw = records
        .iter()
        .filter(|r| matches!(r, OutRecord::Raw(_)))
        .count();
    assert_eq!(
        (raw, stats.frames),
        (1185, 1185),
        "every frame is archived verbatim, dark window or not"
    );

    assert_eq!(
        digest,
        golden("kraken-20s-checksum-fail.sha256"),
        "replay output changed; update the .sha256 only if the change was intended"
    );
}

/// Coinbase: one dropped frame, and every book on the connection with it.
///
/// `sequence_num` is connection-wide, so a missing frame cannot be attributed to
/// a product: the adapter counts one gap, clears *every* book and returns a
/// single `Action::Reconnect`, then re-anchors `expected_seq` on the frame it did
/// get so the rest of the session is not one gap per frame. Live, `main.rs`
/// closes the socket and the venue's fresh `level2` snapshot rebuilds the book.
/// Here nothing answers, and the consequence is the point: an `l2_data` `update`
/// with no snapshot behind it is dropped rather than used to build a
/// thin-but-plausible book, so the symbol emits *no snapshot at all* for the
/// remaining ~13 s - 9 against the clean fixture's 19, the last of them carrying
/// a `seq` from before the gap.
#[test]
fn coinbase_sequence_gap_reconnects_and_the_books_stay_dark() {
    let adapter = Adapter::Coinbase(CoinbaseAdapter::new(instruments(
        Exchange::Coinbase,
        "ATOM-USD",
    )));
    let (records, stats, digest) = replay_fixture(adapter, "coinbase-20s-seq-gap.jsonl");
    let books = books(&records);

    assert_eq!(stats.actions, 1, "exactly one Reconnect for one gap");
    assert_eq!(
        stats.frames, 158,
        "the fixture is the clean one minus exactly one frame"
    );
    assert_eq!(
        books.len(),
        9,
        "19 snapshots on the clean fixture; the gap ends them"
    );
    assert!(
        books.iter().all(|b| b.seq < 80),
        "a snapshot was emitted from a book touched after the gap at sequence_num 80"
    );

    assert_eq!(
        digest,
        golden("coinbase-20s-seq-gap.sha256"),
        "replay output changed; update the .sha256 only if the change was intended"
    );
}

/// Binance: one out-of-order partial-depth frame, and one frame of damage.
///
/// The regression raises `Action::Resubscribe("BTCUSDT")` - not `Reconnect`, as
/// ADR-027's policy table words it; the combined stream is resubscribed per
/// symbol - and drops that book, so a snapshot taken between the regression and
/// the next in-order frame returns `None` rather than a stale top-20. Recovery
/// needs no venue reply at all, which makes this the cheapest of the three
/// policies: every `@depth20@100ms` frame *is* a complete top-20, so the next one
/// 100 ms later rebuilds the book unaided. One action, and the whole cost is one
/// sampled snapshot - the grid line that falls on the swapped pair, where the
/// clean fixture samples `lastUpdateId` 99193217508 and this one has no book to
/// sample. 9 snapshots against the clean fixture's 10, and the ~100 ms hole is
/// the entire blast radius.
#[test]
fn binance_last_update_id_regression_costs_one_sample() {
    let adapter = Adapter::Binance(BinanceAdapter::new(instruments(
        Exchange::Binance,
        "BTCUSDT",
    )));
    let (records, stats, digest) = replay_fixture(adapter, "binance-10s-regression.jsonl");
    let books = books(&records);

    assert_eq!(
        stats.actions, 1,
        "exactly one Resubscribe for one regression"
    );
    assert_eq!(books.len(), 9, "10 on the clean fixture: one sample lost");
    assert!(
        books.iter().all(|b| b.seq != 99_193_217_508),
        "the clean fixture samples this lastUpdateId at the grid line the \
         regression falls on; here there is no book to sample"
    );
    assert!(
        books.windows(2).all(|w| w[0].seq < w[1].seq),
        "a sampled snapshot carried a lastUpdateId that had gone backwards"
    );
    assert!(
        books.iter().all(|b| b.checksum_ok.is_none()),
        "ADR-027: binance publishes no checksum, so the question stays unanswerable"
    );

    assert_eq!(
        digest,
        golden("binance-10s-regression.sha256"),
        "replay output changed; update the .sha256 only if the change was intended"
    );
}
