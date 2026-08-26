# ADR-019: Rust capture tier replaces Kotlin feed handlers

**Status:** Accepted
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Ingestion

---

## Context

v2's capture tier is three Kotlin/Ktor containers, one per exchange, each producing
trades to two Redpanda topics ([ADR-002](ADR-002-kotlin-feed-handlers.md)). They work:
~150 msg/s sustained, ~0.03 CPU / 134 MiB each measured under live load
([`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md)).
The problem is not what they cost. It is what they cannot be asked to do.

- **The only wall clock on the trade path is taken after JSON parse and
  normalisation** (`services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/TradeNormalizer.kt:28`).
  Kraken alone stamps anything, and it does so at raw-publish time
  (`.../KrakenWebSocketClient.kt:229`). Exchange-clock skew and platform delay are
  therefore inseparable in every stored row, and no query can tell them apart after
  the fact.
- **There is no L2 book at all.** Adding one means a second connection per exchange,
  a book state machine, and Kraken's CRC32 checksum — none of which exists in the
  Kotlin tier and all of which would have to be written from scratch there.
- **Coinbase's `sequence_num` is parsed and discarded**
  (`.../CoinbaseWebSocketClient.kt:178`); a dropped message is silent.
- **Kraken runs WS v1 with synthesised trade IDs** —
  `"KRAKEN-${timestampMs}-${pair.hashCode()}"` (`.../TradeNormalizer.kt:60`) — so two
  trades in the same millisecond on the same pair are indistinguishable.
- **Replay determinism is not available.** v3 replay pushes archived frames back
  through the *same* adapter code the live path runs, asserted by content hash
  ([`../research/2026-08-26-v3-requirements-clarification.md`](../research/2026-08-26-v3-requirements-clarification.md)
  Q1). That requires no `HashMap` iteration on emit paths, no `f64` on the record
  path, and no wall-clock read outside the frame-receipt stamp. The JVM tier holds
  none of those three properties today, and `BigDecimal` on the hot record path is
  the sort of thing that gets quietly reintroduced.

Every one of these is a change to the frame-receipt path. Retrofitting them into the
Kotlin handlers is not cheaper than a rewrite — it *is* the rewrite, in a language
that then has to carry a JVM decimal library and a JVM per exchange.

The constraint is unchanged: one host, 16 CPU / 40 GB
([ADR-010](ADR-010-resource-budget.md)), CPU binding.

---

## Decision

**We will replace the three Kotlin feed handlers with a single Rust `k2-capture`
binary run once per exchange, carrying trades and L2 book on one connection each,
because the properties v3 needs — receive-timestamp-before-parse, exact fixed-point
arithmetic, and bit-for-bit replay determinism — are properties of the frame path,
and the frame path has to be rewritten to get them regardless of language.**

Scope: the capture tier only. Spark, Prefect, ClickHouse and the notebooks stay in
their current languages; this is not a "rewrite it in Rust" programme.

---

## Rationale

**This is explicitly not a latency argument.** K2 reads public WebSocket feeds over
the open internet. Transit dominates everything the process does by two orders of
magnitude, the platform is not a trading path, and no number published from it
should be read as a latency claim about a colocated system
([ADR-018](ADR-018-v3-lake-first-rust-capture.md) Context; non-goals reaffirmed in
the requirements clarification). At ~150 msg/s the capture tier is not the
bottleneck and never will be. Choosing Rust for speed here would be cargo-culting.

The five reasons that do hold:

1. **One connection per exchange carrying trades *and* book.** Binance combined
   `/stream?streams=` takes `@trade` and `@depth20@100ms` together; Kraken v2 takes
   `trade`, `book` and `instrument`; Coinbase takes `market_trades`, `level2` and
   `heartbeats`. Spike S2 confirmed a single Kraken connection carried 944 book
   frames plus trades plus precision metadata in 12 s
   ([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s2--kraken-instrument-channel)).
   One socket per exchange means one sequence space, one reconnect policy, and one
   place where the book and the trades agree about time.
2. **`recv_ts_ns` before the parser.** `SystemTime::now()` as the first statement on
   frame receipt, before a byte is deserialised. This is the discipline the whole
   latency-decomposition story rests on, and it is a one-line property that is
   impossible to add credibly once a normalisation layer sits in front of it.
3. **Fixed-point without a decimal library.** `i64` at 1e-8 is a primitive in Rust;
   the checksum formatter, the book map keys and the wire encoding all use the same
   integer ([ADR-020](ADR-020-avro-fixed-point-contracts.md)). Spike S1 established
   that Kraken's CRC32 must be formatted from decimal strings or `i64` units and
   never from `f64` — an `f64` round-trip desyncs the book silently while the
   checksum reports success. On the JVM the equivalent is `BigDecimal` on every
   record, which is allocation on the hot path and an invitation to reach for
   `double` when it shows up in a profile.
4. **Determinism for replay.** No `HashMap` iteration on emit paths, `BTreeMap<i64,i64>`
   for book state, no `f64` on the record path, no wall-clock read outside the
   receipt stamp. These are cheap to hold from day one and expensive to retrofit —
   the same argument that puts `recv_ts_ns` before the parser.
5. **Footprint, and one language for capture and replay.** Spike S6 built the real
   dependency set — vendored `rdkafka` with `libz-static` and `zstd` — onto
   `gcr.io/distroless/cc-debian12:nonroot` at **42.8 MB**
   ([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s6--rdkafka-on-distroless)),
   against three JVMs today. `k2-replay` is a subcommand of the same crate calling
   the same `handle_frame(bytes, recv_ts_ns)`, so research and production share one
   parser by construction rather than by agreement.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep Kotlin, add book + recv_ts + Kraken v2 + sequencing in place** | This is the same rewrite with a worse ending. Every listed gap is on the frame-receipt path, so the diff touches the same code either way — and it lands with `BigDecimal` on the record path, three JVMs, and a second implementation of the parser for replay because the JVM tier has no replay story. The work is not saved, only the language choice is. |
| **Kotlin for trades, Rust for the book tier** | Two languages in one tier, book/sequencing/checksum logic split across both, six containers instead of three, and the trade and book streams on separate connections — so the two products of one exchange no longer share a sequence space or a receive clock. Rejected in ADR-018 for the same reason. |
| **Go** | Real candidate: small binaries, good WebSocket and Kafka libraries, faster to write than Rust. Loses on the two properties this decision is actually about — `float64` is the idiomatic numeric type and map iteration is *deliberately* randomised, which is the exact hazard the replay contract forbids. Both are avoidable in Go with discipline; in Rust the compiler and `BTreeMap` make them the default. |
| **Python with `uvloop`** | The v1 tier this project already left ([ADR-002](ADR-002-kotlin-feed-handlers.md)): GIL contention on serialisation, `fastavro` ~10× slower than JVM Avro, and no way to hold a memory bound on Coinbase's 44k-level full-depth book. Determinism is achievable; footprint and the book are not. |

---

## Consequences

**Easier:** taking the receive stamp before parse; carrying trades and book on one
connection with one sequence space; exact arithmetic end to end; adding an exchange
(one adapter, one `handle_frame`); running the live parser over the archive in CI;
three ~43 MB containers instead of three JVMs, against a CPU-binding budget.

**Harder:** the repo gains a language it does not use anywhere else, and CI gains a
`rust` job (fmt, clippy `-D warnings`, test, `Swatinem` cache) plus a Docker matrix
entry. Compile times are minutes where Gradle was seconds. `cargo-chef` and
distroless make the image cheap but the Dockerfile subtle — S6 shows how — and
`rdkafka`'s `libz-static` + `zstd` features are not optional, which is a footgun for
the next person who edits `Cargo.toml`. Debugging moves from "read the JSON in
Redpanda Console" to "decode Avro by schema id". Nobody else on this project reads
Rust, which for a solo repo is a stated cost, not a hypothetical one.

**Committed to:** three capture containers at 0.25 CPU / 256 MB (Coinbase 512 MB for
the full-depth book), `cpuset`-pinned away from ClickHouse and Spark; a parallel-run
window where Rust and Kotlin both produce, to separate topics
([ADR-020](ADR-020-avro-fixed-point-contracts.md) fixes the `market.crypto.v3.*`
prefix); and retiring `services/feed-handler-kotlin/` to `legacy/v2-kotlin/` once
parity holds, removed from compose and CI in the same PR.

**Retirement trigger — what "parity holds" means.** Kotlin is retired when, over one
labelled parallel-run window, **per-symbol trade counts and trade IDs from the Rust
tier match the Kotlin tier exactly** in both ClickHouse and the lake, across all
three exchanges, with any divergence explained rather than tolerated. Kraken is the
expected exception and is explained in advance: v1's synthesised
`"KRAKEN-${ms}-${hash}"` IDs collide by construction, so Kraken parity is asserted on
counts and on v2's real integer `trade_id` being present and unique — not on ID
equality with a v1 identifier that was never real.

**The window is 2 hours, labelled — not 24.** ADR-018 and the original Phase C plan
both said 24 h. The maintainer's decision of 2026-08-26 set the burn-in and parity
window at 2 hours, explicitly labelled as a 2-hour sample wherever its numbers are
published, with the 24-hour continuous run kept as a Phase F+ revisit trigger; the
plan records it at
[`../plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md`](../plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md)
("3 exchanges × 2 h window clean (labelled)"). The honest consequence, stated here
rather than discovered later: a 2-hour window cannot observe Binance's 24-hour
connection-lifetime reconnect, an exchange's daily maintenance window, or any
diurnal volume peak. It is a sample, not a soak, and every number derived from it
carries its window in the row.

**Risks:** Coinbase's WebSocket rate limits are documented inconsistently — S5 got
`level2` with no JWT and no error frames, but the documented "8 per second per IP"
was never pushed. A vendored `librdkafka` on distroless is proven to *run* (S6) and
not yet proven to survive a broker outage. And the Kotlin tier is the only capture
implementation that has ever run in production here; retiring it removes the
comparison baseline, which is why parity is a gate rather than a report.

**Revisit when:** the Phase C burn-in numbers are published in `docs/benchmarks/`. If
per-symbol trade counts diverge from Kotlin by more than 0 over the labelled window
with no explanation, or Kraken checksum pass rate is below 100 %, or the three
capture containers exceed 1.5 CPU combined, this ADR gets an Outcome section before
Kotlin is retired — and Kotlin stays until it does.

---

## Related

- [ADR-002](ADR-002-kotlin-feed-handlers.md) — the decision this supersedes, and its measured Outcome
- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — the umbrella; Appendix A carries spikes S1, S2, S5 and S6
- [ADR-020](ADR-020-avro-fixed-point-contracts.md) — the wire contract this tier produces
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — the book product and per-exchange resync policy
- [`../plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md`](../plans/2026-08-26-v3-quant-research-platform/002-phase-c-rust-capture.md) — Phase C scope, exit criteria, verification
- [`../research/2026-08-26-v3-requirements-clarification.md`](../research/2026-08-26-v3-requirements-clarification.md) — Q1 (replay owns the parser), and the reaffirmed non-goals
- [`../runbooks/capture-down.md`](../runbooks/capture-down.md), [`../runbooks/capture-feed-stale.md`](../runbooks/capture-feed-stale.md) — operating this tier

---

## Outcome

_What is recorded below are design points that did not survive contact with the
running tier, followed by the retirement itself._

### As-built corrections, 2026-08-26

**The producer compresses with `zstd`, not the `lz4` the Phase C plan named.** JSON on
the `raw.*` topics is what makes the extra CPU worth paying, and the multi-MB Coinbase
`level2` snapshots compress best of all: one captured 4,803,578-byte snapshot came out
at 383,011 bytes, 12.5:1 at `zstd -3`. That is one frame of one shape, not a topic
ratio; the capacity model's G2 prediction is deliberately left at the lz4-era 0.40 and
scored in Phase F rather than adjusted on a single sample.

**`records_produced_total` is an enqueue counter, and a delivery counter had to be added
beside it.** `sink.rs` increments it when `send_result` returns `Ok`, which is the moment
the record enters librdkafka's local queue — so it climbs at full rate throughout a broker
outage. `CaptureProduceStalled` was originally written against it and could not fire in
the only scenario it names. `k2_capture_records_delivered_total`, incremented from the
delivery report, is the counter that actually goes flat, and the alert now reads that.

**Metric series that alerts read are created at zero on startup, and three were not.**
`precision_loss_total`, `unknown_frames_total` and the new `records_delivered_total` had
no seeded series, so `increase(...) > 0` could not fire on the first event — which for
precision loss is precisely the event this ADR wanted observed. Conversely
`checksum_failures_total` was seeded for all three venues, advertising 23
permanently-zero series for two venues that publish no checksum; it is now Kraken-only.

**Staleness is per *continuous* stream, and the set is narrower than "every subscribed
channel".** Stamping `k2_capture_last_message_ts_seconds` on Kraken's `status`/`control`
and Coinbase's `subscriptions` fired a permanent critical about two minutes after every
healthy connect. Kraken's `instrument` channel joined them for a different reason: at
0.0017 frames/s (2 frames in 29 minutes, measured 2026-08-26) against a 60 s threshold it
is a reference channel, not a liveness signal. The gauge is also now seeded at process
start, because a subscription the venue silently rejects otherwise has no series and
`time() - <absent>` cannot fire.

**The Binance 23 h scheduled reconnect was specified in Phase C scope, asserted by three
documents, and not implemented.** It exists now as `connection_expired()` /
`BINANCE_MAX_CONNECTION_AGE` in `main.rs`, and `k2_capture_reconnects_total` gained a
`reason` label (`scheduled` / `involuntary`) so the correlation the sequence-gaps runbook
asks an operator to make is actually possible.

**One call on the frame path blocks, and now says by how much.** The Avro encode awaits
the schema registry on first use of a subject; `reqwest`'s default is no timeout, so a
registry that accepted the connection and never answered would stall the socket read
indefinitely — the failure the sink's own design notes claim to avoid. It is capped at
5 s (`REGISTRY_TIMEOUT`).

**The image could not be built by `docker compose` at all.** The compose build context
was the crate directory, under which the Dockerfile's root-relative COPYs and
`include_str!("../../../schemas/avro/...")` cannot resolve; `capture-kraken` and
`capture-coinbase` had no `build:` key, so a selective bring-up tried to pull a tag from
no registry; `make test-rust` mounted only the crate, so the pre-PR gate did not compile;
and the CI docker leg carried the same wrong context. All four are fixed, and
`K2_GIT_SHA` (`git describe --always --dirty`) is now passed by compose, CI and the
Makefile — every automated build until then shipped `git_sha="unknown"`.

### Measured correction, 2026-08-26 — the 32 MiB buffer was unreachable

**The producer's 32 MiB queue was sized in minutes and capped in seconds, so it never
did its job.** `queue.buffering.max.kbytes=32768` buys 194 / 204 / 446 s of broker
outage before a record is dropped, and that arithmetic is what the FMEA's delayed→lost
boundary and the capacity model's §5 memory line both rest on. Sitting on top of it,
`message.timeout.ms` was 30 s: any record still undelivered after half a minute was
failed regardless of how empty the queue was.

The first `make chaos` run measured the consequence rather than inferring it. With the
broker paused, `capture-queue-full.sh --exchange kraken` saw its first drop at **102 s
against a predicted 204 s** — 50 % early — and across the 388 s fault window **231,744
records were lost with `reason="queue_full"` at exactly zero**. Every one was a timeout
counted `delivery`. A prediction being half wrong is the cheap part; the expensive part
is that the wrong half was the buffer the design advertises.

`message.timeout.ms` is now **300000** (5 minutes, and librdkafka's own default), which
covers the queue's slack at every venue. `enable.idempotence=true` does not constrain
it — that flag adjusts `max.in.flight.requests.per.connection`, `retries`, `acks` and
`queuing.strategy` only; only a `transactional.id`, which this producer does not set,
would clamp `message.timeout.ms` to `transaction.timeout.ms`
(librdkafka `CONFIGURATION.md`). The four numbers the docs quote are now asserted by a
unit test (`sink.rs::producer_config_carries_the_numbers_the_docs_quote`) rather than by
grep.

What changed is *which* cap binds, not whether loss happens. Past ~204 s of kraken
outage the records are gone either way; what the 30 s cap threw away was the first 204 s,
which were meant to be free. The new failure shape: `queue_full` is the first signal at
binance and kraken rates, `delivery` at coinbase's slower rate, and a `delivery` tick
during an outage now means the outage outran five minutes rather than thirty seconds.
Evidence: [`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

### Retirement, 2026-08-26

**Kotlin is retired.** `services/feed-handler-kotlin/` is archived at
`legacy/v2-kotlin/` with a README, its six v2 topics inventoried, and its
feed-handler crash runbook and alert rules moved alongside it. The three
`feed-handler-*` services, their Prometheus scrape jobs and their three alert
rules are out of `docker-compose.yml` and `docker/prometheus/`; the `kotlin` CI
job and the `feed-handler` docker-matrix entry are out of
`.github/workflows/ci.yml`. `make test` no longer runs them —
`make test-legacy-kotlin` does, deliberately outside the default target.

**Parity over the labelled 2-hour window.** Per-symbol trade counts and trade IDs
from the Rust tier against the Kotlin tier, all three exchanges, in ClickHouse
and the lake, per the retirement trigger above:

«WINDOW3»

**Sequence gaps, checksum failures and resource use over the same window:**

«WINDOW3»

Kraken's expected exception stands as written: v1's synthesised
`"KRAKEN-${ms}-${hash}"` IDs collide by construction, so Kraken parity is
asserted on counts and on v2's real integer `trade_id` being present and unique,
never on ID equality with a v1 identifier that was never real. The v1/v2 symbol
spellings are a second, smaller Kraken-only divergence, recorded in
`scripts/parity/README.md`.

**The window is a 2-hour sample and every number above carries that label.** It
cannot observe the Binance 23 h scheduled reconnect, a venue's daily maintenance
window, or any diurnal volume peak. The 24-hour continuous run stays a Phase F+
revisit trigger, and the SLO error budgets built on this data are provisional
until one exists.

### What the retirement cost and returned

The budget moved exactly as the Phase C addendum to
[ADR-010](ADR-010-resource-budget.md) predicted: steady state from
16.10 CPU / 23.125 GiB across 18 long-running services to
**14.60 CPU / 21.625 GiB across 15**, the full 1.5 CPU / 1.5 GB the three JVMs
declared, verified by summing `deploy.resources.limits` over
`docker compose --env-file .env.example config` (that ADR's addendum carries the
command and its output). Bootstrap peak is 16.10 CPU / 23.125 GiB across all 19
services — what steady state used to be during the parallel run.

Loaded Prometheus rules go from 27 to 24 (14 v2 + 10 v3 capture). Grafana stays
at five dashboards: no dashboard read only feed-handler metrics, so none was
archived; the "Feed Handlers" row came out of `k2-pipeline-overview.json`, whose
v3 equivalents already existed in `k2-l2-capture.json`, and the two Stack Health
panels were repointed at `k2_capture_*` series.

Two consequences the Consequences section named as costs have now landed:

**The comparison baseline is gone.** The Kotlin tier was the only capture
implementation that had ever run here, which is why parity was a gate rather
than a report. From this commit there is nothing to diff a suspect Rust number
against except the archive and the exchange's own REST API.

**The v2 hot tier is frozen, not dropped.** The Kotlin handlers were the only
producers of `market.crypto.trades.<ex>[.raw]`, so `k2.bronze_trades_*`,
`k2.silver_trades` and the six `k2.ohlcv_*` tables stop advancing at the
retirement timestamp and keep expiring rows under their TTLs. Dropping the `k2`
database and deleting the `.raw` topics is Phase E, after the v3 hot tier exists
([`../research/2026-08-26-v3-requirements-clarification.md`](../research/2026-08-26-v3-requirements-clarification.md)
Q5). `schemas/avro/normalized-trade.avsc` and the six v2 topics stay until then;
`docker/redpanda/init.sh` keeps creating the topics and says why.

**One thing this ADR did not anticipate.** Retiring the handlers freed
`config/instruments.yaml` to carry Kraken's WS **v2** spellings — the registry
had been pinned to `XBT/USD` and `XDG/USD` because the v1 handlers read the same
file, and `kraken.rs` held a two-row alias table to bridge it. Both are gone, so
`native` is once again exactly the bytes on the wire with nothing mapping it.
That was a hidden cost of running the two tiers side by side, and it is only
visible as a saving now that one of them is gone.
