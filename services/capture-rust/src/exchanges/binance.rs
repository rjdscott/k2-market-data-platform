//! Binance spot combined stream: `<sym>@trade` + `<sym>@depth20@100ms`.
//!
//! The combined endpoint (`/stream?streams=a/b/c`) carries the subscription in
//! the URL, so there is nothing to send after the socket opens and
//! [`BinanceAdapter::subscribe_messages`] is empty. Every frame is one envelope,
//! `{"stream":"btcusdt@depth20@100ms","data":{...}}`.
//!
//! Three facts about this venue that shape the code (ADR-027):
//!
//! - **The book is stateless.** `@depth20@100ms` is a *partial* stream: every
//!   frame is a complete top-20 on each side, so the local book is replaced
//!   outright and no delta is ever applied. Level 21 is recoverable by replay of
//!   the raw topic, not by this process.
//! - **`lastUpdateId` is the only continuity signal, and it must strictly
//!   increase per symbol.** A smaller-or-equal id means the stream went
//!   backwards - a replayed or reordered frame - and the frame is dropped, the
//!   book cleared and `k2_capture_gaps_total` ticked. No timestamp travels on
//!   the partial stream, so `BookSnapshotL2.exchange_ts` is `None` rather than
//!   a fabricated clock reading; `checksum_ok` is `None` because the venue
//!   publishes no checksum.
//! - **Trades are not sequenced.** `t` is the trade id, not a sequence, so
//!   `Trade.seq` is `0` ("this venue does not sequence this stream") and
//!   `trade_id` carries `t`.
//!
//! Stream names spell the symbol in lowercase (`btcusdt`) while the registry
//! and the trade payload spell it `BTCUSDT`; the lowercase -> native map is
//! built once in [`BinanceAdapter::new`] so the hot path is one `BTreeMap` get.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::value::RawValue;

use super::{Action, Handled, count_decimal_error, count_unknown};
use crate::book::{Book, Side as BookSide};
use crate::config::Instruments;
use crate::decimal::parse_fixed;
use crate::record::{BookSnapshotRecord, OutRecord, RawMessageRecord, Side, TradeRecord};

const EXCHANGE: &str = "binance";
/// The partial stream is 20 deep and so is the product (ADR-018).
const SNAPSHOT_LEVELS: usize = 20;
/// Stream suffixes as they appear in the combined-stream URL.
const TRADE_STREAM: &str = "trade";
const DEPTH_STREAM: &str = "depth20@100ms";

/// Per-symbol book state. Reset on reconnect and on a `lastUpdateId` regression.
#[derive(Debug, Default)]
struct BookState {
    book: Book,
    /// `lastUpdateId` of the frame currently in `book`.
    last_update_id: i64,
    last_recv_ts_ns: i64,
    /// Frame counter at that point - the foreign key into the raw topic.
    last_conn_msg_seq: i64,
}

#[derive(Debug)]
pub struct BinanceAdapter {
    instruments: Instruments,
    /// lowercase stream spelling -> registry native (`btcusdt` -> `BTCUSDT`).
    by_lower: BTreeMap<String, String>,
    conn_id: String,
    conn_msg_seq: i64,
    books: BTreeMap<String, BookState>,
}

impl BinanceAdapter {
    pub fn new(instruments: Instruments) -> Self {
        let by_lower = instruments
            .natives()
            .into_iter()
            .map(|n| (n.to_ascii_lowercase(), n))
            .collect();
        Self {
            instruments,
            by_lower,
            conn_id: String::new(),
            conn_msg_seq: 0,
            books: BTreeMap::new(),
        }
    }

