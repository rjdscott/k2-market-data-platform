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
| [2026-11-24-governed-self-serve-platform/](./2026-11-24-governed-self-serve-platform/README.md) | Data contracts and a generator, Airflow 3 replacing Prefect, Trino and dbt marts, lineage, entitlements, an FX data product, a controls catalogue, SLOs and release engineering | Phases P0 to P3 executed; P4 to P7 superseded by the two 2026-11-26 plans below; tracked via ADR-031+ and git tags |
| [2026-11-24-venue-reference-bars/](./2026-11-24-venue-reference-bars/README.md) | Venue-published daily bars as an independent product; reconciliation of captured bars against them, published in the catalog; a day-level trust signal | Phases V0 to V2; follows the governed self-serve platform plan |
| [2026-11-26-unified-product-platform/](./2026-11-26-unified-product-platform/README.md) | The domain-agnostic data product platform, to the demo tag: the old pipeline retired; releases, deploy and rules; identity; the run path through ECB FX; serving through SQL and REST; Kraken with a security master; an enriched product across three domains; the catalog site, lineage and usage | Phases `000` to `007`; tag `v3.0.0-beta.1`; implements ADR-056 |
| [2026-11-26-unified-product-platform-to-release/](./2026-11-26-unified-product-platform-to-release/README.md) | The same platform from the demo tag to `v3.0.0`: the remaining connector kinds, Binance and Coinbase, objectives and controls, infrastructure as code for this host, hardening, the release gate | Phases `000` to `005`; tag `v3.0.0`; implements ADR-056 |

**Superseded phases.** The two 2026-11-26 plans supersede phases P4 to P7 of
[2026-11-24-governed-self-serve-platform/](./2026-11-24-governed-self-serve-platform/README.md)
(the FX data product; access, lineage and catalog; controls, SLOs and release; the governance
pack and release gate). That plan's files are dated records and are not edited; its P4 to P7
phase files describe work that will not be built in that form. The decision is
`docs/adr/ADR-056-unified-data-product-platform.md`.
