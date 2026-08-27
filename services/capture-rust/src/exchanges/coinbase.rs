//! Coinbase Advanced Trade WebSocket: `level2` + `market_trades` + `heartbeats`
//! on one connection, no JWT (spike S5, ADR-018 Appendix A).
//!
//! Three facts about this venue that shape the code:
//!
//! - **`sequence_num` is connection-wide, across every channel.** Subscribe
//!   acks, heartbeats, trades and book deltas all draw from one counter, so the
//!   continuity check is one `expected == got` per frame, not per channel. A
//!   gap invalidates *every* book on the connection: there is no way to know
//!   which product the missing frame carried, and a book with one lost delta
//!   is wrong forever - `level2` sends absolute `new_quantity` per level and
//!   there is no per-product resync short of a fresh snapshot. The policy is
//!   therefore reconnect-and-resnapshot (ADR-027): this adapter reports one
//!   [`Action::Reconnect`] and drops its books, so `snapshot` returns `None`
//!   rather than a plausible-looking lie until the new connection's snapshots
//!   land.
//! - **Full depth, no top-N option.** The `level2` snapshot is the whole book
//!   (5.2 MB / 43,974 levels for BTC-USD in S5), so the local `BTreeMap` holds
//!   everything and top-20 is a truncation at sample time. Memory is watched
//!   through `k2_capture_book_levels_total`, updated after every apply.
//! - **No checksum, so `checksum_ok` is `None`** - "unanswerable", never
//!   collapsed to `true`.
//!
//! Timestamps: the frame's `timestamp` is RFC 3339 with nanoseconds; each
//! book level carries its own `event_time` (microseconds). A snapshot's
//! `exchange_ts` is the latest `event_time` folded into that symbol's book,
//! because that is the last instant the venue vouches for the book's state.
//! Trade `side` is the **taker** side, published in upper case (`BUY`/`SELL`);
//! it is matched case-insensitively and written as the Avro enum.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::value::RawValue;

use super::{Action, Handled, count_decimal_error, count_unknown, parse_micros};
use crate::book::{Book, Side as BookSide};
use crate::config::Instruments;
use crate::decimal::parse_fixed;
use crate::record::{BookSnapshotRecord, OutRecord, RawMessageRecord, Side, TradeRecord};

const EXCHANGE: &str = "coinbase";
/// Top-N depth of the emitted `BookSnapshotL2` (ADR-027: 20 is the product).
const SNAPSHOT_LEVELS: usize = 20;

/// Per-symbol book state. Everything here is reset on reconnect or gap.
#[derive(Debug, Default)]
struct BookState {
    book: Book,
    last_recv_ts_ns: i64,
    /// Latest `event_time` applied to this book, microseconds since the epoch.
    last_exchange_ts_us: Option<i64>,
    /// Frame counter of the last applied frame - the foreign key into `raw`.
    last_conn_msg_seq: i64,
    /// `sequence_num` of the last applied frame - `BookSnapshotL2.seq`.
    last_seq: i64,
}

#[derive(Debug)]
pub struct CoinbaseAdapter {
    instruments: Instruments,
    conn_id: String,
    conn_msg_seq: i64,
    /// The `sequence_num` the next sequenced frame must carry. `None` until
    /// the first one arrives; Coinbase starts at 0 but the check does not
    /// assume that.
    expected_seq: Option<i64>,
    books: BTreeMap<String, BookState>,
}

impl CoinbaseAdapter {
    /// Infallible, like [`BinanceAdapter::new`](super::BinanceAdapter::new):
    /// the registry natives are the wire spelling, so there is no alias step
    /// to fail. Only Kraken's constructor returns a `Result`, and only because
    /// its v1 -> v2 symbol translation can collide.
    pub fn new(instruments: Instruments) -> Self {
        Self {
            instruments,
            conn_id: String::new(),
            conn_msg_seq: 0,
            expected_seq: None,
            books: BTreeMap::new(),
        }
    }