    pub fn begin_connection(&mut self, conn_id: &str) {
        self.conn_id = conn_id.to_string();
        self.conn_msg_seq = 0;
        // `lastUpdateId` continuity is meaningless across a connection boundary
        // and the first partial frame is a complete book, so nothing survives.
        self.books.clear();
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

    /// The `streams=` query the combined endpoint wants, in registry order:
    /// `btcusdt@trade/btcusdt@depth20@100ms/...`. The caller appends it to
    /// [`crate::config::Exchange::default_ws_url`]; there is no subscribe frame.
    pub fn stream_query(&self) -> String {
        self.instruments
            .natives()
            .iter()
            .map(|n| n.to_ascii_lowercase())
            .flat_map(|s| [format!("{s}@{TRADE_STREAM}"), format!("{s}@{DEPTH_STREAM}")])
            .collect::<Vec<_>>()
            .join("/")
    }

    /// Empty: on the combined endpoint the URL *is* the subscription.
    pub fn subscribe_messages(&self) -> Vec<String> {
        Vec::new()
    }

    /// Empty: the partial stream cannot be resubscribed per symbol, and does
    /// not need to be - the next frame is a complete top-20. The
    /// [`Action::Resubscribe`] this adapter raises on a regression therefore
    /// sends nothing; it is the "book dropped, resync pending" signal, and the
    /// resync is the next in-order frame.
    pub fn resubscribe_messages(&self, _native: &str) -> Vec<String> {
        Vec::new()
    }

    pub fn handle_frame(&mut self, bytes: &[u8], recv_ts_ns: i64) -> Handled {
        self.conn_msg_seq += 1;
        let conn_msg_seq = self.conn_msg_seq;

        let envelope: Option<Envelope> = serde_json::from_slice(bytes).ok();
        let (stream, native) = match &envelope {
            Some(e) => self.route(e.stream),
            None => ("unparseable", None),
        };

        let mut out = Handled {
            stream: stream.to_string(),
            records: vec![OutRecord::Raw(RawMessageRecord {
                exchange: EXCHANGE.into(),
                stream: stream.to_string(),
                symbol: native.clone(),
                recv_ts_ns,
                conn_id: self.conn_id.clone(),
                conn_msg_seq,
                payload: bytes.to_vec(),
            })],
            actions: Vec::new(),
        };

        if matches!(stream, "unknown" | "unparseable") {
            count_unknown(EXCHANGE, stream);
        }
        let (Some(envelope), Some(native)) = (envelope, native) else {
            return out;
        };
        match stream {
            "trade" => match serde_json::from_str::<TradeData>(envelope.data.get()) {
                Ok(t) => {
                    if let Some(rec) = self.trade_record(&native, &t, recv_ts_ns, conn_msg_seq) {
                        out.records.push(OutRecord::Trade(rec));
                    }
                }
                Err(e) => {
                    tracing::warn!(error = ?e, "binance trade did not parse");
                    count_unknown(EXCHANGE, stream);
                }
            },
            "depth20" => match serde_json::from_str::<DepthData>(envelope.data.get()) {
                Ok(d) => {
                    out.actions
                        .extend(self.replace_book(&native, &d, recv_ts_ns, conn_msg_seq))
                }
                Err(e) => {
                    tracing::warn!(error = ?e, "binance depth did not parse");
                    count_unknown(EXCHANGE, stream);
                }
            },
            _ => {}
        }
        out
    }

    /// Top-20 snapshot of one symbol's book. `None` before the first depth
    /// frame, and after a regression until the next in-order frame.
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
            seq: state.last_update_id,
            checksum_ok: None,
            bid_px: top.bid_px,
            bid_qty: top.bid_qty,
            ask_px: top.ask_px,
            ask_qty: top.ask_qty,
            // The partial stream carries no venue timestamp (ADR-027).
            exchange_ts: None,
            recv_ts_ns: state.last_recv_ts_ns,
            snapshot_ts_ns: now_ns,
            conn_id: self.conn_id.clone(),
            conn_msg_seq: state.last_conn_msg_seq,
        })
    }

    // ── internals ───────────────────────────────────────────────────────────

    /// `btcusdt@depth20@100ms` -> (`depth20`, `BTCUSDT`). The label is the
    /// first segment after the symbol - the `@100ms` cadence is not a stream.
    /// A symbol the registry does not list yields `None`, which makes the frame
    /// raw-only; it is still counted under its stream label.
    fn route(&self, stream: &str) -> (&'static str, Option<String>) {
        let mut parts = stream.split('@');
        let symbol = parts.next().unwrap_or("");
        let label = match parts.next() {
            Some("trade") => "trade",
            Some("depth20") => "depth20",
            _ => return ("unknown", None),
        };
        (label, self.by_lower.get(symbol).cloned())
    }

    fn trade_record(
        &self,
        native: &str,
        t: &TradeData<'_>,
        recv_ts_ns: i64,
        conn_msg_seq: i64,
    ) -> Option<TradeRecord> {
        let canonical = self.instruments.canonical(native)?;
        let price = parse_units(t.price, native, "price")?;
        let qty = parse_units(t.qty, native, "qty")?;
        Some(TradeRecord {
            exchange: EXCHANGE.into(),
            symbol: native.to_string(),
            canonical_symbol: canonical.to_string(),
            // Raw JSON text of `t`, so 6621701993 stays 6621701993.
            trade_id: t.trade_id.get().to_string(),
            price,
            qty,
            side: aggressor_side(t.buyer_is_maker),
            // `T` is the match time in milliseconds; `E` is when the event was
            // emitted and is not the trade's time.
            exchange_ts: t.trade_time_ms.checked_mul(1_000)?,
            recv_ts_ns,
            seq: 0,
            conn_id: self.conn_id.clone(),
            conn_msg_seq,
        })
    }

    /// Replace the symbol's book with this frame, or drop the frame if its
    /// `lastUpdateId` does not advance.
    fn replace_book(
        &mut self,
        native: &str,
        d: &DepthData<'_>,
        recv_ts_ns: i64,
        conn_msg_seq: i64,
    ) -> Vec<Action> {
        let prev = self.books.get(native).map(|s| s.last_update_id);
        if prev.is_some_and(|prev| d.last_update_id <= prev) {
            // The stream went backwards. The book we hold is newer than this
            // frame, but we no longer know whether the *next* one will be, so
            // drop it: `snapshot` returns None instead of a plausible lie until
            // an in-order frame rebuilds it.
            self.books.remove(native);
            metrics::counter!("k2_capture_gaps_total", "exchange" => EXCHANGE).increment(1);
            metrics::counter!("k2_capture_resyncs_total", "exchange" => EXCHANGE).increment(1);
            tracing::warn!(
                symbol = native,
                prev,
                got = d.last_update_id,
                "binance lastUpdateId regressed, book dropped"
            );
            return vec![Action::Resubscribe(native.to_string())];
        }

        let state = self.books.entry(native.to_string()).or_default();
        state.book.clear();
        for (side, levels) in [(BookSide::Bid, &d.bids), (BookSide::Ask, &d.asks)] {
            for (px, qty) in levels {
                match (parse_fixed(px), parse_fixed(qty)) {
                    (Ok(px), Ok(qty)) => state.book.apply(side, px, qty),
                    (px, qty) => {
                        for e in [px.err(), qty.err()].into_iter().flatten() {
                            count_decimal_error(EXCHANGE, e);
                        }
                    }
                }
            }
        }
        state.last_update_id = d.last_update_id;
        state.last_recv_ts_ns = recv_ts_ns;
        state.last_conn_msg_seq = conn_msg_seq;
        Vec::new()
    }
}

