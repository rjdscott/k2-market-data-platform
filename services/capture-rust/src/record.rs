//! The three v3 wire records, mirroring `schemas/avro/*.avsc` field for field.
//!
//! The `.avsc` files are the contract; these structs are a view of it. They are
//! embedded at compile time with `include_str!` rather than copied into this
//! crate, so there is exactly one definition of the wire format in the repo and
//! a schema edit that this crate has not been taught about fails
//! `schemas_match_the_structs` at `cargo test` rather than at the offload
//! boundary hours later.
//!
//! Values are handed to `schema_registry_converter` as `(field_name, Value)`
//! pairs; the encoder assembles the record against the schema it fetched from
//! the registry, which is what makes a name typo a serialisation error instead
//! of a silently absent column.

use std::sync::LazyLock;

use apache_avro::Schema;
use apache_avro::types::Value;
use serde::Serialize;

pub const TRADE_SCHEMA_JSON: &str = include_str!("../../../schemas/avro/trade.avsc");
pub const BOOK_SCHEMA_JSON: &str = include_str!("../../../schemas/avro/book-snapshot-l2.avsc");
pub const RAW_SCHEMA_JSON: &str = include_str!("../../../schemas/avro/raw-message.avsc");

pub static TRADE_SCHEMA: LazyLock<Schema> =
    LazyLock::new(|| Schema::parse_str(TRADE_SCHEMA_JSON).expect("trade.avsc is not valid Avro"));
pub static BOOK_SCHEMA: LazyLock<Schema> = LazyLock::new(|| {
    Schema::parse_str(BOOK_SCHEMA_JSON).expect("book-snapshot-l2.avsc is not valid Avro")
});
pub static RAW_SCHEMA: LazyLock<Schema> = LazyLock::new(|| {
    Schema::parse_str(RAW_SCHEMA_JSON).expect("raw-message.avsc is not valid Avro")
});

/// Taker side. The Avro enum's symbol order is `["buy", "sell"]` and the index
/// is what goes on the wire, so this discriminant is part of the contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    fn avro(self) -> Value {
        match self {
            Side::Buy => Value::Enum(0, "buy".into()),
            Side::Sell => Value::Enum(1, "sell".into()),
        }
    }
}

/// `schemas/avro/trade.avsc`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TradeRecord {
    pub exchange: String,
    pub symbol: String,
    pub canonical_symbol: String,
    pub trade_id: String,
    /// 1e-8 fixed point - see [`crate::decimal`].
    pub price: i64,
    pub qty: i64,
    pub side: Side,
    /// Microseconds since the epoch, from the venue's clock.
    pub exchange_ts: i64,
    pub recv_ts_ns: i64,
    /// 0 where the venue does not sequence this stream. Not nullable by design.
    pub seq: i64,
    pub conn_id: String,
    pub conn_msg_seq: i64,
}

/// `schemas/avro/book-snapshot-l2.avsc`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BookSnapshotRecord {
    pub exchange: String,
    pub symbol: String,
    pub canonical_symbol: String,
    pub depth: i32,
    pub seq: i64,
    /// `None` where the venue publishes no checksum - never collapsed to `true`.
    pub checksum_ok: Option<bool>,
    pub bid_px: Vec<i64>,
    pub bid_qty: Vec<i64>,
    pub ask_px: Vec<i64>,
    pub ask_qty: Vec<i64>,
    pub exchange_ts: Option<i64>,
    pub recv_ts_ns: i64,
    pub snapshot_ts_ns: i64,
    pub conn_id: String,
    pub conn_msg_seq: i64,
}

/// `schemas/avro/raw-message.avsc`. The system of record: every frame, verbatim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RawMessageRecord {
    pub exchange: String,
    pub stream: String,
    pub symbol: Option<String>,
    pub recv_ts_ns: i64,
    pub conn_id: String,
    pub conn_msg_seq: i64,
    pub payload: Vec<u8>,
}

/// What an adapter produces from one frame.
///
/// An enum rather than three separate return channels because the order the
/// records were produced in is itself information a replay must preserve: the
/// raw frame always precedes the records derived from it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum OutRecord {
    Trade(TradeRecord),
    Book(BookSnapshotRecord),
    Raw(RawMessageRecord),
}

fn long_array(v: &[i64]) -> Value {
    Value::Array(v.iter().map(|&x| Value::Long(x)).collect())
}

impl OutRecord {
    /// Every value [`OutRecord::topic_kind`] can return, and therefore every
    /// registry subject one capture process can need. `Sink::warm_up` fetches
    /// one schema per entry at startup so the frame path never meets a cold
    /// cache; `topic_kinds_covers_every_variant` is what keeps the two in step.
    pub const TOPIC_KINDS: [&'static str; 3] = ["trades", "book", "raw"];

    /// Last segment of the topic name: `market.crypto.v3.<this>.<exchange>`.
    pub fn topic_kind(&self) -> &'static str {
        match self {
            OutRecord::Trade(_) => "trades",
            OutRecord::Book(_) => "book",
            OutRecord::Raw(_) => "raw",
        }
    }

