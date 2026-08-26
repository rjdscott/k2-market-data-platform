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

## Q5 — Cutover authority: who signs off on the destructive steps?

**Asked.** Three steps in the plan are destructive and not reversible from git
alone: retiring Kotlin to `legacy/v2-kotlin/`, removing `docker/offload/` plus
the hadoop warehouse bind mount, and dropping the `k2` database and deleting
the `.raw` JSON topics. Does each of these need a human yes per step, or is
one authorization enough to run the plan unattended?

**Answered: once, now.** Conditional on the plan's own comparison gates for
each step — parity counts, audits green, CI green — the maintainer authorizes
all three cutovers in advance, each landing in its own PR with the comparison
evidence pasted into the PR body. No further approval is sought once a gate
is met.

**Rejected: per-step approval.** It blocks unattended execution for a
decision that was already made — the gates exist precisely so the yes/no is
mechanical, not judgment-per-step. **Rejected: never cut over, run v2 beside
v3 through Phase G.** ADR-010's Outcome section already shows the budget is
tight: steady-state v2 sits at 15.35 CPU, and adding capture (+0.75 CPU) for
a parallel run brings it to 16.10 CPU — over the 16 CPU budget documented
there. Running both stacks side by side through Phase G is not an option this
host has room for; the gated, once-now authorization is what makes a
16 CPU/40 GB single host survive the migration at all.

**Lands in:** Phase C (Kotlin retirement), Phase D (`docker/offload/` +
warehouse removal), Phase E (drop `k2`, delete `.raw` topics) — each cutover
its own PR, comparison evidence pasted, ADR-010 Outcome cited.

---

## Q6 — Burn-in windows: real wall-clock duration, or shortened?

**Asked.** The plan as drafted specifies 24 h per exchange for the Phase C
capture burn-in and 3 days of lake audits for Phase D. Run those for real, or
shorten them for unattended execution?

**Answered: 2 h windows, every published number labelled with its window.**
Every burn-in and audit window across the plan becomes 2 h, and every number
that comes out of one states the window explicitly in the document that
publishes it — "over 2 h on 2026-08-27", never "per day" or a bare rate that
implies a longer observation. The phase files are amended in place to say
2 h rather than 24 h/3 days.

**Rejected: real 24 h windows.** Wall-clock-bound execution with nothing to
verify in between means roughly a week sitting idle waiting on burn-ins
across Phases C–D alone. **Rejected: 24 h for Phase C only, shortened
elsewhere.** A window that changes meaning phase to phase is worse than one
that is short everywhere and says so everywhere — consistency here is what
lets a reader trust the label instead of re-deriving what each number covers.

**Second-order consequences accepted.** Tail behaviour is not observed in any
Phase C/D number: the 23 h Binance scheduled reconnect, daily compaction
seams, and overnight liquidity patterns fall outside a 2 h window by
construction, and every document that publishes a number from these windows
must say so rather than imply completeness. The Phase F SLO error budgets
built on this data are therefore provisional, not final, until a longer
window exists. **Revisit when:** the first 24 h continuous run is performed —
that is the trigger, not a calendar date.

**Lands in:** `002-phase-c-rust-capture.md`, `003-phase-d-lake-tier.md`,
`005-phase-f-notebooks-numbers-docs.md`, this plan's `README.md` (each
burn-in/audit window reduced to 2 h, labelled); the "Revisit when" trigger
carries into `docs/operations/slos.md` when Phase F writes it.

---

## Q7 — v2 data: migrate the existing ClickHouse and Iceberg data into the lake?

**Asked.** v2's `k2` ClickHouse database and its bind-mounted Iceberg
warehouse hold real captured trades. Is that data worth carrying into the v3
lake, or does the lake start empty?

**Answered: disposable, no migration.** v3's lake starts from nothing;
nothing from v2's `k2` database or its Iceberg warehouse is copied forward.

**Rejected: migrating v2 bronze/silver into the v3 lake.** v2's data was
written by a JDBC offload with no `recv_ts_ns` and no sequence/gap detection
— it is a lossy copy of a lossy copy. Importing it would plant rows in
`raw.messages` that look like the verbatim archive the rest of the platform
is built to trust, while actually carrying none of its provenance guarantees.
That pollutes the one property the lake exists to have — every row traceable
to the exact frame that produced it — for the sake of keeping data that was
never captured to the v3 standard in the first place.

**Lands in:** Phase D (lake starts empty, no v2 import step in scope).

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
