# Runbook: Recover a stale capture stream, and read the ingress latency alert

Covers a `k2-capture` container that is **up and scrapeable but not receiving** on one
or more of its streams, and the exchange-to-receive latency alert. For a container
that is down or failing to produce, see [capture-down.md](./capture-down.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase C chaos run fills this in.** The capture tier
> (ADR-019) is not built. Commands marked ✅ were verified against the running v2
> stack on 2026-08-26 with the service name substituted; the rest are written against
> the Phase C design.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | One stream silent while the process stays healthy | < 3 min | not yet verified — Phase C chaos run |
| 2 | Exchange-to-receive p99 elevated | investigate, no restart | not yet verified — Phase C chaos run |

---

## 1. Stream silent, process healthy

**Symptom** — the container is running, `/metrics` responds, `CaptureDown` is not
firing, and one stream has simply stopped. Often *one* stream: book snapshots stop
while trades keep flowing, or the reverse.

**Detection** — `CaptureFeedStale` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
time() - k2_capture_last_message_ts_seconds > 60
```

Fires after `for: 2m`. The metric is labelled `{exchange, stream}` with the venue's
channel name (`trade`, `book`, `depth20`, `l2_data`, `market_trades`, `heartbeat(s)`,
`instrument`), so the alert names which subscription went quiet.

Only continuous streams carry this gauge. One-shot acknowledgements — Kraken
`status`/`control`, Coinbase `subscriptions` — arrive once per (re)subscribe and
are deliberately not stamped (`CONTINUOUS` in `services/capture-rust/src/main.rs`).
The first 2 h window (2026-08-26 12:39Z) fired this alert on exactly those three
acks two minutes after a healthy connect; that was the alert's only false positive
and is the reason the allowlist exists. A firing on a name not in that list is a
new stream the allowlist does not know about, not a stale feed.

**Expected behaviour** — the WebSocket client sends and answers heartbeats and
reconnects on its own backoff, so a dropped connection self-heals inside the 2-minute
window and this alert does not fire. It fires for the case a liveness check cannot
see: the socket is open, the process is fine, and the **subscription** is dead — the
exchange accepted a `subscribe` and stopped delivering, or a silent half-open TCP
connection is holding the read side open with nothing arriving.

All three venues are liquid enough on the captured instruments that 60 seconds of
silence on a subscribed stream is anomalous rather than idle. Binance also performs a
scheduled reconnect at ~23 hours of connection life; that produces a brief, expected
`conn_id` change, not a 60-second silence.

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

# every stream stamping again — all three ages should drop under 60
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

**Record the gap.** Frames not received are not recoverable — public feeds do not
replay. The silent window is permanently absent from `raw.messages`; note it in the
day's completeness audit.

**Measured** — not yet verified. `scripts/chaos/capture-pause.sh` (Phase C)
`kill -STOP`s a capture container to stall the read side without closing the socket,
waits for `CaptureFeedStale`, then measures the time from `SIGCONT` until every
stream's `k2_capture_last_message_ts_seconds` is fresh again.

---

## 2. Exchange-to-receive p99 elevated

**Symptom** — no data missing, nothing down; the ingress latency panel steps up and
stays there.

**Detection** — `CaptureIngressLatencyHigh` from
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
(ADR-018) — this is a *something changed* signal, not a latency SLO, and the absolute
value should never be quoted as a platform latency figure. A step change is the
signal; the level is not.

Binance's partial-depth book stream carries no `exchange_ts` at all, so book frames
from that venue never enter this histogram (ADR-027). A Binance number here is trades
only.

**Expected behaviour** — nothing self-heals, because in most cases nothing is broken.
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

# 4. Is the capture container itself starved? cpuset-pinned at 0.25 CPU.
docker stats --no-stream k2-capture-binance k2-capture-kraken k2-capture-coinbase
```

- **Host clock** → fix NTP. Every historical `recv_ts_ns` taken while it was wrong is
  wrong, and that is worth an entry in the audit record because it is not visible in
  the data afterwards.
- **Transit / venue** → nothing to fix. Note it, and confirm completeness is
  unaffected (`k2_capture_gaps_total` flat, `CaptureFeedStale` not firing). Late data
  that all arrives is not a data-quality problem.
- **Capture starved** → if `docker stats` shows the container pegged at its CPU quota,
  raise the limit or move its `cpuset` further from ClickHouse and Spark, and update
  ADR-010's Outcome with the new budget as the project guardrails require.

**Do not restart the container for this alert.** A restart loses a window of data and
does not change transit time, a venue's clock, or this host's.

**Measured** — not yet verified. Phase C's noisy-neighbour experiment (Spark
compaction on its cpuset while capture ingress latency is sampled) produces the only
number that belongs here: how much of this metric the platform can move on its own.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** not yet verified — the capture tier is Phase C and unbuilt. Commands
marked ✅ were run against the v2 stack on 2026-08-26 with the service name
substituted. Stamp this line with a date and a commit at the Phase C chaos run.