    pub fn begin_connection(&mut self, conn_id: &str) {
        self.conn_id = conn_id.to_string();
        self.conn_msg_seq = 0;
        self.expected_seq = None;
        self.books.clear();
        self.publish_levels();
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

    /// Three subscribe frames, one per channel, all products in each. S5 sent
    /// exactly this shape and saw no error frames; the documented limit is 8
    /// unauthenticated messages per second per IP, so three is safe.
    pub fn subscribe_messages(&self) -> Vec<String> {
        ["level2", "market_trades", "heartbeats"]
            .into_iter()
            .map(|channel| subscribe(channel, &self.instruments.natives(), "subscribe"))
            .collect()
    }

    /// Unsubscribe then subscribe `level2` for one product. This adapter never
    /// asks for it - a gap is an [`Action::Reconnect`] - but the contract
    /// method exists and these are the frames the venue would want.
    pub fn resubscribe_messages(&self, native: &str) -> Vec<String> {
        let one = [native.to_string()];
        vec![
            subscribe("level2", &one, "unsubscribe"),
            subscribe("level2", &one, "subscribe"),
        ]
    }

    pub fn handle_frame(&mut self, bytes: &[u8], recv_ts_ns: i64) -> Handled {
        self.conn_msg_seq += 1;
        let conn_msg_seq = self.conn_msg_seq;

        let envelope: Option<Envelope> = serde_json::from_slice(bytes).ok();
        let body = envelope.as_ref().map(Body::parse).unwrap_or(Body::Other);
        let stream = match &envelope {
            Some(e) => e.channel.unwrap_or("unknown"),
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
            history: false,
        };

        let Some(envelope) = envelope else {
            count_unknown(EXCHANGE, "unparseable");
            return out;
        };
        if let Some(seq) = envelope.sequence_num {
            out.actions.extend(self.check_sequence(seq));
        }

        match body {
            Body::L2(events) => {
                for e in events {
                    self.apply_l2(&e, recv_ts_ns, conn_msg_seq, envelope.sequence_num);
                }
                self.publish_levels();
            }
            Body::Trades(events) => {
                out.history = events.iter().any(|e| e.typ == "snapshot");
                for t in events.iter().flat_map(|e| &e.trades) {
                    if let Some(rec) =
                        self.trade_record(t, recv_ts_ns, conn_msg_seq, envelope.sequence_num)
                    {
                        out.records.push(OutRecord::Trade(rec));
                    }
                }
            }
            Body::Control => {}
            Body::Other => count_unknown(EXCHANGE, stream),
        }
        out
    }

    /// Top-20 snapshot of one symbol's book. `None` before the first `level2`
    /// snapshot and after a sequence gap - an empty snapshot would look like a
    /// book with no liquidity.
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
            seq: state.last_seq,
            checksum_ok: None,
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

    /// `sequence_num` must be exactly the previous one plus one, on every
    /// sequenced frame. Anything else (a jump forward, a repeat, a regression)
    /// is one gap: the counter ticks once, every book is dropped, and one
    /// [`Action::Reconnect`] is returned. `expected_seq` is re-anchored on the
    /// frame we did get so a gap the caller chooses not to act on is counted
    /// once, not on every frame after it.
    fn check_sequence(&mut self, got: i64) -> Vec<Action> {
        let expected = self.expected_seq.replace(got + 1);
        match expected {
            Some(want) if want != got => {
                metrics::counter!("k2_capture_gaps_total", "exchange" => EXCHANGE).increment(1);
                metrics::counter!("k2_capture_resyncs_total", "exchange" => EXCHANGE).increment(1);
                tracing::warn!(
                    expected = want,
                    got,
                    "coinbase sequence_num gap; every book on this connection is invalid"
                );
                self.books.clear();
                self.publish_levels();
                vec![Action::Reconnect]
            }
            _ => Vec::new(),
        }
    }

    fn apply_l2(&mut self, e: &L2Event<'_>, recv_ts_ns: i64, conn_msg_seq: i64, seq: Option<i64>) {
        if self.instruments.canonical(e.product_id).is_none() {
            return;
        }
        let state = match e.typ {
            "snapshot" => {
                let s = self.books.entry(e.product_id.to_string()).or_default();
                s.book.clear();
                s.last_exchange_ts_us = None;
                s
            }
            // An update with no snapshot behind it (after a gap, or a venue
            // that sends deltas first) would build a partial book that samples
            // as a thin-but-plausible one. Drop it; the raw archive has it.
            "update" => match self.books.get_mut(e.product_id) {
                Some(s) => s,
                None => return,
            },
            other => {
                tracing::warn!(kind = other, "coinbase l2_data event of unknown type");
                return;
            }
        };

        for lvl in &e.updates {
            let side = match lvl.side {
                "bid" => BookSide::Bid,
                "offer" => BookSide::Ask,
                other => {
                    tracing::warn!(side = other, "coinbase level with an unknown side, dropped");
                    continue;
                }
            };
            match (parse_fixed(lvl.price_level), parse_fixed(lvl.new_quantity)) {
                (Ok(px), Ok(qty)) => state.book.apply(side, px, qty),
                (px, qty) => {
                    for err in [px.err(), qty.err()].into_iter().flatten() {
                        count_decimal_error(EXCHANGE, err);
                    }
                }
            }
            if let Some(us) = parse_micros(lvl.event_time) {
                state.last_exchange_ts_us = state.last_exchange_ts_us.max(Some(us));
            }
        }
        state.last_recv_ts_ns = recv_ts_ns;
        state.last_conn_msg_seq = conn_msg_seq;
        state.last_seq = seq.unwrap_or(0);
    }

    fn trade_record(
        &self,
        t: &TradeData<'_>,
        recv_ts_ns: i64,
        conn_msg_seq: i64,
        seq: Option<i64>,
    ) -> Option<TradeRecord> {
        let canonical = self.instruments.canonical(t.product_id)?;
        let price = parse_units(t.price, t.product_id, "price")?;
        let qty = parse_units(t.size, t.product_id, "size")?;
        let side = if t.side.eq_ignore_ascii_case("buy") {
            Side::Buy
        } else if t.side.eq_ignore_ascii_case("sell") {
            Side::Sell
        } else {
            tracing::warn!(
                side = t.side,
                "coinbase trade with an unknown side, dropped"
            );
            return None;
        };
        let exchange_ts = parse_micros(t.time)?;
        Some(TradeRecord {
            exchange: EXCHANGE.into(),
            symbol: t.product_id.to_string(),
            canonical_symbol: canonical.to_string(),
            trade_id: t.trade_id.to_string(),
            price,
            qty,
            side,
            exchange_ts,
            recv_ts_ns,
            seq: seq.unwrap_or(0),
            conn_id: self.conn_id.clone(),
            conn_msg_seq,
        })
    }

    /// `k2_capture_book_levels_total` - the memory proxy for a venue whose
    /// books are full depth (S5: 44k levels for one product).
    fn publish_levels(&self) {
        metrics::gauge!("k2_capture_book_levels_total", "exchange" => EXCHANGE)
            .set(self.total_levels() as f64);
    }
}

fn subscribe(channel: &str, product_ids: &[String], typ: &str) -> String {
    serde_json::json!({"type": typ, "product_ids": product_ids, "channel": channel}).to_string()
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
                "coinbase decimal rejected, record dropped"
            );
            count_decimal_error(EXCHANGE, e);
            None
        }
    }
}

