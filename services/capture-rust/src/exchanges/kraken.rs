//! Kraken spot WebSocket v2: `instrument` + `trade` + `book` on one connection.
//!
//! One connection carries all three channels, which is what makes the book
//! verifiable: the `instrument` channel publishes `price_precision` and
//! `qty_precision` per pair, and the CRC32 checksum Kraken attaches to every
//! book frame is computed over strings formatted to exactly those precisions.
//! Precision therefore comes from the feed at connect time, not from
//! `config/instruments.yaml` (spike S2, ADR-018 Appendix A).
//!
//! Two facts about this venue that shape the code:
//!
//! - **No sequence numbers.** Kraken v2 book and trade frames carry no sequence,
//!   so `Trade.seq` and `BookSnapshotL2.seq` are written as `0` - "this venue
//!   does not sequence", per the field docs in the `.avsc`. The checksum plays
//!   the role gap detection plays elsewhere: a missed update shows up as a
//!   mismatch on the very next frame, which is stronger than a sequence gap
//!   because it also catches a *mis-applied* update, not just a missing one.
//! - **Prices are JSON numbers, not strings.** They are read as raw JSON text
//!   (`serde_json::value::RawValue`) and parsed straight to `i64` at 1e-8, so no
//!   `f64` ever touches the record path. 1489 consecutive frames sampled on
//!   2026-08-26 contained no scientific notation; if that changes,
//!   `DecimalError::Scientific` is the alert.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::value::RawValue;

use super::{Action, Handled};
use crate::book::{Book, Side as BookSide};
use crate::config::Instruments;
use crate::decimal::{DecimalError, checksum_digits, parse_fixed};
use crate::record::{BookSnapshotRecord, OutRecord, RawMessageRecord, Side, TradeRecord};

const EXCHANGE: &str = "kraken";
/// Kraken's checksum is defined over the top 10 levels of each side.
const CHECKSUM_LEVELS: usize = 10;
/// Top-N depth of the emitted `BookSnapshotL2` (ADR-018: 20 is the product).
const SNAPSHOT_LEVELS: usize = 20;
/// Subscription depth. 25 is the shallowest depth the checksum is defined over,
/// so it is the shallowest depth we can verify - see `config/instruments.yaml`.
const DEFAULT_BOOK_DEPTH: u32 = 25;
/// Book frames held per symbol while waiting for the `instrument` snapshot.
/// In practice the snapshot arrives first (it is the first subscription sent);
/// this bound exists so a venue that never sends one costs a bounded amount of
/// memory instead of the container.
const MAX_PENDING_FRAMES: usize = 512;

/// Per-symbol book state. Everything here is reset on reconnect.
#[derive(Debug, Default)]
struct BookState {
    book: Book,
    /// Receipt time of the last frame folded into this book.
    last_recv_ts_ns: i64,
    /// Venue timestamp of that frame, microseconds since the epoch.
    last_exchange_ts_us: Option<i64>,
    /// Frame counter at that point - the foreign key into the raw topic.
    last_conn_msg_seq: i64,
    /// `None` until the first checksum has been evaluated, so a snapshot taken
    /// before any verification says "unanswerable" rather than claiming true.
    checksum_ok: Option<bool>,
}

/// A book frame parked until the pair's precision is known.
#[derive(Debug)]
struct PendingFrame {
    recv_ts_ns: i64,
    conn_msg_seq: i64,
    bytes: Vec<u8>,
}

#[derive(Debug)]
pub struct KrakenAdapter {
    instruments: Instruments,
    conn_id: String,
    conn_msg_seq: i64,
    /// native symbol -> (price_precision, qty_precision), from `instrument`.
    precision: BTreeMap<String, (u32, u32)>,
    books: BTreeMap<String, BookState>,
    pending: BTreeMap<String, Vec<PendingFrame>>,
}

/// Kraken WS v1 and v2 spell two base assets differently, and
/// `config/instruments.yaml` has to keep the v1 spellings for as long as the
/// Kotlin v1 handlers read the same file. So the v2 adapter translates on the
/// way in: `XBT/USD` in the registry subscribes as `BTC/USD` on the wire, and
/// `Trade.symbol` / `BookSnapshotL2.symbol` carry `BTC/USD` - "as the exchange
/// spells it on the wire", which is what the `.avsc` contract says. The
/// registry's `canonical` is untouched and stays authoritative.
///
/// A prefix match on the base asset only, never a substring: `XBT/` -> `BTC/`
/// leaves a quote currency or an unrelated ticker containing `XBT` alone.
///
// ponytail: remove with the Kotlin handlers - instruments.yaml then carries v2 spellings
const V1_TO_V2_BASE: &[(&str, &str)] = &[("XBT/", "BTC/"), ("XDG/", "DOGE/")];