/// `m` is "buyer is the maker": the resting order was a bid, so the aggressor
/// sold. `Trade.side` is the taker side, hence `m == true` -> `Sell`.
fn aggressor_side(buyer_is_maker: bool) -> Side {
    if buyer_is_maker {
        Side::Sell
    } else {
        Side::Buy
    }
}

fn parse_units(text: &str, symbol: &str, field: &str) -> Option<i64> {
    match parse_fixed(text) {
        Ok(v) => Some(v),
        Err(e) => {
            tracing::warn!(
                symbol,
                field,
                text,
                ?e,
                "binance decimal rejected, record dropped"
            );
            count_decimal_error(EXCHANGE, e);
            None
        }
    }
}

// ── wire types ──────────────────────────────────────────────────────────────
//
// Binance sends prices and quantities as JSON *strings* (`"78436.77000000"`),
// so `&str` already is the exact wire text; only `t` needs `RawValue`.

#[derive(Deserialize)]
struct Envelope<'a> {
    #[serde(borrow)]
    stream: &'a str,
    #[serde(borrow)]
    data: &'a RawValue,
}

#[derive(Deserialize)]
struct TradeData<'a> {
    #[serde(borrow, rename = "t")]
    trade_id: &'a RawValue,
    #[serde(borrow, rename = "p")]
    price: &'a str,
    #[serde(borrow, rename = "q")]
    qty: &'a str,
    #[serde(rename = "T")]
    trade_time_ms: i64,
    #[serde(rename = "m")]
    buyer_is_maker: bool,
}

