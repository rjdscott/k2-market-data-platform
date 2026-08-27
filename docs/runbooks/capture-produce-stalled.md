# Runbook: Recover a capture produce path stalled before the queue drops data

Covers `CaptureProduceStalled` — capture is still reading the exchange but nothing is
reaching the broker, so librdkafka's queue is filling with no counter moving yet. It
does **not** cover the queue actually dropping records — that is
[capture-down.md §2](./capture-down.md#2-produce-errors--records-built-broker-rejecting),
which `CaptureProduceErrors` fires for once the queue is full.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Measured 2026-08-26** by `scripts/chaos/capture-queue-full.sh --exchange kraken`
> ([`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv)),
> and the run found the boundary this runbook is built around in the wrong place — see
> §1's Measured block before trusting the predicted numbers below.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Produce stalled — broker down, queue filling, nothing dropped yet | < 2 min | **0 s** to resume producing once the broker returned (2026-08-26). But the stalled-but-not-dropping window was **102 s, not the predicted 204 s** — half the budget this runbook assumes |

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
([capacity-model.md §4](../architecture/15-capacity-model.md)):

| Exchange | Time to first drop |
|---|---|
| binance | **194 s** |
| kraken | **204 s** |
| coinbase | **446 s** |

Binance is the tightest window. Inside it, nothing is lost yet; past it, the loss is
permanent and unrecoverable — public WebSocket feeds do not replay
([failure-modes.md](../architecture/16-failure-modes.md), Redpanda broker-down row).

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

**Measured MTTR 2026-08-26** — `scripts/chaos/capture-queue-full.sh --exchange kraken`
paused the broker for 388 s:

| | |
|---|---|
| stalled-but-not-dropping window | **102 s** — against a predicted 204 s, **50 % short** |
| `CaptureProduceErrors` fired | 256 s after the fault |
| producing again after `docker unpause` | **0 s** |
| records lost over the window | 231,744, **none of them `reason="queue_full"`** |

**The clock this runbook gives you was half what it claimed, and for a reason worth
knowing.** The 204 s comes from 32 MiB ÷ kraken's wire rate, and that arithmetic was
right — but `message.timeout.ms` was 30 s, so records were failed on a timer long before
the queue was anywhere near full, and every drop was counted `reason="delivery"`. The
queue's slack was unreachable. Fixed the same day
(`message.timeout.ms=300000`,
[ADR-019 Outcome](../adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)),
which should restore the full 204 s — **but that is a prediction again, not a
measurement, until the script is re-run.** Until then, treat the clock above as
unverified and check `reason` on the first drop: `queue_full` means the buffer did its
job, `delivery` means it did not.
Source: [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** 2026-08-26 (`make chaos`). The MTTR is measured; the 204 s stall
budget is not, and is under test after the `message.timeout.ms` fix. Re-stamp on the
re-run.
