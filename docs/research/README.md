# Research

Analysis that happened *before* a decision: the costing, ranking and comparison work that
an ADR then commits to. Research holds the reasoning; ADRs (`../adr/`) hold the verdict;
benchmarks (`../benchmarks/`) hold the measurements that settled it.

## Conventions

- One file per investigation, `YYYY-MM-DD-<slug>.md`, dated when the analysis was done.
- **Research is allowed to be wrong in hindsight.** It is never rewritten to match what
  was eventually decided — the ADR's `Outcome` section carries the correction, and
  [`../MIGRATION-JOURNEY.md`](../MIGRATION-JOURNEY.md) scores the predictions.
- The moment a conclusion becomes a commitment it becomes an ADR. ADRs, plans and audits
  cite research; they do not restate it.
- Estimates are labelled as estimates. A number that was guessed says so.

## Index

| Document | Question | Outcome |
|----------|----------|---------|
| [2026-08-28-replay-fidelity-limits.md](./2026-08-28-replay-fidelity-limits.md) | Byte-faithful replay invites simulation the archive cannot support — what research does top-20 @ 1 Hz over public WebSockets carry, and what does it not? | A limits table, each row naming the property of the data that causes it; cited from ADR-029 and the notebooks README so the limits arrive with the data |
| [2026-08-26-v3-requirements-clarification.md](./2026-08-26-v3-requirements-clarification.md) | Four requirements ADR-018 left open — replay scope, estimation discipline, fault injection, SLOs — asked and answered before the v3 phase plan was amended | Rust `k2-replay` through the live adapters; predicted-then-measured capacity model; local `make chaos`; three SLOs with error budgets. Carried into Phases C–G and ADR-029 |
| [2026-08-26-v3-spikes/](./2026-08-26-v3-spikes/README.md) | Retained inputs for the twelve Phase B verify-first spikes behind ADR-018 Appendix A — can each result be re-derived, not just read? | Cargo projects, WS capture scripts, ClickHouse/Lakekeeper compose files and re-run commands per spike; two (S7, S12) needed no files, just public-registry commands |
| [2026-02-09-v2-investment-analysis.md](./2026-02-09-v2-investment-analysis.md) | Which v2 changes are worth doing, ranked by risk against reward, before any code is written? | Produced ADR-001 … ADR-010. Predictions scored in [`../MIGRATION-JOURNEY.md`](../MIGRATION-JOURNEY.md) — the misses were all in the same direction: underestimating what orchestration is worth |
