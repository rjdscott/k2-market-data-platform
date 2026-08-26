# `scripts/chaos/` — fault injection for the capture tier

Five scripts that break the running stack on purpose, wait for the alert that is
supposed to notice, measure how long recovery took, and put the stack back. They
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

**None of these has been run.** The capture image is not built and no `k2-capture-*`
container exists on this host, so every script would fail its preflight today. They
are committed unrun on purpose — the alert rules, the runbooks and the FMEA all
already claim behaviour that only these scripts can check, and shipping the checker
alongside the claim is the point.

---

## The scripts

| Script | Fault | Expects | FMEA rows it proves |
|---|---|---|---|
| `capture-kill.sh` | `docker kill --signal=KILL` one capture container, held down past the alert window | `CaptureDown` | capture / SIGKILL |
| `capture-pause.sh` | `docker pause` one capture container until its feed reads stale | `CaptureFeedStale` | capture / SIGSTOP; coinbase `sequence_num` gap; binance `lastUpdateId` regression; the *signal* of venue-side maintenance |
| `capture-queue-full.sh` | `docker pause` the broker so librdkafka's 32 MiB queue fills and capture starts dropping | `CaptureProduceErrors` (`reason="queue_full"`) | capture → Redpanda / producer queue full |
| `redpanda-stop.sh` | `docker stop` the broker; `--cold-start` also recreates a capture container while it is down | `CaptureProduceErrors` | Redpanda / broker down; schema registry / down mid-run; schema registry / down at start |
| `capture-corrupt-frame.sh` | none — prints SKIP and exits 0 | — | corrupt frame (**not automatable until `k2-replay`, Phase G**) |

All five take `--exchange binance|kraken|coinbase`, defaulting to `kraken`.
`capture-kill.sh` also takes `--hold <seconds>` (default 150) and
`redpanda-stop.sh` takes `--cold-start`.

Two design notes worth knowing before reading them:

- **`capture-queue-full.sh` pauses the broker; `redpanda-stop.sh` stops it.** A
  paused broker leaves every TCP connection open and stops answering, so
  librdkafka keeps enqueueing — the purest queue-full injection. A stopped broker
  refuses the connection and librdkafka fails differently. Same neighbourhood,
  different failure, two scripts.
- **`capture-corrupt-frame.sh` is a SKIP, not a stub.** It exists so `make chaos`
  reports the gap on every run rather than quietly not covering it. The reasons
  are in its header: TLS leaves no seam to flip a byte in a live frame, and
  pushing chosen bytes through the running binary is exactly what `k2-replay`
  (Phase G) is for. A unit test covers the adapter in the meantime and the script
  says so.

`lib.sh` holds everything that observes rather than breaks — `prom_query`,
`wait_for_alert`, `wait_for_alert_clear`, `wait_for_metric`, `metrics`, `stamp`,
`report`, and the preflight. Keeping it out of the fault scripts is what makes
"measure" identical across them, and therefore comparable.

---

## Prerequisites

- The stack is up (`make up`) and **healthy** — `docker compose ps` shows no
  restarting service. Injecting a fault into a stack that is already broken
  measures nothing.
- The capture containers for the exchange under test are running. They are built
  in Phase C; until then every script stops at its preflight with a clear message.
- `jq` and `docker` on the host. Prometheus reachable on `localhost:9090`
  (override with `K2_CHAOS_PROM`).
- Capture `/metrics` is read through a `curlimages/curl` sidecar on the compose
  network, because the capture image is distroless and `:8082` is not published.
  Override the network with `K2_CHAOS_NETWORK` if the compose project name differs
  from `k2-market-data-platform`.
- **Nothing you care about is running.** These scripts drop real market data, and
  public WebSocket feeds do not replay it. Every window a script breaks is
  permanently absent from `raw.messages`.

Run them one at a time, from anywhere:

```bash
scripts/chaos/capture-pause.sh --exchange coinbase
```

Each restores the stack on exit, including on `Ctrl-C` — a chaos script that can
leave the stack broken is a fault of its own.

---

## Results, and how they reach the FMEA

Each run appends one line to `scripts/chaos/results/<UTC-date>.tsv`:

```
ts	script	expected_alert	t_fire_s	t_recover_s
```

`t_fire_s` is seconds from injection to the alert entering `firing`; it reads
`none` when the alert legitimately did not fire (a short `capture-kill.sh --hold`
self-heals inside the 2-minute window, which is the documented expected behaviour,
not a failure) and `skip` for the corrupt-frame placeholder. `t_recover_s` is
seconds from restoring the stack to the metric that defines "recovered" — a fresh
`k2_capture_last_message_ts_seconds`, or `k2_capture_records_produced_total`
climbing again.

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
column of the four `docs/runbooks/capture-*.md` MTTR tables, which today all read
"not yet verified — Phase C chaos run".

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
3. `trap` the restore, so an interrupted run leaves the stack up.
4. End with `report`, and add the script to the table above and to the `chaos`
   target in the `Makefile`.
5. If the fault cannot be injected honestly, say so in a SKIP script rather than
   approximating it. An injection that proves something other than the failure is
   worse than an admitted gap.
