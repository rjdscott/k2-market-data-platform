# ADR-028: Non-goals and honest limits of a single-host research platform

**Status:** Accepted
**Date:** 2026-08-28
**Author:** Rob Scott
**Category:** Scope

---

## Context

Every v3 ADR was decided inside the same constraints, and each restated the part of them
it needed. [ADR-018](ADR-018-v3-lake-first-rust-capture.md) carries a four-bullet non-goal
list; [Q9](../research/2026-08-26-v3-requirements-clarification.md#q9--scale-target)
repeats it to argue that a budget constraint is not an architectural ceiling;
[ADR-027](ADR-027-book-snapshot-and-sequencing.md) spends a paragraph on what 1 Hz cannot
see; [ADR-029](ADR-029-research-production-parity-contract.md) points at
[the replay fidelity limits](../research/2026-08-28-replay-fidelity-limits.md) so that a
parity contract is not mistaken for a claim about the market; chapter
[01](../architecture/01-what-k2-is.md) has a four-bullet "what it is not" for readers who
read nothing else.

Four partial lists in four documents is how a limit gets quietly dropped. The failure this
ADR is written against is not a reader misunderstanding the platform — it is a *future
change* that crosses one of these lines without anyone noticing there was a line, because
the sentence that drew it lived in an ADR about something else. A non-goal with no revisit
trigger is also indistinguishable from an oversight, and an oversight is what gets fixed
by accident.

The forces are the ones every other v3 ADR names: one host, 16 CPU / 40 GB
([ADR-010](ADR-010-resource-budget.md)) — as deployed, 15 long-running services at
**14.60 CPU / 25.625 GiB** of declared limits
([docker-resources.md](../operations/docker-resources.md)); public WebSocket feeds over the
open internet, measured venue-to-receive **42.2 ms p50 / 206.8 ms p99** on Binance and
higher on the other two
([2026-08-27](../benchmarks/2026-08-27.md#latency--exchange-timestamp--k2-receive)); one
maintainer; and an archive growing at **≈ 9.8 GB/day** on disk, with **≈ 60 days** of runway
at the capacity model's 10.4 GB/day ([2026-08-27](../benchmarks/2026-08-27.md#lake), § Disk).

---

## Decision

**We will keep one list of what K2 deliberately does not do, each entry carrying the cost
or fact that decided it, a concrete trigger that would reopen it, and the nearest thing the
platform does offer — because a limit stated once, in the ADR that happened to hit it, is a
limit that will be crossed by a change that never read that ADR.**

Scope: the platform as built through Phase G. Every entry below is a *decision*, not unfinished
work. The unfinished work is in
[16-failure-modes.md](../architecture/16-failure-modes.md) and the "Not wired up" section of
[observability.md](../operations/observability.md#not-wired-up), and it is being closed. These
are not.

No diagram: this is a list, and a box labelled "out of scope" drawn around it would carry no
information the list does not.

---

## The non-goals

### 1. Not highly available: one host, no replication, no failover

One broker, one ClickHouse, one MinIO, one Spark, one Prefect, one disk. HA here would mean
three of each stateful service inside 16 CPU / 40 GB, and the honest version — a second host —
does not exist. What is bought instead is *recoverability*, measured: restart recovery of
**0–14 s across five injected capture faults**
([`scripts/chaos/results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv)) and
**37–42 s across four lake faults** ([2026-08-27](../benchmarks/2026-08-27.md#mttr)). Nothing
survives a dead disk: the archive is the system of record and it has one copy.

- **Nearest thing K2 offers:** rebuild-from-lake as a first-class, timed path — every derived
  layer and the whole hot tier are functions of `raw.messages`
  ([ADR-025](ADR-025-clickhouse-derived-hot-tier.md),
  [09](../architecture/09-lake-layers.md)) — and the bronze rebuild of the entire archive
  measured **520 s for 61,888,291 rows** ([2026-08-27](../benchmarks/2026-08-27.md#lake)).
- **Revisit when:** a second host or an AWS account is provisioned (Q9's own trigger), or a
  disk failure has actually cost an archive no venue can re-serve.

### 2. No L3: no order ids, no queue position, no cancel attribution

Public L2 feeds publish price-level aggregates. The L3 feeds that carry order ids are not
subscribed on any of the three venues, and on two of them no public equivalent exists. This is
a property of the *feed*, not of the pipeline, so no amount of capture work recovers it.

- **Nearest thing K2 offers:** depth to level 20 and per-second BBO, plus the completeness
  evidence to say what is missing rather than assume it — trade-id holes, sequence gaps and
  checksum results as rows in `silver.*` and `audit.checks`. Kraken's checksum passed
  **0 failures / 14,118,820 frames checked**
  ([2026-08-27](../benchmarks/2026-08-27.md#ingestion--capture-tier)).
- **Revisit when:** an L3 feed is subscribed on any venue — the trigger already recorded in
  [the fidelity limits](../research/2026-08-28-replay-fidelity-limits.md#outcome).

### 3. No full-depth, sub-second book as a queryable product

Top-20 at 1 Hz is the product; the deltas are the record. The argument, the per-venue
sequencing table and the plain statement of what 1 Hz cannot see are
[ADR-027](ADR-027-book-snapshot-and-sequencing.md) and the fidelity limits, and are not
restated here. The one number that decided it: a single Coinbase BTC-USD opening snapshot is
**5,195,904 bytes across 43,974 levels**
([ADR-018 Appendix A, S5](ADR-018-v3-lake-first-rust-capture.md#appendix-a--phase-b-verify-first-spikes-2026-08-26)),
before deltas, before 34 instruments.

- **Nearest thing K2 offers:** `k2-capture replay --depth N --interval-ms M` over a pinned
  snapshot — a top-50 at 100 ms is a command over the archive, not a table anyone pays for
  daily ([ADR-027 Outcome](ADR-027-book-snapshot-and-sequencing.md#outcome)).
- **Revisit when:** `k2_capture_book_levels_total{exchange="coinbase"}` exceeds 80,000, or a
  question in `notebooks/` needs sub-second book state and the replay path proves too slow to
  answer it (ADR-027's triggers, unchanged).

### 4. Crypto only: three venues, 34 instruments, 23 canonical symbols

Binance 12, Kraken 11, Coinbase 11 ([`config/instruments.yaml`](../../config/instruments.yaml)),
mapping to 23 canonical symbols. No equities, futures or FX path is designed: those feeds are
paid and session-bound, and carry corporate actions, auctions and halts that no layer here
models. The venue count is not the expensive part — a fourth exchange costs **0.25 CPU / 256 MB**,
one container ([docker-resources.md](../operations/docker-resources.md#sizing-a-new-service)).
The asset class is.

- **Nearest thing K2 offers:** cross-venue work on one `canonical_symbol`, with the
  native → canonical mapping held as data rather than code, so a fourth crypto venue is a
  config entry and a container ([adding-new-exchanges.md](../operations/adding-new-exchanges.md)).
- **Revisit when:** a question in `notebooks/` needs an instrument no configured venue lists,
  or a fourth venue's tables fit inside the disk runway measured in § 12.

### 5. Public feeds only: no authenticated channels, no venue REST backfill

No API keys, no private order flow, no paid market data, and no historical backfill from any
venue's REST endpoint. Two reasons, one of each kind. The cost: a key is a secret to rotate and
an entitlement to track, on a stack with no TLS and passwords in `.env`
([SECURITY.md](../../SECURITY.md)). The fact: a REST backfill is a *different record* from a
WebSocket frame — different shape, different timestamps, no `recv_ts_ns`, no `conn_id` — so
archiving one would plant rows in `raw.messages` that look verbatim and are not. That is the
same failure [Q7](../research/2026-08-26-v3-requirements-clarification.md#q7--v2-data-migrate-the-existing-clickhouse-and-iceberg-data-into-the-lake)
rejected for v2's data. The history therefore starts when capture started, and gaps stay gaps.

- **Nearest thing K2 offers:** gaps that are *visible* — sequence gaps, trade-id holes and
  offset continuity are audited and queryable, so a hole is a row rather than a silence.
- **Revisit when:** a venue withdraws from its public feed a stream K2 depends on, or adds a
  venue timestamp to a stream that lacks one today (Binance's book stream carries none).

### 6. No query API, no serving layer beyond ClickHouse SQL

[ADR-005](ADR-005-kotlin-spring-boot-api.md) designed one, scored it **3/10 ROI**, and it was
never built. Nothing has needed it since: the consumers are Grafana, `clickhouse-client` and
DuckDB notebooks, and all three speak SQL. An HTTP API for those is a second copy of the query
surface plus auth, pagination and a deployment.

- **Nearest thing K2 offers:** the `gold` database in ClickHouse for the head and DuckDB over
  the lake for the record, reading the same tables
  ([10](../architecture/10-clickhouse-gold.md), [`notebooks/`](../../notebooks/README.md));
  candles are computed on read, so the nearest thing to an endpoint is a table function.
- **Revisit when:** a consumer that cannot open a database connection needs the data — a
  browser or mobile client, or a service on another host.

### 7. No simulation or execution engine

K2 produces data and never fills. The archive cannot supply an execution model — no queue
position, no hidden or iceberg liquidity, no auctions or halts, and a receive clock that mixes
venue processing with internet transit inseparably in any single row — and a backtester built
on it would be quietly asserting all four.
[The fidelity limits](../research/2026-08-28-replay-fidelity-limits.md) list what that rules
out, in words, so nobody has to infer it from a schema.

- **Nearest thing K2 offers:** signal research with the execution model stated and defended by
  the researcher — features from unsampled trades and 1 Hz books, event bars at any threshold,
  every number reproducible from a snapshot id
  ([ADR-029](ADR-029-research-production-parity-contract.md)).
- **Revisit when:** an execution model is written down and defended in `notebooks/` and its
  inputs are shown to exist in the archive — which today means § 2 has to move first.

### 8. No multi-tenancy, and no auth beyond the read-only `quant` user

One maintainer, one host. There are two identities: `default` for the pipeline and `quant` for
research — `readonly=2`, 3 GiB and 2 threads per query, a 300 s cap, `gold` only
([`docker/clickhouse/users.xml`](../../docker/clickhouse/users.xml)). That is a *blast-radius*
control, not an access-control system: it exists so a backtest cannot take the server down, not
so two people can be told apart. There is no TLS anywhere, every service authenticates with a
password from `.env`, and the ports bind to localhost ([SECURITY.md](../../SECURITY.md)).

- **Nearest thing K2 offers:** the `quant` profile, and a catalog that already has the concept
  if the lake ever needs one ([ADR-023](ADR-023-lakekeeper-rest-catalog.md)).
- **Revisit when:** a second person needs an account, or any port is exposed off localhost. That
  is the same trigger branch protection has in [CLAUDE.md](../../CLAUDE.md): a second contributor.

### 9. No cloud deployment: the scale-out path is designed, not exercised

Every endpoint, region, path-style flag and catalog URI is environment-driven, and
[17](../architecture/17-scale-out-path.md) maps all ten tiers to AWS at 200× today's rate. None
of it has been deployed, benchmarked or costed: there is no account, and
[Q9](../research/2026-08-26-v3-requirements-clarification.md#q9--scale-target) put the
deployment out of scope. That page exists to keep the single-host code honest — a hard-coded
endpoint fails it — not to claim AWS behaviour that cannot be checked from here.

- **Nearest thing K2 offers:** the per-tier mapping table and the eight-variable flip list, every
  claim scoped to *this repository's code* rather than to a cloud.
- **Revisit when:** an AWS account or a second host is provisioned (Q9's trigger). Until then
  every figure on that page stays labelled "designed, not exercised".

### 10. No SLA on freshness, and no error budgets behind the stated targets

Targets exist — exchange → silver p99, lake ingest lag, commit freshness, audit failures, MTTR —
in [observability.md § SLOs](../operations/observability.md#slos), with alerts that fire on them.
Error budgets do not: there is no burn-rate alert and no `docs/operations/slos.md`.
[Q4](../research/2026-08-26-v3-requirements-clarification.md#q4--what-does-the-platform-promise-about-itself)
specified three SLOs, and Phase F was to derive them from a 24 h burn-in that has not been run —
the longest recorded window is **6 h 30 min** ([2026-08-27](../benchmarks/2026-08-27.md)).
Nothing routes anywhere either: Alertmanager is unconfigured, so an alert is a red row in a UI a
human has to be looking at ([11](../architecture/11-observability.md)). On a single host these
would be objectives rather than guarantees in any case — one reboot spends a month's budget in
one event.

- **Nearest thing K2 offers:** 28 alert rules as code with thresholds sized from measurement, 22
  of them carrying a runbook annotation and 17 a unit test, and staleness measured by ageing a
  timestamp rather than reading a gauge that would freeze during the outage it backstops.
- **Revisit when:** a continuous window of ≥ 24 h is recorded in `docs/benchmarks/` — that is the
  input the objectives were always waiting on.

### 11. No schema-evolution automation

Contracts move by hand, together or not at all: Avro, ClickHouse DDL, lake DDL, the ingest
projection, the docs and the tests, in one PR. The registry enforces `BACKWARD_TRANSITIVE`
globally ([`docker/redpanda/init.sh`](../../docker/redpanda/init.sh)), evolution is
add-nullable-only at every layer with `raw.messages` frozen
([13](../architecture/13-schema-design.md)), and `tests/test_wire_format.py` fails when the Avro
schema and the DDL drift apart. A migration tool would be a framework for three records changed
a handful of times — and it would not catch the failure that actually happens here, which is a
half-migrated contract failing silently at the ingest boundary rather than at build time.

- **Nearest thing K2 offers:** the `/schema-change` skill (the rule as a procedure) and
  `tests/test_wire_format.py` (the same rule as a failing test).
- **Revisit when:** a change is needed that `BACKWARD_TRANSITIVE` cannot express — a type
  narrowing or a field removal — or a drift reaches the lake with that test green.

### 12. No retention policy: raw is kept forever, and the disk is the only limit

`raw.messages` has no TTL and no row is ever deleted
([`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql),
[ADR-021](ADR-021-raw-first-archive-and-lineage.md),
[Q8](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host));
gold in ClickHouse likewise has no TTL
([ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)). A 30- or 7-day TTL was
considered and rejected: it bounds the disk problem and unbounds a worse one, because the replay
window becomes the TTL and the archive stops being the system of record for anything older.
There is no tiering either — no Glacier, no cold storage class — because there is one disk. So
the policy is arithmetic and an alert: **≈ 9.8 GB/day** measured over 14.9 h of archive,
**≈ 630 GB** reusable, **≈ 60 days** at the capacity model's 10.4 GB/day
([2026-08-27](../benchmarks/2026-08-27.md#lake), § Disk).

- **Nearest thing K2 offers:** an 80 % disk alert with an operator escape hatch — expand the
  disk, or decide — and per-table storage measured rather than assumed.
- **Revisit when:** `k2_lake_disk_used_ratio` crosses 0.80, which at the measured rate is a dated
  event rather than a hypothetical.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Do it all** — HA, L3, full-depth deltas, an API, cloud, SLOs with error budgets | Priced, not hand-waved: HA is 3× the stateful services inside 16 CPU / 40 GB; full-depth L2 starts at a 5.2 MB opening snapshot for one symbol; an L3 subscription does not exist publicly on two of three venues; the cloud path has no account behind it. Attempting all of them on this budget produces a platform that does each badly and none provably — the opposite of the property this repo exists to demonstrate. |
| **Say nothing** — ship the platform and let the limits be inferred | The status quo, and how four partial lists in four documents happened. A limit nobody wrote down reads as an oversight, so it gets "fixed" by a change that never priced it; and a reader infers capability from a schema, where an `Array(Float64)` of 20 levels *looks* like a book. Silence also makes a wrong claim cheap to publish: nothing stops a number quoted off the 1 Hz product from being read as microstructure. |
| **A `LIMITATIONS.md` at the repo root** rather than an ADR | No revisit triggers, no supersession semantics: a limit lifted would be a silent edit, which is exactly what the immutable-ADR rule exists to prevent. The one property this list needs is that crossing a line leaves a record. |
| **A non-goal section in every ADR** — the status quo, formalised | Restating the same four constraints in every ADR is where drift starts; ADR-027 and ADR-029 already state the 1 Hz limit in different words. One list, cited from many, has one place to be wrong. |

---

## Consequences

**Easier:** answering "can K2 do X" in one place, with the reason and the number; reviewing a
change against a line it might cross, since each entry names its trigger; publishing a research
result with its limits attached, because the words already exist and can be quoted; and saying
no, which is the operation this list makes cheap.

**Harder:** twelve entries is twelve claims that can go stale, and a limit lifted in code without
being lifted here makes this page the wrong kind of authority.
[ADR-027](ADR-027-book-snapshot-and-sequencing.md#outcome) has already had that experience — its
deltas became queryable in Phase E while its Decision still said they were not, and the
divergence was caught by reading, not by a test. Nothing here is enforced by CI: only § 12's disk
ratio and § 3's book-levels bound are metrics, and the other ten triggers are events a human has
to notice.

**Committed to:** this file as the single list, cited from
[01-what-k2-is.md](../architecture/01-what-k2-is.md) rather than recopied into it; a limit lifted
means a new ADR and an appended `Outcome` here, never a silent edit;
and every entry keeping its three parts — reason with a number, revisit trigger, nearest offering
— so that a bare "we don't do that" cannot be added to it.

**Risks:** the numbers age. Most entries cite [2026-08-27](../benchmarks/2026-08-27.md) or the
capacity model, and that benchmark is a 6.5 h window, not a steady state — § 12's disk runway is
the most likely to move, and a benchmark that supersedes it will not update this page by itself.
A subtler risk: a list this confident invites the reading that everything *not* on it is
supported, which is false. The gaps in
[16-failure-modes.md](../architecture/16-failure-modes.md) and
[observability.md](../operations/observability.md#not-wired-up) are the other half of the picture,
and are deliberately not duplicated here.

**Revisit when:** any entry's own trigger fires — each names one — or a new benchmark supersedes
[2026-08-27](../benchmarks/2026-08-27.md), at which point the figures in § 1, § 4, § 10 and § 12
are re-derived against it and this ADR gets an `Outcome` rather than an edit.

---

## References

- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — the umbrella whose four-bullet non-goal list this expands; 028 was reserved in its follow-on table
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — the 1 Hz top-20 product and what it cannot see
- [ADR-029](ADR-029-research-production-parity-contract.md) — the parity contract these limits bound
- [`docs/research/2026-08-28-replay-fidelity-limits.md`](../research/2026-08-28-replay-fidelity-limits.md) — the same limits as research consequences, each with its cause
- [`docs/research/2026-08-26-v3-requirements-clarification.md`](../research/2026-08-26-v3-requirements-clarification.md) — Q4 (SLOs), Q7 (no v2 import), Q8 (retention), Q9 (scale target)
- [`docs/architecture/01-what-k2-is.md`](../architecture/01-what-k2-is.md), [`15-capacity-model.md`](../architecture/15-capacity-model.md), [`17-scale-out-path.md`](../architecture/17-scale-out-path.md)
- [`docs/benchmarks/2026-08-27.md`](../benchmarks/2026-08-27.md) — every measured number quoted above, with the command per row
