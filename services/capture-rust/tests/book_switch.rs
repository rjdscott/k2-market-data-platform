//! The registry's `book_capture` switch (ADR-050), through the public API.
//!
//! Two properties. What each venue asks for: with the switch false a capture
//! subscribes to exactly what it does with the switch true, minus the venue's
//! book channel, and with it true to exactly today's strings. And what it
//! decodes: nothing that turns a frame into records reads the switch, so every
//! committed synthetic fixture replays to the same bytes, its golden hash,
//! whichever way the switch is set. That second one is what keeps the paused
//! book code under test.

use std::io::BufReader;
use std::path::{Path, PathBuf};

use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Adapter, BinanceAdapter, CoinbaseAdapter, KrakenAdapter};
use k2_capture::record::OutRecord;
use k2_capture::replay;
use sha2::{Digest, Sha256};

const REGISTRY: &str = "version: 2
instruments:
  binance:
    - { native: BTCUSDT, canonical: BTC/USDT }
    - { native: ETHUSDT, canonical: ETH/USDT }
  kraken:
    - { native: BTC/USD, canonical: BTC/USD }
    - { native: ETH/USD, canonical: ETH/USD }
  coinbase:
    - { native: BTC-USD, canonical: BTC/USD }
    - { native: ETH-USD, canonical: ETH/USD }
";

fn adapter(exchange: Exchange, yaml: &str) -> Adapter {
    let i = Instruments::parse(yaml, exchange).expect("registry");
    match exchange {
        Exchange::Binance => Adapter::Binance(BinanceAdapter::new(i)),
        Exchange::Kraken => Adapter::Kraken(KrakenAdapter::new(i)),
        Exchange::Coinbase => Adapter::Coinbase(CoinbaseAdapter::new(i)),
    }
}

/// The URL a capture dials and the frames it sends after the socket opens.
fn asks(exchange: Exchange, book: bool) -> (String, Vec<String>) {
    let yaml = format!(
        "{REGISTRY}book_capture: {{ binance: {book}, kraken: {book}, coinbase: {book} }}\n"
    );
    let a = adapter(exchange, &yaml);
    (a.ws_url("wss://venue/stream"), a.subscribe_messages())
}

#[test]
fn binance_off_drops_exactly_the_depth20_streams() {
    let (on, on_msgs) = asks(Exchange::Binance, true);
    let (off, off_msgs) = asks(Exchange::Binance, false);
    assert_eq!(
        on,
        "wss://venue/stream?streams=btcusdt@trade/btcusdt@depth20@100ms/ethusdt@trade/ethusdt@depth20@100ms"
    );
    assert_eq!(
        off,
        "wss://venue/stream?streams=btcusdt@trade/ethusdt@trade"
    );
    assert!(
        on_msgs.is_empty() && off_msgs.is_empty(),
        "the URL is the subscription"
    );
}

#[test]
fn kraken_off_drops_exactly_the_book_subscribe() {
    let instrument = r#"{"method":"subscribe","params":{"channel":"instrument","snapshot":true}}"#;
    let book = r#"{"method":"subscribe","params":{"channel":"book","depth":25,"snapshot":true,"symbol":["BTC/USD","ETH/USD"]}}"#;
    let trade = r#"{"method":"subscribe","params":{"channel":"trade","snapshot":false,"symbol":["BTC/USD","ETH/USD"]}}"#;
    let (on_url, on) = asks(Exchange::Kraken, true);
    let (off_url, off) = asks(Exchange::Kraken, false);
    assert_eq!(on, vec![instrument, book, trade]);
    // `instrument` stays: the security master and the book replay's checksum read it.
    assert_eq!(off, vec![instrument, trade]);
    assert_eq!(
        (on_url.as_str(), off_url.as_str()),
        ("wss://venue/stream", "wss://venue/stream")
    );
}

#[test]
fn coinbase_off_drops_exactly_level2() {
    let frame = |channel: &str| {
        format!(
            r#"{{"channel":"{channel}","product_ids":["BTC-USD","ETH-USD"],"type":"subscribe"}}"#
        )
    };
    let (_, on) = asks(Exchange::Coinbase, true);
    let (_, off) = asks(Exchange::Coinbase, false);
    assert_eq!(
        on,
        vec![frame("level2"), frame("market_trades"), frame("heartbeats")]
    );
    // `heartbeats` stays: it keeps the connection-wide sequence_num moving.
    assert_eq!(off, vec![frame("market_trades"), frame("heartbeats")]);
}

/// The shipped registry with every venue's switch set to `book`.
fn shipped_registry(book: bool) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/instruments.yaml");
    let mut doc: serde_yaml::Value =
        serde_yaml::from_str(&std::fs::read_to_string(path).expect("registry")).expect("yaml");
    for venue in ["binance", "kraken", "coinbase"] {
        doc["book_capture"][venue] = serde_yaml::Value::Bool(book);
    }
    serde_yaml::to_string(&doc).expect("yaml")
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// `k2-capture replay --fixture <name>` over the full registry, as bytes.
fn replayed(exchange: Exchange, yaml: &str, name: &str) -> Vec<u8> {
    let mut a = adapter(exchange, yaml);
    let input = BufReader::new(std::fs::File::open(fixtures().join(name)).expect("fixture"));
    let mut bytes = Vec::new();
    replay::run(&mut a, input, &replay::Options::default(), |r| {
        replay::write_jsonl(&mut bytes, r)
    })
    .expect("replay");
    bytes
}

#[test]
fn every_fixture_replays_to_its_golden_bytes_with_the_switch_either_way() {
    let (on, off) = (shipped_registry(true), shipped_registry(false));
    for (exchange, name) in [
        (Exchange::Kraken, "synthetic-kraken-20s"),
        (Exchange::Kraken, "synthetic-kraken-20s-checksum-fail"),
        (Exchange::Binance, "synthetic-binance-10s"),
        (Exchange::Binance, "synthetic-binance-10s-regression"),
        (Exchange::Coinbase, "synthetic-coinbase-20s"),
        (Exchange::Coinbase, "synthetic-coinbase-20s-seq-gap"),
    ] {
        let jsonl = format!("{name}.jsonl");
        let bytes = replayed(exchange, &on, &jsonl);
        assert!(
            bytes == replayed(exchange, &off, &jsonl),
            "{name}: the switch changed a decoded byte"
        );

        let records: Vec<OutRecord> = bytes
            .split(|&b| b == b'\n')
            .filter(|l| !l.is_empty())
            .map(|l| serde_json::from_slice(l).expect("record"))
            .collect();
        for kind in ["trade", "book"] {
            assert!(
                records.iter().any(|r| matches!(
                    (kind, r),
                    ("trade", OutRecord::Trade(_)) | ("book", OutRecord::Book(_))
                )),
                "{name}: no {kind} record, so the comparison above proves nothing about it"
            );
        }
        let golden =
            std::fs::read_to_string(fixtures().join(format!("{name}.sha256"))).expect("golden");
        assert_eq!(
            format!("{:x}", Sha256::digest(&bytes)),
            golden.split_whitespace().next().expect("hash"),
            "{name}"
        );
    }
}