    /// `records_produced_total{kind}` label, and the word used in logs.
    pub fn kind(&self) -> &'static str {
        match self {
            OutRecord::Trade(_) => "trade",
            OutRecord::Book(_) => "book",
            OutRecord::Raw(_) => "raw",
        }
    }

    /// Kafka message key: the canonical symbol, so every record for one
    /// instrument lands on one partition and stays ordered. Raw frames that
    /// belong to no single instrument (heartbeats, subscribe acks) have no key
    /// and are spread round-robin, which is correct - they have no ordering
    /// relationship to anything.
    pub fn key(&self) -> Option<&str> {
        match self {
            OutRecord::Trade(t) => Some(&t.canonical_symbol),
            OutRecord::Book(b) => Some(&b.canonical_symbol),
            OutRecord::Raw(r) => r.symbol.as_deref(),
        }
    }

    pub fn recv_ts_ns(&self) -> i64 {
        match self {
            OutRecord::Trade(t) => t.recv_ts_ns,
            OutRecord::Book(b) => b.recv_ts_ns,
            OutRecord::Raw(r) => r.recv_ts_ns,
        }
    }

    /// Field/value pairs for `EasyAvroEncoder::encode`.
    ///
    /// Union members are written positionally (`Value::Union(index, ..)`) and
    /// the index has to match the `.avsc`: `["null", T]` everywhere in v3, so
    /// null is 0 and the value is 1. `schemas_match_the_structs` pins that.
    pub fn avro_fields(&self) -> Vec<(&'static str, Value)> {
        match self {
            OutRecord::Trade(t) => vec![
                ("exchange", Value::String(t.exchange.clone())),
                ("symbol", Value::String(t.symbol.clone())),
                (
                    "canonical_symbol",
                    Value::String(t.canonical_symbol.clone()),
                ),
                ("trade_id", Value::String(t.trade_id.clone())),
                ("price", Value::Long(t.price)),
                ("qty", Value::Long(t.qty)),
                ("side", t.side.avro()),
                ("exchange_ts", Value::TimestampMicros(t.exchange_ts)),
                ("recv_ts_ns", Value::Long(t.recv_ts_ns)),
                ("seq", Value::Long(t.seq)),
                ("conn_id", Value::String(t.conn_id.clone())),
                ("conn_msg_seq", Value::Long(t.conn_msg_seq)),
            ],
            OutRecord::Book(b) => vec![
                ("exchange", Value::String(b.exchange.clone())),
                ("symbol", Value::String(b.symbol.clone())),
                (
                    "canonical_symbol",
                    Value::String(b.canonical_symbol.clone()),
                ),
                ("depth", Value::Int(b.depth)),
                ("seq", Value::Long(b.seq)),
                ("checksum_ok", union(b.checksum_ok.map(Value::Boolean))),
                ("bid_px", long_array(&b.bid_px)),
                ("bid_qty", long_array(&b.bid_qty)),
                ("ask_px", long_array(&b.ask_px)),
                ("ask_qty", long_array(&b.ask_qty)),
                (
                    "exchange_ts",
                    union(b.exchange_ts.map(Value::TimestampMicros)),
                ),
                ("recv_ts_ns", Value::Long(b.recv_ts_ns)),
                ("snapshot_ts_ns", Value::Long(b.snapshot_ts_ns)),
                ("conn_id", Value::String(b.conn_id.clone())),
                ("conn_msg_seq", Value::Long(b.conn_msg_seq)),
            ],
            OutRecord::Raw(r) => vec![
                ("exchange", Value::String(r.exchange.clone())),
                ("stream", Value::String(r.stream.clone())),
                ("symbol", union(r.symbol.clone().map(Value::String))),
                ("recv_ts_ns", Value::Long(r.recv_ts_ns)),
                ("conn_id", Value::String(r.conn_id.clone())),
                ("conn_msg_seq", Value::Long(r.conn_msg_seq)),
                ("payload", Value::Bytes(r.payload.clone())),
            ],
        }
    }

    pub fn schema(&self) -> &'static Schema {
        match self {
            OutRecord::Trade(_) => &TRADE_SCHEMA,
            OutRecord::Book(_) => &BOOK_SCHEMA,
            OutRecord::Raw(_) => &RAW_SCHEMA,
        }
    }
}

/// `["null", T]` union: null is member 0, the value is member 1.
fn union(v: Option<Value>) -> Value {
    match v {
        Some(inner) => Value::Union(1, Box::new(inner)),
        None => Value::Union(0, Box::new(Value::Null)),
    }
}

#[cfg(test)]
pub(crate) mod samples {
    //! Minimal valid samples of every variant. Outside `mod tests` because
    //! `sink.rs` uses them too: the question "does warm_up cover every subject
    //! an OutRecord can need" belongs to the sink, the variants belong here.
    use super::*;