/// Registry spelling -> v2 wire spelling. Unknown symbols pass through
/// unchanged, which is every symbol but two.
fn to_v2_symbol(registry_native: &str) -> String {
    for (v1, v2) in V1_TO_V2_BASE {
        if let Some(rest) = registry_native.strip_prefix(v1) {
            return format!("{v2}{rest}");
        }
    }
    registry_native.to_string()
}

impl KrakenAdapter {
    pub fn new(instruments: Instruments) -> anyhow::Result<Self> {
        Ok(Self {
            instruments: instruments.map_natives(to_v2_symbol)?,
            conn_id: String::new(),
            conn_msg_seq: 0,
            precision: BTreeMap::new(),
            books: BTreeMap::new(),
            pending: BTreeMap::new(),
        })
    }

    pub fn begin_connection(&mut self, conn_id: &str) {
        self.conn_id = conn_id.to_string();
        self.conn_msg_seq = 0;
        // Precision is re-published by the `instrument` snapshot on every
        // connect, and books must be rebuilt from a fresh snapshot, so none of
        // it survives the boundary.
        self.precision.clear();
        self.books.clear();
        self.pending.clear();
    }

    pub fn symbols(&self) -> Vec<String> {
        self.instruments.natives()
    }

    pub fn total_levels(&self) -> usize {
        self.books.values().map(|s| s.book.len()).sum()
    }

    pub fn depth(&self, native: &str) -> Option<usize> {
        self.books.get(native).map(|s| s.book.len())
    }

    /// Subscribe frames, in the order they must be sent: `instrument` first so
    /// precision is known before the first book frame lands.
    pub fn subscribe_messages(&self) -> Vec<String> {
        let mut msgs = vec![
            serde_json::json!({
                "method": "subscribe",
                "params": {"channel": "instrument", "snapshot": true}
            })
            .to_string(),
        ];

        // Group by depth so a per-instrument `book_depth` override in the
        // registry is honoured rather than silently ignored.
        let mut by_depth: BTreeMap<u32, Vec<String>> = BTreeMap::new();
        for i in self.instruments.iter() {
            by_depth
                .entry(i.book_depth.unwrap_or(DEFAULT_BOOK_DEPTH))
                .or_default()
                .push(i.native.clone());
        }
        for (depth, symbols) in by_depth {
            msgs.push(
                serde_json::json!({
                    "method": "subscribe",
                    "params": {"channel": "book", "symbol": symbols, "depth": depth, "snapshot": true}
                })
                .to_string(),
            );
        }

        msgs.push(
            serde_json::json!({
                "method": "subscribe",
                "params": {"channel": "trade", "symbol": self.instruments.natives(), "snapshot": false}
            })
            .to_string(),
        );
        msgs
    }

    /// The `depth` this symbol is subscribed at - the number of levels per side
    /// the venue will keep us informed about, and therefore the number the
    /// local book may hold.
    fn subscription_depth(&self, native: &str) -> u32 {
        self.instruments
            .iter()
            .find(|i| i.native == native)
            .and_then(|i| i.book_depth)
            .unwrap_or(DEFAULT_BOOK_DEPTH)
    }

    /// Unsubscribe then resubscribe with `snapshot: true`. Both frames are
    /// needed: Kraken answers a second subscribe for a live subscription with
    /// an error rather than a fresh snapshot.
    pub fn resubscribe_messages(&self, native: &str) -> Vec<String> {
        let depth = self.subscription_depth(native);
        vec![
            serde_json::json!({
                "method": "unsubscribe",
                "params": {"channel": "book", "symbol": [native], "depth": depth}
            })
            .to_string(),
            serde_json::json!({
                "method": "subscribe",
                "params": {"channel": "book", "symbol": [native], "depth": depth, "snapshot": true}
            })
            .to_string(),
        ]
    }

