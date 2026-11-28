# Audits

Point-in-time sweeps of one surface: what the repo claims, against what the
stack actually does. This is a public repo, so published claims are audited
rather than trusted — the most common finding is a doc that was true two
refactors ago.

One file per audit, `YYYY-MM-DD-<surface>.md`.

## Conventions

- **Verdict first**, then scope, the audited commit, method, and the findings
  table: `severity | file:line | claim | reality | fix`.
- Severities: `BLOCKER` (misleads a user into a broken state, or a live
  correctness bug) · `HIGH` (user-visible wrong claim or foot-gun) · `MED` ·
  `LOW`. Counts by severity go in the header so audits compare across dates.
- **Verify or drop.** Every finding carries a `file:line` or a command with its
  output. A finding that can't be demonstrated does not ship.
- **Never edited after publication.** Remediation is recorded by appending
  `Resolved in <commit>` lines below the table, and by publishing a *new* dated
  audit — the re-run is the gate (zero BLOCKER/HIGH), not an edit.
- Audits cover a surface. A single diff or PR gets `/code-review` instead.
- Use the `/audit` skill.

## Index

| Date | Surface | Verdict | Findings |
|------|---------|---------|----------|
| [2026-08-26](./2026-08-26-doc-accuracy.md) | Documentation vs the running stack | Headline numbers all slightly wrong; two documents walked a new user into a broken stack | 31 — 2 BLOCKER, 8 HIGH, 13 MED, 8 LOW · resolved in `535997a` |
| [2026-09-03](./2026-09-03-ux-end-to-end.md) | End-to-end user stories across every tool (quant, operator, developer, newcomer), UX and coherence | Only one of the quant story's five requirements worked; four of eight browser UIs were broken outright; a <15-core host fails silently on first run; the parity gate and the doc gate both passed while lying | 87 — 20 BLOCKER, 37 HIGH, 21 MED, 9 LOW · partially resolved in `91e9d6c`, `9932f42`, `9f868b1`, PR #133, PR #134 |
| [2026-11-26](./2026-11-26-p3-trino-dbt-marts.md) | Phase P3 as shipped and running: Trino fences, dbt marts, quality results, the ingest batch audit | The SQL fences hold, but MinIO is on every interface under one weak root key that every catalog shares; any Trino user can roll back the quality record; the parity gate's pin is a day the lake no longer holds; ADR-034's ingest trigger has fired | 24: 1 BLOCKER, 4 HIGH, 9 MED, 10 LOW · partially resolved in #218, #220, #228 |
| [2026-11-26](./2026-11-26-p2-airflow-replaces-prefect.md) | Phase P2 (Airflow 3 replaces the Prefect scheduler) as shipped and running: DAGs, image, alerts, runbooks, run history since the cutover, secrets and retention | The scheduling guarantees hold on 289 runs since the switch, but nothing bounds the Spark event log each run leaves (about 13 GiB a day on the shared disk), `make test` retags the image the live scheduler starts from, and each Airflow container has its own Fernet key | 12: 0 BLOCKER, 2 HIGH, 4 MED, 6 LOW |
| [2026-11-26](./2026-11-26-p0-prerequisites-guardrails.md) | Phase P0 (prerequisites and guardrails) as shipped: the gates, data terms, lock exit code, parity pin, security posture and merge process | P0's Exit criteria hold, but four of its guardrails pass while violating what they guard (the parity pin after 2026-12-03, the known-gap audit row, the synthetic-data gate, required checks that skip admins); the never-expired raw archive has one copy that any container on the engine can delete | 23: 1 BLOCKER, 6 HIGH, 7 MED, 9 LOW · partially resolved in #221, #226, #228, #229, #231 |
| [2026-11-26](./2026-11-26-phase-000-clear-the-ground.md) | Phase 000 of the unified product platform plan (clear the ground) as run on the live stack: the Verification block, what the phase says is gone, what still runs, the drop copy and its manifests, the three runbooks it wrote | The exit holds but for two disclosed items that wait on the clock (the locked scratch bucket, the tag); nothing retired still runs, the 16 drop manifests match the copy on disk, `make gates` passes with five SKIP lines; the run history of the retired DAGs survives only in a dump `make backup` rotates out around 2026-12-08 | 10: 0 BLOCKER, 0 HIGH, 4 MED, 6 LOW |
| [2026-11-27](./2026-11-27-phase-001-release-and-rules-spine.md) | Phase 001 of the unified product platform plan (the release and rules spine) against its plan: the Verification block line by line on the live stack after the cutover to releases, the Scope bullets, the decided deviations, the pinned images, a sample of 12 doc claims | Not yet: the live release verifies (release-verify, identity, health, budget, Slack all pass), but the pinned MinIO image can no longer be pulled, so the phase's CI Trino job has never passed, and five HIGH from the pull request's two reviews are being fixed; three plan lines cannot run as written; four runbooks still describe the stack before the cutover | 19: 0 BLOCKER, 6 HIGH, 5 MED, 8 LOW |
| [2026-11-27](./2026-11-27-phases-002-003-run-spine-and-serving.md) | Phases 002 and 003 of the unified product platform plan (the run spine through ECB FX; serving and teaching) as built on branch `phase/002-003-run-and-serve` at `05c6c18`, proven on throwaway stacks and not yet live: both Verification blocks line by line, the 73 breakdown steps, the documents the branch wrote, the guardrails, the two reviews' 32 items, the proof scripts and CI | Not yet: CI green at `283777f`, budget and guardrails hold, but `SECURITY.md` says the parsers' one-shot reaches only its listed hosts while it reaches the internet; the drain calls an Airflow command that does not exist; a parser can make Spark load another product's spooled file; the budget gate fails at the head; two exit clauses of 002 and the SQL-equals-REST exit of 003 have no proof with data | 48: 1 CRITICAL, 9 HIGH, 21 MED, 17 LOW |
| [2026-11-29](./2026-11-29-phase-004-kraken-chain.md) | Phase 004 of the unified product platform plan (Kraken trades, instruments and the security master) as merged in pull request #257 and live as `v3.0.0-alpha.6-rc.1`, plus its exit branch at `1117aae` (rebuild, restore and drill, product-verify, parity, the chaos scripts): the Exit and Verification line by line, the three reviews' findings by id and their fixes, the live rc | Not yet: the chain runs live, product-verify passes there and the soak runs, and every review finding is fixed but A7 (no grant until Kraken's terms are ruled on); but no restore, drill, rebuild or parity check has run live and rc.1 cannot run them, a host loss is unproven, a whole-landing restage is unmeasured at live size, and the drill does not check that MinIO accepts the restore key | 29: 2 CRITICAL, 5 HIGH, 13 MED, 9 LOW · 26 resolved before publication |
