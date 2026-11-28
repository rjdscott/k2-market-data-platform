# `scripts/chaos/`: fault injection for the capture tier and the run path

Three groups. The run path's four (plan 002's verification, [below](#the-run-path-scripts-plan-002)) break one product's run on a throwaway lake, or on the live stack under a deploy; the Kafka products' six ([plan 004](#the-kafka-product-scripts-plan-004)) break kraken-trades' run on a throwaway lake. The capture tier's five come first here: four break the running stack on purpose, wait for the alert that is
supposed to notice, measure how long recovery took, and put the stack back; the
fifth records an honest gap it cannot yet inject. They
are the `proof` column of
[`docs/architecture/16-failure-modes.md`](../../docs/architecture/16-failure-modes.md):
an FMEA whose recovery times are estimates is a wish list, and these are what turn
that column into measurements.

**Maintainer-run, never CI.** The GitHub-hosted runner has ~7 GB of RAM and this
stack budgets 28.688 GiB at its bootstrap peak (`python3 scripts/check-budget.py`), so a
nightly chaos job could not host Redpanda, Spark, Trino, MinIO and three capture
containers at once: it would fail for resource
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
[`docs/architecture/16-failure-modes.md`](../../docs/architecture/16-failure-modes.md) and in
the `docs/runbooks/capture-*.md` MTTR tables comes from that file.

Runs are still scheduled deliberately rather than taken opportunistically: each script
drops real market data from whichever venue it targets, and the two broker scripts drop
it for the whole stack. Never during a labelled burn-in or parity window, whose evidence
it would destroy.

### What the first run found

| Finding | Detail |
|---|---|
| **The 32 MiB producer queue was unreachable** | `capture-queue-full.sh --exchange kraken` predicted the first drop at 204 s and measured **102 s, −50 %**. Of the records lost across the 388 s fault window (counted in ADR-019's Outcome), **zero** carried `reason="queue_full"`: `message.timeout.ms` was 30 s, so every record expired on a timer while the buffer sat half empty, counted `delivery`. Fixed the same day: `message.timeout.ms=300000` ([ADR-019 Outcome](../../docs/adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)). **The 204 s prediction is now under test again: re-run to score it.** |
| **A pause does not manufacture a sequence gap** | Both `capture-pause.sh` runs gave `gaps_total` 0 → 0 and `reconnects_total` 0 → 1. The reconnect starts a fresh sequence series, so there is no discontinuity to detect. Gap *detection* remains unproven and waits on `k2-replay` (Phase G). |
| **Detection is slow, recovery is fast** | `CaptureDown` 119–165 s, `CaptureProduceErrors` 256 s; recovery 0–14 s across every script. Almost all of every MTTR is spent noticing. |
| **Back-to-back runs cost a measurement** | `redpanda-stop.sh` ran one minute after `capture-queue-full.sh`, with `CaptureProduceErrors` still firing, so its `t_fire` reads 0 rather than an independent number. Space runs by the alert's `for:` window. |
| **The broker survives a six-minute pause** | `rpk cluster health` clean after both, single-node Raft, no manual intervention. |

Still unrun: `redpanda-stop.sh --cold-start` (the 2026-08-26 run took the default warm
path) and `capture-corrupt-frame.sh` (SKIP by design until `k2-replay`).

### The lake scripts retired unrun

The four `lake-*.sh` scripts were never run, and they retired with the old lake ingest
they faulted ([ADR-056](../../docs/adr/ADR-056-unified-data-product-platform.md)). They
are archived in [`legacy/v3-lake-ingest/scripts/chaos/`](../../legacy/v3-lake-ingest/scripts/chaos/);
the ingest pause they called (`pause_lake_ingest` and its helpers) left `lib.sh` with them
and is in that file's git history. `make chaos` prints a SKIP line in their place until
phase 004 brings fault scripts for the new lake products.

---

## The scripts

| Script | Fault | Expects | FMEA rows it proves |
|---|---|---|---|
| `capture-kill.sh` | `docker kill --signal=KILL` one capture container, held down past the alert window | `CaptureDown` | capture / SIGKILL |
| `capture-pause.sh` | `docker pause` one capture container until its scrape target reads down | `CaptureDown` (a paused target is stale-marked, so `CaptureFeedStale` cannot fire on it) | capture / SIGSTOP; coinbase `sequence_num` gap; binance `lastUpdateId` regression; the *signal* of venue-side maintenance |
| `capture-queue-full.sh` | `docker pause` the broker so librdkafka's 32 MiB queue fills and capture starts dropping | `CaptureProduceErrors` — `reason="queue_full"` at binance/kraken rates, `reason="delivery"` at coinbase's, where `message.timeout.ms` binds first. The script prints which it expects and both counters | capture → Redpanda / producer queue full |
| `redpanda-stop.sh` | `docker stop` the broker; `--cold-start` also recreates a capture container while it is down | `CaptureProduceErrors`, or `CaptureDown` under `--cold-start` (warm-up is fatal, so the container crash-loops rather than failing produces) | Redpanda / broker down; schema registry / down mid-run; schema registry / down at start |
| `capture-corrupt-frame.sh` | none — prints SKIP and exits 0 | — | corrupt frame (**not automatable until `k2-replay`, Phase G**) |

The five `capture-*`/`redpanda-*` scripts take
`--exchange binance|kraken|coinbase`, defaulting to `kraken`.
`capture-kill.sh` also takes `--hold <seconds>` (default 150) and
`redpanda-stop.sh` takes `--cold-start`.

Four design notes worth knowing before reading them:

- **`capture-queue-full.sh` pauses the broker; `redpanda-stop.sh` stops it.** A
  paused broker leaves every TCP connection open and stops answering, so
  librdkafka keeps enqueueing — the purest queue-full injection. A stopped broker
  refuses the connection and librdkafka fails differently. Same neighbourhood,
  different failure, two scripts.
- **Those two scripts take down the whole stack, not just one container.**
  Redpanda is the single broker *and* the single schema registry, so all three
  `capture-*` containers and Console lose it too (and ClickHouse's Kafka-engine
  consumers, on a stack running the paused hot tier, ADR-057): the `--exchange`
  you pass is only the one being measured. `capture-queue-full.sh
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

## The run-path scripts (plan 002)

Plan 002's verification block names five scripts for the ECB run path. Four are in this
directory and the fifth, `fx-idempotent.sh`, is a check rather than a fault, so it lives in
`scripts/verify/`. The four throwaway ones take a compiled release
(`bash <script> <release dir>`, for example the one `scripts/verify/ci-validate.sh` prints) and
build a throwaway lake of their own from it (`scripts/lib/throwaway-lake.sh`, the script's name
as its prefix). Every task in them runs the release's own code, in the shape the DAG factory
gives it, on the machinery `scripts/verify/run-path.sh` (spine-e2e's `run` stage, nine claims; one
recorded run of 2026-11-27 printed `run-path: 9 PASS, 0 FAIL in 263 s`) shares through
`scripts/lib/run-path-lib.sh`. Each prints one PASS or FAIL line per claim and `<n> PASS, <m>
FAIL`, stops at the first FAIL, and refuses to start beside another container job or under
10 GiB available. None touches the running stack but `deploy-mid-run.sh`, which is for the
operator on the live stack after the first deploy of the release under test.

| Script | What it proves | Where it runs | Recorded result |
|---|---|---|---|
| `fx-bad-file.sh` | the corrupt synthetic ECB file lands and fails its audit, bronze `main` does not move, the next file is refused at load with its bytes landed, the operator's clearing statement (`ALTER TABLE <bronze> REPLACE BRANCH audit AS OF VERSION <main>`) lets its load proceed, and both landing objects are their spooled raw files byte for byte | a throwaway lake, prefix `fx-bad-file` | 2026-11-27: `fx-bad-file: 3 PASS, 0 FAIL in 126 s` |
| [`../verify/fx-idempotent.sh`](../verify/fx-idempotent.sh) | the same bytes again add no landing object, no `landing_files` row and no bronze row, and the build passes with the same silver and gold rows: every count before and after | a throwaway lake, prefix `fx-idempotent` | 2026-11-27: `fx-idempotent: 5 PASS, 0 FAIL in 123 s` (every count 1 -> 1 or 15 -> 15) |
| `dbt-failing-test.sh` | a planted data test failure (gold's newest USD row twice, the second at rate 999) restores silver and gold to their prior snapshots with a `rolled_back` row each; a reader polling gold through its view every 50 ms never sees the failed build; expire removes the build's snapshots by id; the expire task's run record is written | a throwaway lake, prefix `dbt-failing-test` | 2026-11-27: `dbt-failing-test: 6 PASS, 0 FAIL in 160 s` (150 polls, each the 15 tested rows) |
| `trino-stop-mid-build.sh` | the running dbt runner's environment holds exactly the seven names the release gives its kind and the image's own, no secret; with Trino stopped once the build's `started` rows are in, the wrapper exits non-zero and publishes nothing; the next run writes `reconciled` first, then builds and publishes | a throwaway lake, prefix `trino-stop` | 2026-11-27: `trino-stop: 6 PASS, 0 FAIL in 159 s`, on the second run: the first stopped at claim 4 on the harness's own name check, which counted the Python base image's `GPG_KEY` (the public fingerprint of Python's release-signing key) as a forbidden name; the check now applies to the names the run adds to the image's own |
| `deploy-mid-run.sh <dag_id> <release id>` | a run triggered on the live release R-1 and overtaken by `make deploy` finishes on R-1, every run-record row of it says R-1, and the next run says the new release | the live stack; refused unless `make deploy PLAN=1` shows no drain and nothing recreated (a documentation-only or rules-only release); `--dry-run` prints the plan | 2026-11-27, live: `deploy-mid-run: 4 PASS, 0 FAIL in 105 s` (R-1 `v3.0.0-alpha.5-8353ee58fd2c`, target `v3.0.0-alpha.5.1-rc.1-f29ee6610326`). The first run, onto `v3.0.0-alpha.5`, printed `2 PASS, 2 FAIL in 119 s`: the run finished on R-1 with R-1 in its rows, and the deploy read dbt's `silver_rates__dbt_tmp` as drift at verify and rolled back; `scripts/deploy.py` now waits for a running build's own relations (`in_flight`). The passing run did not meet such a relation, so that wait is proven by `tests/test_deploy.py` alone |

Two things these scripts record rather than hide. The expire task's run record that
`dbt-failing-test.sh` writes is the row the DAG's `record` task writes for an expire that
succeeded, event `passed`: plan 002 names an `expired` row, and no code writes that event (the
script's header says so). And on a throwaway lake the dbt runner's flags are
`run-path-lib.sh`'s, which mirror `oneshot.py`; the operator's own flags are held to the same
names by `docker/airflow/tests/test_oneshot.py`.

## The Kafka product scripts (plan 004)

Plan 004's six, each on a throwaway lake of its own with a Redpanda (`K2_THROWAWAY_REDPANDA=1`), on
the release's own `kraken-trades` and the products around it, every task in the DAG factory's shape
through `scripts/lib/run-path-lib.sh`, frames and listings synthetic. Each takes a compiled release as
the run-path ones do, prints one numbered PASS or FAIL line per claim and `<n> PASS, <m> FAIL`, stops
at the first FAIL and exits 0 only when every claim passed. They run a scenario
[`../verify/kafka-path.sh`](../verify/kafka-path.sh) or [`../verify/run-path.sh`](../verify/run-path.sh)
already proves through the same probes, on the 004 products, worded as the plan's exit lines. With no
Airflow on a throwaway, what only the scheduler does (the refresh trigger, XCom between tasks) is
`docker/airflow/tests/test_factory.py`'s to prove; each header says where that line falls. The
runbooks they exercise are [accept-data-loss.md](../../docs/runbooks/accept-data-loss.md) and
[kafka-batch-blocked.md](../../docs/runbooks/kafka-batch-blocked.md).

| Script | What it proves | Where it runs | Recorded result |
|---|---|---|---|
| `kafka-connector-kill.sh` | kraken-trades' load killed after its landing commit, then before it, then retried under its run id: each next run lands and stages the range once, no hole (kafka-path.sh a4 to a6) | a throwaway lake, prefix `kafka-connector-kill` | not yet run |
| `kafka-below-log-start.sh` | the log start moved past the resume point (`rpk topic trim-prefix`, Redpanda v25.3.4): the load fails naming each partition; the accept-data-loss load lands from the log start, its audit passes; the release's `factory.offset_gap`, in compose's Airflow image, writes the `offset_gap` row and `k2runs.offset_gaps` reads it back | a throwaway lake, prefix `kafka-below-log-start` | not yet run |
| `unknown-symbol.sh` | an unlisted symbol's trade held beside a listed one's in one batch; the listed one publishes, the build is `degraded`, `factory.Refresh`'s rule starts the instruments run, which lists it; the next build takes it in once | a throwaway lake, prefix `unknown-symbol` | not yet run |
| `bronze-failed-then-repaired.sh` | a frame of a schema id not yet registered fails `parses`; the next load lands and is refused, bronze unmoved; the schema registered and the clearing statement run, the next run restages, publishes and silver takes both trades | a throwaway lake, prefix `bronze-failed` | not yet run |
| `venue-test-fails.sh` | a kraken-trades build that fails its tests rolls back; canonical-trades and candles then change no row (run-path.sh claim 17) | a throwaway lake, prefix `venue-test-fails` | not yet run |
| `pause-input.sh <input>` | `kraken-trades`: canonical-trades builds with it paused and is `partial: market-capture.kraken-trades.silver_trade` (run-path.sh claim 16); phase 005 adds `ecb-fx-rates` | a throwaway lake, prefix `pause-input` | not yet run |

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
  permanently absent from the topics and from everything read from them. That
  includes any burn-in or parity window in flight: a chaos run during one
  invalidates its evidence.

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
[`docs/architecture/16-failure-modes.md`](../../docs/architecture/16-failure-modes.md),
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
