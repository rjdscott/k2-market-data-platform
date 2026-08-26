//! Prometheus exposition on `:${K2_METRICS_PORT:-8082}`.
//!
//! Names and label sets are fixed by the Phase C plan
//! (`docs/plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md`)
//! because the alert rules and the Grafana dashboard are written against them.
//! Every metric is `describe_`d, so the HELP text ships with the series rather
//! than living in a wiki: `k2_capture_exchange_to_recv_seconds` in particular
//! carries a caveat that has to travel with the number.
//!
//! Counters are zeroed for the labels this process will use, at startup. A
//! counter that only appears when it first increments cannot be alerted on -
//! `rate(...[5m]) > 0` never fires on a series that does not exist yet, and
//! `absent()` fires on a healthy process that has simply had no failures.

use std::net::SocketAddr;

use anyhow::{Context, Result};
use metrics::{counter, describe_counter, describe_gauge, describe_histogram, gauge};
use metrics_exporter_prometheus::{Matcher, PrometheusBuilder};

/// Set at build time by the Dockerfile; `unknown` for a local `cargo run`.
const GIT_SHA: &str = match option_env!("K2_GIT_SHA") {
    Some(sha) => sha,
    None => "unknown",
};

/// Seconds. The tail is generous on purpose: this histogram measures venue
/// clock skew plus internet path, and both can be seconds without anything
/// being wrong with K2.
const RECV_LAG_BUCKETS: &[f64] = &[
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
];

