# ADR-031: A governed, self-serve platform layer over the lake

**Status:** Proposed
**Date:** 2026-11-24
**Author:** Rob Scott
**Category:** Architecture (umbrella)

---

## Context

K2 at `abdc804` is a platform one team can run well. The evidence is in the repository: an
exactly-once ingest ([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)), parity at
tolerance zero across three engines
([ADR-029](ADR-029-research-production-parity-contract.md)), a type-2 security master
([ADR-030](ADR-030-scd2-security-master.md)), fault-injection results and measured numbers.

It is not yet a platform a second team could build on. An inventory of the tree says why:

- **A product's definition is scattered.** Columns are in `docker/lake/ddl/lake.sql`,
  identifier fields in four Python modules, wire shape in `schemas/avro/`, schedules in
  `docker/lake/flows/deploy_lake.py`, retention in CLI defaults in
  `docker/lake/maintenance.py`, and the description a reader needs in a hand-kept page,
  `docs/operations/data-catalog.md`. The plan's Ground truth lists six places where two of
  these disagree today. Nothing names an owner.
- **Adding a product means editing the platform.** There is no path for a product author
  that does not go through the ingest code. The nearest thing is a five-place checklist in the
  `schema-change` skill.
- **Access control is one profile.** ClickHouse has a read-only `quant` user. The lake has
  none: Lakekeeper runs unauthenticated and every reader holds the MinIO root keys
  (`docker-compose.yml:621-639`, `docker/lake/init-lake.sh:89`). A market data platform
  normally separates realtime from delayed entitlement; K2 cannot express the difference.
- **Derived marts have no home.** Spark is the lake's only writer and DuckDB serves
  notebooks. A consumer team's SQL model would have to be added to the ingest job.
- **The orchestrator decision has already been reversed once**
  ([ADR-008](ADR-008-eliminate-prefect-orchestration.md)), and what replaced it is a thin
  wrapper: two flows that `docker exec` a script.
- **Compliance is a claim, not a report.** Audits write `lake.audit.checks`, but no control is
  written down, so nothing can state which controls passed yesterday.

Two constraints shape any answer. The single-host budget is nearly spent: 14.60 of 16 CPU
declared, with the bootstrap peak 0.10 over ([ADR-010](ADR-010-resource-budget.md), latest
Outcome), on an engine of 39.16 GiB (`docker info`). And
[ADR-028](ADR-028-non-goals-and-honest-limits.md) lists several of the missing pieces as
deliberate non-goals, each with a revisit trigger, and requires a new ADR to lift one.

---

## Decision

**We will add a governed, self-serve layer to K2 in place, driven by one data contract per
product, because the missing capability is not another pipeline but the means for other teams
to add pipelines safely.** The data product, not the table, is the unit: it has an owner, a
lifecycle, declared inputs and declared access points. Scope: the crypto domain, the single
host. Cloud infrastructure code and catalog-level access control are separate plans and
separate decisions.

This ADR is the umbrella. It decides the shape and reserves the numbers. Each follow-on ADR
makes one decision and lands, Accepted, in the pull request that builds it.

### Follow-on ADRs

| ADR | Decision | Supersedes or lifts |
|---|---|---|
| 032 | Product descriptors and ODCS v3 contracts; the `k2gen` generator; outputs committed, drift fails CI | ADR-028 §11 |
| 033 | Data mesh: four domains; the platform team owns the paved road | |
| 034 | Airflow 3 replaces Prefect; default-profile budget of 20 CPU / 36 GiB | orchestration clauses of ADR-008, ADR-014, ADR-017, ADR-018; the 16 CPU clause of ADR-010 |
| 035 | Trino and dbt for marts; Spark keeps ingest, backfill and compaction | departs from ADR-006 for marts; ADR-028 §6 for SQL serving; scopes ADR-029 §3 |
| 036 | A file-source ingestion pattern: immutable landing, then write-audit-publish where a bad write can wait | |
| 037 | An FX reference domain from ECB reference rates; derivation in dbt, not at ingest | ADR-028 §4 |
| 038 | OpenLineage for lineage, OpenMetadata for the catalog | |
| 039 | Serving as declared access points on one catalog; entitlements enforced on Trino, catalog-level control as the stated target | ADR-028 §8, for that path |
| 040 | A controls catalogue as code | |
| 041 | SLOs and error budgets for consumer-facing products | ADR-028 §10, on its own trigger |
| 042 | Release engineering: contract semver and a changelog | |