#[derive(Deserialize)]
struct DepthData<'a> {
    #[serde(rename = "lastUpdateId")]
    last_update_id: i64,
    #[serde(borrow, default)]
    bids: Vec<(&'a str, &'a str)>,
    #[serde(borrow, default)]
    asks: Vec<(&'a str, &'a str)>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Exchange;

    const REGISTRY: &str =
        "version: 2\ninstruments:\n  binance:\n    - { native: BTCUSDT, canonical: BTC/USDT }\n";

    fn adapter() -> BinanceAdapter {
        let mut a = BinanceAdapter::new(Instruments::parse(REGISTRY, Exchange::Binance).unwrap());
        a.begin_connection("conn-1");
        a
    }

    // A frame recorded 2026-08-26 from the live combined stream.
    const TRADE: &str = r#"{"stream":"btcusdt@trade","data":{"e":"trade","E":1787746340684,"s":"BTCUSDT","t":6621701993,"p":"78436.77000000","q":"0.00014000","T":1787746340684,"m":false,"M":true}}"#;

    fn depth(last_update_id: i64, bid: &str, ask: &str) -> String {
        format!(
            r#"{{"stream":"btcusdt@depth20@100ms","data":{{"lastUpdateId":{last_update_id},"bids":[["{bid}","1.00000000"]],"asks":[["{ask}","2.00000000"]]}}}}"#
        )
    }

    #[test]
    fn trade_frame_parses_to_a_trade_record() {
        let h = adapter().handle_frame(TRADE.as_bytes(), 7);
        assert_eq!(h.stream, "trade");
        assert!(
            matches!(&h.records[0], OutRecord::Raw(r) if r.symbol.as_deref() == Some("BTCUSDT"))
        );
        let OutRecord::Trade(t) = &h.records[1] else {
            panic!("no trade record")
        };
        assert_eq!(t.trade_id, "6621701993");
        assert_eq!(t.price, 7_843_677_000_000);
        assert_eq!(t.qty, 14_000);
        assert_eq!(t.exchange_ts, 1_787_746_340_684_000, "T ms -> micros");
        assert_eq!((t.seq, t.recv_ts_ns, t.conn_msg_seq), (0, 7, 1));
        assert_eq!(t.canonical_symbol, "BTC/USDT");
        assert!(h.actions.is_empty());
    }

    #[test]
    fn buyer_is_maker_means_the_aggressor_sold() {
        assert_eq!(aggressor_side(true), Side::Sell);
        assert_eq!(aggressor_side(false), Side::Buy);
        let sell = TRADE.replace(r#""m":false"#, r#""m":true"#);
        let h = adapter().handle_frame(sell.as_bytes(), 0);
        assert!(matches!(&h.records[1], OutRecord::Trade(t) if t.side == Side::Sell));
    }

    #[test]
    fn depth_frame_replaces_the_whole_book() {
        let mut a = adapter();
        assert!(a.snapshot("BTCUSDT", 0).is_none(), "no book before a frame");
        a.handle_frame(depth(10, "100.00", "101.00").as_bytes(), 1);
        a.handle_frame(depth(11, "200.00", "201.00").as_bytes(), 2);
        let s = a.snapshot("BTCUSDT", 3).unwrap();
        assert_eq!(s.bid_px, vec![20_000_000_000], "old level must not survive");
        assert_eq!(s.ask_px, vec![20_100_000_000]);
        assert_eq!(s.seq, 11);
        assert_eq!((s.checksum_ok, s.exchange_ts), (None, None));
        assert_eq!((s.recv_ts_ns, s.snapshot_ts_ns, s.conn_msg_seq), (2, 3, 2));
    }

    #[test]
    fn last_update_id_regression_drops_the_book_and_asks_for_a_resync() {
        let mut a = adapter();
        a.handle_frame(depth(10, "100.00", "101.00").as_bytes(), 1);
        for stale in [10, 9] {
            let h = a.handle_frame(depth(stale, "100.00", "101.00").as_bytes(), 2);
            assert_eq!(
                h.actions,
                vec![Action::Resubscribe("BTCUSDT".into())],
                "id {stale}"
            );
            assert!(
                a.snapshot("BTCUSDT", 3).is_none(),
                "book survived a regression"
            );
            // The next in-order frame is a complete book again.
            a.handle_frame(depth(12, "100.00", "101.00").as_bytes(), 4);
            assert!(a.snapshot("BTCUSDT", 5).is_some());
        }
    }

    #[test]
    fn unknown_stream_or_symbol_is_raw_only() {
        let mut a = adapter();
        for frame in [
            r#"{"stream":"btcusdt@kline_1m","data":{}}"#,
            r#"{"stream":"ethusdt@trade","data":{}}"#,
            "not json",
        ] {
            let h = a.handle_frame(frame.as_bytes(), 0);
            assert_eq!(h.records.len(), 1, "{frame}");
            assert!(matches!(&h.records[0], OutRecord::Raw(r) if r.symbol.is_none()));
        }
        assert_eq!(a.conn_msg_seq, 3, "every frame counts, parsed or not");
    }
}
