# Runbook: Recover a capture container that is down or not producing

Covers a `k2-capture` container that Prometheus cannot scrape, and one that is
running but failing to produce to Redpanda. It does **not** cover a container that is
up and scrapeable but whose feed has gone quiet — that is
[capture-feed-stale.md](./capture-feed-stale.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase C chaos run fills this in.** The capture tier
> (ADR-019) is not built. Commands below are written against the Phase C design;
> those marked ✅ were verified against the running v2 stack on 2026-08-26 with the
> service name substituted. `make chaos` (`scripts/chaos/*.sh`) induces each failure,
> waits for the alert, and measures recovery — the **Measured** rows are filled from
> that run, not from an estimate.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Capture container down or crash-looping | < 2 min | not yet verified — Phase C chaos run |
| 2 | Produce errors: records built, broker rejecting | < 5 min | not yet verified — Phase C chaos run |

---

## 1. Capture container down

**Symptom** — no data from one exchange. Grafana's capture panels flatline for that
venue; `hot.trades` stops receiving rows for it while the other two carry on.

**Detection** — `CaptureDown` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
up{job=~"capture-.*"} == 0
```

Fires after `for: 2m`.

**Expected behaviour** — Docker restarts the container on failure, and the capture
process reconnects to the exchange on its own backoff, so a single crash self-heals
in well under the alert's 2-minute window and never fires. An alert that *does* fire
means the process is failing to start or crash-looping — the restart is not working,
so restarting it again is unlikely to be the fix on its own.

**What is lost:** everything the exchange sent during the outage. Public WebSocket
feeds do not replay, so that window is permanently absent from `raw.messages` and
from every table derived from it. This is a completeness gap, and it belongs in the
audit record — see step 5.

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
| `0` | Clean SIGTERM shutdown — someone or something stopped it | `docker compose up -d capture-kraken` |
| `137` | OOM-killed | Go to [capture-checksum-failure.md §2](./capture-checksum-failure.md#2-book-depth-degraded) for the Coinbase book memory bound, then raise the limit in `docker-compose.yml` |
| `101` | Rust panic — the message is the last log line | Do not restart into a loop; the panic will repeat |
| `1` | Startup failure — usually config, registry or broker | Step 4 |

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

**Measured** — not yet verified. `scripts/chaos/capture-kill.sh` (Phase C) induces
this by `docker kill`-ing one capture container, waits for `CaptureDown`, and
measures recovery as the time until `k2_capture_records_produced_total` increments
again. That number, and its date, replace this line.

---

## 2. Produce errors — records built, broker rejecting

**Symptom** — the capture container is up and reading frames (`k2_capture_messages_total`
climbing) but `k2_capture_records_produced_total` is flat or lagging it.

**Detection** — `CaptureProduceErrors` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
rate(k2_capture_produce_errors_total[5m]) > 0.1
```

Fires after `for: 3m`.

**Expected behaviour** — librdkafka retries internally and rides out a brief broker
hiccup without the counter moving far. **It does not spill to disk.** Its queue is the
only buffer in this tier (`queue.buffering.max.kbytes=32768`), and it drops on full
with a counter (ADR-019). So a sustained produce-error rate is permanent data loss
rather than backpressure, and the clock is running.

**Recovery**

```bash
# 1. Broker first — this is the common cause
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

**Measured** — not yet verified. `scripts/chaos/redpanda-stop.sh` (Phase C) stops the
broker mid-ingest, waits for `CaptureProduceErrors`, restarts it, and measures both
recovery time and the records dropped in between — the second number is the one that
matters here, and it is the reason this runbook exists.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** not yet verified — the capture tier is Phase C and unbuilt. Commands
marked ✅ were run against the v2 stack on 2026-08-26 with the service name
substituted. Stamp this line with a date and a commit at the Phase C chaos run.