    pub fn handle_frame(&mut self, bytes: &[u8], recv_ts_ns: i64) -> Handled {
        self.conn_msg_seq += 1;
        let conn_msg_seq = self.conn_msg_seq;

        let envelope: Option<Envelope> = serde_json::from_slice(bytes).ok();
        let body = envelope.as_ref().map(Body::parse).unwrap_or(Body::Other);
        let stream = match &envelope {
            Some(e) => e.stream_name(),
            None => "unparseable",
        };

        let mut out = Handled {
            stream: stream.to_string(),
            records: vec![OutRecord::Raw(RawMessageRecord {
                exchange: EXCHANGE.into(),
                stream: stream.to_string(),
                symbol: body.single_symbol().map(str::to_string),
                recv_ts_ns,
                conn_id: self.conn_id.clone(),
                conn_msg_seq,
                payload: bytes.to_vec(),
            })],
            actions: Vec::new(),
        };

        let frame_type = envelope.as_ref().and_then(|e| e.typ).unwrap_or("");
        match body {
            Body::Instrument(pairs) => {
                for p in pairs {
                    if self.instruments.canonical(p.symbol).is_some() {
                        self.precision
                            .insert(p.symbol.to_string(), (p.price_precision, p.qty_precision));
                    }
                }
                out.actions.extend(self.drain_pending());
            }
            Body::Trade(trades) => {
                for t in trades {
                    if let Some(rec) = self.trade_record(&t, recv_ts_ns, conn_msg_seq) {
                        out.records.push(OutRecord::Trade(rec));
                    }
                }
            }
            Body::Book(frames) => {
                for f in frames {
                    if self.precision.contains_key(f.symbol) {
                        out.actions.extend(self.apply_book(
                            &f,
                            frame_type,
                            recv_ts_ns,
                            conn_msg_seq,
                        ));
                    } else {
                        self.park(f.symbol, recv_ts_ns, conn_msg_seq, bytes);
                    }
                }
            }
            Body::Other => {}
        }
        out
    }

    /// Top-20 snapshot of one symbol's book. `None` before the first book frame
    /// - an empty snapshot would look like a book with no liquidity.
    pub fn snapshot(&self, native: &str, now_ns: i64) -> Option<BookSnapshotRecord> {
        let state = self.books.get(native)?;
        if state.book.is_empty() {
            return None;
        }
        let canonical = self.instruments.canonical(native)?;
        let top = state.book.top_n(SNAPSHOT_LEVELS);
        Some(BookSnapshotRecord {
            exchange: EXCHANGE.into(),
            symbol: native.to_string(),
            canonical_symbol: canonical.to_string(),
            depth: top.depth(),
            // Kraken v2 publishes no sequence on book frames; 0 means "this
            // venue does not sequence this stream", per book-snapshot-l2.avsc.
            seq: 0,
            checksum_ok: state.checksum_ok,
            bid_px: top.bid_px,
            bid_qty: top.bid_qty,
            ask_px: top.ask_px,
            ask_qty: top.ask_qty,
            exchange_ts: state.last_exchange_ts_us,
            recv_ts_ns: state.last_recv_ts_ns,
            snapshot_ts_ns: now_ns,
            conn_id: self.conn_id.clone(),
            conn_msg_seq: state.last_conn_msg_seq,
        })
    }

    // ── internals ───────────────────────────────────────────────────────────

    fn trade_record(
        &self,
        t: &TradeData<'_>,
        recv_ts_ns: i64,
        conn_msg_seq: i64,
    ) -> Option<TradeRecord> {
        let canonical = self.instruments.canonical(t.symbol)?;
        let price = self.parse_units(t.price.get(), t.symbol, "price")?;
        let qty = self.parse_units(t.qty.get(), t.symbol, "qty")?;
        let side = match t.side {
            "buy" => Side::Buy,
            "sell" => Side::Sell,
            other => {
                tracing::warn!(side = other, "kraken trade with an unknown side, dropped");
                return None;
            }
        };
        let exchange_ts = parse_micros(t.timestamp)?;
        Some(TradeRecord {
            exchange: EXCHANGE.into(),
            symbol: t.symbol.to_string(),
            canonical_symbol: canonical.to_string(),
            // The venue's integer id, stringified without reformatting: this is
            // the raw JSON text, so 105994519 stays 105994519.
            trade_id: t.trade_id.get().to_string(),
            price,
            qty,
            side,
            exchange_ts,
            recv_ts_ns,
            seq: 0,
            conn_id: self.conn_id.clone(),
            conn_msg_seq,
        })
    }

