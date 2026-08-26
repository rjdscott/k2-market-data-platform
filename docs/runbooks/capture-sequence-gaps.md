# Runbook: Investigate a capture sequence gap, and a resync storm

Covers a break in exchange sequence continuity (messages lost between the venue and
this host), and repeated book resyncs. Sequencing differs per exchange — the policy
table is in [ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md) and reproduced
below because you need it to read the alert.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **No MTTR here is measured — the Phase C chaos run fills them in.** The capture
> tier (ADR-019) is built and running. Commands marked ✅ were run against it on
> 2026-08-26. What has not happened is a fault injection, so every **Measured** cell
> below reads "not yet".

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Sequence gap — messages lost | recovery automatic; **investigation < 30 min** | not yet verified — Phase C chaos run |
| 2 | Resync storm — the book keeps being rebuilt | < 15 min | not yet verified — Phase C chaos run |

---

## 1. Sequence gap

**Symptom** — usually none that is visible. Data keeps flowing, dashboards look
normal, and a counter has moved. That is the point of the counter: v2 had no gap
detection at all, so a dropped message was silent
([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) Context).

**Detection** — `CaptureSequenceGaps` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
increase(k2_capture_gaps_total[10m]) > 0
```

Fires after `for: 5m`.

**What "a gap" means per exchange** — three mechanisms, not one:

| Exchange | Continuity signal | Gap looks like | Automatic policy |
|----------|-------------------|----------------|------------------|
| **Binance** | `lastUpdateId` on `<sym>@depth20@100ms` | `lastUpdateId` regresses or repeats backwards | Reconnect; the next partial-depth frame is itself a complete top-20, so no snapshot fetch is needed |
| **Kraken v2** | none — the book stream carries no sequence number | n/a; drift is caught by CRC32 instead → [capture-checksum-failure.md](./capture-checksum-failure.md) | n/a |
| **Coinbase** | `sequence_num`, **connection-wide across all channels** | numeric skip in `sequence_num` | Reconnect and rebuild from a fresh `level2` snapshot — a connection-wide counter cannot be resynced per symbol |

Coinbase's counter spanning all channels rather than one per channel was established
by measurement, not documentation: spike S5 saw `sequence_num 0 → 676` across 677
frames of `l2_data`, `market_trades` and `heartbeats` together, 0 gaps
([ADR-018 Appendix A](../adr/ADR-018-v3-lake-first-rust-capture.md#s5--coinbase-level2-without-jwt)).
A gap on Coinbase therefore does not tell you which stream lost a message.

**Expected behaviour** — the reconnect and rebuild are automatic and complete in
seconds; by the time the alert fires the book is already correct again. **Nothing you
do restores the lost messages** — public feeds do not replay, so the frames are gone
from `raw.messages` permanently. This alert exists so the loss is recorded rather than
recovered.

That makes this an investigation, not a repair. Gaps = 0 is a Phase C exit criterion
(ADR-019), so a non-zero counter is either a real loss window that must be documented,
or a bug in the gap detector — and those need opposite responses.

**Recovery**

```bash
# 1. Which exchange, how many, when                                   ✅ verified (v2)
curl -s localhost:9090/api/v1/alerts | \
  jq '.data.alerts[] | select(.labels.alertname=="CaptureSequenceGaps")
      | {exchange: .labels.exchange, since: .activeAt}'

curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=increase(k2_capture_gaps_total[1h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'

# 2. Did a reconnect happen at the same moment? If gaps and reconnects moved
#    together, the gap is the reconnect boundary and is expected, not a loss.
#    The `reason` label separates the two kinds: "scheduled" is Binance's own
#    23 h connection recycle, "involuntary" is everything else.
curl -s --get localhost:9090/api/v1/query --data-urlencode \
 'query=increase(k2_capture_reconnects_total[1h])' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.metric.reason) \(.value[1])"'

# 3. What the process itself saw
docker logs k2-capture-coinbase --tail 300 | grep -i -E 'gap|sequence|reconnect|conn_id'
```

Then classify, because the response differs:

- **Gap coincides with a reconnect or a `conn_id` change** → expected. A new
  connection starts a new sequence space. If step 2 shows the increment under
  `reason="scheduled"`, this is Binance's own 23 h connection recycle
  (`BINANCE_MAX_CONNECTION_AGE` in `main.rs`, ahead of the venue's 24 h cut-off) and
  the correlation is exact rather than approximate; Kraken and Coinbase have no such
  timer and will only ever show `reason="involuntary"`. Confirm the timestamps line up
  and close it. If the gap
  detector is counting reconnect boundaries as gaps, that is a **capture bug** — the
  boundary is detectable via `conn_id` and should be excluded.
- **Gap with no reconnect** → a real loss on a live connection. Record the window.
- **Repeated gaps** → go to §2 below; something is causing them rather than one event
  having happened.

**Record the gap — this is the actual deliverable of this runbook.** Note the
exchange, the window, the count, and whether a reconnect explains it, in the day's
completeness audit. A query over that period must read a documented hole rather than
an unexplained one; that traceability is the whole reason the archive exists.

```bash
# Bound the affected window from the data: the conn_id in force at the time locates
# the exact frames in the raw topic that survived.
docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.coinbase \
  --num 5 --offset end --use-schema-registry=value                    # ✅ verified (v2 topic)
```

**Measured** — not yet verified. `scripts/chaos/capture-pause.sh` (Phase C)
`kill -STOP`s the Coinbase container long enough to force the exchange to advance
`sequence_num` past it, then measures the gap counter increment and the time to a
rebuilt book after `SIGCONT`. That is the induced case; it also tells us whether the
detector counts the reconnect boundary correctly.

---

## 2. Resync storm

**Symptom** — the book keeps being rebuilt. Individual recoveries all succeed, and
they keep happening.

**Detection** — `CaptureResyncStorm` from
[`docker/prometheus/rules/capture-alerts.yml`](../../docker/prometheus/rules/capture-alerts.yml):

```promql
increase(k2_capture_resyncs_total[15m]) > 3
```

Fires after `for: 5m`.

**Expected behaviour** — one resync is the policy working and is unremarkable. Four in
fifteen minutes is the policy being invoked repeatedly, which means resyncing is not
fixing the underlying cause. Each resync also leaves a `conn_id` boundary across which
`seq` continuity is meaningless, so a storm degrades book quality even though every
individual recovery succeeded.

**Recovery**

```bash
# 1. Which exchange, and is it correlated with gaps or checksum failures?  ✅ verified (v2)
for m in resyncs gaps checksum_failures reconnects; do
  echo "== $m"
  curl -s --get localhost:9090/api/v1/query \
    --data-urlencode "query=increase(k2_capture_${m}_total[1h])" | \
    jq -r '.data.result[] | "  \(.metric.exchange) \(.value[1])"'
done

# 2. The reason each resync was triggered
docker logs k2-capture-kraken --tail 500 | grep -i -E 'resync|resubscribe|checksum|gap'

# 3. Is the container being starved into dropping frames?
docker stats --no-stream k2-capture-kraken
```

Read the correlation:

| Also rising | Meaning | Go to |
|-------------|---------|-------|
| `checksum_failures_total` (Kraken) | The book is genuinely drifting; the checksum path may be at fault | [capture-checksum-failure.md](./capture-checksum-failure.md) |
| `gaps_total` (Coinbase / Binance) | Messages are being lost repeatedly — network or a starved container | §1 above, then the CPU check |
| `reconnects_total{reason="involuntary"}` only | The connection is unstable; the book rebuild is a consequence, not the problem | [capture-feed-stale.md](./capture-feed-stale.md), and check the venue status page |
| Nothing else | Resyncs are being triggered without a detected cause — a capture bug | Capture the log lines and open an issue |

**Do not raise the resync threshold to silence this.** The alert fires because book
quality is degraded; a higher threshold degrades it silently. If a venue genuinely
resyncs this often in normal operation, that is a fact worth recording in ADR-027's
Outcome, with the measurement behind it.

**Measured** — not yet verified. `scripts/chaos/capture-corrupt-frame.sh` (Phase C)
injects a corrupt book level to force a resync, then repeats it to confirm the storm
alert fires at the intended rate and that recovery time does not degrade across
successive resyncs.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** commands marked ✅ were run against the running capture tier on
2026-08-26; no MTTR on this page is measured, because nothing has been fault-injected. Commands
marked ✅ were run against the v2 stack on 2026-08-26 with the service name
substituted. Stamp this line with a date and a commit at the Phase C chaos run.
