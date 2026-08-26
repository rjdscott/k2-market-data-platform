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
which were meant to be free.

**The new failure shape is a prediction, not a measurement.** With
`message.timeout.ms=300000` the arithmetic says `queue_full` should now be the first
signal at binance and kraken rates and `delivery` at coinbase's slower one — but the
only run on record is the one above, taken at 30 s, and it is the run that proved this
kind of arithmetic wrong. **It is unscored until `capture-queue-full.sh` is re-run
against the 5-minute timeout**, and the 204 s row in
[`../architecture/failure-modes.md`](../architecture/failure-modes.md) says so rather
than quietly correcting itself. Evidence for the 30 s measurement:
[`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv).

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

Generated by `scripts/parity/compare_trades.py --exchange <ex> --window-start
2026-08-26T14:15:00Z --window-end 2026-08-26T16:15:00Z`, run once per exchange
over the same window against the live topics, capture binary
`git_sha=v3-phase-b-33-gf808d87`. The `v2` / `v3` / `Δ` columns count **unique
exchange `trade_id`s, not records**: venues re-send trades and both tiers carry
the re-send identically, so repeats are reported in `dup-v2` / `dup-v3` and are
never folded into Δ ([`../../scripts/parity/README.md`](../../scripts/parity/README.md)).

**How to read the verdict column.** PASS/FAIL is the script's, per symbol, not a
judgement written here: `|Δ| ≤ tolerance`, where **tolerance = `max(2, 0.1% of
count)`**. `only-v2` / `only-v3` carry that same tolerance on the id-join path
(Binance, Coinbase) but only the constant **`EDGE_ALLOWANCE = 2` on the Kraken
multiset path** — Kraken has no `mismatch` column, so a real divergence has
nowhere else to surface and the proportional slope would swallow it. Three things
get no tolerance at all: `px/qty/side mismatch` must be 0, `exchange_ts` delta on
a matched id must stay under 1,000 µs, and a symbol one tier saw and the other did
not is a FAIL at any count (`scripts/parity/compare_trades.py::SymbolStats.passed`).

**Binance — 12/12 PASS, and exactly:** 1,569,505 unique trade ids on each side,
`only-v2` and `only-v3` both **0 on every symbol**, `px/qty/side mismatch` 0,
max `exchange_ts` delta on matched ids **0 µs**.

| symbol | v2 | v3 | Δ | dup-v2 | dup-v3 | only-v2 | only-v3 | px/qty/side mismatch | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ADA/USDT | 26,356 | 26,356 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| ATOM/USDT | 2,993 | 2,993 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| AVAX/USDT | 28,138 | 28,138 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| BNB/USDT | 101,524 | 101,524 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| BTC/USDT | 521,497 | 521,497 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| DOGE/USDT | 99,149 | 99,149 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| DOT/USDT | 2,688 | 2,688 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| ETH/USDT | 292,470 | 292,470 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| LINK/USDT | 59,670 | 59,670 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| LTC/USDT | 26,968 | 26,968 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| SOL/USDT | 148,814 | 148,814 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |
| XRP/USDT | 259,238 | 259,238 | +0 | 0 | 0 | 0 | 0 | 0 | PASS |

**Coinbase — the script says FAIL on 8 of 11 symbols, and every one of the 451
divergent ids is v3 carrying a trade v2 did not have.** `only-v2` is **0 on
every symbol**: the v3 id set is a strict superset of the v2 id set, symbol by
symbol. `px/qty/side mismatch` is 0 everywhere, so the two tiers never disagreed
about a trade they both saw.

| symbol | v2 | v3 | Δ | dup-v2 | dup-v3 | only-v2 | only-v3 | px/qty/side mismatch | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ADA/USD | 8,436 | 8,547 | -111 | 200 | 384 | 0 | 111 | 0 | **FAIL** |
| ATOM/USD | 1,341 | 1,343 | -2 | 300 | 400 | 0 | 2 | 0 | PASS |
| AVAX/USD | 2,909 | 2,920 | -11 | 321 | 408 | 0 | 11 | 0 | **FAIL** |
| BTC/USD | 151,053 | 151,121 | -68 | 7 | 540 | 0 | 68 | 0 | PASS |
| DOGE/USD | 5,676 | 5,685 | -9 | 141 | 500 | 0 | 9 | 0 | **FAIL** |
| DOT/USD | 4,162 | 4,168 | -6 | 102 | 0 | 0 | 6 | 0 | **FAIL** |
| ETH/USD | 44,954 | 44,985 | -31 | 0 | 0 | 0 | 31 | 0 | PASS |
| LINK/USD | 5,363 | 5,392 | -29 | 118 | 624 | 0 | 29 | 0 | **FAIL** |
| LTC/USD | 4,987 | 4,992 | -5 | 113 | 0 | 0 | 5 | 0 | **FAIL** |
| SOL/USD | 18,301 | 18,347 | -46 | 313 | 2 | 0 | 46 | 0 | **FAIL** |
| XRP/USD | 36,381 | 36,514 | -133 | 197 | 33 | 0 | 133 | 0 | **FAIL** |

Totals: 283,563 v2 ids, 284,014 v3 ids, 451 `only-v3`, 0 `only-v2`, 0 mismatches;
285,375 and 286,905 records consumed. Max `exchange_ts` delta on matched ids
**999 µs**, inside v2's millisecond truncation.

**Kraken — the id join is unavailable by construction, so this table is a
multiset comparison and the script says FAIL on 7 of 12 symbols.** v2's ids are
synthesised `KRAKEN-<ms>-<hash>` and collide: over this window BTC/USD carried
**9,093 v2 records under 3,923 distinct synthesised ids**, a 57 % collision rate,
so only the v3 side can be deduplicated. `v2` therefore counts records, `v3`
counts unique venue trade ids, and `dup-v2` reports id *collisions* rather than
re-sends.

| symbol | v2 | v3 | Δ | dup-v2 | dup-v3 | only-v2 | only-v3 | px/qty/side mismatch | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ADA/USD | 1,480 | 1,481 | -1 | 569 | 0 | 2 | 3 | n/a | **FAIL** |
| ATOM/USD | 318 | 318 | +0 | 150 | 0 | 0 | 0 | n/a | PASS |
| AVAX/USD | 421 | 422 | -1 | 143 | 0 | 1 | 2 | n/a | PASS |
| BTC/USD | 9,093 | 9,148 | -55 | 5,170 | 0 | 2 | 57 | n/a | **FAIL** |
| DOGE/USD | 0 | 1,322 | -1,322 | 0 | 0 | 0 | 1,322 | n/a | **FAIL** |
| DOT/USD | 965 | 967 | -2 | 95 | 0 | 0 | 2 | n/a | PASS |
| ETH/USD | 3,304 | 3,309 | -5 | 1,399 | 0 | 1 | 6 | n/a | **FAIL** |
| LINK/USD | 869 | 869 | +0 | 390 | 0 | 0 | 0 | n/a | PASS |
| LTC/USD | 1,002 | 1,003 | -1 | 403 | 0 | 1 | 2 | n/a | PASS |
| SOL/USD | 3,079 | 3,083 | -4 | 1,117 | 0 | 1 | 5 | n/a | **FAIL** |
| XDG/USD | 1,320 | 0 | +1,320 | 593 | 0 | 1,320 | 0 | n/a | **FAIL** |
| XRP/USD | 7,241 | 7,245 | -4 | 3,790 | 0 | 2 | 6 | n/a | **FAIL** |

Totals: 29,092 v2 records, 29,167 v3 unique ids, 13,819 v2 id collisions,
**0 `dup-v3` across all 29,167 records** — spanning both v3 reconnects, which is
the evidence that `"snapshot": false` on the `trade` subscription
(`services/capture-rust/src/exchanges/kraken.rs`) does what it says. Excluding
the Dogecoin naming row below: 10 `only-v2` and 83 `only-v3` on 29,092 records.

**Every non-zero divergence, attributed.** The retirement trigger asks for *"any
divergence explained rather than tolerated"*, so each one is named here:

1. **The 451 Coinbase `only-v3` ids are v2's reconnect.** The v2 Kotlin handler
   dropped its Coinbase socket at `15:02:13.728Z` and was back at `15:02:20.174Z`
   — **6.4 s blind**. **415 of the 451** carry an exchange timestamp inside minute
   `15:02`. `docker logs k2-feed-handler-coinbase`; the reconnect table for both
   tiers is in [`../../scripts/parity/README.md`](../../scripts/parity/README.md).
2. **The 83 Kraken `only-v3` cluster at v2's three reconnects** — `14:54:12.117Z`
   (6.9 s), `15:43:59.701Z` (6.5 s), `16:11:07.403Z` (6.5 s). v2's fixed 5 s
   backoff is why its gaps are ~3× v3's.
3. **The 10 Kraken `only-v2` are v3's two reconnects** — `15:01:55.617Z` and
   `15:55:48.305Z`, **2.0 s each**, both the venue closing the socket, neither
   correlated with a gap, checksum failure or resync. This is the one column that
   counts against v3, and it totals ten trades in two hours.
4. **`XDG/USD` vs `DOGE/USD` is a v2 bug this comparison surfaced**, not noise.
   The Kotlin normaliser predates `config/instruments.yaml` and emits Kraken's
   native `XDG/USD` as the canonical symbol; v3 resolves it through the registry.
   The same ~1,321 Dogecoin trades therefore appear as one all-`only-v2` row and
   one all-`only-v3` row. The script does not fold them together on purpose.
5. **The `dup-v2` / `dup-v3` columns are venue re-sends, not tier divergence.**
   Coinbase re-sent trade `69662829` (exchange `time` `15:31:09.261488Z`) 21 m 49 s
   later at `15:52:58.890Z` on the **same connection** — `conn_id` `8e980ee6-…`,
   zero reconnects — and *both* tiers wrote it to their topic twice, byte-identical
   to the first copy. Over the window 2,533 v3 and 1,538 v2 Coinbase ids arrived
   more than once and every copy agreed with its own first copy. On Kraken,
   `dup-v2` is the synthesised-id collision above and has no v3 counterpart.

**Maintainer decision, 2026-08-26, recorded as a decision rather than a
finding:** *"the gate is satisfied by explanation, not by a rerun — every
divergence is attributable to the v2 tier or the venue, none to v3; a
'reconnect-free window' would be selecting the sample to flatter v2."* Two of the
three tables carry red rows and they stay red in this record: the trigger was
written as *explained, not tolerated*, and re-running until a window came back
green would have been the tolerating.

**Sequence gaps, checksum failures and resource use over the same window:**

Same window, same binary. Every figure is Prometheus's own
`increase(<counter>[7200s])` evaluated at `2026-08-26T16:15:00Z` — its
extrapolation over the full two hours, not a hand count — divided by 7200 s
where a rate is shown. The per-stream breakdown is in
[`../../services/capture-rust/README.md`](../../services/capture-rust/README.md)
§ *Measured*.

| Metric | Query | Binance | Coinbase | Kraken |
|---|---|---:|---:|---:|
| Messages in | `sum(increase(k2_capture_messages_total[7200s]))/7200` | 306.2 /s | 167.1 /s | 947.4 /s |
| Records enqueued, 2 h (`raw`+`trade`+`book`) | `increase(k2_capture_records_produced_total[7200s])` | 3,860,314 | 1,569,206 | 6,929,394 |
| Produce errors, all four `reason`s | `increase(k2_capture_produce_errors_total[7200s])` | **0** | **0** | **0** |
| Sequence gaps | `increase(k2_capture_gaps_total[7200s])` | **0** | **0** | **0** |
| Checksum failures | `increase(k2_capture_checksum_failures_total[7200s])` | n/a — venue publishes none | n/a | **0** |
| Resyncs | `increase(k2_capture_resyncs_total[7200s])` | **0** | **0** | **0** |
| Reconnects, `involuntary` / `scheduled` | `increase(k2_capture_reconnects_total[7200s])` | 0 / 0 | 0 / 0 | **2** / 0 |
| Container restarts | `docker inspect -f '{{.RestartCount}}'`, all three `healthy` | 0 | 0 | 0 |

**Zero alerts fired** over the window: `count(ALERTS{alertstate="firing"})` as a
`query_range` over 14:15Z–16:15Z returns an empty result. Both Kraken reconnects
were the venue closing the socket cleanly (`docker logs k2-capture-kraken`,
`reconnecting wait=500ms` at `15:01:55.617Z` and `15:55:48.305Z`); the CRC32-verified
`book` stream and `trade` sequencing came back clean on both, which is why gaps,
checksums and resyncs are all zero next to a non-zero reconnect count.

Exchange→recv, trades only, from
`histogram_quantile({0.5,0.95,0.99}, sum by (job, le) (rate(k2_capture_exchange_to_recv_seconds_bucket[7200s])))`
at `16:15:00Z`. **This is venue clock skew plus the internet path to this host,
not a platform-internal latency**, and nothing here is a latency claim:

| Exchange | p50 | p95 | p99 |
|----------|----:|----:|----:|
| Binance | 68 ms | 99 ms | 224 ms |
| Kraken | 178 ms | 247 ms | 494 ms |
| Coinbase | 193 ms | 474 ms | 2,297 ms |

**Resource use, and the number the retirement was ultimately about.**
`docker stats --no-stream` at `2026-08-26T16:15:00Z`, both tiers running side by
side on the same host in the same two minutes:

| Container | %CPU (of 1 host core) | RSS | Limit |
|---|---:|---:|---:|
| `k2-capture-binance` | 3.14 % | **10.54 MiB** | 256 MiB |
| `k2-capture-kraken` | 3.22 % | **12.02 MiB** | 256 MiB |
| `k2-capture-coinbase` | 3.57 % | **30.65 MiB** | 512 MiB |
| `k2-feed-handler-binance` (Kotlin) | 3.05 % | 158.3 MiB | 512 MiB |
| `k2-feed-handler-kraken` (Kotlin) | 1.39 % | 144.3 MiB | 512 MiB |
| `k2-feed-handler-coinbase` (Kotlin) | 1.58 % | 151.7 MiB | 512 MiB |

Three JVMs at **144–158 MiB** against three Rust processes at **10.5 / 12.0 /
30.7 MiB** — **15.0× / 12.0× / 5.0×** smaller. Coinbase's is the narrowest ratio
because its full-depth `level2` book (140,120 levels at window end) is the only
one of the three big enough to show in a heap. CPU is not comparable across the
two rows: the tiers carry different quotas (0.25 vs 0.5 CPU) and neither is near
its own. Summing the `%CPU` column above, the three capture containers came to
**0.0993 CPU combined** at their observed rates, against the 1.5 CPU ceiling this ADR's *Revisit when* names.

**Injected failures, same day, 16:42Z–16:57Z.** The window says nothing about
what breaks, so the failure paths were injected rather than argued
([`../../scripts/chaos/README.md`](../../scripts/chaos/README.md); results in
`scripts/chaos/results/2026-08-26.tsv`, rows written up in
[`../architecture/failure-modes.md`](../architecture/failure-modes.md)):

| Injection | Expected alert | Fired after | Recovered after |
|---|---|---:|---:|
| `capture-kill.sh --exchange kraken --hold 150` | `CaptureDown` | 119 s | 3 s |
| `capture-pause.sh --exchange coinbase` | `CaptureDown` | 165 s | 28 s |
| `capture-pause.sh --exchange binance` | `CaptureDown` | 152 s | 30 s |
| `capture-queue-full.sh --exchange kraken` | `CaptureProduceErrors` | 256 s | 0 s |
| `redpanda-stop.sh --exchange kraken` (warm) | `CaptureProduceErrors` | — | 14 s |

Every alert fired and every container recovered without manual intervention. Two
results are worth the space they take: the queue-full run **caught a capacity
prediction wrong** (below), and `redpanda-stop.sh` produced no independent
time-to-alert because `CaptureProduceErrors` was still firing from the queue-full
run a minute earlier — the two scripts want spacing by the alert's `for: 5m`, and
that is a defect in how the runs were sequenced, not in the alert. A 45 s broker
stop lost **7,821 kraken records**; the 388 s one lost **231,744**. The
`--cold-start` registry case is written and **not yet run**, and
`capture-corrupt-frame.sh` was skipped in this batch — two of the six rows in the
results file are therefore uninjected.

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

### What the prediction got wrong

Three numbers this ADR and its neighbours predicted were scored by the window and
the chaos run. Two of them were wrong, and they are worth more than the ones that
were right. The first — capacity-model.md's **204 s** of producer slack before a
kraken record is lost to a broker outage, measured at **102 s** with zero
`reason="queue_full"` — is written up above under
[*Measured correction, 2026-08-26*](#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)
and is not repeated here. The other two:

#### Binance `depth20` runs at 88.2 frames/s, not the predicted 120

The capacity model derived **120 /s** from `10 partial books/s/symbol × 12
symbols` — the venue's fixed 100 ms cadence, which by construction ticks whether
or not the book moved. Measured over the window,
`increase(k2_capture_messages_total{job="capture-binance",stream="depth20"}[7200s])/7200`
= **88.2 /s**, 27 % under. The premise that the stream is a metronome is what
did not hold; the model over-provisions, which is the safe direction, and the
constant is scored rather than adjusted on one window.

#### Coinbase's latency tail is a cold-connect transient, not a steady state

The `exchange_to_recv` p99 of **2,297 ms** above is real but is not what a
Coinbase connect looks like. On a separate earlier window (12:40Z–13:40Z, a
different binary) the p99 pinned at the histogram's top bucket for ~2 minutes
while ~30 MB of `level2` snapshot frames drained, then fell back under 1 s by
+4 minutes —
`histogram_quantile(0.99, sum by (le) (rate(k2_capture_exchange_to_recv_seconds_bucket{job="capture-coinbase"}[1m])))`,
`query_range` 13:43Z–13:49Z. Any SLO built on a percentile from a window that
contains a Coinbase connect is measuring the snapshot, not the feed.

### What the retirement cost and returned

The budget moved exactly as the Phase C addendum to
[ADR-010](ADR-010-resource-budget.md) predicted: steady state from
16.10 CPU / 23.125 GiB across 18 long-running services to
**14.60 CPU / 21.625 GiB across 15**, the full 1.5 CPU / 1.5 GB the three JVMs
declared, verified by summing `deploy.resources.limits` over
`docker compose --env-file .env.example config` (that ADR's addendum carries the
command and its output). Bootstrap peak is 16.10 CPU / 23.125 GiB across all 19
services — what steady state used to be during the parallel run.

Loaded Prometheus rules go from 27 to 23 (13 v2 + 10 v3 capture) — the three
`FeedHandler*` rules and `ClickHouseBronzeInsertRateLow`. Grafana stays
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

**Five v2 alerts lose their subject, and they are retired on three different
schedules.** Freezing the hot tier freezes everything downstream of it: `k2.*`
gains no rows, so ClickHouse's insert rate goes to zero and the offload watermark
cannot advance. An alert that can only ever fire is noise, not monitoring — so
each one goes when the thing it watches goes, and no sooner:

| Alert | Measures | Retired |
|---|---|---|
| `ClickHouseBronzeInsertRateLow` | Kotlin-fed `k2.*` ingest rate, via `ClickHouseProfileEvents_InsertedRows` | **Here.** Archived at `legacy/v2-kotlin/runbooks/clickhouse-v2-ingest-alerts.yml`, with the `clickhouse:insert_rate:5m` recording rule that shared its expression and had no reader |
| `IcebergOffloadLagElevated` | watermark age > 20 min | **Phase D**, with `docker/offload/` |
| `IcebergOffloadLagCritical` | watermark age > 30 min | Phase D |
| `IcebergOffloadThroughputLow` | `rate(offload_rows_total[1h])` | Phase D |
| `IcebergOffloadWatermarkStale` | watermark age > 26 h | Phase D |

The four `IcebergOffload*` rules stay loaded and fire from this PR until the
Phase D PR deletes `docker/offload/` — expected, not incidents, and each
description says so. They are not archived here because unlike the ClickHouse
one they still describe a component that exists; they go when it does.

**The offload's source is frozen from this PR, so Phase D loses a step.** The
plan had Phase D run the old offload beside the new lake ingest and compare. With
`k2.*` gaining no rows there is nothing for the old side to offload, so that
comparison would measure nothing; the maintainer dropped it on 2026-08-27. Phase D
deletes `docker/offload/`, the hadoop warehouse bind mount, and these four rules
together.

**Sequencing, in one line:** `ClickHouseBronzeInsertRateLow` goes here; the four
`IcebergOffload*` rules go in the Phase D PR with `docker/offload/`; the `k2`
database and the six v2 topics go in the Phase E PR. **Revisit when:** the Phase D
PR lands — if those four rules are still loaded after it, this Outcome was
wrong about the sequencing.

**One thing this ADR did not anticipate.** Retiring the handlers freed
`config/instruments.yaml` to carry Kraken's WS **v2** spellings — the registry
had been pinned to `XBT/USD` and `XDG/USD` because the v1 handlers read the same
file, and `kraken.rs` held a two-row alias table to bridge it. Both are gone, so
`native` is once again exactly the bytes on the wire with nothing mapping it.
That was a hidden cost of running the two tiers side by side, and it is only
visible as a saving now that one of them is gone.
