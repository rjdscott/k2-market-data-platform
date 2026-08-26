//! Records out to Redpanda as Confluent-framed Avro.
//!
//! There is no internal channel between the frame loop and this module.
//! librdkafka's own queue is the only buffer in the process - a second queue
//! would add a second place for records to disappear and a second thing to size
//! against the container's memory limit, and it would hide backpressure behind
//! a bounded channel instead of surfacing it as `queue_full`.
//!
//! When that queue fills, records are **dropped and counted**, not blocked on:
//! blocking the frame loop stops reading the socket, which makes the venue drop
//! us, which loses more than the records we were trying to save.
//! `// ponytail: spill-to-file when an outage costs data.`

use std::time::Duration;

use anyhow::{Context, Result};
use rdkafka::ClientConfig;
use rdkafka::error::{KafkaError, RDKafkaErrorCode};
use rdkafka::message::{Header, OwnedHeaders};
use rdkafka::producer::{FutureProducer, FutureRecord, Producer};
use schema_registry_converter::async_impl::easy_avro::EasyAvroEncoder;
use schema_registry_converter::async_impl::schema_registry::SrSettings;
use schema_registry_converter::schema_registry_common::SubjectNameStrategy;

use crate::record::OutRecord;

/// Largest single record the producer will enqueue, in bytes. Must agree with
/// `ws::MAX_MESSAGE_BYTES` (8 MiB): a frame the socket accepts must be
/// producible, or the raw archive silently loses exactly the frames that
/// matter most. Coinbase's `level2` subscribe snapshot for BTC-USD measured
/// 5,195,904 bytes / 43,974 levels (ADR-018 Appendix A, S5) — over
/// librdkafka's 1,000,000-byte default, which rejected it at enqueue with
/// `MessageSizeTooLarge` on every (re)connect. The topic side is
/// `max.message.bytes=8388608` on `market.crypto.v3.raw.*` (docker/redpanda/init.sh).
/// `// ponytail: ws.rs owns its own copy; a shared const across modules is not
/// worth the cross-module coupling for two numbers a grep keeps in step.`
pub const MESSAGE_MAX_BYTES: usize = 8 * 1024 * 1024;

pub struct Sink {
    producer: FutureProducer,
    encoder: EasyAvroEncoder,
    /// `market.crypto.v3` unless `K2_TOPIC_PREFIX` says otherwise.
    topic_prefix: String,
    exchange: &'static str,
}

impl Sink {
    pub fn new(
        brokers: &str,
        schema_registry_url: &str,
        topic_prefix: String,
        exchange: &'static str,
    ) -> Result<Self> {
        let producer: FutureProducer = ClientConfig::new()
            .set("bootstrap.servers", brokers)
            // 32 MB of local buffer. At the measured v2 rate this is minutes of
            // headroom across a broker restart, and it is the number the
            // container's memory limit is sized around.
            // One 5.2 MB snapshot (S5) is ~16% of it; five products
            // reconnecting at once fit with room to spare.
            .set("queue.buffering.max.kbytes", "32768")
            // See `MESSAGE_MAX_BYTES`: the socket cap and the produce cap agree.
            .set("message.max.bytes", MESSAGE_MAX_BYTES.to_string())
            // Exactly-once semantics on the producer side: a retry after a
            // timeout cannot duplicate a record, which matters because the lake
            // is append-only and nothing downstream dedups raw frames.
            .set("enable.idempotence", "true")
            .set("acks", "all")
            // zstd is not in librdkafka's default codec set; the build enables
            // it explicitly (spike S6) and this is where it gets used. JSON
            // payloads on the raw topic are what makes it worth the CPU, and
            // the multi-MB `level2` snapshots (repetitive price/size arrays)
            // compress best of all — see the README for the measured ratio.
            .set("compression.type", "zstd")
            // Fail a record after 30 s rather than holding it forever: a record
            // that old is better dropped and counted than silently pinned in
            // the queue behind a dead broker.
            .set("message.timeout.ms", "30000")
            .create()
            .context("creating the Kafka producer")?;

        Ok(Self {
            producer,
            encoder: EasyAvroEncoder::new(SrSettings::new(schema_registry_url.to_string())),
            topic_prefix,
            exchange,
        })
    }

    /// Encode and enqueue one record. Never blocks the caller on the broker.
    pub async fn send(&self, record: &OutRecord) {
        let topic = format!(
            "{}.{}.{}",
            self.topic_prefix,
            record.topic_kind(),
            self.exchange
        );
        // TopicNameStrategy: subject = `<topic>-value`, the Confluent default
        // and what `schemas/README.md` registers. The `false` is "this is not a
        // key schema"; no `-key` subjects exist.
        let strategy = SubjectNameStrategy::TopicNameStrategy(topic.clone(), false);

        let payload = match self.encoder.encode(record.avro_fields(), strategy).await {
            Ok(bytes) => bytes,
            Err(e) => {
                tracing::error!(topic, error = %e, "avro encode failed, record dropped");
                self.count_error("encode");
                return;
            }
        };

        // recv_ts_ns rides in a header as well as the body so a lag monitor can
        // read it without deserialising the payload (verified readable from
        // ClickHouse 24.3 via `_headers`, spike S4). The body stays authoritative.
        let recv_ts = record.recv_ts_ns().to_string();
        let headers = OwnedHeaders::new().insert(Header {
            key: "recv_ts_ns",
            value: Some(&recv_ts),
        });

        let mut message = FutureRecord::to(&topic).payload(&payload).headers(headers);
        if let Some(key) = record.key() {
            message = message.key(key);
        }

        match self.producer.send_result(message) {
            Ok(delivery) => {
                metrics::counter!(
                    "k2_capture_records_produced_total",
                    "exchange" => self.exchange,
                    "kind" => record.kind(),
                )
                .increment(1);
                // The delivery report arrives later. Awaiting it here would
                // serialise production on the round trip to the broker; ignoring
                // it entirely would make a broker-side rejection invisible. So:
                // await it on a detached task purely to count.
                let exchange = self.exchange;
                tokio::spawn(async move {
                    if let Ok(Err((e, _))) = delivery.await {
                        tracing::warn!(error = %e, "kafka delivery failed");
                        metrics::counter!(
                            "k2_capture_produce_errors_total",
                            "exchange" => exchange,
                            "reason" => "delivery",
                        )
                        .increment(1);
                    }
                });
            }
            Err((KafkaError::MessageProduction(RDKafkaErrorCode::QueueFull), _)) => {
                self.count_error("queue_full");
            }
            Err((e, _)) => {
                tracing::warn!(error = %e, "producer rejected a record");
                self.count_error("enqueue");
            }
        }
    }

    /// Hand the queue 5 seconds to drain on SIGTERM. Anything still queued
    /// after that is lost, and the counter above is what says so.
    pub fn flush(&self, timeout: Duration) {
        if let Err(e) = self.producer.flush(timeout) {
            tracing::warn!(error = %e, "producer flush did not complete");
        }
    }

    fn count_error(&self, reason: &'static str) {
        metrics::counter!(
            "k2_capture_produce_errors_total",
            "exchange" => self.exchange,
            "reason" => reason,
        )
        .increment(1);
    }
}
