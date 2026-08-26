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
//! One thing here can still block the frame loop: `send` awaits the Avro
//! encoder, and the encoder makes an HTTP call to the schema registry the first
//! time it meets a subject (and again after a schema change). `reqwest`'s
//! default is no timeout at all, so a registry that accepts the connection and
//! never answers would stall the socket read for as long as it felt like.
//!
//! Two things bound that, and it is worth being exact about which bounds what,
//! because a 5 s cap on one encode is not a bound on the session:
//!
//! * `warm_up` fetches the schema for every subject this process can produce to
//!   (`OutRecord::TOPIC_KINDS`, three of them) before the first WebSocket
//!   connect, and `run` treats a failure as fatal. That is what keeps a healthy
//!   session off the registry entirely: the encoder's cache is warm before a
//!   frame ever arrives, so a registry that dies mid-session costs nothing.
//! * `REGISTRY_TIMEOUT` caps one encode at 5 s. It is the bound on a *cold*
//!   start where the registry is reachable but slow, and on the one case
//!   `warm_up` cannot pre-empt.
//!
//! `// ponytail: that remaining case is a mid-session schema evolution against a
//! sick registry. The new subject version is not cached, the converter does not
//! cache retriable errors, and the id cache only fills on success - so it is 5 s
//! per record for as long as the registry stays sick, not 5 s once. Bounded per
//! record, unbounded in aggregate. The fix if it ever bites is a negative cache
//! (skip the fetch for N seconds after a failure, count the skips as
//! reason="encode"); not worth the state until a schema actually evolves.`

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

