# v3 requirements clarification — four questions before the plan was amended

**Date:** 2026-08-26
**Author:** Rob Scott
**Question:** ADR-018 fixes the *shape* of v3 (lake-first, Rust capture, derived hot tier). Four requirements it deliberately left open had to be settled before the phase plan could be amended: what replay means here, how estimates are held to account, how faults get injected, and what the platform promises about its own availability. This records what was asked, what was answered, and what was rejected — so the plan implements decisions rather than re-deriving them.

> These are requirements clarifications, not measurements. Where a number appears below it is an assumption, labelled as one. The answers were given by the maintainer on the date above; the plan
> ([`../plans/2026-08-26-v3-quant-research-platform/`](../plans/2026-08-26-v3-quant-research-platform/README.md))
> carries them into phases, and the ADRs carry the ones that constrain future work.

---

## Q1 — Replay: what is it for, and who owns the parser?

**Asked.** v3 will hold every frame verbatim in `raw.messages`. Does "replay"
mean (a) a research convenience — reconstruct books and trades in DuckDB from the
archive, in Python, for notebooks; or (b) a production artefact — push the
recorded frames back through the *same* capture code that ran live?

**Answered: (b).** A Rust `k2-replay` in the capture crate, reading
`raw.messages` (or a JSONL fixture) and calling the same
`handle_frame(bytes, recv_ts_ns)` the live path calls, with a virtual clock
driven by the recorded `recv_ts_ns`, and deterministic output — same input, same
bytes out, asserted by content hash in a test.

**Rejected: (a), research-only DuckDB reconstruction.** It is cheaper and it
looks equivalent, and it is not: a second implementation of the parser and the
book state machine means parity between research and production becomes something
*argued* in a document instead of *enforced* by a test. The two implementations
drift on the first exchange quirk one of them handles — and the drift is silent,
because nothing compares them. The whole reason the archive is verbatim is so
that the production parser can be re-run over it; reconstructing separately
discards that. Rejected on the same principle that puts one parser behind live
and replay in the first place.

**Second-order consequences accepted.** Determinism becomes a real constraint on
the capture code, not a nice property: no `HashMap` iteration on emit paths, no
`f64` on the record path, no wall-clock reads outside the frame-receipt stamp.
Those are cheap to hold from day one and expensive to retrofit — which is the
same argument that put `recv_ts_ns` before the parser in ADR-018.

**Lands in:** Phase G (`006-phase-g-replay-parity.md`), ADR-029.

---

## Q2 — Estimation: are capacity numbers predicted, or only reported?

**Asked.** v2's numbers were all measured after the fact. For v3, is a capacity
model worth writing before the system exists, given it will be partly wrong?

**Answered: predict, then measure, and keep the error.**
`docs/architecture/capacity-model.md` is written in Phase C — *before* the first
burn-in sample — with a `predicted` column only: msg/s per stream, msg/s per core
for one capture container, bytes/day per topic and per lake table, headroom
arithmetic against the 16 CPU / 40 GB budget, each row naming the assumption it
rests on. Phase F adds `measured` and `error %` from the 24 h burn-in. Predicted
values are never edited to match reality; a row that was 3× out stays on the
page, with one line naming the assumption that was wrong.

**Rejected: measure-only, publish afterwards.** It produces a document that is
always right and teaches nothing. An estimate that was never written down cannot
be scored, and an engineer who never scores an estimate does not get better at
making them. This repo already does this at the decision level —
[`../MIGRATION-JOURNEY.md`](../MIGRATION-JOURNEY.md) scores the v2 predictions,
and the misses were all in one direction — so extending it to capacity is
consistent rather than novel.

**Assumption to check first (labelled: assumption).** The prediction that most
likely misses is bytes/day after zstd on `raw.messages`: compression ratio on
exchange JSON is guessed, not derived, and it drives both the storage line and
the compaction cadence. That row is expected to carry the largest error.

**Lands in:** Phase C (predicted column, committed before burn-in), Phase F
(measured + error %), ADR-010 outcome update.

---

## Q3 — Fault injection: where does it run?

