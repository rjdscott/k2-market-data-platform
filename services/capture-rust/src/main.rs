//! `k2-capture` - three subcommands, one venue per process.
//!
//! * `run`         connect, capture, produce. The thing compose starts.
//! * `healthcheck` ask our own `/metrics` whether a feed is still live. The
//!   runtime image is distroless: there is no curl and no shell to run one in,
//!   so the healthcheck has to be the binary itself.
//! * `record`      dump frames as JSONL for a test fixture.
//!
//! The runtime is `current_thread` on purpose. One connection cannot saturate a
//! core, the container is limited to a quarter of one, and a single-threaded
//! scheduler makes the interleaving of the frame loop and the 1 Hz sampler
//! deterministic rather than a function of how many cores the host had free.

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand};
use k2_capture::config::{Exchange, Instruments};
use k2_capture::exchanges::{Action, Adapter, BinanceAdapter, CoinbaseAdapter, KrakenAdapter};
use k2_capture::metrics as k2_metrics;
use k2_capture::record::OutRecord;
use k2_capture::sink::Sink;
use k2_capture::ws::{Backoff, Feed, http_get, now_ns};
use metrics::{counter, gauge, histogram};
use uuid::Uuid;

/// Streams whose counters are created at zero on startup, so an alert can fire
/// on a feed that has been silent since boot rather than on an absent series.
///
/// Only the *continuous* streams (`CONTINUOUS`) stamp
/// `k2_capture_last_message_ts_seconds`, and only those are watched by the
/// session watchdog and the healthcheck. Everything else here is either a
/// one-shot acknowledgement or a low-rate reference channel:
///
/// * Kraken `status` / `control`, Coinbase `subscriptions` — one acknowledgement
///   per (re)subscribe and then legitimately never again. A staleness gauge on
///   them fires `CaptureFeedStale` ~2 minutes after every healthy connect; that
///   happened on the first 2 h window (2026-08-26 12:39Z, all three acks).
/// * Kraken `instrument` — a reference snapshot at subscribe plus the occasional
///   reference change. Measured over 10 minutes on 2026-08-26 it ran at
///   0.0017 frames/s (2 frames in 29 minutes) against a 60 s threshold, while
///   every genuinely continuous stream ran at >= 1.0/s. It is a fourth permanent
///   critical false positive, not a liveness signal.
const KRAKEN_STREAMS: &[&str] = &[
    "book",
    "trade",
    "instrument",
    "heartbeat",
    "status",
    "control",
];
const BINANCE_STREAMS: &[&str] = &["trade", "depth20"];
const COINBASE_STREAMS: &[&str] = &["l2_data", "market_trades", "heartbeats", "subscriptions"];
const CONTINUOUS: &[&str] = &[
    "book",
    "trade",
    "heartbeat",
    "depth20",
    "l2_data",
    "market_trades",
    "heartbeats",
];

/// A stream that keeps delivering while the subscription is healthy, as opposed
/// to a one-shot acknowledgement or a low-rate reference channel. Only these
/// carry a staleness timestamp.
fn is_continuous(stream: &str) -> bool {
    CONTINUOUS.contains(&stream)
}

/// Continuous streams for one venue - what the staleness gauge, the session
/// watchdog and the healthcheck all agree to watch.
fn continuous_streams_for(exchange: Exchange) -> Vec<&'static str> {
    streams_for(exchange)
        .iter()
        .copied()
        .filter(|s| is_continuous(s))
        .collect()
}

/// Binance closes a WebSocket 24 h after it opens, mid-frame, with no warning.
/// Pre-empting it at 23 h turns an involuntary disconnect into a scheduled one:
/// the socket is closed cleanly between frames, the producer queue is not
/// carrying a half-parsed book, and the reconnect happens at a moment we chose.
/// Kraken and Coinbase publish no such lifetime, so they get none.
const BINANCE_MAX_CONNECTION_AGE: Duration = Duration::from_secs(23 * 3600);

fn max_connection_age(exchange: Exchange) -> Option<Duration> {
    match exchange {
        Exchange::Binance => Some(BINANCE_MAX_CONNECTION_AGE),
        Exchange::Kraken | Exchange::Coinbase => None,
    }
}

