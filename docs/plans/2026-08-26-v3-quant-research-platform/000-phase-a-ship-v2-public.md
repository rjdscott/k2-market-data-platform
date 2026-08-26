# Phase A — Ship v2 public now (this week)

**Depends on:** none
**Delivers:** Ships v2 to the public repo with doc-accuracy fixes, a CLAUDE.md/skills upgrade, screenshots, and a v3 roadmap section, tagged v2.1.0.
**Exit:** Verify: fresh-clone quickstart still green; link check; grep sweep.

## Scope

0. **Doc-accuracy fixes from adversarial review (31 findings; sonnet, one PR, then re-review by opus):**
   - BLOCKER: `docs/development/setup.md:31-47` "apply schema by hand" block → DDL auto-applies from `docker/clickhouse/ddl/01-k2-schema.sql`; delete the 8 stale "iceberg-scheduler scrape commented out / 9 alerts blind" blockquotes (`docs/architecture/README.md:145`, `MIGRATION-JOURNEY.md:128`, `platform-principles.md:59`, runbooks `failure-recovery:111`, `iceberg-offload-{failure,lag,performance}:13`, `iceberg-scheduler-recovery:17`).
   - HIGH: remove invented `FeedHandlerDown` "cannot fire" gap (`observability.md:48-51,76,167`, `failure-recovery.md:81`); dedupe `FeedHandlerDown`/`FeedHandlerMetricsDown` (delete the duplicate, fix description, headline becomes **17 alert rules** everywhere); repoint all `docker/clickhouse/schema/` references to `ddl/01-k2-schema.sql` (`README.md:187`, `architecture/README.md:118`, `clickhouse-database-standard.md:41-47`, `adding-new-exchanges.md`); recompute budget once from compose — **14 long-running + 2 one-shot, 15.1 CPU / 21.875 GB, headroom 0.9 / 18.1 GB** — propagate to README, architecture, docker-resources, decisions/README, platform-principles, operations/README, cost-model, positioning, legacy/v1/README, ADR-010/016 outcomes, compose summary comment; `clickhouse:query_duration_p99` → `_mean`; delete "no gradlew" claims (`docs/README.md:35`, `technology-stack.md:36`); "Hadoop catalog on MinIO" → bind-mounted local warehouse (`decisions/README.md:3,19`, `ADR-007:253`, `ADR-006:263`).
   - MED/LOW: README quickstart add `set -a && . ./.env && set +a`; README mermaid bronze+silver+gold → Spark, `:9363` on CH; `docker/README.md:14,29`; one-shot count `architecture/README.md:209`; `docker-resources.md:3-4,73`; Phase 7 status reconcile (`MIGRATION-JOURNEY.md:50,129` vs README); 2 broken anchors `#scenario-1-worker-not-running`; `testing.md:10,39`; `latency-budgets.md:32` rebase to ~150 msg/s; `prefect-schedules.md:49` TTL; `technology-stack.md:45`; `observability.md:17-23,88`; `decisions/README.md:3` wording; `streaming-sources.md:113` 34 instruments; `CLAUDE.md:73-75`; `README.md:114` count.
   - Gate: re-run the adversarial review (same prompt) → zero BLOCKER/HIGH; relative-link + anchor check clean.
0b. **CLAUDE.md upgrade + skills (borrow from `../sailflow`):** rewrite `CLAUDE.md` in sailflow's shape — thin, honest ("none of these are enforced; main unprotected by ADR"), tiered doc surfaces (Tier 0 ADRs+runbooks, Tier 2 audits/benchmarks since public), one-skill-per-surface pointing at each surface's README, verification habits ("verify or drop", "revisit when: metric/date/event"), project guardrails (numbers need provenance; schema changes move Avro+CH+Iceberg together; never commit session logs). Add `.claude/skills/`: `adr` (K2 template `ADR-NNN-slug.md`, sections Context/Decision/Rationale/Consequences/Outcome/Related, immutability + supersession, updates `docs/adr/README.md`), `runbook` (K2 shape: symptom/detection/steps/measured MTTR/last-verified), `schema-change` (Avro + registry compat + CH DDL + Iceberg DDL + docs in lockstep, checklist), `benchmark-report` (dated numbers snapshot `docs/benchmarks/<date>.md`, every number traceable to a command), `release-check` (fresh-clone verify: clone → `make up` → health → smoke queries → README numbers hold), `audit` (point-in-time surface audit, severity-coded, verify-or-drop). Skip `plan` (conflicts with no-progress-files rule). Brief `.claude/README.md` explaining the AI-assisted workflow for reviewers. Keep `.claude/settings.local.json` ignored.
1. Merge PR #65 (monitoring/UI fixes) after CI green.
2. Commit screenshots `docs/images/{grafana-pipeline-overview,redpanda-console-topics,prefect-deployments}.jpg`; replace README `<!-- screenshot -->` comments with images (pipeline overview in README; others in `docs/operations/observability.md`). Fix README diagram (bronze+silver+gold → Spark; Exchanges colour; registry).
3. Add **"Where v2 falls short / v3 roadmap"** section to README + `docs/architecture/README.md` (the 7 gaps, the v3 target diagram above, link to ADR-018..); ADR-018 `Proposed: v3 lake-first, Rust capture` as the umbrella ADR. Tag `v2.1.0`.
4. User flips visibility; enables secret scanning/push protection/Dependabot alerts.
Verify: fresh-clone quickstart still green; link check; grep sweep.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.

_Phase A landed 2026-08-26 — commits 6428563, a583483, tag v2.1.0._
