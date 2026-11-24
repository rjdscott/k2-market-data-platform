# Plans

Design documents for multi-phase work: what is being built, in what order, and
the exact command that proves each phase landed. One directory per plan,
`YYYY-MM-DD-<slug>/`, dated when it was authored: a `README.md` (context,
decisions, ground truth, target architecture, phases table, verification,
risks) plus one `NNN-<slug>.md` file per phase, `NNN` zero-padded from `000`
and never renumbered.

## Conventions

- **A plan is a design document, not a tracker.** No status tables, no
  checkboxes, no progress logs — this repo does not commit progress artefacts
  (see `CLAUDE.md`). Status lives in ADR statuses, git tags and CI.
- Sections in order: Context · Decisions (user-confirmed) · Ground truth
  (verified facts with `file:line` citations) · Target architecture (Mermaid) ·
  Phases · Verification · Risks.
- Every phase is one PR-sized, independently verifiable slice, ordered by
  dependency then risk, and ends with **Exit:** criteria plus runnable
  verification commands.
- A plan executes decisions already recorded in `docs/adr/`; it cites
  ADRs, it does not re-litigate them.
- When a phase lands, append one dated line to it —
  `_Phase C landed 2026-09-14 — commit a1b2c3d, tag v3.0.0-capture._` — and
  nothing else. Phases are never renumbered.
- Use the `/plan` skill; it enforces the above.

## Index

| Plan | Scope | Status |
|------|-------|--------|
| [2026-08-26-v3-quant-research-platform/](./2026-08-26-v3-quant-research-platform/README.md) | v3: Rust capture tier, lake-first Iceberg system of record, ClickHouse as derived hot tier, DuckDB query layer | Phases A–G; tracked via ADR-018+ and git tags |
| [2026-11-24-governed-self-serve-platform/](./2026-11-24-governed-self-serve-platform/README.md) | Data contracts and a generator, Airflow 3 replacing Prefect, Trino and dbt marts, lineage, entitlements, an FX data product, a controls catalogue, SLOs and release engineering | Phases P0 to P7; tracked via ADR-031+ and git tags |