/// Has this connection reached the age at which we close it ourselves?
///
/// Split out from `session` so it is testable without a socket: `session` needs
/// a live venue, this needs two integers.
fn connection_expired(exchange: Exchange, opened_ns: i64, now_ns: i64) -> bool {
    match max_connection_age(exchange) {
        Some(max) => now_ns.saturating_sub(opened_ns) >= max.as_nanos() as i64,
        None => false,
    }
}

#[derive(Parser)]
#[command(name = "k2-capture", version, about = "K2 v3 market data capture")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Capture a venue and produce to Redpanda.
    Run(RunArgs),
    /// Exit 0 if EVERY continuous stream has produced a frame recently, 1 otherwise.
    Healthcheck(HealthArgs),
    /// Record raw frames as JSONL on stdout, for a replay fixture.
    Record(RecordArgs),
}

#[derive(Args)]
struct RunArgs {
    #[arg(long, env = "K2_EXCHANGE")]
    exchange: Exchange,
    #[arg(
        long,
        env = "K2_INSTRUMENTS_FILE",
        default_value = "/app/config/instruments.yaml"
    )]
    instruments_file: PathBuf,
    #[arg(long, env = "K2_KAFKA_BROKERS", default_value = "redpanda:9092")]
    kafka_brokers: String,
    #[arg(
        long,
        env = "K2_SCHEMA_REGISTRY_URL",
        default_value = "http://redpanda:8081"
    )]
    schema_registry_url: String,
    #[arg(long, env = "K2_METRICS_PORT", default_value_t = 8082)]
    metrics_port: u16,
    #[arg(long, env = "K2_SNAPSHOT_INTERVAL_MS", default_value_t = 1000)]
    snapshot_interval_ms: u64,
    #[arg(long, env = "K2_TOPIC_PREFIX", default_value = "market.crypto.v3")]
    topic_prefix: String,
    /// Override the venue's public endpoint - a local recorder, or staging.
    #[arg(long, env = "K2_WS_URL")]
    ws_url: Option<String>,
}

#[derive(Args)]
struct HealthArgs {
    #[arg(long, env = "K2_METRICS_PORT", default_value_t = 8082)]
    metrics_port: u16,
    /// A feed quieter than this is considered dead. 60 s is well past the
    /// slowest instrument channel and well inside a compose restart window.
    #[arg(long, default_value_t = 60)]
    max_age_seconds: u64,
}

#[derive(Args)]
struct RecordArgs {
    #[arg(long, env = "K2_EXCHANGE")]
    exchange: Exchange,
    #[arg(long, default_value_t = 20)]
    seconds: u64,
    /// Native symbols to keep, comma separated. Trimming keeps a committed
    /// fixture small enough to read in a diff.
    #[arg(long, value_delimiter = ',')]
    symbols: Vec<String>,
    #[arg(
        long,
        env = "K2_INSTRUMENTS_FILE",
        default_value = "/app/config/instruments.yaml"
    )]
    instruments_file: PathBuf,
    #[arg(long, env = "K2_WS_URL")]
    ws_url: Option<String>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    // Logs go to stderr because `record` writes its fixture to stdout, and a
    // log line in the middle of a JSONL file is a corrupt fixture.
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    match Cli::parse().command {
        Command::Run(args) => run(args).await,
        Command::Healthcheck(args) => healthcheck(args).await,
        Command::Record(args) => record(args).await,
    }
}

fn build_adapter(exchange: Exchange, instruments: Instruments) -> Result<Adapter> {
    match exchange {
        Exchange::Kraken => Ok(Adapter::Kraken(KrakenAdapter::new(instruments)?)),
        Exchange::Binance => Ok(Adapter::Binance(BinanceAdapter::new(instruments))),
        Exchange::Coinbase => Ok(Adapter::Coinbase(CoinbaseAdapter::new(instruments))),
    }
}

fn streams_for(exchange: Exchange) -> &'static [&'static str] {
    match exchange {
        Exchange::Kraken => KRAKEN_STREAMS,
        Exchange::Binance => BINANCE_STREAMS,
        Exchange::Coinbase => COINBASE_STREAMS,
    }
}

