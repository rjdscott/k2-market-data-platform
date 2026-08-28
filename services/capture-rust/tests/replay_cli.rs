//! Properties of the replay driver itself, beyond "same bytes as last time":
//! speed does not change the output, and the depth override reaches the
//! snapshot. Per-venue invariants and golden hashes live in the other
//! `replay*.rs` files.

use std::io::{BufRead, BufReader};
use std::path::Path;

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, BinanceAdapter, KrakenAdapter};
use k2_capture::record::OutRecord;
use k2_capture::replay::{self, Options};

fn registry() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml")
}

fn fixture(name: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

fn run(adapter: &mut Adapter, input: &[u8], opts: &Options) -> (Vec<u8>, replay::Stats) {
    let mut bytes = Vec::new();
    let stats = replay::run(adapter, BufReader::new(input), opts, |r| {
        replay::write_jsonl(&mut bytes, r)
    })
    .expect("replay");
    (bytes, stats)
}

/// `--speed realtime` sleeps; it must not change a byte. The first second of
/// the Binance fixture keeps the wall-clock cost of the sleeping pass small.
#[test]
fn realtime_and_max_produce_identical_bytes() {
    let head: Vec<u8> = BufReader::new(std::fs::File::open(fixture("binance-10s.jsonl")).unwrap())
        .lines()
        .take(60)
        .map(|l| l.unwrap() + "\n")
        .collect::<String>()
        .into_bytes();
    let adapter = || {
        Adapter::Binance(BinanceAdapter::new(
            Instruments::load(&registry(), Exchange::Binance).unwrap(),
        ))
    };
    let fast = run(&mut adapter(), &head, &Options::default());
    let slow = run(
        &mut adapter(),
        &head,
        &Options {
            realtime: true,
            ..Options::default()
        },
    );
    assert!(
        fast.1.frames == 60 && fast.1.records > 60,
        "the head carries frames and records"
    );
    assert_eq!(fast, slow, "speed changed the output");
}

/// `--depth 25` on Kraken, which subscribes at 25, yields snapshots deeper than
/// the product's 20; `--interval-ms 100` yields ~10x the snapshots.
#[test]
fn depth_and_interval_reach_the_snapshots() {
    let text = std::fs::read(fixture("kraken-20s.jsonl")).unwrap();
    let adapter = || {
        Adapter::Kraken(KrakenAdapter::new(
            Instruments::load(&registry(), Exchange::Kraken).unwrap(),
        ))
    };
    let books = |bytes: &[u8]| -> Vec<OutRecord> {
        bytes
            .split(|&b| b == b'\n')
            .filter(|l| !l.is_empty())
            .map(|l| serde_json::from_slice::<OutRecord>(l).unwrap())
            .filter(|r| matches!(r, OutRecord::Book(_)))
            .collect()
    };
    let (product, _) = run(&mut adapter(), &text, &Options::default());
    let (deep, _) = run(
        &mut adapter(),
        &text,
        &Options {
            depth: Some(25),
            snapshot_interval_ns: 100_000_000,
            ..Options::default()
        },
    );
    let product = books(&product);
    let deep = books(&deep);
    assert!(
        product
            .iter()
            .all(|r| matches!(r, OutRecord::Book(b) if b.bid_px.len() <= 20))
    );
    assert!(
        deep.iter().any(
            |r| matches!(r, OutRecord::Book(b) if b.bid_px.len() > 20 && b.bid_px.len() <= 25)
        ),
        "no snapshot deeper than 20 at --depth 25"
    );
    assert!(
        deep.len() >= product.len() * 5,
        "100 ms sampling gave {} vs {} at 1 s",
        deep.len(),
        product.len()
    );
}