/// Install the exporter and register every metric this binary emits.
/// `symbols` is every instrument this process subscribes to; `checksummed_symbols`
/// is the subset for a venue that publishes a book checksum, and empty for one
/// that does not. Binance and Coinbase publish none, so seeding 23
/// permanently-zero `k2_capture_checksum_failures_total` series for them
/// advertised a capability that does not exist.
pub fn install(
    port: u16,
    exchange: &'static str,
    streams: &[&'static str],
    symbols: &[String],
    checksummed_symbols: &[String],
) -> Result<()> {
    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    PrometheusBuilder::new()
        .with_http_listener(addr)
        .set_buckets_for_metric(
            Matcher::Full("k2_capture_exchange_to_recv_seconds".to_string()),
            RECV_LAG_BUCKETS,
        )
        .context("setting histogram buckets")?
        .install()
        .with_context(|| format!("binding the metrics listener on {addr}"))?;

    describe();
    zero(exchange, streams, symbols, checksummed_symbols);
    gauge!("k2_capture_build_info", "version" => env!("CARGO_PKG_VERSION"), "git_sha" => GIT_SHA)
        .set(1.0);
    Ok(())
}

fn describe() {
    describe_counter!(
        "k2_capture_messages_total",
        "WebSocket frames received, by exchange and by the subscription the frame arrived on."
    );
    describe_counter!(
        "k2_capture_bytes_total",
        "Bytes of WebSocket frame payload received, by exchange and stream."
    );
    describe_counter!(
        "k2_capture_records_produced_total",
        "Records handed to librdkafka, by exchange and record kind (raw, trade, book)."
    );
    describe_counter!(
        "k2_capture_produce_errors_total",
        "Records that did not reach Redpanda, by reason. queue_full means the local \
         librdkafka queue was full and the record was dropped; encode means the schema \
         registry or Avro encoder rejected it; enqueue means librdkafka refused it \
         locally (a frame over message.max.bytes); delivery means the broker did."
    );
    describe_counter!(
        "k2_capture_gaps_total",
        "Detected discontinuities in the venue's own sequence numbering. Always 0 for \
         Kraken v2, which publishes no sequence and uses a book checksum instead."
    );
    describe_counter!(
        "k2_capture_checksum_failures_total",
        "Book updates whose venue-published checksum did not match the locally \
         maintained book. Each one drops the book and triggers a resync."
    );
    describe_counter!(
        "k2_capture_resyncs_total",
        "Book resubscriptions requested after the local book became untrustworthy."
    );
    describe_counter!(
        "k2_capture_records_delivered_total",
        "Records the broker acknowledged, by exchange. This is the counter that \
         stops moving when Redpanda goes away; records_produced_total counts the \
         local enqueue and keeps climbing through an outage."
    );
    describe_counter!(
        "k2_capture_reconnects_total",
        "WebSocket reconnects, by exchange and reason. scheduled means K2 closed \
         the socket at its configured maximum connection age (Binance, 23 h, \
         ahead of the venue's own 24 h cut-off); involuntary is everything else."
    );
    describe_counter!(
        "k2_capture_precision_loss_total",
        "Decimal values the 1e-8 fixed-point contract cannot hold exactly. The record \
         is dropped, never rounded - a rounded price is a wrong price that looks right \
         forever. reason distinguishes too_many_dp from a malformed or out-of-range value."
    );
    describe_counter!(
        "k2_capture_unknown_frames_total",
        "Frames archived to the raw topic but not understood: an unknown channel, a body \
         that did not match its channel, or a frame that was not JSON. Never a drop - \
         the raw record is always emitted first."
    );
    describe_histogram!(
        "k2_capture_exchange_to_recv_seconds",
        "Exchange timestamp to local receipt; includes internet path and clock skew, \
         not a latency SLO."
    );
    describe_gauge!(
        "k2_capture_book_depth",
        "Resting price levels currently held in the local book, per symbol."
    );
    describe_gauge!(
        "k2_capture_book_levels_total",
        "Resting price levels currently held across every symbol on this connection."
    );
    describe_gauge!(
        "k2_capture_last_message_ts_seconds",
        "Unix time of the last frame received on this stream. The staleness alert and \
         the healthcheck subcommand both read this."
    );
    describe_gauge!(
        "k2_capture_build_info",
        "Always 1. Carries the binary version and git sha as labels."
    );
}

/// Create the series that alerts fire on, at zero, before anything happens.
///
/// Every counter an alert reads has to be here. `increase(x[1h]) > 0` needs two
/// samples of `x`: a series born at 1 and flat afterwards yields 0, so the
/// *first* event is exactly the one an unseeded counter misses — and the first
/// precision-loss event is the one whose alert description says it needs an ADR.
fn zero(
    exchange: &'static str,
    streams: &[&'static str],
    symbols: &[String],
    checksummed_symbols: &[String],
) {
    for stream in streams {
        counter!("k2_capture_messages_total", "exchange" => exchange, "stream" => *stream)
            .increment(0);
        counter!("k2_capture_bytes_total", "exchange" => exchange, "stream" => *stream)
            .increment(0);
        // Any frame can turn out to be one we cannot parse, so every stream gets
        // an unknown_frames series. `count_unknown` labels by stream.
        counter!("k2_capture_unknown_frames_total", "exchange" => exchange, "stream" => *stream)
            .increment(0);
    }
    // `exchanges::count_unknown` also emits these two synthetic stream names for
    // a frame whose channel we do not recognise, or that was not JSON at all.
    for stream in ["unknown", "unparseable"] {
        counter!("k2_capture_unknown_frames_total", "exchange" => exchange, "stream" => stream)
            .increment(0);
    }
    for kind in ["raw", "trade", "book"] {
        counter!("k2_capture_records_produced_total", "exchange" => exchange, "kind" => kind)
            .increment(0);
    }
    for reason in ["queue_full", "encode", "enqueue", "delivery"] {
        counter!("k2_capture_produce_errors_total", "exchange" => exchange, "reason" => reason)
            .increment(0);
    }
    // One per `DecimalError` variant, matching `exchanges::count_decimal_error`.
    for reason in [
        "too_many_dp",
        "scientific",
        "malformed",
        "overflow",
        "negative",
    ] {
        counter!("k2_capture_precision_loss_total", "exchange" => exchange, "reason" => reason)
            .increment(0);
    }
    for reason in ["scheduled", "involuntary"] {
        counter!("k2_capture_reconnects_total", "exchange" => exchange, "reason" => reason)
            .increment(0);
    }
    for name in [
        "k2_capture_gaps_total",
        "k2_capture_resyncs_total",
        "k2_capture_records_delivered_total",
    ] {
        counter!(name, "exchange" => exchange).increment(0);
    }
    for symbol in checksummed_symbols {
        counter!(
            "k2_capture_checksum_failures_total",
            "exchange" => exchange,
            "symbol" => symbol.clone(),
        )
        .increment(0);
    }
    // A gauge, and it needs seeding for the same reason the counters do: the
    // frame path only creates it once `adapter.depth(symbol)` returns a book, so
    // a symbol whose book subscription is silently never served has no series at
    // all — and `max_over_time(<absent>[10m]) < 20` is an empty vector, which is
    // precisely the collapse CaptureBookDepthDegraded exists to catch. Seeded at
    // 0, not at a plausible depth: a healthy connect overwrites it within the
    // first snapshot tick, well inside the alert's 10 m window.
    for symbol in symbols {
        gauge!(
            "k2_capture_book_depth",
            "exchange" => exchange,
            "symbol" => symbol.clone(),
        )
        .set(0.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every series an alert reads has to exist before the event it alerts on,
    /// or the rule evaluates an empty vector — which is to say it never fires.
    /// This renders the exposition straight after `zero` and asserts the series
    /// are there, `k2_capture_book_depth` included: it is a gauge the frame path
    /// creates lazily, so a book subscription the venue silently never serves
    /// had no series at all and `CaptureBookDepthDegraded` could not fire for
    /// exactly the symbol it was written for.
    #[test]
    fn zero_seeds_every_series_an_alert_reads() {
        let recorder = PrometheusBuilder::new().build_recorder();
        let handle = recorder.handle();
        let symbols = vec!["BTC/USD".to_string()];
        metrics::with_local_recorder(&recorder, || {
            zero("kraken", &["book", "trade"], &symbols, &symbols);
        });
        let rendered = handle.render();

        for want in [
            "k2_capture_book_depth",
            "k2_capture_checksum_failures_total",
            "k2_capture_gaps_total",
            "k2_capture_produce_errors_total",
            "k2_capture_precision_loss_total",
            "k2_capture_records_delivered_total",
            "k2_capture_resyncs_total",
        ] {
            assert!(rendered.contains(want), "{want} is not seeded:\n{rendered}");
        }
        // Per symbol, not one series for the venue: the alert is per book.
        assert!(
            rendered
                .lines()
                .any(|l| l.starts_with("k2_capture_book_depth{") && l.contains("BTC/USD")),
            "book depth is not seeded per symbol:\n{rendered}"
        );
    }
}