async fn run(args: RunArgs) -> Result<()> {
    let exchange = args.exchange;
    let instruments = Instruments::load(&args.instruments_file, exchange)?;
    tracing::info!(
        %exchange,
        instruments = instruments.len(),
        "loaded the instrument registry"
    );

    let mut adapter = build_adapter(exchange, instruments)?;
    let symbols = adapter.symbols();
    // Only Kraken publishes a book checksum, so only Kraken gets a
    // `k2_capture_checksum_failures_total` series per symbol. Seeding 23 of them
    // for Binance and Coinbase advertised a capability those venues do not have.
    let checksummed: &[String] = match exchange {
        Exchange::Kraken => &symbols,
        Exchange::Binance | Exchange::Coinbase => &[],
    };
    let continuous = continuous_streams_for(exchange);
    k2_metrics::install(
        args.metrics_port,
        exchange.as_str(),
        streams_for(exchange),
        &symbols,
        checksummed,
    )?;

    // Seed the staleness gauge for every continuous stream at process start.
    // The gauge is otherwise created by the first frame, so a subscription the
    // venue silently rejects has no series at all — `time() - <absent>` is an
    // empty vector, `CaptureFeedStale` cannot fire on it, and `CaptureDown`
    // stays green because the process is perfectly healthy. Seeding at *now*
    // rather than at 0 means a healthy start does not fire either: the series
    // ages out of the 60 s threshold only if no frame ever arrives.
    let started_s = now_ns() as f64 / 1e9;
    for stream in &continuous {
        gauge!("k2_capture_last_message_ts_seconds",
            "exchange" => exchange.as_str(), "stream" => *stream)
        .set(started_s);
    }

    let sink = Sink::new(
        &args.kafka_brokers,
        &args.schema_registry_url,
        args.topic_prefix.clone(),
        exchange.as_str(),
    )?;
    // Before the socket, not after: this is the one place a schema registry
    // round trip is allowed to be slow. Once the frame loop is running the
    // encoder must never have to fetch anything (sink.rs header), and a
    // registry that is unreachable at start means no record can be built at
    // all — so failing here and letting compose restart us is the honest
    // outcome, not reading a venue we cannot produce.
    sink.warm_up().await?;
    tracing::info!("schema registry warm; every subject cached");

    let url = adapter.ws_url(
        &args
            .ws_url
            .unwrap_or_else(|| exchange.default_ws_url().to_string()),
    );
    let snapshot_interval = Duration::from_millis(args.snapshot_interval_ms);
    let mut backoff = Backoff::new();
    let mut shutdown = Shutdown::new()?;

    loop {
        let outcome = session(
            &url,
            &mut adapter,
            &sink,
            &symbols,
            snapshot_interval,
            &mut shutdown,
            &mut backoff,
        )
        .await;

        let reason = match outcome {
            Ok(Session::Shutdown) => break,
            Ok(Session::Scheduled) => "scheduled",
            Ok(Session::Disconnected) => "involuntary",
            Err(e) => {
                // `?e` and not `%e`, here and at every other error site in this
                // crate: Display on an `anyhow::Error` prints only the outermost
                // `.context(..)`, so "capture session ended: connecting" told us
                // nothing about the DNS or TLS failure underneath it. Debug
                // prints the whole `Caused by:` chain.
                tracing::warn!(error = ?e, "capture session ended");
                "involuntary"
            }
        };

        counter!("k2_capture_reconnects_total",
            "exchange" => exchange.as_str(), "reason" => reason)
        .increment(1);
        let wait = backoff.next_delay();
        tracing::info!(?wait, "reconnecting");
        tokio::select! {
            _ = tokio::time::sleep(wait) => {}
            _ = shutdown.wait() => break,
        }
    }

    tracing::info!("flushing the producer");
    sink.flush(Duration::from_secs(5));
    Ok(())
}

enum Session {
    /// The venue, the network or the watchdog ended it.
    Disconnected,
    /// We ended it, on purpose, at the scheduled connection age.
    Scheduled,
    Shutdown,
}

