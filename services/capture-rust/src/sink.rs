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
//!
//! One thing here does still block the frame loop, and the bound on it is
//! explicit rather than accidental: `send` awaits the Avro encoder, and the
//! encoder makes an HTTP call to the schema registry the first time it meets a
//! subject (and again after a schema change). `reqwest`'s default is no timeout
//! at all, so a registry that accepts the connection and never answers would
//! stall the socket read for as long as it felt like — the exact failure this
//! module header says the design avoids. `REGISTRY_TIMEOUT` caps that stall; a
//! timed-out encode is counted as `reason="encode"` and the loop carries on.
//! `// ponytail: a warm-up encode per subject at startup would take the call off
//! the frame path entirely; the 5 s cap is what makes that an optimisation
//! rather than a correctness fix.`

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

/// How long an Avro encode may wait on the schema registry before it is a
/// failure. This sits on the frame path (see the module header), so it is a cap
/// on how long a sick registry can stop us reading the venue's socket. 5 s is
/// two orders of magnitude above the observed local round trip and an order of
/// magnitude below the 60 s at which the venue-silence watchdog reconnects.
const REGISTRY_TIMEOUT: Duration = Duration::from_secs(5);

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

        let sr = SrSettings::new_builder(schema_registry_url.to_string())
            .set_timeout(REGISTRY_TIMEOUT)
            .build()
            .context("building the schema registry client")?;

        Ok(Self {
            producer,
            encoder: EasyAvroEncoder::new(sr),
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
                tracing::error!(topic, error = ?e, "avro encode failed, record dropped");
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
                //
                // `records_produced_total` above counts the *enqueue*, which is
                // why it keeps climbing at full rate through a broker outage.
                // `records_delivered_total` here is the one that goes flat, and
                // it is what `CaptureProduceStalled` alerts on.
                let exchange = self.exchange;
                tokio::spawn(async move {
                    match delivery.await {
                        Ok(Ok(_)) => {
                            metrics::counter!(
                                "k2_capture_records_delivered_total",
                                "exchange" => exchange,
                            )
                            .increment(1);
                        }
                        Ok(Err((e, _))) => {
                            tracing::warn!(error = ?e, "kafka delivery failed");
                            metrics::counter!(
                                "k2_capture_produce_errors_total",
                                "exchange" => exchange,
                                "reason" => "delivery",
                            )
                            .increment(1);
                        }
                        // The producer was dropped before the report landed:
                        // neither delivered nor rejected, and nothing to say.
                        Err(_) => {}
                    }
                });
            }
            Err((KafkaError::MessageProduction(RDKafkaErrorCode::QueueFull), _)) => {
                self.count_error("queue_full");
            }
            Err((e, _)) => {
                tracing::warn!(error = ?e, "producer rejected a record");
                self.count_error("enqueue");
            }
        }
    }

    /// Hand the queue 5 seconds to drain on SIGTERM. Anything still queued
    /// after that is lost, and the counter above is what says so.
    pub fn flush(&self, timeout: Duration) {
        if let Err(e) = self.producer.flush(timeout) {
            tracing::warn!(error = ?e, "producer flush did not complete");
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