Numbers come from this table, not from the highest file in the directory: phases land out of
numeric order. 043 is taken by the venue reference bars plan.

---

## Rationale

- **The contract is the smallest thing that fixes the most.** Ownership, classification,
  retention, SLOs, schedules and catalog entries all need one declared source. Generating
  them from a contract removes the disagreements listed in the plan by construction, and a
  committed output with a CI drift check makes every generated change reviewable in a pull
  request.
- **Extending in place keeps the proof.** The parity checks, the audits and the fault tests
  already exist. A rewrite would discard them; an extension has to keep them green at every
  merge, which is a stronger claim than a new build passing its own tests.
- **Two engines, one boundary.** Spark's ingest is offset-sensitive and already proven.
  Consumer marts are set-based SQL that analysts change often. Giving each its engine, and
  stating the line between them, is the guidance a second team needs.
- **The earlier rejections answered different questions.** ADR-008 rejected Airflow as
  heavier than Prefect under a 16 CPU budget. ADR-006 rejected Trino as a replacement for
  Spark in batch ETL. Here the budget is raised on the record, and Trino is added beside
  Spark for a job Spark was never given.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| A separate repository for the governed platform | Loses the existing gates as a regression suite and splits what a reader has to follow |
| Keep Prefect and add only contracts and dbt | Keeps an orchestrator whose two flows carry no logic, and whose managed equivalent is not the one a cloud migration of this design would use |
| Rewrite the gold layer in dbt | Replaces working, parity-tested code for no new capability. One aggregate is ported as an example instead |
| Hand-written governance documents with no generator | Recreates the disagreement this ADR starts from, one layer up |
| Keep 16 CPU and put Trino and Airflow behind compose profiles | A default `docker compose up` would then show none of the new layer |

---

## Consequences

**Easier:** adding a product (one file); answering who owns a table, who may read it through
Trino and whether it met its SLO.

**Harder:** the stack is larger. More services to start, pin, patch and explain, and a longer
fresh-clone time than the 15 minutes measured in
[fresh-install](../runbooks/fresh-install.md).

**Committed to:** the Open Data Contract Standard v3 as the contract format and the
descriptor schema as a versioned interface; generated files never edited
by hand; every existing gate green at every merge; a default-profile ceiling of 20 CPU /
36 GiB at the bootstrap peak, enforced in CI.

**Risks:** the orchestrator swap touches the system-of-record writer's schedule, so Airflow
runs the ingest in its write-nothing mode first and cuts over only when every slot ran.
Entitlements bind one read path of four; the others stay unauthenticated and the documents
say so. The catalog does not fit the default budget and is opt-in on a larger engine.

**Revisit when:** any follow-on ADR is rejected in its phase (this ADR is then amended by a
successor, not edited); or the default-profile bootstrap total exceeds 20 CPU or 36 GiB; or a
second read path needs entitlements, which means catalog-level auth and a new decision.

---

## References

- Plan: [2026-11-24-governed-self-serve-platform](../plans/2026-11-24-governed-self-serve-platform/README.md)
- Precedent for an umbrella with reserved numbers: [ADR-018](ADR-018-v3-lake-first-rust-capture.md)
- [ADR-006](ADR-006-spark-batch-only.md), [ADR-008](ADR-008-eliminate-prefect-orchestration.md),
  [ADR-010](ADR-010-resource-budget.md), [ADR-028](ADR-028-non-goals-and-honest-limits.md)

---

*2026-11-25: the 20 CPU / 36 GiB ceiling this ADR committed to is superseded by
[ADR-045](ADR-045-ceiling-25-cpu-48-gib.md) (25 CPU / 48 GiB); the revisit trigger on the
default bootstrap total fired that day, when Trino took the peak to 21.30 CPU / 36.812 GiB.*