/// One connection, from subscribe to close.
///
/// Split out from `run` so the reconnect loop reads as "connect, capture until
/// something ends it, back off, repeat" rather than as four levels of nesting.
async fn session(
    url: &str,
    adapter: &mut Adapter,
    sink: &Sink,
    symbols: &[String],
    snapshot_interval: Duration,
    shutdown: &mut Shutdown,
    backoff: &mut Backoff,
) -> Result<Session> {
    let venue = adapter.exchange();
    let exchange = venue.as_str();
    let continuous = continuous_streams_for(venue);
    let mut feed = Feed::connect(url).await?;
    let opened_ns = now_ns();
    let conn_id = Uuid::new_v4().to_string();
    adapter.begin_connection(&conn_id);
    tracing::info!(conn_id, url, "connected");

    for message in adapter.subscribe_messages() {
        feed.send(&message).await?;
    }
    backoff.reset();

    let mut ticker = tokio::time::interval(snapshot_interval);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    // Per continuous stream, not one number for the whole socket. Kraken's 1 Hz
    // heartbeat keeps a single `last_frame_ns` fresh forever, so a `book`
    // subscription the venue silently stopped serving would never be noticed
    // and the connection would never be recycled.
    let mut last_frame_ns: std::collections::HashMap<&'static str, i64> =
        continuous.iter().map(|s| (*s, opened_ns)).collect();
    // Work queued inside the select and performed after it: the select holds a
    // mutable borrow of `feed` for as long as its branches are alive.
    let mut to_send: Vec<String> = Vec::new();

    loop {
        tokio::select! {
            frame = feed.next_frame() => {
                let Some(frame) = frame? else {
                    tracing::info!("peer closed the connection");
                    return Ok(Session::Disconnected);
                };
                let handled = adapter.handle_frame(&frame.payload, frame.recv_ts_ns);
                if let Some(slot) = last_frame_ns.get_mut(handled.stream.as_str()) {
                    *slot = frame.recv_ts_ns;
                }

                counter!("k2_capture_messages_total",
                    "exchange" => exchange, "stream" => handled.stream.clone()).increment(1);
                counter!("k2_capture_bytes_total",
                    "exchange" => exchange, "stream" => handled.stream.clone())
                    .increment(frame.payload.len() as u64);
                if is_continuous(&handled.stream) {
                    gauge!("k2_capture_last_message_ts_seconds",
                        "exchange" => exchange, "stream" => handled.stream.clone())
                        .set(frame.recv_ts_ns as f64 / 1e9);
                }

                for record in &handled.records {
                    if let OutRecord::Trade(t) = record {
                        // Venue clock to our clock. Negative values are real and
                        // are kept: they mean the venue's clock is ahead of ours,
                        // which is exactly what this histogram is for.
                        let lag = (t.recv_ts_ns as f64 / 1e9) - (t.exchange_ts as f64 / 1e6);
                        histogram!("k2_capture_exchange_to_recv_seconds", "exchange" => exchange)
                            .record(lag);
                    }
                    sink.send(record).await;
                }
                for action in handled.actions {
                    match action {
                        // Binance returns no frames here: its resync is the
                        // next in-order partial frame.
                        Action::Resubscribe(symbol) => {
                            to_send.extend(adapter.resubscribe_messages(&symbol));
                        }
                        Action::Reconnect => {
                            tracing::warn!("adapter asked for a fresh connection");
                            feed.close().await;
                            return Ok(Session::Disconnected);
                        }
                    }
                }
            }
            _ = ticker.tick() => {
                let now = now_ns();
                for symbol in symbols {
                    if let Some(snapshot) = adapter.snapshot(symbol, now) {
                        sink.send(&OutRecord::Book(snapshot)).await;
                    }
                    if let Some(depth) = adapter.depth(symbol) {
                        gauge!("k2_capture_book_depth",
                            "exchange" => exchange, "symbol" => symbol.clone())
                            .set(depth as f64);
                    }
                }
                gauge!("k2_capture_book_levels_total", "exchange" => exchange)
                    .set(adapter.total_levels() as f64);

                // A venue that stops sending without closing the socket looks
                // healthy at the TCP layer forever. Every continuous stream runs
                // at >= 1 frame/s across all three venues (measured 2026-08-26,
                // 10 min), so 60 s of silence on ANY ONE of them is a dead
                // subscription, not a quiet market — and a dead subscription is
                // exactly what a whole-socket timer cannot see.
                if let Some((stream, since)) = last_frame_ns
                    .iter()
                    .find(|(_, ts)| now - **ts > 60_000_000_000)
                {
                    tracing::warn!(
                        stream,
                        silent_s = (now - *since) / 1_000_000_000,
                        "no frames on a continuous stream for 60s, reconnecting"
                    );
                    return Ok(Session::Disconnected);
                }

                // Binance closes the socket itself at 24 h. Getting there first
                // is the difference between a disconnect we chose and one that
                // lands mid-frame. See `connection_expired`.
                if connection_expired(venue, opened_ns, now) {
                    tracing::info!(
                        age_s = (now - opened_ns) / 1_000_000_000,
                        "scheduled reconnect: connection reached its maximum age"
                    );
                    feed.close().await;
                    return Ok(Session::Scheduled);
                }
            }
            _ = shutdown.wait() => {
                tracing::info!("shutdown signal, closing the connection");
                feed.close().await;
                return Ok(Session::Shutdown);
            }
        }

        for message in to_send.drain(..) {
            feed.send(&message).await?;
        }
    }
}

