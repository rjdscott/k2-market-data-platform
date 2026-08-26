# `scripts/chaos/` — fault injection for the capture tier

Five scripts: four break the running stack on purpose, wait for the alert that is
supposed to notice, measure how long recovery took, and put the stack back; the
fifth records an honest gap it cannot yet inject. They
are the `proof` column of
[`docs/architecture/failure-modes.md`](../../docs/architecture/failure-modes.md):
an FMEA whose recovery times are estimates is a wish list, and these are what turn
that column into measurements.

**Maintainer-run, never CI.** The GitHub-hosted runner has ~7 GB of RAM and this
stack budgets ~22 GB, so a nightly chaos job could not host ClickHouse, Redpanda,
Spark, MinIO and three capture containers at once — it would fail for resource
reasons more often than for real ones, and a flaky nightly everyone learns to
ignore is worse than a gate someone actually runs
([requirements clarification Q3](../../docs/research/2026-08-26-v3-requirements-clarification.md#q3--fault-injection-where-does-it-run)).
What this buys is stated plainly rather than dressed up: the guarantee is *"these
failures were injected and measured on the dates in `results/`"*, not *"these
failures are injected nightly"*.

---

## Status

**First run: 2026-08-26, 16:40–16:57Z**, binary `v3-phase-b-33-gf808d87` —
[`results/2026-08-26.tsv`](results/2026-08-26.tsv). Five faults injected, one SKIP.
`CaptureDown` and `CaptureProduceErrors` both fired on the faults they name; every
recovery number now published in
[`docs/architecture/failure-modes.md`](../../docs/architecture/failure-modes.md) and in
the `docs/runbooks/capture-*.md` MTTR tables comes from that file.

Runs are still scheduled deliberately rather than taken opportunistically: each script
drops real market data from whichever venue it targets, and the two broker scripts drop
it for the whole stack. Never during a labelled burn-in or parity window, whose evidence
it would destroy.

### What the first run found

| Finding | Detail |
|---|---|
| **The 32 MiB producer queue was unreachable** | `capture-queue-full.sh --exchange kraken` predicted the first drop at 204 s and measured **102 s, −50 %**. Of 231,744 records lost across the 388 s fault window, **zero** carried `reason="queue_full"` — `message.timeout.ms` was 30 s, so every record expired on a timer while the buffer sat half empty, counted `delivery`. Fixed the same day: `message.timeout.ms=300000` ([ADR-019 Outcome](../../docs/adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)). **The 204 s prediction is now under test again — re-run to score it.** |
| **A pause does not manufacture a sequence gap** | Both `capture-pause.sh` runs gave `gaps_total` 0 → 0 and `reconnects_total` 0 → 1. The reconnect starts a fresh sequence series, so there is no discontinuity to detect. Gap *detection* remains unproven and waits on `k2-replay` (Phase G). |
| **Detection is slow, recovery is fast** | `CaptureDown` 119–165 s, `CaptureProduceErrors` 256 s; recovery 0–14 s across every script. Almost all of every MTTR is spent noticing. |
| **Back-to-back runs cost a measurement** | `redpanda-stop.sh` ran one minute after `capture-queue-full.sh`, with `CaptureProduceErrors` still firing, so its `t_fire` reads 0 rather than an independent number. Space runs by the alert's `for:` window. |
| **The broker survives a six-minute pause** | `rpk cluster health` clean after both, single-node Raft, no manual intervention. |

Still unrun: `redpanda-stop.sh --cold-start` (the 2026-08-26 run took the default warm
path) and `capture-corrupt-frame.sh` (SKIP by design until `k2-replay`).

---

## The scripts

| Script | Fault | Expects | FMEA rows it proves |
|---|---|---|---|
| `capture-kill.sh` | `docker kill --signal=KILL` one capture container, held down past the alert window | `CaptureDown` | capture / SIGKILL |
| `capture-pause.sh` | `docker pause` one capture container until its scrape target reads down | `CaptureDown` (a paused target is stale-marked, so `CaptureFeedStale` cannot fire on it) | capture / SIGSTOP; coinbase `sequence_num` gap; binance `lastUpdateId` regression; the *signal* of venue-side maintenance |
| `capture-queue-full.sh` | `docker pause` the broker so librdkafka's 32 MiB queue fills and capture starts dropping | `CaptureProduceErrors` — `reason="queue_full"` at binance/kraken rates, `reason="delivery"` at coinbase's, where `message.timeout.ms` binds first. The script prints which it expects and both counters | capture → Redpanda / producer queue full |
| `redpanda-stop.sh` | `docker stop` the broker; `--cold-start` also recreates a capture container while it is down | `CaptureProduceErrors`, or `CaptureDown` under `--cold-start` (warm-up is fatal, so the container crash-loops rather than failing produces) | Redpanda / broker down; schema registry / down mid-run; schema registry / down at start |
| `capture-corrupt-frame.sh` | none — prints SKIP and exits 0 | — | corrupt frame (**not automatable until `k2-replay`, Phase G**) |

All five take `--exchange binance|kraken|coinbase`, defaulting to `kraken`.
`capture-kill.sh` also takes `--hold <seconds>` (default 150) and
`redpanda-stop.sh` takes `--cold-start`.

Four design notes worth knowing before reading them:

- **`capture-queue-full.sh` pauses the broker; `redpanda-stop.sh` stops it.** A
  paused broker leaves every TCP connection open and stops answering, so
  librdkafka keeps enqueueing — the purest queue-full injection. A stopped broker
  refuses the connection and librdkafka fails differently. Same neighbourhood,
  different failure, two scripts.
- **Those two scripts take down the whole stack, not just capture.** Redpanda is
  the single broker *and* the single schema registry, so the three Kotlin feed
  handlers, ClickHouse's Kafka-engine consumers, Console and Prefect all lose it
  too — capture is only the tier being measured. `capture-queue-full.sh
  --exchange coinbase` is the longest: 300 s to the predicted first loss plus the
  alert's `for: 5m` and the wait puts the broker under `docker pause` for up to
  ~28 minutes, and `redpanda-stop.sh` stops it for up to ~15. A pause beyond a
  few minutes is itself a risk on single-node Raft, so both end by printing
  `rpk cluster health` rather than assuming a clean return. Measured 2026-08-26: a
  388 s pause left `rpk cluster health` clean.
- **Two caps decide when `capture-queue-full.sh` sees its first loss**, and the
  script predicts which one binds. 32 MiB of queue is 194 / 204 / 446 s of slack
  at the modelled wire rates; `message.timeout.ms=300000` in `sink.rs` is a flat
  300 s. Smaller wins. Getting the other one back is a finding about the capacity
  model or about `sink.rs` having drifted from `MESSAGE_TIMEOUT` at the top of the
  script — which is exactly how the 2026-08-26 run caught a 30 s timeout making
  the 32 MiB unreachable.
- **`capture-corrupt-frame.sh` is a SKIP, not a stub.** It exists so `make chaos`
  reports the gap on every run rather than quietly not covering it. The reasons
  are in its header: TLS leaves no seam to flip a byte in a live frame, and
  pushing chosen bytes through the running binary is exactly what `k2-replay`
  (Phase G) is for. A unit test covers the adapter in the meantime and the script
  says so.

`lib.sh` holds everything that observes rather than breaks — `prom_query`,
`alert_state`, `wait_for_alert`, `wait_for_alert_clear`, `wait_for_metric`,
`stamp`, `report`, and the preflight. Keeping it out of the fault scripts is what
makes "measure" identical across them, and therefore comparable.

Every wait is **scoped to one venue**. `alert_state` takes the exchange and matches
it against either label an alert can carry it in — the sample's own `exchange`, or
the scrape `job` (`capture-<exchange>`) for `up`-based alerts like `CaptureDown`.
Without that scope `capture-kill.sh --exchange binance` would return "firing" off an
unrelated kraken alert and every number after the wait would belong to the wrong
venue.

---

## Prerequisites

- The stack is up (`make up`) and **healthy** — `docker compose ps` shows no
  restarting service. Injecting a fault into a stack that is already broken
  measures nothing.
- The capture container for the exchange under test is running; a script that
  cannot find it stops at its preflight with a clear message.
- `jq` and `docker` on the host. Prometheus reachable on `localhost:9090`
  (override with `K2_CHAOS_PROM`). Prometheus is the only reader of capture
  `/metrics` — the image is distroless and `:8082` is not published — so the
  scripts measure exactly the numbers the alerts measure.
- **Nothing you care about is running.** These scripts drop real market data, and
  public WebSocket feeds do not replay it. Every window a script breaks is
  permanently absent from `raw.messages`. That includes any burn-in or parity
  window in flight: a chaos run during one invalidates its evidence.

Run them one at a time, from anywhere:

```bash
scripts/chaos/capture-pause.sh --exchange coinbase
```

Every script that injects a fault restores the stack on exit, including on
`Ctrl-C`, via a `trap` armed before the fault and cleared after the restore — a
chaos script that can leave the stack broken is a fault of its own.
`capture-corrupt-frame.sh` has no trap because it injects nothing: it prints its
SKIP banner, appends a `skip` row and exits.

> **Never `kill -9` a running chaos script.** The trap is armed *after* the fault is
> injected and cleared *after* the restore, so a SIGKILL of the script — unlike `Ctrl-C`,
> which the trap handles — skips the restore entirely and leaves whatever it broke
> broken. This happened at 16:39Z on 2026-08-26: a killed run left
> `k2-capture-kraken Exited (137)` with nothing to bring it back, and the real run had to
> start from a repaired stack. If you must stop a run, `Ctrl-C` it and wait for the
> restore line; if you already killed one, check `docker compose ps` for a stopped
> container and `docker unpause k2-redpanda` before doing anything else.

---

## Results, and how they reach the FMEA

Each run appends one line to `scripts/chaos/results/<UTC-date>.tsv`:

```
ts	script	expected_alert	t_fire_s	t_recover_s
```

`ts` is when the row was written — the end of the run, not the injection; the FMEA
quotes injection times, so the same run appears there a few minutes earlier.
`t_fire_s` is seconds from injection to the alert entering `firing`; it reads
`none` when the alert legitimately did not fire (a short `capture-kill.sh --hold`
self-heals inside the 2-minute window, which is the documented expected behaviour,
not a failure) and `skip` for the corrupt-frame placeholder. `t_recover_s` is
seconds from restoring the stack to the metric that defines "recovered" — a fresh
`k2_capture_last_message_ts_seconds`, or `k2_capture_records_produced_total`
climbing past its **mid-outage** level.

Both cells can also read `unmeasured`, and that is a real outcome rather than a
missing one: it means a wait timed out, so nothing was observed. A timeout is never
folded into a duration and published as one. `t_fresh + <the timeout>` is a constant
wearing a measurement's clothes, and these cells are hand-copied into the FMEA and
the runbook MTTR tables.

Note what `k2_capture_records_produced_total` counts: local **enqueue**, not
delivery (`sink.rs`). A recovery time measured on it says the producer resumed
accepting records, which is why `redpanda-stop.sh` baselines it mid-outage — against
the pre-fault sample the comparison is already satisfied before the broker is even
restarted, and the answer is always ~0.

**Results are committed.** They are the evidence behind every recovery number this
repo publishes, and an uncommitted measurement is one nobody can check. They are
not written into `docs/` — that surface holds the conclusion, not the raw log.

**The FMEA is updated by hand, with the date.** Copy the measured recovery time
into the matching row of
[`docs/architecture/failure-modes.md`](../../docs/architecture/failure-modes.md),
alongside the run date, in the same PR as the results file. It is deliberately not
automated: a number that appears in a published document without someone reading
the run it came from is exactly how v2 ended up with benchmark figures nobody could
trace back to a command. The same hand-copy discipline fills the **Measured**
column of the `docs/runbooks/capture-*.md` MTTR tables and the *Measured MTTR* section of
[`docs/runbooks/redpanda.md`](../../docs/runbooks/redpanda.md). Cells whose fault was not
injected keep reading "not yet verified" with the concrete trigger that will fill them —
a measured page and an honest page are the same page.

The gate on the result is in
[`003-phase-d-lake-tier.md`](../../docs/plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md#verification):
every script sees its expected alert, the stack returns to green within the
script's bound, and the FMEA has no empty cell.

---

## Adding a script

1. Write the FMEA row first. If you cannot name the detection signal and what is
   lost versus delayed, the script has nothing to assert.
2. Source `lib.sh`; call `preflight` with every container you touch; call `banner`
   with the alert and the runbook so a reader of the terminal output knows what is
   supposed to happen before it does.
3. `trap` the restore — armed *before* the fault, cleared *after* the restore, so
   there is no window in which an interrupt leaves the stack broken. Scope every
   wait to the venue under test by passing the exchange.
4. End with `report`, and add the script to the table above and to the `chaos`
   target in the `Makefile`.
5. If the fault cannot be injected honestly, say so in a SKIP script rather than
   approximating it. An injection that proves something other than the failure is
   worse than an admitted gap.
