# Runbook: Recover a capture produce path stalled before the queue drops data

Covers `CaptureProduceStalled` — capture is still reading the exchange but nothing is
reaching the broker, so librdkafka's queue is filling with no counter moving yet. It
does **not** cover the queue actually dropping records — that is
[capture-down.md §2](./capture-down.md#2-produce-errors--records-built-broker-rejecting),
which `CaptureProduceErrors` fires for once the queue is full.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Recovery times not yet measured — the Phase C chaos run fills them in.** The
> capture tier (ADR-019) is built and running, and the commands marked ✅ were run
> against it on 2026-08-26. What has not happened is a fault injection:
> `scripts/chaos/capture-queue-full.sh` fills the queue and measures the boundary
> between "stalled" and "dropping" against the predicted numbers below, and until it
> runs every MTTR on this page reads "not yet".

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Produce stalled — broker down, queue filling, nothing dropped yet | < 2 min | not yet — `scripts/chaos/capture-queue-full.sh` fills this |

---

## 1. Produce stalled

**Symptom** — no immediate alarm elsewhere: `CaptureDown` is not firing (the metrics
endpoint is healthy) and `CaptureProduceErrors` is not firing yet (nothing has been
dropped). Grafana's capture panels still show `k2_capture_records_produced_total` climbing for
the affected exchange, but `k2_capture_records_delivered_total` for the same exchange is
flat.

**Read those two counters carefully — they are not interchangeable.**
`records_produced_total` counts the *local enqueue*: `sink.rs` increments it the moment
librdkafka accepts the record into its 32 MiB queue, which it keeps doing at full rate
for the entire outage. `records_delivered_total` is incremented from the delivery
report, so it is the one that goes flat when the broker is gone. An earlier version of
this alert watched `records_produced_total` and could not fire in the scenario it was
written for.

**Detection** — `CaptureProduceStalled` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
sum by (exchange) (rate(k2_capture_records_delivered_total[1m])) == 0
and
sum by (exchange) (rate(k2_capture_records_produced_total[1m])) > 0
```

Fires after `for: 30s` — deliberately tighter than `CaptureProduceErrors`'s `5m`,
because this alert exists to buy time before the queue fills, not to confirm loss
after the fact. The expression is pinned by a `promtool` unit test
([`docker/prometheus/tests/capture-alerts.test.yml`](../../docker/prometheus/tests/capture-alerts.test.yml),
`make check-alerts`) with a case that enqueues at full rate while delivering nothing.

**Expected behaviour** — none of this self-heals from the capture side. librdkafka
keeps retrying and enqueueing in the background; the frame-read loop is deliberately
never blocked by a stalled sink ([`services/capture-rust/README.md`](../../services/capture-rust/README.md)),
so nothing here recovers on its own until whatever is downstream comes back. Treat this
alert as a clock, not a transient.

**Diagnosis**

```bash
# 1. Is the broker up?
docker exec k2-redpanda rpk cluster health                              # ✅ verified (v2)

# 2. Is the schema registry reachable? The capture image has no shell/curl, so use
#    the one-shot curl sidecar on the compose network, same pattern as capture-down.md.
docker run --rm --network k2-market-data-platform_k2-net \
  curlimages/curl:8.11.1 -s http://redpanda:8081/subjects | \
  jq -r '.[] | select(startswith("market.crypto.v3"))'                  # ✅ verified pattern (v2)

# 3. What is the capture process itself saying?
docker logs k2-capture-kraken --tail 100
```

## The clock

There is no spill-to-disk in this tier — the librdkafka queue
(`queue.buffering.max.kbytes=32768`, 32 MiB, ADR-019) is the only buffer, and it drops
on full with a counter. From the moment production stopped, time to the first
permanent drop, at the predicted per-container wire rate
([capacity-model.md §4](../architecture/capacity-model.md)):

| Exchange | Time to first drop |
|---|---|
| binance | **194 s** |
| kraken | **204 s** |
| coinbase | **446 s** |

Binance is the tightest window. Inside it, nothing is lost yet; past it, the loss is
permanent and unrecoverable — public WebSocket feeds do not replay
([failure-modes.md](../architecture/failure-modes.md), Redpanda broker-down row).

**Recovery**

```bash
# Broker down is the common cause — bring it back
docker compose up -d redpanda                                           # ✅ verified pattern (v2)
docker exec k2-redpanda rpk cluster health                              # confirm healthy

# Capture needs no restart: the queue drains and production resumes on its own
# once produce succeeds again.
docker run --rm --network k2-market-data-platform_k2-net \
  curlimages/curl:8.11.1 -s http://capture-kraken:8082/metrics | \
  grep -E '^k2_capture_records_(produced|delivered)_total'              # ✅ rates converge again

# Verify nothing actually dropped while it was stalled
docker run --rm --network k2-market-data-platform_k2-net \
  curlimages/curl:8.11.1 -s http://capture-kraken:8082/metrics | \
  grep 'k2_capture_produce_errors_total{.*reason="queue_full"'
```

If `produce_errors_total{reason="queue_full"}` did not increment, the recovery landed
inside the clock above and nothing was lost — close it out. If it did increment, this
became a [capture-down.md §2](./capture-down.md#2-produce-errors--records-built-broker-rejecting)
incident partway through; record the loss window in the completeness audit before
closing.

**Measured MTTR: not yet — `scripts/chaos/capture-queue-full.sh` fills this.**

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** commands marked ✅ were run against the running capture tier on
2026-08-26. No MTTR on this page is measured — nothing has been fault-injected. Stamp
this line with a date and a commit at the Phase C chaos run.