/// SIGTERM (compose stop) and SIGINT (a terminal), as one future.
struct Shutdown {
    sigterm: tokio::signal::unix::Signal,
    sigint: tokio::signal::unix::Signal,
}

impl Shutdown {
    fn new() -> Result<Self> {
        use tokio::signal::unix::{SignalKind, signal};
        Ok(Self {
            sigterm: signal(SignalKind::terminate()).context("installing the SIGTERM handler")?,
            sigint: signal(SignalKind::interrupt()).context("installing the SIGINT handler")?,
        })
    }

    async fn wait(&mut self) {
        tokio::select! {
            _ = self.sigterm.recv() => {}
            _ = self.sigint.recv() => {}
        }
    }
}

/// Ask our own exporter when each stream last saw a frame.
///
/// Reads `k2_capture_last_message_ts_seconds` rather than a dedicated health
/// endpoint so the healthcheck and the staleness alert cannot disagree: they
/// read the same number.
async fn healthcheck(args: HealthArgs) -> Result<()> {
    let body = http_get("127.0.0.1", args.metrics_port, "/metrics").await?;
    let now = now_ns() as f64 / 1e9;
    let oldest = oldest_stream_ts(&body);

    // The OLDEST stream, not the newest. Taking the max meant Kraken's 1 Hz
    // heartbeat reported the container healthy while `book` and `trade` were
    // both silent — the healthcheck was green on precisely the failure it
    // exists to catch. Only continuous streams carry this gauge (see
    // `CONTINUOUS`), and `run` seeds all of them at process start, so an absent
    // series is a broken exporter rather than a stream that has yet to speak.
    match oldest {
        Some(ts) if now - ts <= args.max_age_seconds as f64 => {
            println!("ok: oldest stream {:.1}s behind", now - ts);
            Ok(())
        }
        Some(ts) => {
            eprintln!(
                "stale: a stream has had no frame for {:.1}s (limit {}s)",
                now - ts,
                args.max_age_seconds
            );
            std::process::exit(1);
        }
        None => {
            // A non-zero exit is the whole interface here; the message is for
            // whoever reads `docker inspect`.
            eprintln!("stale: no k2_capture_last_message_ts_seconds series on /metrics");
            std::process::exit(1);
        }
    }
}

/// Oldest `k2_capture_last_message_ts_seconds` in a Prometheus exposition body,
/// or `None` when the metric is absent entirely. Pure, so the "green while the
/// primary feed is dead" bug has a test that does not need a running exporter.
fn oldest_stream_ts(body: &str) -> Option<f64> {
    body.lines()
        .filter(|l| l.starts_with("k2_capture_last_message_ts_seconds{"))
        .filter_map(|l| l.rsplit(' ').next())
        .filter_map(|v| v.parse::<f64>().ok())
        .fold(None, |acc: Option<f64>, v| {
            Some(acc.map_or(v, |a| a.min(v)))
        })
}