// ── wire types ──────────────────────────────────────────────────────────────
//
// Prices and quantities are JSON strings on this venue, borrowed as `&str`
// and handed to `parse_fixed` untouched.

#[derive(Deserialize)]
struct Envelope<'a> {
    #[serde(borrow, default)]
    channel: Option<&'a str>,
    #[serde(default)]
    sequence_num: Option<i64>,
    #[serde(borrow, default)]
    events: Option<&'a RawValue>,
}

enum Body<'a> {
    L2(Vec<L2Event<'a>>),
    Trades(Vec<TradeEvent<'a>>),
    /// `subscriptions` and `heartbeats`: archived, sequenced, nothing derived.
    Control,
    Other,
}

impl<'a> Body<'a> {
    fn parse(e: &Envelope<'a>) -> Body<'a> {
        let (Some(channel), Some(events)) = (e.channel, e.events) else {
            return Body::Other;
        };
        match channel {
            "l2_data" => serde_json::from_str(events.get()).map(Body::L2),
            "market_trades" => serde_json::from_str(events.get()).map(Body::Trades),
            "subscriptions" | "heartbeats" => Ok(Body::Control),
            _ => return Body::Other,
        }
        .unwrap_or_else(|err| {
            tracing::warn!(error = ?err, channel, "coinbase frame body did not match its channel");
            Body::Other
        })
    }

    /// The one product this frame concerns, or `None` if it concerns none or
    /// several - `RawMessage.symbol` is nullable precisely for this.
    fn single_symbol(&self) -> Option<&'a str> {
        let symbols: Vec<&str> = match self {
            Body::L2(v) => v.iter().map(|e| e.product_id).collect(),
            Body::Trades(v) => v
                .iter()
                .flat_map(|e| e.trades.iter().map(|t| t.product_id))
                .collect(),
            _ => return None,
        };
        let first = *symbols.first()?;
        symbols.iter().all(|s| *s == first).then_some(first)
    }
}

#[derive(Deserialize)]
struct L2Event<'a> {
    #[serde(borrow, rename = "type")]
    typ: &'a str,
    #[serde(borrow)]
    product_id: &'a str,
    #[serde(borrow, default)]
    updates: Vec<L2Level<'a>>,
}