    pub(crate) fn sample_trade() -> OutRecord {
        OutRecord::Trade(TradeRecord {
            exchange: "kraken".into(),
            symbol: "XBT/USD".into(),
            canonical_symbol: "BTC/USD".into(),
            trade_id: "105994519".into(),
            price: 4_528_520_000_000,
            qty: 184_000,
            side: Side::Sell,
            exchange_ts: 1_787_730_846_075_373,
            recv_ts_ns: 1_787_730_846_075_373_000,
            seq: 0,
            conn_id: "b6c3f0f0-0000-4000-8000-000000000001".into(),
            conn_msg_seq: 42,
        })
    }

    pub(crate) fn sample_book() -> OutRecord {
        OutRecord::Book(BookSnapshotRecord {
            exchange: "kraken".into(),
            symbol: "XBT/USD".into(),
            canonical_symbol: "BTC/USD".into(),
            depth: 2,
            seq: 0,
            checksum_ok: Some(true),
            bid_px: vec![100, 99],
            bid_qty: vec![1, 2],
            ask_px: vec![101, 102],
            ask_qty: vec![3, 4],
            exchange_ts: Some(1_787_730_846_075_373),
            recv_ts_ns: 1_787_730_846_075_373_000,
            snapshot_ts_ns: 1_787_730_847_000_000_000,
            conn_id: "b6c3f0f0-0000-4000-8000-000000000001".into(),
            conn_msg_seq: 43,
        })
    }

    pub(crate) fn sample_raw() -> OutRecord {
        OutRecord::Raw(RawMessageRecord {
            exchange: "kraken".into(),
            stream: "book".into(),
            symbol: Some("XBT/USD".into()),
            recv_ts_ns: 1_787_730_846_075_373_000,
            conn_id: "b6c3f0f0-0000-4000-8000-000000000001".into(),
            conn_msg_seq: 44,
            payload: b"{\"channel\":\"book\"}".to_vec(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::samples::*;
    use super::*;

    fn field_names(schema: &Schema) -> Vec<String> {
        match schema {
            Schema::Record(r) => r.fields.iter().map(|f| f.name.clone()).collect(),
            other => panic!("expected a record schema, got {other:?}"),
        }
    }

    /// The guard CLAUDE.md's "schema changes move together or not at all" rests
    /// on: add a field to an `.avsc` without teaching this crate about it and
    /// this fails, in the same PR, before anything reaches a topic.
    #[test]
    fn schemas_match_the_structs() {
        for record in [sample_trade(), sample_book(), sample_raw()] {
            let want = field_names(record.schema());
            let got: Vec<String> = record
                .avro_fields()
                .iter()
                .map(|(n, _)| (*n).to_string())
                .collect();
            assert_eq!(got, want, "{} fields drifted from its .avsc", record.kind());
        }
    }

    /// Every value we emit is legal for its schema - union indices, enum
    /// symbols, logical types and all.
    #[test]
    fn values_validate_against_the_schemas() {
        for record in [sample_trade(), sample_book(), sample_raw()] {
            let mut r = apache_avro::types::Record::new(record.schema())
                .expect("schema is a record schema");
            for (name, value) in record.avro_fields() {
                r.put(name, value);
            }
            let value: Value = r.into();
            assert!(
                value.validate(record.schema()),
                "{} does not validate against its schema",
                record.kind()
            );
        }
    }

    /// Encode/decode round trip through the Avro codec proper - catches a union
    /// index or logical type that validates but does not survive the wire.
    #[test]
    fn records_round_trip_through_avro() {
        for record in [sample_trade(), sample_book(), sample_raw()] {
            let schema = record.schema();
            let mut r = apache_avro::types::Record::new(schema).unwrap();
            for (name, value) in record.avro_fields() {
                r.put(name, value);
            }
            let mut encoded = Vec::new();
            apache_avro::GenericSingleObjectWriter::new_with_capacity(schema, 256)
                .expect("writer")
                .write_value(Value::from(r), &mut encoded)
                .expect("encode");
            let decoded = apache_avro::GenericSingleObjectReader::builder()
                .schema(schema.clone())
                .build()
                .expect("reader")
                .read_value(&mut encoded.as_slice())
                .expect("decode");
            assert!(decoded.validate(schema));
        }
    }

    /// Null must be union member 0 and the value member 1 in every v3 union;
    /// a `.avsc` that reorders one would corrupt every record silently.
    #[test]
    fn nullable_unions_put_null_first() {
        for (schema, field) in [
            (&*BOOK_SCHEMA, "checksum_ok"),
            (&*BOOK_SCHEMA, "exchange_ts"),
            (&*RAW_SCHEMA, "symbol"),
        ] {
            let Schema::Record(r) = schema else {
                panic!("not a record")
            };
            let f = r.fields.iter().find(|f| f.name == field).unwrap();
            let Schema::Union(u) = &f.schema else {
                panic!("{field} is not a union")
            };
            assert_eq!(
                u.variants()[0],
                Schema::Null,
                "{field}: null is not member 0"
            );
        }
    }
}
