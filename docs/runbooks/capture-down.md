# Runbook: Recover a capture container that is down or not producing

Covers a `k2-capture` container that Prometheus cannot scrape, and one that is
running but failing to produce to Redpanda. It does **not** cover a container that is
up and scrapeable but whose feed has gone quiet, that is
[capture-feed-stale.md](./capture-feed-stale.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **The MTTRs below are measured**, from the first `make chaos` run on 2026-08-26
> (16:40–16:57Z, binary `v3-phase-b-33-gf808d87`,
> [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv)).
> Commands marked ✅ were run against the live capture tier the same day. Numbers here
> are hand-copied from that run; a cell still reading "not yet verified" is one whose
> fault was not injected.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Capture container down or crash-looping | < 2 min | **3 s** to recover once restarted (2026-08-26, `capture-kill.sh --exchange kraken`); `CaptureDown` fired 119 s into a deliberate 150 s hold |
| 2 | Produce errors: records built, broker rejecting | < 5 min | **0 s** to resume producing after the broker returned, **14 s** to pass the mid-outage enqueue level (2026-08-26, `capture-queue-full.sh` / `redpanda-stop.sh`); `CaptureProduceErrors` fired 256 s in |

---

## 1. Capture container down

**Symptom**, no data from one exchange. Grafana's capture panels flatline for that
venue; `hot.trades` stops receiving rows for it while the other two carry on.

**Detection**, `CaptureDown` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
up{job=~"capture-.*"} == 0
```

Fires after `for: 2m`.

**Expected behaviour**, Docker restarts the container on failure, and the capture
process reconnects to the exchange on its own backoff, so a single crash self-heals
in well under the alert's 2-minute window and never fires. An alert that *does* fire
means the process is failing to start or crash-looping, the restart is not working,
so restarting it again is unlikely to be the fix on its own.

**What is lost:** everything the exchange sent during the outage. Public WebSocket
feeds do not replay, so that window is permanently absent from `raw.messages` and
from every table derived from it. This is a completeness gap, and it belongs in the
audit record, see step 5.

**Recovery**

```bash
# 1. Confirm which exchange, and how long                             ✅ verified (v2)
curl -s localhost:9090/api/v1/alerts | \
  jq '.data.alerts[] | select(.labels.alertname=="CaptureDown")
      | {exchange: .labels.exchange, state, since: .activeAt}'

# 2. Is the container running, and has it been restarting?
docker ps -a --filter name=k2-capture --format 'table {{.Names}}\t{{.Status}}'
docker inspect k2-capture-kraken -f 'restarts={{.RestartCount}} exit={{.State.ExitCode}}'

# 3. Why did it stop. The capture image is distroless with no shell, so the logs
#    are the only view inside it.
docker logs k2-capture-kraken --tail 100
```

Read the exit code before restarting anything:

| Exit | Meaning | Next step |
|------|---------|-----------|
| `0` | Clean SIGTERM shutdown, someone or something stopped it | `docker compose up -d capture-kraken` |
| `137` | OOM-killed | Go to [capture-checksum-failure.md §2](./capture-checksum-failure.md#2-book-depth-degraded) for the Coinbase book memory bound, then raise the limit in `docker-compose.yml` |
| `101` | Rust panic, the message is the last log line | Do not restart into a loop; the panic will repeat |
| `1` | Startup failure, usually config, registry or broker | Step 4 |

```bash
# 4. Startup dependencies, in the order the process needs them
docker exec k2-redpanda rpk cluster health                            # ✅ verified (v2)
curl -s localhost:8081/subjects | jq -r '.[] | select(startswith("market.crypto.v3"))'
#    9 subjects expected; an Avro encode cannot proceed without a schema id (ADR-020)
docker exec k2-redpanda rpk topic list | grep 'market.crypto.v3'      # ✅ verified (v2)

# 5. Bring it back
docker compose up -d capture-kraken
docker exec k2-capture-kraken /k2-capture healthcheck                 # exit 0 when serving

# 6. Confirm records are flowing again
docker run --rm --network k2-market-data-platform_k2-net \
  curlimages/curl:8.11.1 -s http://capture-kraken:8082/metrics | \
  grep -E '^k2_capture_(records_produced|messages)_total'             # ✅ verified pattern (v2)

docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.kraken \
  --num 1 --offset end --use-schema-registry=value                    # ✅ verified (v2 topic)
```

> The metrics endpoint has no host port mapping and the image has no `curl`, so
> `curl localhost:8082/metrics` does **not** work here. The one-shot curl container on
> the compose network above is the working equivalent; Prometheus is the other
> (`curl -s 'localhost:9090/api/v1/query?query=k2_capture_records_produced_total'`).

**Record the gap.** Before closing: note the outage window and append it to the
completeness audit for the day, so a later query over that period reads a documented
hole rather than an unexplained one.

**Measured 2026-08-26**, `scripts/chaos/capture-kill.sh --exchange kraken --hold 150`
SIGKILLed the container, held it down, and measured recovery as the time until it was
scrapeable again *and* a fresh frame had arrived (`up == 1` alone would pass on a process
that came back and never reconnected to the venue):

| | |
|---|---|
| time to `CaptureDown` firing | **119 s**, inside a deliberate 150 s hold, so this is the alert's own latency (`for: 2m` + one scrape), not a slow restart |
| scrapeable after `docker compose up -d` | **3 s** |
| fresh frames after that | **0 s**, the venue reconnect landed inside the same scrape interval |
| `docker restart` count | 0 → 0, i.e. nothing crash-looped |

**Time-to-recover: 3 s.** The number to remember is the asymmetry: detection takes ~2 min
and recovery takes seconds, so almost all of the MTTR target is spent noticing. A
container that is genuinely crash-looping shows the opposite shape, `CaptureDown` firing
with the restart count climbing, and that is the case §1's exit-code step is for.
Source: [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

---

## 2. Produce errors: records built, broker rejecting

**Symptom**, the capture container is up and enqueueing records
(`k2_capture_records_produced_total` climbing) but `k2_capture_records_delivered_total`
is flat or lagging it. Note which counter is which: `records_produced_total` counts the
*local enqueue* into librdkafka's queue and keeps climbing through a broker outage;
`records_delivered_total` is incremented from the delivery report and is the one that
stops. `CaptureProduceStalled` fires on that divergence *before* anything is dropped , 
see [capture-produce-stalled.md](./capture-produce-stalled.md).

**Detection**, `CaptureProduceErrors` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
increase(k2_capture_produce_errors_total[10m]) > 0
```

Fires after `for: 5m`. The counter is seeded at zero for all four `reason` values at
startup, so the **first** produce error trips this, there is no rate floor, because a
rate floor of `0.1/s` tolerates 8,640 permanently-lost records a day and this tier has
no spill-to-disk.

**Expected behaviour**, librdkafka retries internally and rides out a brief broker
hiccup without the counter moving far. **It does not spill to disk.** Its queue is the
only buffer in this tier (`queue.buffering.max.kbytes=32768`), and it drops on full
with a counter (ADR-019). So a sustained produce-error rate is permanent data loss
rather than backpressure, and the clock is running.

**Recovery**

```bash
# 1. Broker first: this is the common cause
docker exec k2-redpanda rpk cluster health                            # ✅ verified (v2)
docker exec k2-redpanda rpk group list                                # ✅ verified (v2)
df -h /var/lib/docker                                                 # a full disk stops writes

# 2. Schema registry second. An Avro record cannot be encoded without a schema id,
#    so registry unavailability presents as a produce failure (ADR-020).
curl -s localhost:8081/subjects | jq 'length'                         # expect 9 v3 + v2 subjects
curl -s localhost:8081/config | jq -r .compatibilityLevel             # expect BACKWARD_TRANSITIVE

# 3. Does the topic still exist with the partition count the producer expects?
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.kraken  # ✅ verified (v2)

# 4. The error text itself
docker logs k2-capture-kraken --tail 200 | grep -i -E 'produce|rdkafka|registry'
```

Then, by cause:

- **Broker unhealthy or partitions leaderless** → [redpanda.md](./redpanda.md), then
  re-check; the capture process reconnects on its own once the broker recovers.
- **Registry unreachable** → `docker compose restart redpanda`; the registry is
  in-process. Re-run the subject check before declaring it fixed.
- **Disk full** → free space before restarting anything. Restarting a producer into a
  full disk drops the next window too.
- **Nothing wrong upstream** → the fault is in the sink; capture the log line and
  open an issue rather than restart-looping.

**Measured 2026-08-26**, two runs, both against kraken, one pausing the broker and one
stopping it:

| | |
|---|---|
| time to `CaptureProduceErrors` firing | **256 s** from the fault (`capture-queue-full.sh`, broker paused), ~154 s after the first record was actually dropped |
| producing again after `docker unpause` | **0 s** |
| past the mid-outage enqueue level after `docker start` | **14 s** (`redpanda-stop.sh`), with **no capture restart** |
| records lost, 388 s paused | **231,744** on kraken alone |
| records lost, 45 s stopped | **7,821** on kraken alone |

**The second number is the one that matters, and it is worse than the design said.** A
45 s broker outage should have lost nothing, the 32 MiB queue buys 204 s at kraken's
rate, but `message.timeout.ms` was 30 s, so records expired on the clock instead. That
is fixed (`message.timeout.ms=300000`,
[ADR-019 Outcome](../adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable));
the loss figures above are from *before* the fix and are the reason this runbook exists.
Re-run to score the fix. Source:
[`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** 2026-08-26 (`make chaos`). Both MTTRs on this page are measured, from
[`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv);
commands marked ✅ were run against the running capture tier the same day. Re-stamp when
the queue-full script is re-run against `message.timeout.ms=300000`.