#[derive(Deserialize)]
struct L2Level<'a> {
    #[serde(borrow)]
    side: &'a str,
    #[serde(borrow)]
    event_time: &'a str,
    #[serde(borrow)]
    price_level: &'a str,
    #[serde(borrow)]
    new_quantity: &'a str,
}

#[derive(Deserialize)]
struct TradeEvent<'a> {
    /// `snapshot` on subscribe (recent history), `update` live.
    #[serde(borrow, default, rename = "type")]
    typ: &'a str,
    #[serde(borrow, default)]
    trades: Vec<TradeData<'a>>,
}

#[derive(Deserialize)]
struct TradeData<'a> {
    #[serde(borrow)]
    product_id: &'a str,
    #[serde(borrow)]
    trade_id: &'a str,
    #[serde(borrow)]
    price: &'a str,
    #[serde(borrow)]
    size: &'a str,
    #[serde(borrow)]
    time: &'a str,
    #[serde(borrow)]
    side: &'a str,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Exchange;

    fn adapter() -> CoinbaseAdapter {
        let yaml = "version: 2\ninstruments:\n  coinbase:\n    - { native: ATOM-USD, canonical: ATOM/USD }\n    - { native: DOT-USD, canonical: DOT/USD }\n";
        let mut a = CoinbaseAdapter::new(Instruments::parse(yaml, Exchange::Coinbase).unwrap());
        a.begin_connection("test-conn");
        a
    }

    fn l2(seq: i64, typ: &str, levels: &[(&str, &str, &str)]) -> String {
        let updates: Vec<String> = levels
            .iter()
            .map(|(side, px, qty)| {
                format!(
                    r#"{{"side":"{side}","event_time":"2026-08-26T12:13:45.754166Z","price_level":"{px}","new_quantity":"{qty}"}}"#
                )
            })
            .collect();
        format!(
            r#"{{"channel":"l2_data","timestamp":"2026-08-26T12:13:45.818626985Z","sequence_num":{seq},"events":[{{"type":"{typ}","product_id":"ATOM-USD","updates":[{}]}}]}}"#,
            updates.join(",")
        )
    }

    fn heartbeat(seq: i64) -> String {
        format!(
            r#"{{"channel":"heartbeats","timestamp":"2026-08-26T12:14:04.888417268Z","sequence_num":{seq},"events":[{{"current_time":"2026-08-26 12:14:04.885181187 +0000 UTC m=+24626.287167218","heartbeat_counter":24626}}]}}"#
        )
    }

    const SNAPSHOT: &[(&str, &str, &str)] = &[
        ("bid", "1.5577", "92.81"),
        ("bid", "1.5576", "160.49"),
        ("offer", "1.5589", "256.59"),
        ("offer", "1.5590", "10"),
    ];

    #[test]
    fn every_frame_produces_a_raw_record_first() {
        let mut a = adapter();
        for (i, frame) in [heartbeat(0), l2(1, "snapshot", SNAPSHOT), "not json".into()]
            .iter()
            .enumerate()
        {
            let h = a.handle_frame(frame.as_bytes(), 1_000 + i as i64);
            let OutRecord::Raw(raw) = &h.records[0] else {
                panic!("first record is not the raw frame")
            };
            assert_eq!(raw.conn_msg_seq, i as i64 + 1);
            assert_eq!(raw.payload, frame.as_bytes());
        }
        assert_eq!(a.handle_frame(b"{oops", 5).stream, "unparseable");
        assert_eq!(
            a.handle_frame(&heartbeat(2).into_bytes(), 6).stream,
            "heartbeats"
        );
    }

    #[test]
    fn trade_frame_becomes_a_trade_record_with_the_frame_sequence() {
        let mut a = adapter();
        let frame = r#"{"channel":"market_trades","timestamp":"2026-08-26T12:13:45.82034548Z","sequence_num":7,"events":[{"type":"snapshot","trades":[{"product_id":"ATOM-USD","trade_id":"62534898","price":"1.5594","size":"5.88","time":"2026-08-26T12:13:40.017221Z","side":"SELL"},{"product_id":"ATOM-USD","trade_id":"62534899","price":"1.5595","size":"1","time":"2026-08-26T12:13:41.000000Z","side":"BUY"}]}]}"#;
        let h = a.handle_frame(frame.as_bytes(), 9_999);
        assert_eq!(h.records.len(), 3, "raw + two trades");
        let OutRecord::Trade(t) = &h.records[1] else {
            panic!("no trade record")
        };
        assert_eq!(t.price, 155_940_000);
        assert_eq!(t.qty, 588_000_000);
        assert_eq!(t.side, Side::Sell, "upper-case SELL maps to the enum");
        assert_eq!(t.trade_id, "62534898");
        assert_eq!(t.exchange_ts, 1_787_746_420_017_221);
        assert_eq!(t.seq, 7, "the frame's connection-wide sequence_num");
        assert_eq!(t.canonical_symbol, "ATOM/USD");
        let OutRecord::Trade(t2) = &h.records[2] else {
            panic!("no second trade")
        };
        assert_eq!(t2.side, Side::Buy);
        let OutRecord::Raw(raw) = &h.records[0] else {
            panic!()
        };
        assert_eq!(raw.symbol.as_deref(), Some("ATOM-USD"));
        assert!(h.history, "a snapshot event is the venue replaying history");

        let live = frame.replace("\"type\":\"snapshot\"", "\"type\":\"update\"");
        let h = a.handle_frame(live.as_bytes(), 10_000);
        assert_eq!(h.records.len(), 3);
        assert!(!h.history, "an update event is live");
    }

    #[test]
    fn snapshot_then_update_applies_and_zero_qty_removes() {
        let mut a = adapter();
        a.handle_frame(l2(0, "snapshot", SNAPSHOT).as_bytes(), 100);
        let s = a.snapshot("ATOM-USD", 150).expect("book after snapshot");
        assert_eq!(s.bid_px, vec![155_770_000, 155_760_000]);
        assert_eq!(s.ask_px, vec![155_890_000, 155_900_000]);
        assert_eq!(s.depth, 2);
        assert_eq!(s.seq, 0);
        assert_eq!(s.checksum_ok, None, "no checksum on this venue");
        assert_eq!(s.exchange_ts, Some(1_787_746_425_754_166));
        assert_eq!(s.recv_ts_ns, 100);
        assert_eq!(s.snapshot_ts_ns, 150);

        a.handle_frame(
            l2(
                1,
                "update",
                &[("bid", "1.5577", "0"), ("offer", "1.5589", "300.5")],
            )
            .as_bytes(),
            200,
        );
        let s = a.snapshot("ATOM-USD", 250).unwrap();
        assert_eq!(s.bid_px, vec![155_760_000], "qty 0 removed the best bid");
        assert_eq!(s.ask_qty[0], 30_050_000_000, "absolute replace, not an add");
        assert_eq!(s.seq, 1);
        assert_eq!(s.recv_ts_ns, 200);
        assert_eq!(a.total_levels(), 3);
        assert_eq!(a.depth("ATOM-USD"), Some(3));
    }

    #[test]
    fn an_update_before_any_snapshot_is_not_a_book() {
        let mut a = adapter();
        a.handle_frame(l2(0, "update", SNAPSHOT).as_bytes(), 1);
        assert!(a.snapshot("ATOM-USD", 2).is_none());
    }

    /// The connection-wide counter: a heartbeat, a trade frame and a book
    /// frame all consume one number each, and a skip anywhere invalidates
    /// every book.
    #[test]
    fn a_sequence_gap_drops_every_book_and_asks_for_a_reconnect() {
        let mut a = adapter();
        assert!(
            a.handle_frame(l2(0, "snapshot", SNAPSHOT).as_bytes(), 1)
                .actions
                .is_empty()
        );
        assert!(
            a.handle_frame(heartbeat(1).as_bytes(), 2)
                .actions
                .is_empty()
        );
        assert!(a.snapshot("ATOM-USD", 3).is_some());

        let h = a.handle_frame(heartbeat(3).as_bytes(), 4); // 2 never arrived
        assert_eq!(h.actions, vec![Action::Reconnect]);
        assert!(
            a.snapshot("ATOM-USD", 5).is_none(),
            "a book with a lost delta is not sampled"
        );
        assert_eq!(a.total_levels(), 0);

        // Re-anchored on the frame we got: 4 is fine, and a regression is a gap.
        assert!(
            a.handle_frame(heartbeat(4).as_bytes(), 6)
                .actions
                .is_empty()
        );
        assert_eq!(
            a.handle_frame(heartbeat(4).as_bytes(), 7).actions,
            vec![Action::Reconnect]
        );
    }

    #[test]
    fn subscribe_messages_are_three_channels_without_auth() {
        let a = adapter();
        let msgs = a.subscribe_messages();
        assert_eq!(msgs.len(), 3);
        for (m, ch) in msgs.iter().zip(["level2", "market_trades", "heartbeats"]) {
            assert!(m.contains(&format!("\"channel\":\"{ch}\"")), "{m}");
            assert!(
                m.contains("\"product_ids\":[\"ATOM-USD\",\"DOT-USD\"]"),
                "{m}"
            );
            assert!(!m.contains("jwt"), "{m}");
        }
        let re = a.resubscribe_messages("DOT-USD");
        assert!(
            re[0].contains("\"type\":\"unsubscribe\"") && re[1].contains("\"type\":\"subscribe\"")
        );
    }

    #[test]
    fn begin_connection_resets_every_per_connection_fact() {
        let mut a = adapter();
        a.handle_frame(l2(0, "snapshot", SNAPSHOT).as_bytes(), 1);
        a.begin_connection("second-conn");
        assert!(a.books.is_empty() && a.conn_msg_seq == 0 && a.expected_seq.is_none());
        // The new connection starts its own sequence at 0 again - not a gap.
        assert!(
            a.handle_frame(heartbeat(0).as_bytes(), 2)
                .actions
                .is_empty()
        );
    }
}