    /// Apply one book frame and verify the checksum it carries.
    fn apply_book(
        &mut self,
        f: &BookData<'_>,
        frame_type: &str,
        recv_ts_ns: i64,
        conn_msg_seq: i64,
    ) -> Vec<Action> {
        if self.instruments.canonical(f.symbol).is_none() {
            return Vec::new();
        }
        let (price_precision, qty_precision) = match self.precision.get(f.symbol) {
            Some(&p) => p,
            None => return Vec::new(),
        };
        let depth = self.subscription_depth(f.symbol) as usize;

        let state = self.books.entry(f.symbol.to_string()).or_default();
        // A `snapshot` frame replaces the book outright; an `update` folds in.
        if frame_type == "snapshot" {
            state.book.clear();
        }

        let mut ok = true;
        for (side, levels) in [(BookSide::Bid, &f.bids), (BookSide::Ask, &f.asks)] {
            for lvl in levels {
                match (parse_fixed(lvl.price.get()), parse_fixed(lvl.qty.get())) {
                    (Ok(px), Ok(qty)) => state.book.apply(side, px, qty),
                    (px, qty) => {
                        for e in [px.err(), qty.err()].into_iter().flatten() {
                            count_decimal_error(e);
                        }
                        ok = false;
                    }
                }
            }
        }

        // The subscription only reports levels inside `depth`; anything past it
        // will never be deleted by the venue, so it has to go now or it will
        // corrupt the checksum later. See `Book::truncate`.
        state.book.truncate(depth);

        state.last_recv_ts_ns = recv_ts_ns;
        state.last_conn_msg_seq = conn_msg_seq;
        state.last_exchange_ts_us = f.timestamp.and_then(parse_micros);

        // A level we could not parse means the local book is already wrong;
        // fail the checksum without computing it so the resync path is the same.
        let computed = if ok {
            book_checksum(
                &state.book.top_pairs(BookSide::Ask, CHECKSUM_LEVELS),
                &state.book.top_pairs(BookSide::Bid, CHECKSUM_LEVELS),
                price_precision,
                qty_precision,
            )
        } else {
            f.checksum.wrapping_add(1)
        };

        if computed == f.checksum {
            state.checksum_ok = Some(true);
            Vec::new()
        } else {
            state.checksum_ok = Some(false);
            // The book is not trustworthy any more. Dropping it stops a wrong
            // book being sampled at 1 Hz until the resync lands; `snapshot`
            // then returns None rather than a plausible-looking lie.
            state.book.clear();
            metrics::counter!(
                "k2_capture_checksum_failures_total",
                "exchange" => EXCHANGE,
                "symbol" => f.symbol.to_string(),
            )
            .increment(1);
            metrics::counter!("k2_capture_resyncs_total", "exchange" => EXCHANGE).increment(1);
            tracing::warn!(
                symbol = f.symbol,
                expected = f.checksum,
                computed,
                "kraken book checksum mismatch, resyncing"
            );
            vec![Action::Resubscribe(f.symbol.to_string())]
        }
    }

    /// Park a book frame that arrived before its pair's precision did.
    fn park(&mut self, symbol: &str, recv_ts_ns: i64, conn_msg_seq: i64, bytes: &[u8]) {
        let queue = self.pending.entry(symbol.to_string()).or_default();
        if queue.len() >= MAX_PENDING_FRAMES {
            tracing::warn!(
                symbol,
                "no instrument precision after {MAX_PENDING_FRAMES} book frames, dropping the oldest"
            );
            queue.remove(0);
        }
        queue.push(PendingFrame {
            recv_ts_ns,
            conn_msg_seq,
            bytes: bytes.to_vec(),
        });
    }

    /// Replay parked frames for every symbol whose precision has now arrived,
    /// in receipt order.
    fn drain_pending(&mut self) -> Vec<Action> {
        let ready: Vec<String> = self
            .pending
            .keys()
            .filter(|s| self.precision.contains_key(*s))
            .cloned()
            .collect();
        let mut actions = Vec::new();
        for symbol in ready {
            let Some(frames) = self.pending.remove(&symbol) else {
                continue;
            };
            for pf in frames {
                let Ok(envelope) = serde_json::from_slice::<Envelope>(&pf.bytes) else {
                    continue;
                };
                let frame_type = envelope.typ.unwrap_or("");
                if let Body::Book(datas) = Body::parse(&envelope) {
                    for data in datas.iter().filter(|d| d.symbol == symbol) {
                        actions.extend(self.apply_book(
                            data,
                            frame_type,
                            pf.recv_ts_ns,
                            pf.conn_msg_seq,
                        ));
                    }
                }
            }
        }
        actions
    }

    fn parse_units(&self, text: &str, symbol: &str, field: &str) -> Option<i64> {
        match parse_fixed(text) {
            Ok(v) => Some(v),
            Err(e) => {
                tracing::warn!(
                    symbol,
                    field,
                    text,
                    ?e,
                    "kraken decimal rejected, record dropped"
                );
                count_decimal_error(e);
                None
            }
        }
    }
}

