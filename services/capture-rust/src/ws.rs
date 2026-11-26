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

/// The bound on every network await in this module that the session watchdog
/// does not already cover: the connect (DNS on tokio's blocking pool, TCP, the
/// TLS handshake, the HTTP 101 upgrade), a send, a ping, the close handshake.
///
/// None of those had one, and on 2026-11-26 the Coinbase capture sat inside
/// `connect` for 80 minutes with no log line: the watchdog only exists once a
/// session is connected, and the reconnect loop only logs when `connect`
/// returns. The kernel does not rescue it: a SYN gives up after ~127 s
/// (`tcp_syn_retries=6`) and unacknowledged data after ~15 min
/// (`tcp_retries2=15`), but a socket whose bytes were acknowledged and never
/// answered has nothing in flight and waits for ever (no keepalive is set).
///
/// 10 s is ~7x the 1.3 s between `schema registry warm` and `connected` in the
/// recreated container's log that day, and leaves room for glibc to retry a
/// lost DNS query once (its timeout is 5 s).
/// A healthy send completes as soon as the bytes are in the kernel buffer, so
/// one that takes 10 s means the peer has stopped reading.
pub const IO_TIMEOUT: Duration = Duration::from_secs(10);

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
    /// Receipt time of the newest Ping or Pong not yet taken by `take_liveness`.
    last_liveness_ns: Option<i64>,
}

impl Feed {
    pub async fn connect(url: &str) -> Result<Self> {
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_MESSAGE_BYTES))
            .max_frame_size(Some(MAX_MESSAGE_BYTES));
        // ponytail: a timed-out DNS lookup leaves its blocking-pool thread
        // inside getaddrinfo until that returns; one per attempt at most, and
        // the stall exit in main.rs ends the process long before tokio's
        // 512-thread pool limit.
        let (stream, _response) = bounded(
            &format!("connecting to {url}"),
            connect_async_with_config(url, Some(config), false),
        )
        .await?;
        Ok(Self {
            stream,
            last_liveness_ns: None,
        })
    }

    /// Next data frame, or `None` when the peer closed.
    ///
    /// Control frames are not frames to the archive. A Ping from the venue
    /// (tungstenite answers it itself) and a Pong, the venue's answer to
    /// [`Feed::ping`], are kept only as a liveness event for
    /// [`Feed::take_liveness`] ([`is_liveness`]). Neither is something the
    /// exchange sent us about the market, so neither is archived or advances
    /// `conn_msg_seq`.
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
                Some(Ok(m)) if is_liveness(&m) => {
                    self.last_liveness_ns = Some(recv_ts_ns);
                    continue;
                }
                Some(Ok(_)) => continue, // a raw frame
                Some(Err(e)) => Err(e).context("reading a websocket frame"),
            };
        }
    }

    pub async fn send(&mut self, text: &str) -> Result<()> {
        bounded(
            "sending a websocket frame",
            self.stream.send(Message::text(text.to_string())),
        )
        .await
    }

    /// A WebSocket Ping, the protocol's own liveness probe: the peer must
    /// answer with a Pong (RFC 6455 5.5.2). An empty payload; no market data.
    pub async fn ping(&mut self) -> Result<()> {
        bounded(
            "sending a websocket ping",
            self.stream.send(Message::Ping(Default::default())),
        )
        .await
    }

    /// When the newest Ping or Pong arrived, if one has since the last call.
    pub fn take_liveness(&mut self) -> Option<i64> {
        self.last_liveness_ns.take()
    }

    /// Best-effort close handshake. A failure here is not worth reporting: we
    /// are on the way out and the socket is about to be dropped regardless.
    pub async fn close(mut self) {
        let _ = bounded("closing", self.stream.close(None)).await;
    }
}

/// `io`, or an error naming `what` once it has taken longer than `IO_TIMEOUT`.
/// The error goes back up to `run`, which logs it and takes the backoff path.
async fn bounded<T, E>(what: &str, io: impl Future<Output = Result<T, E>>) -> Result<T>
where
    anyhow::Error: From<E>,
{
    match tokio::time::timeout(IO_TIMEOUT, io).await {
        Ok(done) => done.map_err(anyhow::Error::from).context(what.to_string()),
        Err(_) => bail!("{what}: timed out after {}s", IO_TIMEOUT.as_secs()),
    }
}

/// A control frame that proves the peer is alive: its answer to our Ping, or
/// its own Ping. Binance pings every client about every 20 s and expects the
/// Pong within a minute, so counting its pings keeps a venue that pings us but
/// does not answer ours from being reconnected every 61 s (ADR-050).
pub fn is_liveness(message: &Message) -> bool {
    matches!(message, Message::Ping(_) | Message::Pong(_))
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
    fn the_venues_ping_and_its_pong_are_liveness_and_nothing_else_is() {
        assert!(is_liveness(&Message::Ping(Default::default())));
        assert!(is_liveness(&Message::Pong(Default::default())));
        assert!(!is_liveness(&Message::text("{}")));
        assert!(!is_liveness(&Message::Binary(Default::default())));
        assert!(!is_liveness(&Message::Close(None)));
    }

    /// The 2026-11-26 Coinbase hang, reproduced: a peer that accepts the TCP
    /// connection, takes our bytes and never answers. Over `ws` that is the
    /// HTTP upgrade, over `wss` the TLS handshake; either left `Feed::connect`
    /// pending for ever and the reconnect loop silent. On tokio's paused
    /// clock: without the bound the hour below elapses at once and the test
    /// fails, with it `connect` errors at exactly `IO_TIMEOUT`.
    #[tokio::test(start_paused = true)]
    async fn a_peer_that_accepts_and_never_answers_is_abandoned_at_the_bound() {
        use std::io::Read;
        for scheme in ["ws", "wss"] {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let url = format!("{scheme}://{}", listener.local_addr().unwrap());
            let (first_bytes, received) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                let mut buf = [0u8; 512];
                let n = socket.read(&mut buf).unwrap();
                first_bytes.send(buf[..n].to_vec()).unwrap();
                // Silent, socket open, until the client gives up.
                let _ = socket.read_to_end(&mut Vec::new());
            });

            let started = tokio::time::Instant::now();
            let hour = Duration::from_secs(3600);
            let e = match tokio::time::timeout(hour, Feed::connect(&url)).await {
                Ok(Err(e)) => e,
                Ok(Ok(_)) => panic!("{scheme}: a peer that never answered completed a handshake"),
                Err(_) => panic!(
                    "{scheme}: Feed::connect still pending after an hour, the 2026-11-26 hang"
                ),
            };
            let waited = started.elapsed();
            assert!(
                waited >= IO_TIMEOUT && waited < IO_TIMEOUT + Duration::from_secs(1),
                "{scheme}: gave up after {waited:?}"
            );
            assert!(
                format!("{e:#}").contains("timed out after 10s"),
                "{scheme}: {e:#}"
            );

            // The stall is the peer's answer, not our connect: it had our bytes.
            let sent = received.recv_timeout(Duration::from_secs(5)).unwrap();
            match scheme {
                "ws" => assert!(sent.starts_with(b"GET / HTTP/1.1"), "{sent:?}"),
                _ => assert_eq!(sent.first(), Some(&0x16), "a TLS handshake record"),
            }
        }
    }

    #[test]
    fn now_ns_is_nanoseconds_not_millis() {
        // 2026-01-01 in nanoseconds; catches a unit slip that would otherwise
        // only show up as a wrong answer in the lake.
        assert!(now_ns() > 1_767_225_600_000_000_000);
    }
}