**Asked.** The failure modes need proof, not prose. CI nightly chaos job, or
local scripts run by the maintainer?

**Answered: local `make chaos`.** `scripts/chaos/*.sh` behind a Makefile target:
kill and pause each capture container, stop Redpanda, pause ClickHouse, stop
Lakekeeper mid-ingest, corrupt a frame. Each script prints the alert it expects
to fire, waits for it, then measures time-to-recovery from the metric that
defines "recovered". The measured recovery time is written back into the row of
`docs/architecture/failure-modes.md` that the script proves — so the FMEA's
recovery column is measured, in the same way runbook MTTRs are measured here
rather than estimated.

**Rejected: a nightly CI chaos job.** The GitHub-hosted runner has ~7 GB of RAM
and the stack budgets ~22 GB; it cannot host ClickHouse, Redpanda, Spark, MinIO
and three capture containers at once, so a "chaos" job there would exercise a
stack shaped nothing like the real one and would fail for resource reasons more
often than for real ones. A flaky nightly that everyone learns to ignore is worse
than a gate someone actually runs. CI keeps what fits it — unit tests, the schema
job, the parity job, the replay hash.

**Consequence accepted, stated plainly.** This makes chaos a maintainer-run gate,
not a continuous one: the guarantee is "these failures were injected and measured
on the dates recorded", not "these failures are injected nightly". The dates go in
the document. Revisit when a self-hosted runner with ≥32 GB exists.

**Lands in:** Phase D (capture, lake, Redpanda targets), Phase E (ClickHouse
targets), `docs/architecture/failure-modes.md`.

---

## Q4 — What does the platform promise about itself?

**Asked.** Alert thresholds already exist. Do they need to become SLOs with error
budgets, on a single-host platform with no HA?

**Answered: three SLOs, with error budgets, burn-rate alerts and runbooks.**
Capture freshness (age of the last message per stream), lake ingest lag, and
hot-tier query latency (`hot.ohlcv` p99 over a 7-day window). Each carries an
objective, a measurement window, the error budget in minutes/month it implies,
and what spending it forces — a freeze on that tier's changes until it recovers.
Alerts are multi-window burn-rate (fast 1 h/5 m, slow 6 h/30 m), each with a
`runbook_url` that resolves.

**Rejected: threshold alerts only.** A bare threshold answers "is it broken right
now" and cannot answer "have we been quietly missing our own target all month",
which is the question that decides whether to spend the next week on features or
on reliability. Thresholds also page on every brief blip and stay silent through a
slow bleed; burn rate inverts both. The cost is real — three SLOs is more rule
YAML and more thinking about what the objective should be — and it is the point.

**Deliberate limit.** These are objectives, not guarantees. One broker, one
ClickHouse, one host: a machine reboot spends a month's budget in one event, and
the SLO document says so instead of implying a redundancy that does not exist.

**Lands in:** Phase F (`docs/operations/slos.md`, `slo-alerts.yml`, runbooks).

---

## Non-goals, reaffirmed

Unchanged from ADR-018 and restated here because every answer above was given
inside these constraints, and each one would be answered differently without them:

- **Not a trading path.** Public WebSocket feeds over the open internet. No
  decision in v3 is latency engineering, and no number published from it should
  be read as a latency claim about a colocated system.
- **Public feeds only.** No authenticated or paid market data, no private order
  flow; `recv_ts_ns` is a receive stamp with internet transit and exchange clock
  skew baked in, inseparable in any single row.
- **Single host, 16 CPU / 40 GB** (ADR-010). This is a stated constraint, not an
  accident of what fit, and it is what makes Q3's answer local rather than
  distributed.
- **No HA.** One broker, one ClickHouse, one MinIO, one host. Recovery is
  rebuild-from-lake, and it is timed rather than assumed.

## What this does not settle

Whether replay's lake reader uses PyIceberg-exported JSONL or reads Parquet
directly in Rust — decided in Phase G on whichever keeps the crate's dependency
set smaller, and recorded in ADR-029. Whether `hot.ohlcv` needs materialising is
still a measurement, not a decision (`004-phase-e-hot-tier.md`: only if the
7-day p99 exceeds 1 s).