/// `k2_capture_precision_loss_total` - a decimal the 1e-8 contract cannot hold
/// exactly. The record is dropped, never rounded: a rounded price is a wrong
/// price that looks right forever, a counter is a bug someone fixes.
///
/// No `symbol` label: five reasons times eleven instruments is fifty-five
/// series for something that should never tick, and the offending symbol and
/// text are already in the log line next to every increment.
fn count_decimal_error(e: DecimalError) {
    let reason = match e {
        DecimalError::TooManyDecimals => "too_many_dp",
        DecimalError::Scientific => "scientific",
        DecimalError::Malformed => "malformed",
        DecimalError::Overflow => "overflow",
        DecimalError::Negative => "negative",
    };
    metrics::counter!(
        "k2_capture_precision_loss_total",
        "exchange" => EXCHANGE,
        "reason" => reason,
    )
    .increment(1);
}

/// Kraken v2 book checksum.
///
/// <https://docs.kraken.com/api/docs/guides/spot-ws-book-v2>: format the top 10
/// asks then the top 10 bids as `price` and `qty` at the pair's precision, drop
/// the decimal point, strip leading zeros, concatenate, CRC32 the lot. Asks come
/// first; swapping the two sides produces a plausible-looking wrong number, so
/// the doc example below is the test that pins it.
///
/// Inputs are 1e-8 fixed-point units and the formatting is integer division -
/// an `f64` round trip is lossy past 15 significant digits and would desync the
/// book while the checksum reported success (ADR-018 Appendix A, S1).
pub fn book_checksum(
    asks: &[(i64, i64)],
    bids: &[(i64, i64)],
    price_precision: u32,
    qty_precision: u32,
) -> u32 {
    let mut hasher = crc32fast::Hasher::new();
    for side in [asks, bids] {
        for &(px, qty) in side.iter().take(CHECKSUM_LEVELS) {
            hasher.update(checksum_digits(px, price_precision).as_bytes());
            hasher.update(checksum_digits(qty, qty_precision).as_bytes());
        }
    }
    hasher.finalize()
}

/// RFC 3339 with microseconds, as Kraken writes it, to microseconds since the
/// epoch. `None` on anything else - a timestamp we cannot read is not a
/// timestamp to invent.
fn parse_micros(ts: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(ts)
        .ok()
        .and_then(|dt| dt.timestamp_micros().into())
}

// ── wire types ──────────────────────────────────────────────────────────────
//
// Prices and quantities are `&RawValue`, i.e. the exact JSON text the venue
// sent. Deserialising them as `f64` and re-rendering would be the lossy step
// this whole design exists to avoid.

#[derive(Deserialize)]
struct Envelope<'a> {
    #[serde(borrow, default)]
    channel: Option<&'a str>,
    #[serde(borrow, rename = "type", default)]
    typ: Option<&'a str>,
    #[serde(borrow, default)]
    method: Option<&'a str>,
    #[serde(borrow, default)]
    data: Option<&'a RawValue>,
}

impl<'a> Envelope<'a> {
    /// The `stream` field of `RawMessage`, as the venue names it. Control
    /// frames - subscribe acknowledgements, errors - are kept, not dropped:
    /// they are exactly what explains why a symbol went quiet.
    fn stream_name(&self) -> &'a str {
        match (self.channel, self.method) {
            (Some(c), _) => c,
            (None, Some(_)) => "control",
            _ => "unknown",
        }
    }
}

enum Body<'a> {
    Book(Vec<BookData<'a>>),
    Trade(Vec<TradeData<'a>>),
    Instrument(Vec<PairInfo<'a>>),
    Other,
}

impl<'a> Body<'a> {
    fn parse(e: &Envelope<'a>) -> Body<'a> {
        let Some(data) = e.data else {
            return Body::Other;
        };
        match e.channel {
            Some("book") => serde_json::from_str(data.get()).map(Body::Book),
            Some("trade") => serde_json::from_str(data.get()).map(Body::Trade),
            Some("instrument") => serde_json::from_str::<InstrumentData>(data.get())
                .map(|d| Body::Instrument(d.pairs)),
            _ => return Body::Other,
        }
        .unwrap_or_else(|e| {
            tracing::warn!(error = %e, "kraken frame body did not match its channel");
            Body::Other
        })
    }

    /// The one instrument this frame concerns, or `None` if it concerns none or
    /// several - `RawMessage.symbol` is nullable precisely for this.
    fn single_symbol(&self) -> Option<&'a str> {
        let symbols: Vec<&str> = match self {
            Body::Book(v) => v.iter().map(|d| d.symbol).collect(),
            Body::Trade(v) => v.iter().map(|d| d.symbol).collect(),
            _ => return None,
        };
        let first = *symbols.first()?;
        symbols.iter().all(|s| *s == first).then_some(first)
    }
}

#[derive(Deserialize)]
struct Level<'a> {
    #[serde(borrow)]
    price: &'a RawValue,
    #[serde(borrow)]
    qty: &'a RawValue,
}

#[derive(Deserialize)]
struct BookData<'a> {
    #[serde(borrow)]
    symbol: &'a str,
    #[serde(borrow, default)]
    bids: Vec<Level<'a>>,
    #[serde(borrow, default)]
    asks: Vec<Level<'a>>,
    checksum: u32,
    #[serde(borrow, default)]
    timestamp: Option<&'a str>,
}

