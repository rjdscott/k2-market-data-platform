//! The WebSocket connection, and the one clock reading that matters.
//!
//! `recv_ts_ns` is taken as the **first statement** after a frame comes off the
//! socket - before the payload is copied, before it is parsed, before anything
//! is allocated. It is the only timestamp in the v3 records that K2 controls,
//! and everything downstream that claims to measure ingress latency measures
//! the distance from here.
//!
//! `SystemTime` and not `Instant`: the value is written into records and joined
//! against exchange timestamps in the lake, so it has to be wall clock. A
//! monotonic `Instant` is the right clock for an internal duration histogram
//! and the wrong one for anything that leaves the process.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async_with_config};

/// Explicit ceiling on a single WebSocket message. Coinbase's `level2`
/// subscribe snapshot measured 5.2 MB / ~44k levels (spike S5), and the 1 MiB
/// default of most client libraries kills the connection on the very first
/// frame. 8 MB leaves headroom over the largest frame anyone has measured
/// without letting a runaway frame exhaust a 256 MB container.
const MAX_MESSAGE_BYTES: usize = 8 * 1024 * 1024;

type Stream = WebSocketStream<MaybeTlsStream<TcpStream>>;

/// One frame, stamped on arrival.
#[derive(Debug, Clone)]
pub struct Frame {
    pub recv_ts_ns: i64,
    pub payload: Vec<u8>,
}

/// Wall clock in nanoseconds since the Unix epoch.
///
/// Saturates rather than panicking on a clock before 1970: a container with a
/// broken clock should produce obviously wrong timestamps and keep capturing,
/// not stop capturing.
pub fn now_ns() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0)
}

pub struct Feed {
    stream: Stream,
}

impl Feed {
    pub async fn connect(url: &str) -> Result<Self> {
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_MESSAGE_BYTES))
            .max_frame_size(Some(MAX_MESSAGE_BYTES));
        let (stream, _response) = connect_async_with_config(url, Some(config), false)
            .await
            .with_context(|| format!("connecting to {url}"))?;
        Ok(Self { stream })
    }

    /// Next data frame, or `None` when the peer closed.
    ///
    /// Ping/pong is handled inside tungstenite and deliberately not surfaced:
    /// a pong is not something the exchange sent us about the market, so it is
    /// not a frame the archive needs and it must not advance `conn_msg_seq`.
    pub async fn next_frame(&mut self) -> Result<Option<Frame>> {
        loop {
            let message = self.stream.next().await;
            // FIRST statement after the frame arrives. Nothing above this line
            // may allocate, parse or branch on the payload.
            let recv_ts_ns = now_ns();

            return match message {
                Some(Ok(Message::Text(text))) => Ok(Some(Frame {
                    recv_ts_ns,
                    payload: text.as_bytes().to_vec(),
                })),
                Some(Ok(Message::Binary(bytes))) => Ok(Some(Frame {
                    recv_ts_ns,
                    payload: bytes.to_vec(),
                })),
                Some(Ok(Message::Close(_))) | None => Ok(None),
                Some(Ok(_)) => continue, // ping, pong, raw frame
                Some(Err(e)) => Err(e).context("reading a websocket frame"),
            };
        }
    }

    pub async fn send(&mut self, text: &str) -> Result<()> {
        self.stream
            .send(Message::text(text.to_string()))
            .await
            .context("sending a websocket frame")
    }

    /// Best-effort close handshake. A failure here is not worth reporting: we
    /// are on the way out and the socket is about to be dropped regardless.
    pub async fn close(mut self) {
        let _ = self.stream.close(None).await;
    }
}

/// Exponential backoff between reconnect attempts, capped.
///
/// Capped at 30 s because a venue-side outage longer than that is an incident
/// someone is already looking at, and a capture process that has backed off to
/// ten minutes will not notice the venue coming back.
pub struct Backoff {
    current: Duration,
}

impl Backoff {
    const BASE: Duration = Duration::from_millis(500);
    const CAP: Duration = Duration::from_secs(30);

    pub fn new() -> Self {
        Self {
            current: Self::BASE,
        }
    }

    /// Called after a successful connection lasted long enough to count.
    pub fn reset(&mut self) {
        self.current = Self::BASE;
    }

    pub fn next_delay(&mut self) -> Duration {
        let wait = self.current;
        self.current = (self.current * 2).min(Self::CAP);
        wait
    }
}

impl Default for Backoff {
    fn default() -> Self {
        Self::new()
    }
}

/// Read a URL's body over plain HTTP/1.0, for the `healthcheck` subcommand.
///
/// Ten lines of `TcpStream` rather than a HTTP client crate: this talks to
/// `127.0.0.1` inside the same container, over a connection that cannot be
/// intercepted, and the alternative pulls in a TLS stack and a connection pool
/// to fetch one page from ourselves. `curl` is not an option - the runtime
/// image is distroless and has no shell, let alone curl.
pub async fn http_get(host: &str, port: u16, path: &str) -> Result<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let mut conn = TcpStream::connect((host, port))
        .await
        .with_context(|| format!("connecting to {host}:{port}"))?;
    conn.write_all(format!("GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n").as_bytes())
        .await?;
    let mut body = String::new();
    conn.read_to_string(&mut body).await?;
    match body.split_once("\r\n\r\n") {
        Some((_headers, body)) => Ok(body.to_string()),
        None => bail!("no HTTP body from {host}:{port}{path}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_then_caps() {
        let mut b = Backoff::new();
        assert_eq!(b.next_delay(), Duration::from_millis(500));
        assert_eq!(b.next_delay(), Duration::from_secs(1));
        assert_eq!(b.next_delay(), Duration::from_secs(2));
        for _ in 0..20 {
            b.next_delay();
        }
        assert_eq!(b.next_delay(), Backoff::CAP);
        b.reset();
        assert_eq!(b.next_delay(), Duration::from_millis(500));
    }

    #[test]
    fn now_ns_is_nanoseconds_not_millis() {
        // 2026-01-01 in nanoseconds; catches a unit slip that would otherwise
        // only show up as a wrong answer in the lake.
        assert!(now_ns() > 1_767_225_600_000_000_000);
    }
}
