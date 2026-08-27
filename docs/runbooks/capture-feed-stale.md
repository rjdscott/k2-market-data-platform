# Runbook: Recover a stale capture stream, and read the ingress latency alert

Covers a `k2-capture` container that is **up and scrapeable but not receiving** on one
or more of its streams, and the exchange-to-receive latency alert. For a container
that is down or failing to produce, see [capture-down.md](./capture-down.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Partly measured.** The first `make chaos` run (2026-08-26,
> [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv))
> measured the *recovery* side of row 1 but could not make `CaptureFeedStale` itself
> fire, a paused container is unscrapeable, so Prometheus stale-marks the very gauge
> this alert reads and `CaptureDown` fires instead. The detection half of row 1 and all
> of row 2 remain unverified, and say so rather than borrowing a number.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | One stream silent while the process stays healthy | < 3 min | recovery **8–10 s** to fresh frames after unpause, alert clear a further 18–22 s (2026-08-26, `capture-pause.sh` on coinbase and binance). Time-to-**detection** via this alert: not yet verified, the injection scores `CaptureDown` |
| 2 | Exchange-to-receive p99 elevated | investigate, no restart | not yet verified, no injection exists; Phase F's noisy-neighbour experiment is the trigger |

---

## 1. Stream silent, process healthy

**Symptom**, the container is running, `/metrics` responds, `CaptureDown` is not
firing, and one stream has simply stopped. Often *one* stream: book snapshots stop
while trades keep flowing, or the reverse.

**Detection**, `CaptureFeedStale` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
(
  (time() - k2_capture_last_message_ts_seconds{stream!~"trade|market_trades"} > 60)
  or
  (time() - k2_capture_last_message_ts_seconds{stream=~"trade|market_trades"} > 300)
)
```

Fires after `for: 2m`. The metric is labelled `{exchange, stream}` with the venue's
channel name (`trade`, `book`, `depth20`, `l2_data`, `market_trades`, `heartbeat(s)`),
so the alert names which subscription went quiet.

**The bound is per stream, and it is the process's own.** `CONTINUOUS` in
`services/capture-rust/src/main.rs` is one table of `(stream, Duration)` read by three
things, the session watchdog, `k2-capture healthcheck`, and this rule by hand:

| streams | bound | why |
|---|---|---|
| `book`, `depth20`, `l2_data`, `heartbeat`, `heartbeats` | 60 s | these run at 1 Hz or better on all three venues whatever the market is doing, so a quiet minute is a dead subscription |
| `trade`, `market_trades` | 300 s | Kraken `trade`'s longest measured silence was 20.4 s over 3 h, but "nothing printed" is a market state; a quiet hour on a thin instrument is not a fault, and the answer this alert routes to costs a reconnect plus 11 book resubscriptions |

A flat 60 s on the trade channels fired on quiet markets and had the watchdog recycle
a perfectly healthy socket to answer it. If you change one of these numbers, change it
in the table and in this rule together, `docker/prometheus/tests/capture-alerts.test.yml`
holds a case on each side of both thresholds.

**There is no guard against `CaptureDown`, and none is needed.** This alert is for a
venue that goes quiet while the process stays scrapeable, which is a narrower thing than
"no frames". On a failed scrape Prometheus stale-marks every series from the target, so
a container that is paused, killed or unreachable has no
`k2_capture_last_message_ts_seconds` at all within a scrape or two, `time() - <absent>`
is an empty vector and cannot fire. A dead container is therefore one alert rather than
five because of stale-marking, not because of anything in this expression. Those faults
are `CaptureDown`'s: go to [capture-down.md](./capture-down.md).

Two guards were tried here and both were removed. `and on (job) up == 1` could never be
true, a frozen or gone container stops answering scrapes at once, so `up` drops to 0
within one `scrape_timeout` (10 s) plus the scrape interval, well before 60 s of
staleness accrues, let alone this rule's 2 m `for`, and it stopped this alert firing at
all. The `unless on (job) ALERTS{alertname="CaptureDown"}` that replaced it was inert
for the reason above: by the time `CaptureDown` fires there is no series left to
suppress.

Only continuous streams carry this gauge (`CONTINUOUS` in
`services/capture-rust/src/main.rs`), and two kinds of channel are deliberately left
out of it:

- **One-shot acknowledgements**, Kraken `status`/`control`, Coinbase `subscriptions`.
  They arrive once per (re)subscribe and then legitimately never again. The first 2 h
  window (2026-08-26 12:39Z) fired this alert on exactly those three, two minutes after
  a healthy connect.
- **Low-rate reference channels**, Kraken `instrument`. It is a snapshot at subscribe
  plus the occasional reference change: 0.0017 frames/s over a 10-minute sample on
  2026-08-26 (2 frames in 29 minutes), against a 60 s threshold, while every genuinely
  continuous stream on all three venues ran at 1.0/s or more.

Every continuous stream is also **seeded at process start**, so a subscription the
venue silently rejects has a series that goes stale within the window rather than no
series at all, `time() - <absent>` is an empty vector, and an empty vector cannot fire.
A firing on a name not in `CONTINUOUS` is a new stream the list does not know about,
not a stale feed.

**Expected behaviour**, the WebSocket client sends and answers heartbeats and
reconnects on its own backoff, so a dropped connection self-heals inside the 2-minute
window and this alert does not fire. It fires for the case a liveness check cannot
see: the socket is open, the process is fine, and the **subscription** is dead, the
exchange accepted a `subscribe` and stopped delivering, or a silent half-open TCP
connection is holding the read side open with nothing arriving.

The process enforces the same bounds on itself: the session watchdog reconnects when
**any one** continuous stream passes its own threshold, per stream rather than per
socket, because Kraken's 1 Hz heartbeat would otherwise keep a socket with a dead `book`
subscription alive forever. `k2-capture healthcheck` reads the same table and judges
every stream against its own bound, for both halves of that: the max reported healthy
while `book` and `trade` were dead, and the min against a flat 60 s reported unhealthy
on a quiet market.

Binance also performs a scheduled reconnect at 23 hours of connection life
(`BINANCE_MAX_CONNECTION_AGE` in `main.rs`, ahead of the venue's own 24 h cut-off);
that produces a brief, expected `conn_id` change and a
`k2_capture_reconnects_total{exchange="binance",reason="scheduled"}` increment, not a
60-second silence. Kraken and Coinbase publish no connection lifetime and have no such
timer.

**Recovery**

```bash
# 1. Which exchange and which stream                                  ✅ verified (v2)
curl -s localhost:9090/api/v1/alerts | \
  jq '.data.alerts[] | select(.labels.alertname=="CaptureFeedStale")
      | {exchange: .labels.exchange, stream: .labels.stream, since: .activeAt}'

# 2. Is it one stream or all of them? Age per stream, in seconds.     ✅ verified (v2)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_capture_last_message_ts_seconds' | \
  jq -r '.data.result[] | "\(.metric.exchange)/\(.metric.stream) \(.value[1])s"'

# 3. Has it been reconnecting behind our back?
docker logs k2-capture-coinbase --tail 100 | grep -i -E 'connect|subscribe|close|pong'

# 4. Is the exchange reachable at all from this host?
curl -s -o /dev/null -w '%{http_code}\n' https://api.exchange.coinbase.com/products/BTC-USD/ticker
```

**All streams silent** → the connection is dead but the socket has not noticed.
Restart the container; the reconnect rebuilds every subscription:

```bash
docker compose restart capture-coinbase
docker logs k2-capture-coinbase --tail 30 | grep -i subscribe   # expect all channels ack'd
```

**One stream silent, others flowing** → the subscription was lost without the
connection dropping. This is the more interesting case and it still recovers the same
way, because the capture design carries trades and book on **one** connection per
exchange (ADR-019) and has no per-channel resubscribe outside the Kraken checksum
path. Restart, then confirm each stream separately:

```bash
docker compose restart capture-coinbase

# every stream stamping again: each age under ITS OWN bound, not a flat 60:
# 60s for level2/l2_data/heartbeats, 300s for market_trades (main.rs CONTINUOUS)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_capture_last_message_ts_seconds{exchange="coinbase"}' | \
  jq -r '.data.result[] | "\(.metric.stream) \(.value[1])s"'

# and records actually reaching the broker
docker exec k2-redpanda rpk topic consume market.crypto.v3.book.coinbase \
  --num 1 --offset end --use-schema-registry=value                    # ✅ verified (v2 topic)
```

**Exchange unreachable** → nothing to fix here. Check the venue's status page, record
the outage window for the completeness audit, and let the reconnect backoff do its
job. Do not restart-loop against a down exchange.

**Record the gap.** Frames not received are not recoverable, public feeds do not
replay. The silent window is permanently absent from `raw.messages`; note it in the
day's completeness audit.

**Measured 2026-08-26**, `scripts/chaos/capture-pause.sh` `docker pause`s a capture
container to stall the read side without closing the socket, waits for **`CaptureDown`**,
then measures the time from unpause until the >=1 Hz streams'
`k2_capture_last_message_ts_seconds` are fresh again, `trade`/`market_trades` are
excluded from that gate, because a quiet market would otherwise hold it open for up to
300 s:

| | coinbase | binance |
|---|---|---|
| `CaptureDown` fired after | 165 s | 152 s |
| frames fresh after unpause | **10 s** | **8 s** |
| alert cleared, a further | 18 s | 22 s |
| `gaps_total` | 0 → 0 | 0 → 0 |
| `reconnects_total` | 0 → 1 | 0 → 1 |

**The freshness number is this page's; the `t_fire` is `CaptureDown`'s**, for the reason
two paragraphs up: a paused container is unscrapeable, and its gauges are stale-marked
before 60 s of silence has accrued. So the reconnect side of this runbook is verified
(under 10 s, both venues, no manual step) and the **detection** side is not, no
injection has yet made `CaptureFeedStale` fire, and only a real venue-side silence or
`k2-replay` (Phase G) will. Note also `gaps_total` staying at 0: the reconnect starts a
fresh sequence series, so a pause does not manufacture a gap.
Source: [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

---

## 2. Exchange-to-receive p99 elevated

**Symptom**, no data missing, nothing down; the ingress latency panel steps up and
stays there.

**Detection**, `CaptureIngressLatencyHigh` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
histogram_quantile(
  0.99,
  sum by (exchange, le) (rate(k2_capture_exchange_to_recv_seconds_bucket[5m]))
) > 2
```

Fires after `for: 10m`.

**Read this before acting on it.** The histogram measures `recv_ts_ns - exchange_ts`:
the venue's own clock subtracted from ours. That difference is **internet transit plus
exchange clock skew**, and the two cannot be separated in any single sample. K2 reads
public WebSocket feeds over the open internet and is explicitly not a trading path
(ADR-018), this is a *something changed* signal, not a latency SLO, and the absolute
value should never be quoted as a platform latency figure. A step change is the
signal; the level is not.

**Only trades feed this histogram, on every venue**, `main.rs` records it inside
`if let OutRecord::Trade(t) = record`. No book frame from Binance, Kraken or Coinbase
contributes, so a number here is a trades number on all three, and a book-path latency
regression is invisible in it by construction. (Binance's partial-depth stream carries
no `exchange_ts` to record in any case, ADR-027; Kraken's and Coinbase's book frames do
carry one and are simply not recorded.)

**Expected behaviour**, nothing self-heals, because in most cases nothing is broken.
The usual causes, in the order they are worth checking:

| Cause | Tell |
|-------|------|
| Venue clock step or a change in where they stamp | One exchange only, step change, no packet loss |
| Route change or ISP congestion | Multiple exchanges together, or matching host-level latency |
| Host clock drift | **All three** exchanges move at once, in the same direction |
| Real capture-side delay | Ingress latency and CPU saturation rise together |

**Recovery**

```bash
# 1. One exchange or all three? This is the fork that decides everything below.  ✅ verified (v2)
curl -s --get localhost:9090/api/v1/query --data-urlencode \
 'query=histogram_quantile(0.99, sum by (exchange, le) (rate(k2_capture_exchange_to_recv_seconds_bucket[5m])))' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])s"'

# 2. All three moved together -> suspect our clock, not theirs
timedatectl status | grep -E 'System clock|NTP'

# 3. One exchange only -> transit or their stamping
mtr -r -c 20 stream.binance.com 2>/dev/null || traceroute stream.binance.com

# 4. Is the capture container itself starved? 0.25 CPU quota, cpuset-pinned to
#    cores 12-14 by default (K2_CAPTURE_CPUSET, .env.example).
docker stats --no-stream k2-capture-binance k2-capture-kraken k2-capture-coinbase
```

- **Host clock** → fix NTP. Every historical `recv_ts_ns` taken while it was wrong is
  wrong, and that is worth an entry in the audit record because it is not visible in
  the data afterwards.
- **Transit / venue** → nothing to fix. Note it, and confirm completeness is
  unaffected (`k2_capture_gaps_total` flat, `CaptureFeedStale` not firing). Late data
  that all arrives is not a data-quality problem.
- **Capture starved** → if `docker stats` shows the container pegged at its CPU quota,
  raise the limit or move its `cpuset` (`K2_CAPTURE_CPUSET`, default `12-14`) further
  from ClickHouse and Spark, and update ADR-010's Outcome with the new budget as the
  project guardrails require. Confirm the sets are actually disjoint first:
  `docker inspect -f '{{.HostConfig.CpusetCpus}}' k2-capture-binance k2-spark-iceberg`.

**Do not restart the container for this alert.** A restart loses a window of data and
does not change transit time, a venue's clock, or this host's.

**Measured**, not yet verified, and the 2026-08-26 chaos run did not touch it: no script
injects latency. The noisy-neighbour experiment (Spark compaction on its cpuset while
capture ingress latency is sampled) produces the only number that belongs here, how much
of this metric the platform can move on its own, and it is Phase F's.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** 2026-08-26 (`make chaos`). Recovery times are measured, from
[`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv);
commands marked ✅ were run against the running capture tier the same day. The detection
time for `CaptureFeedStale` itself is still unmeasured, re-stamp when a venue-side
silence or `k2-replay` fires it.