/// Record frames verbatim as JSONL, one object per line, for a replay fixture.
///
/// Subscribes exactly as `run` does - the fixture is only worth anything if it
/// is the same conversation the live path has.
async fn record(args: RecordArgs) -> Result<()> {
    let mut instruments = Instruments::load(&args.instruments_file, args.exchange)?;
    if !args.symbols.is_empty() {
        instruments.retain_native(&args.symbols)?;
    }
    let adapter = build_adapter(args.exchange, instruments)?;
    let url = adapter.ws_url(
        &args
            .ws_url
            .unwrap_or_else(|| args.exchange.default_ws_url().to_string()),
    );

    let mut feed = Feed::connect(&url).await?;
    for message in adapter.subscribe_messages() {
        feed.send(&message).await?;
    }

    let deadline = tokio::time::Instant::now() + Duration::from_secs(args.seconds);
    let mut frames = 0u64;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            frame = feed.next_frame() => {
                let Some(frame) = frame? else { break };
                // Payload as a JSON string: every venue K2 captures sends UTF-8
                // text frames, and a string keeps the fixture readable in a
                // diff. A binary venue would need base64 here.
                let line = FixtureLine {
                    recv_ts_ns: frame.recv_ts_ns,
                    payload: String::from_utf8_lossy(&frame.payload),
                };
                println!("{}", serde_json::to_string(&line)?);
                frames += 1;
            }
        }
    }
    feed.close().await;
    tracing::info!(frames, "recorded");
    Ok(())
}

/// One line of a replay fixture. A struct rather than `json!` so the field
/// order is the declared one - `tests/replay.rs` reads these back and a stable
/// layout keeps the committed file diffable.
#[derive(serde::Serialize)]
struct FixtureLine<'a> {
    recv_ts_ns: i64,
    payload: std::borrow::Cow<'a, str>,
}

#[cfg(test)]
mod stream_tests {
    use super::*;

    #[test]
    fn one_shot_acks_never_stamp_staleness() {
        // `instrument` is here for the same reason as the acks, with a different
        // cause: Kraken sends it at subscribe and then only on a reference
        // change (2 frames in 29 minutes, 2026-08-26). At 0.0017/s against a
        // 60 s threshold it is a guaranteed critical, not a liveness signal.
        for ack in ["status", "control", "subscriptions", "instrument"] {
            assert!(!is_continuous(ack), "{ack} is not a continuous stream");
        }
        for ex in [KRAKEN_STREAMS, BINANCE_STREAMS, COINBASE_STREAMS] {
            assert!(
                ex.iter().any(|s| is_continuous(s)),
                "every exchange has at least one continuous stream"
            );
        }
        for s in CONTINUOUS {
            assert!(
                KRAKEN_STREAMS.contains(s)
                    || BINANCE_STREAMS.contains(s)
                    || COINBASE_STREAMS.contains(s),
                "{s} is not a registered stream"
            );
        }
    }

    #[test]
    fn the_healthcheck_reads_the_oldest_stream_not_the_newest() {
        // Kraken mid-failure: heartbeat is 1 Hz and current, book and trade have
        // been silent for an hour. Taking the max reported 1_000_000_000 and the
        // container stayed `healthy`.
        let body = "\
# HELP k2_capture_last_message_ts_seconds when this stream last saw a frame
k2_capture_last_message_ts_seconds{exchange=\"kraken\",stream=\"heartbeat\"} 1000000000
k2_capture_last_message_ts_seconds{exchange=\"kraken\",stream=\"book\"} 999996400
k2_capture_last_message_ts_seconds{exchange=\"kraken\",stream=\"trade\"} 999996400
k2_capture_messages_total{exchange=\"kraken\",stream=\"book\"} 12
";
        assert_eq!(oldest_stream_ts(body), Some(999_996_400.0));
        assert_eq!(
            oldest_stream_ts("k2_capture_messages_total{exchange=\"kraken\"} 1\n"),
            None,
            "an absent gauge is not a fresh feed"
        );
    }

    #[test]
    fn only_binance_reconnects_on_a_schedule() {
        let day = 24 * 3600 * 1_000_000_000i64;
        let twenty_three_h = 23 * 3600 * 1_000_000_000i64;

        assert!(!connection_expired(
            Exchange::Binance,
            0,
            twenty_three_h - 1
        ));
        assert!(connection_expired(Exchange::Binance, 0, twenty_three_h));
        assert!(
            connection_expired(Exchange::Binance, 0, day),
            "24 h is the venue's own cut-off; we must be gone before it"
        );
        for quiet in [Exchange::Kraken, Exchange::Coinbase] {
            assert!(
                !connection_expired(quiet, 0, 30 * day),
                "{quiet} publishes no connection lifetime, so K2 invents none"
            );
        }
    }
}