#[derive(Deserialize)]
struct TradeData<'a> {
    #[serde(borrow)]
    symbol: &'a str,
    #[serde(borrow)]
    side: &'a str,
    #[serde(borrow)]
    price: &'a RawValue,
    #[serde(borrow)]
    qty: &'a RawValue,
    #[serde(borrow)]
    trade_id: &'a RawValue,
    #[serde(borrow)]
    timestamp: &'a str,
}

#[derive(Deserialize)]
struct InstrumentData<'a> {
    #[serde(borrow, default)]
    pairs: Vec<PairInfo<'a>>,
}

#[derive(Deserialize)]
struct PairInfo<'a> {
    #[serde(borrow)]
    symbol: &'a str,
    price_precision: u32,
    qty_precision: u32,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Exchange;

    // The Kraken doc's worked example: BTC/USD, price_precision 1,
    // qty_precision 8. The doc publishes only the concatenated digit strings;
    // spike S1 recovered the levels by a unique backtracking parse and pinned
    // them with the reconstruction test below. Values here are 1e-8 units.
    const ASKS: [(i64, i64); 10] = [
        (4_528_520_000_000, 100_000),
        (4_528_640_000_000, 154_571_953),
        (4_528_660_000_000, 154_571_109),
        (4_528_960_000_000, 154_560_911),
        (4_529_020_000_000, 15_890_660),
        (4_529_180_000_000, 154_553_491),
        (4_529_470_000_000, 4_454_749),
        (4_529_610_000_000, 35_380_000),
        (4_529_750_000_000, 9_945_542),
        (4_529_950_000_000, 18_772_827),
    ];
    const BIDS: [(i64, i64); 10] = [
        (4_528_350_000_000, 10_000_000),
        (4_528_340_000_000, 154_582_015),
        (4_528_210_000_000, 10_000_000),
        (4_528_100_000_000, 10_000_000),
        (4_528_030_000_000, 154_592_586),
        (4_527_900_000_000, 7_990_000),
        (4_527_760_000_000, 3_310_103),
        (4_527_750_000_000, 30_000_000),
        (4_527_730_000_000, 154_602_737),
        (4_527_660_000_000, 15_445_238),
    ];
    const ASKS_STR: &str = "45285210000045286415457195345286615457110945289615456091145290215890660452918154553491452947445474945296135380000452975994554245299518772827";
    const BIDS_STR: &str = "452835100000004528341545820154528211000000045281010000000452803154592586452790799000045277633101034527753000000045277315460273745276615445238";

    /// The number Kraken publishes for the example above.
    #[test]
    fn doc_example_checksum() {
        assert_eq!(book_checksum(&ASKS, &BIDS, 1, 8), 3_310_070_434);
    }

    /// Proves the reconstructed levels are the doc's levels, byte for byte,
    /// so the checksum test above is testing the algorithm and not a
    /// coincidence between two wrong numbers.
    #[test]
    fn doc_example_digit_strings() {
        let render = |levels: &[(i64, i64)], pp, qp| {
            levels
                .iter()
                .map(|&(px, qty)| checksum_digits(px, pp) + &checksum_digits(qty, qp))
                .collect::<String>()
        };
        assert_eq!(render(&ASKS, 1, 8), ASKS_STR);
        assert_eq!(render(&BIDS, 1, 8), BIDS_STR);
    }

    /// Side order is load-bearing and easy to get backwards.
    #[test]
    fn swapping_the_sides_changes_the_checksum() {
        assert_ne!(
            book_checksum(&BIDS, &ASKS, 1, 8),
            book_checksum(&ASKS, &BIDS, 1, 8)
        );
    }

    fn adapter() -> KrakenAdapter {
        let yaml =
            "version: 2\ninstruments:\n  kraken:\n    - { native: BTC/USD, canonical: BTC/USD }\n";
        let mut a =
            KrakenAdapter::new(Instruments::parse(yaml, Exchange::Kraken).unwrap()).unwrap();
        a.begin_connection("test-conn");
        a
    }

    fn instrument_frame() -> &'static str {
        r#"{"channel":"instrument","type":"snapshot","data":{"assets":[],"pairs":[{"symbol":"BTC/USD","price_precision":1,"qty_precision":8}]}}"#
    }

    #[test]
    fn every_frame_produces_a_raw_record_with_a_monotonic_counter() {
        let mut a = adapter();
        for (i, frame) in [r#"{"channel":"heartbeat"}"#, instrument_frame(), "not json"]
            .into_iter()
            .enumerate()
        {
            let h = a.handle_frame(frame.as_bytes(), 1_000 + i as i64);
            let OutRecord::Raw(raw) = &h.records[0] else {
                panic!("first record is not the raw frame")
            };
            assert_eq!(raw.conn_msg_seq, i as i64 + 1);
            assert_eq!(raw.payload, frame.as_bytes());
            assert_eq!(raw.conn_id, "test-conn");
        }
    }

    #[test]
    fn unparseable_frames_are_archived_not_dropped() {
        let mut a = adapter();
        let h = a.handle_frame(b"{oops", 1);
        assert_eq!(h.stream, "unparseable");
        assert_eq!(h.records.len(), 1);
    }

    #[test]
    fn trade_frame_becomes_a_trade_record() {
        let mut a = adapter();
        let frame = r#"{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"sell","price":78568.6,"qty":0.04244195,"ord_type":"limit","trade_id":105994519,"timestamp":"2026-08-26T11:34:06.075373Z"}]}"#;
        let h = a.handle_frame(frame.as_bytes(), 9_999);
        let OutRecord::Trade(t) = &h.records[1] else {
            panic!("no trade record")
        };
        assert_eq!(t.price, 7_856_860_000_000);
        assert_eq!(t.qty, 4_244_195);
        assert_eq!(t.side, Side::Sell);
        assert_eq!(t.trade_id, "105994519", "integer id stringified verbatim");
        assert_eq!(
            t.exchange_ts, 1_787_744_046_075_373,
            "2026-08-26T11:34:06.075373Z"
        );
        assert_eq!(t.recv_ts_ns, 9_999);
        assert_eq!(t.seq, 0, "kraken v2 does not sequence");
        assert_eq!(t.canonical_symbol, "BTC/USD");
    }

    /// Book frames that beat the `instrument` snapshot are parked, then applied
    /// in receipt order once precision is known - not dropped, and not applied
    /// unverified.
    #[test]
    fn book_frames_are_parked_until_precision_arrives() {
        let mut a = adapter();
        let snapshot = format!(
            r#"{{"channel":"book","type":"snapshot","data":[{{"symbol":"BTC/USD","bids":[{{"price":45283.5,"qty":0.1}}],"asks":[{{"price":45285.2,"qty":0.001}}],"checksum":{},"timestamp":"2026-08-26T07:44:36.205375Z"}}]}}"#,
            book_checksum(
                &[(4_528_520_000_000, 100_000)],
                &[(4_528_350_000_000, 10_000_000)],
                1,
                8
            )
        );

        let h = a.handle_frame(snapshot.as_bytes(), 100);
        assert_eq!(
            h.records.len(),
            1,
            "raw only; the book cannot be verified yet"
        );
        assert!(a.snapshot("BTC/USD", 200).is_none());

        a.handle_frame(instrument_frame().as_bytes(), 300);
        let snap = a.snapshot("BTC/USD", 400).expect("book after the drain");
        assert_eq!(snap.bid_px, vec![4_528_350_000_000]);
        assert_eq!(snap.ask_px, vec![4_528_520_000_000]);
        assert_eq!(snap.checksum_ok, Some(true));
        assert_eq!(snap.recv_ts_ns, 100, "receipt time of the parked frame");
        assert_eq!(snap.snapshot_ts_ns, 400, "the sampler's clock, passed in");
        assert_eq!(snap.seq, 0);
    }

    #[test]
    fn a_bad_checksum_resyncs_and_marks_the_snapshot() {
        let mut a = adapter();
        a.handle_frame(instrument_frame().as_bytes(), 1);
        let frame = r#"{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD","bids":[{"price":45283.5,"qty":0.1}],"asks":[{"price":45285.2,"qty":0.001}],"checksum":1,"timestamp":"2026-08-26T07:44:36.205375Z"}]}"#;
        let h = a.handle_frame(frame.as_bytes(), 2);
        assert_eq!(h.actions, vec![Action::Resubscribe("BTC/USD".into())]);
        assert!(
            a.snapshot("BTC/USD", 3).is_none(),
            "a book that failed its checksum is dropped, not sampled"
        );
        let msgs = a.resubscribe_messages("BTC/USD");
        assert!(msgs[0].contains("unsubscribe") && msgs[1].contains("\"snapshot\":true"));
    }

    /// `config/instruments.yaml` must keep the v1 spellings while the Kotlin
    /// handlers read it, so the v2 adapter has to translate both ways: subscribe
    /// with the v2 name, and match the frames that come back on it.
    #[test]
    fn v1_registry_spellings_subscribe_and_match_as_v2() {
        let yaml = "version: 2\ninstruments:\n  kraken:\n                        - { native: XBT/USD, canonical: BTC/USD }\n                        - { native: XDG/USD, canonical: DOGE/USD }\n                        - { native: ETH/USD, canonical: ETH/USD }\n";
        let mut a =
            KrakenAdapter::new(Instruments::parse(yaml, Exchange::Kraken).unwrap()).unwrap();
        a.begin_connection("test-conn");

        assert_eq!(
            a.symbols(),
            vec!["BTC/USD", "DOGE/USD", "ETH/USD"],
            "registry order kept, base assets translated, ETH left alone"
        );
        let book_subscribe = &a.subscribe_messages()[1];
        assert!(book_subscribe.contains("\"BTC/USD\""));
        assert!(
            !book_subscribe.contains("XBT"),
            "a v1 spelling reached the wire"
        );

        // A frame on the v2 wire name resolves to the registry's canonical.
        let frame = r#"{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"buy","price":78568.7,"qty":0.001,"ord_type":"limit","trade_id":1,"timestamp":"2026-08-26T11:34:06.075373Z"}]}"#;
        let h = a.handle_frame(frame.as_bytes(), 1);
        let OutRecord::Trade(t) = &h.records[1] else {
            panic!("no trade record for the v2 spelling")
        };
        assert_eq!(t.symbol, "BTC/USD", "wire spelling, per the .avsc contract");
        assert_eq!(t.canonical_symbol, "BTC/USD");

        let doge = r#"{"channel":"trade","type":"update","data":[{"symbol":"DOGE/USD","side":"sell","price":0.1,"qty":1.0,"ord_type":"limit","trade_id":2,"timestamp":"2026-08-26T11:34:06.075373Z"}]}"#;
        let h = a.handle_frame(doge.as_bytes(), 2);
        let OutRecord::Trade(t) = &h.records[1] else {
            panic!("no trade record for XDG -> DOGE")
        };
        assert_eq!(
            (t.symbol.as_str(), t.canonical_symbol.as_str()),
            ("DOGE/USD", "DOGE/USD")
        );
    }

    /// A registry that lists both spellings of one instrument would silently
    /// lose one after translation; it fails at construction instead.
    #[test]
    fn a_registry_listing_both_spellings_is_rejected() {
        let yaml = "version: 2\ninstruments:\n  kraken:\n                        - { native: XBT/USD, canonical: BTC/USD }\n                        - { native: BTC/USD, canonical: BTC/USD }\n";
        let err = KrakenAdapter::new(Instruments::parse(yaml, Exchange::Kraken).unwrap())
            .unwrap_err()
            .to_string();
        assert!(err.contains("twice"), "{err}");
    }

    #[test]
    fn subscribe_messages_lead_with_instrument() {
        let a = adapter();
        let msgs = a.subscribe_messages();
        assert_eq!(msgs.len(), 3);
        assert!(msgs[0].contains("\"channel\":\"instrument\""));
        assert!(msgs[1].contains("\"channel\":\"book\"") && msgs[1].contains("\"depth\":25"));
        assert!(msgs[2].contains("\"channel\":\"trade\""));
    }

    #[test]
    fn begin_connection_resets_every_per_connection_fact() {
        let mut a = adapter();
        a.handle_frame(instrument_frame().as_bytes(), 1);
        assert!(!a.precision.is_empty());
        a.begin_connection("second-conn");
        assert!(a.precision.is_empty() && a.books.is_empty() && a.conn_msg_seq == 0);
    }
}
