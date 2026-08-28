//! Recorded frames through the live adapter, with the clock supplied by the
//! recording.
//!
//! This is the one replay driver: `k2-capture replay` calls it, and so do the
//! per-venue fixture tests, so a hash the tests commit is the hash the
//! subcommand prints. There is no replay-only parser anywhere - every frame goes
//! through the same `Adapter::handle_frame(bytes, recv_ts_ns)` the socket loop
//! calls, and the 1 Hz sampler ticks off recorded `recv_ts_ns`, never off
//! `SystemTime`. Same input, same bytes out (ADR-029).

use std::io::{BufRead, Write};
use std::time::Duration;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::exchanges::Adapter;
use crate::record::OutRecord;

/// One line of a fixture or a lake export: the frame and when it arrived.
/// `payload` is a JSON string because every venue here sends UTF-8 text frames.
#[derive(Debug, Serialize, Deserialize)]
pub struct FixtureLine<'a> {
    pub recv_ts_ns: i64,
    #[serde(borrow)]
    pub payload: std::borrow::Cow<'a, str>,
}

/// The `conn_id` a replay stamps on every record when the caller does not
/// supply one. Fixed so the output depends only on the input; live, this is a
/// v4 uuid per connection.
pub const FIXTURE_CONN_ID: &str = "00000000-0000-4000-8000-0000000000fx";

/// The sampler cadence `run` uses by default (`K2_SNAPSHOT_INTERVAL_MS`).
pub const DEFAULT_INTERVAL_NS: i64 = 1_000_000_000;

#[derive(Debug, Clone)]
pub struct Options {
    pub conn_id: String,
    pub snapshot_interval_ns: i64,
    /// Levels per side in each sampled snapshot. `None` keeps the product's
    /// top-20; a replay may ask for more, bounded by what the venue sent.
    pub depth: Option<usize>,
    /// Sleep the recorded inter-frame delta before each frame. The output is
    /// identical either way; this exists for demos.
    pub realtime: bool,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            conn_id: FIXTURE_CONN_ID.to_string(),
            snapshot_interval_ns: DEFAULT_INTERVAL_NS,
            depth: None,
            realtime: false,
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Stats {
    pub frames: usize,
    pub records: usize,
    /// Resubscribe or reconnect requests the adapter raised: Kraken checksum
    /// mismatches, Coinbase sequence gaps, Binance `lastUpdateId` regressions.
    pub actions: usize,
    pub first_recv_ts_ns: Option<i64>,
    pub last_recv_ts_ns: Option<i64>,
}

/// Drive `adapter` over every line of `input`, handing each record to `emit`
/// in the order it was produced (the raw frame first, then what was derived
/// from it, then any snapshots the recorded clock made due).
pub fn run<R: BufRead>(
    adapter: &mut Adapter,
    input: R,
    opts: &Options,
    mut emit: impl FnMut(&OutRecord) -> Result<()>,
) -> Result<Stats> {
    adapter.begin_connection(&opts.conn_id);
    if let Some(depth) = opts.depth {
        adapter.set_snapshot_depth(depth);
    }
    let symbols = adapter.symbols();
    let mut stats = Stats::default();
    let mut next_sample_ns: Option<i64> = None;

    for (n, line) in input.lines().enumerate() {
        let line = line.with_context(|| format!("reading input line {}", n + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let frame: FixtureLine = serde_json::from_str(&line)
            .with_context(|| format!("input line {} is not a fixture line", n + 1))?;
        if opts.realtime
            && let Some(prev) = stats.last_recv_ts_ns
        {
            let delta = frame.recv_ts_ns.saturating_sub(prev);
            if delta > 0 {
                std::thread::sleep(Duration::from_nanos(delta as u64));
            }
        }
        stats.first_recv_ts_ns.get_or_insert(frame.recv_ts_ns);
        stats.last_recv_ts_ns = Some(frame.recv_ts_ns);
        stats.frames += 1;

        let handled = adapter.handle_frame(frame.payload.as_bytes(), frame.recv_ts_ns);
        stats.actions += handled.actions.len();
        for record in &handled.records {
            emit(record)?;
            stats.records += 1;
        }

        // The sampler: due at fixed offsets from the first frame's clock, as
        // `tokio::time::interval` would be from connect. A frame later than the
        // due time triggers exactly one sample, matching MissedTickBehavior::Skip.
        let due = *next_sample_ns.get_or_insert(frame.recv_ts_ns + opts.snapshot_interval_ns);
        if frame.recv_ts_ns >= due {
            for symbol in &symbols {
                if let Some(snapshot) = adapter.snapshot(symbol, frame.recv_ts_ns) {
                    emit(&OutRecord::Book(snapshot))?;
                    stats.records += 1;
                }
            }
            next_sample_ns = Some(frame.recv_ts_ns + opts.snapshot_interval_ns);
        }
    }
    Ok(stats)
}

/// One record per line, the shape `replay --sink jsonl` writes and the shape
/// the committed fixture hashes are over. `serde_json` with the record's
/// declared field order, so the bytes are a function of the record alone.
pub fn write_jsonl(out: &mut impl Write, record: &OutRecord) -> Result<()> {
    serde_json::to_writer(&mut *out, record)?;
    out.write_all(b"\n")?;
    Ok(())
}