/// Everything the producer is configured with, in one pure function so the
/// numbers the README and the FMEA quote can be asserted without a broker.
fn producer_config(brokers: &str) -> ClientConfig {
    let mut config = ClientConfig::new();
    config
        .set("bootstrap.servers", brokers)
        // 32 MiB of local buffer, and the only buffer in the process. Against
        // the per-container wire rate (173.3 / 164.3 / 75.2 kB/s for binance /
        // kraken / coinbase, capacity-model.md §4a–4b) that is 194 / 204 / 446 s
        // of slack across a broker outage, and it is the number the container's
        // memory limit is sized around. One 5.2 MB snapshot (S5) is ~16% of it;
        // five products reconnecting at once fit with room to spare.
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
        // How long one record may wait for delivery — retries included — before
        // librdkafka fails it. This was 30 s, and the first chaos run is what
        // showed the cost of that number.
        //
        // *Predicted:* pause the broker under `capture-queue-full.sh` and kraken
        // enqueues for 204 s before 32 MiB is full and the first record is
        // dropped with `reason="queue_full"`.
        //
        // *Measured* (2026-08-26, `scripts/chaos/results/2026-08-26.tsv`): first
        // drop at **102 s**, 50 % early, and of the 231,744 records lost across
        // the 388 s fault window **zero** carried `reason="queue_full"`. They
        // all expired on this timeout, counted `delivery`, while the queue sat
        // half empty. A buffer sized in minutes behind a cap set in seconds
        // never gets to do its job.
        //
        // *Why 5 minutes:* it is librdkafka's own default and it covers the
        // whole of the queue's slack on every venue (194 / 204 / 446 s), so a
        // broker restart inside the window the 32 MiB was bought for now loses
        // nothing. `enable.idempotence` does not constrain it — that flag
        // adjusts `max.in.flight.requests.per.connection`, `retries`, `acks` and
        // `queuing.strategy` only (librdkafka CONFIGURATION.md). The one setting
        // that would clamp it is `transactional.id`, which this producer does
        // not set.
        //
        // *New failure shape:* loss does not go away, it changes label and moves
        // later. A record still older than 5 minutes is dropped and counted
        // `reason="delivery"`. `queue_full` becomes the *earlier* signal
        // whenever the wire rate fills 32 MiB in under 5 minutes — true at every
        // venue's current rate — so a `queue_full` tick now means "the outage
        // outran the buffer", and a `delivery` tick during an outage means "the
        // broker was reachable enough to keep the queue draining but not enough
        // to finish". Both are permanent loss; neither spills to disk.
        .set("message.timeout.ms", "300000");
    config
}

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
        let producer: FutureProducer = producer_config(brokers)
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

    /// Fetch the schema for every subject this process can produce to, before
    /// the first connect. Fatal on failure: a capture process that cannot reach
    /// the registry cannot build a single record (the schema id is bytes 1-4 of
    /// the Confluent frame), so it is better restarted by compose than left
    /// reading a socket and dropping everything it reads.
    ///
    /// Three round trips, once. What it buys is that the frame path never meets
    /// a cold cache — see the module header for what this does and does not
    /// bound.
    pub async fn warm_up(&self) -> Result<()> {
        for kind in OutRecord::TOPIC_KINDS {
            let strategy = strategy_for(self.topic(kind));
            let subject = strategy.get_subject()?;
            self.encoder
                .get_schema_and_id(&subject, strategy)
                .await
                .with_context(|| format!("fetching the Avro schema for subject {subject}"))?;
            tracing::debug!(subject, "schema cached");
        }
        Ok(())
    }

    /// `<prefix>.<kind>.<exchange>` — where a record of this kind is produced.
    fn topic(&self, topic_kind: &str) -> String {
        format!("{}.{}.{}", self.topic_prefix, topic_kind, self.exchange)
    }

    /// Encode and enqueue one record. Never blocks the caller on the broker.
    pub async fn send(&self, record: &OutRecord) {
        let topic = self.topic(record.topic_kind());
        let strategy = strategy_for(topic.clone());

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

/// TopicNameStrategy: subject = `<topic>-value`, the Confluent default and what
/// `schemas/README.md` registers. The `false` is "this is not a key schema"; no
/// `-key` subjects exist. One function so `warm_up` and `send` cannot warm one
/// subject and then ask for another.
fn strategy_for(topic: String) -> SubjectNameStrategy {
    SubjectNameStrategy::TopicNameStrategy(topic, false)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use crate::record::OutRecord;
    use crate::record::samples::{sample_book, sample_raw, sample_trade};

    use super::{producer_config, strategy_for};

    fn subject(topic_prefix: &str, kind: &str, exchange: &str) -> String {
        strategy_for(format!("{topic_prefix}.{kind}.{exchange}"))
            .get_subject()
            .expect("TopicNameStrategy always has a subject")
    }

    /// `warm_up` warms one subject per `OutRecord::TOPIC_KINDS`; `send` asks for
    /// `record.topic_kind()`. A kind in the second set and not the first is a
    /// registry round trip back on the frame path for that record kind — the
    /// exact hole `warm_up` exists to close, and invisible until a venue with
    /// that record kind meets a sick registry.
    #[test]
    fn warm_up_covers_every_subject_a_record_can_need() {
        for exchange in ["binance", "kraken", "coinbase"] {
            let warmed: BTreeSet<String> = OutRecord::TOPIC_KINDS
                .iter()
                .map(|kind| subject("market.crypto.v3", kind, exchange))
                .collect();
            let needed: BTreeSet<String> = [sample_trade(), sample_book(), sample_raw()]
                .iter()
                .map(|record| {
                    // Exhaustive on purpose: a fourth OutRecord variant stops
                    // compiling here, so nobody can add one without deciding
                    // whether warm_up needs a fourth subject.
                    match record {
                        OutRecord::Trade(_) | OutRecord::Book(_) | OutRecord::Raw(_) => {}
                    }
                    subject("market.crypto.v3", record.topic_kind(), exchange)
                })
                .collect();
            assert_eq!(warmed, needed, "subjects differ for {exchange}");
        }
    }

    /// Four producer settings are quoted as numbers in `README.md`, ADR-019 and
    /// `docs/architecture/failure-modes.md` — the 5-minute delivery cap, the
    /// 32 MiB of queue slack the delayed→lost boundary is computed from, the
    /// 8 MiB frame cap that must equal `ws::MAX_MESSAGE_BYTES`, and idempotence.
    /// Changing one silently makes those documents wrong, and the only place
    /// that shows up is a chaos run months later, so assert them here where the
    /// diff is visible.
    #[test]
    fn producer_config_carries_the_numbers_the_docs_quote() {
        let config = producer_config("localhost:9092");
        for (key, want) in [
            // 5 min, raised from 30 s after the 2026-08-26 chaos run measured
            // every drop as a timeout and none as queue_full.
            ("message.timeout.ms", "300000"),
            // 32 MiB — 194/204/446 s of slack at the modelled wire rates.
            ("queue.buffering.max.kbytes", "32768"),
            // 8 MiB, equal to the WebSocket cap and the raw topics'.
            ("message.max.bytes", "8388608"),
            ("enable.idempotence", "true"),
        ] {
            assert_eq!(config.get(key), Some(want), "{key} drifted from the docs");
        }
        // A `transactional.id` would silently clamp message.timeout.ms to
        // transaction.timeout.ms (60 s default) and undo the change above.
        assert_eq!(config.get("transactional.id"), None);
    }
}
