# `.claude/` — the AI-assisted workflow

This project is built with [Claude Code](https://claude.com/claude-code) as a
pair. This directory is the configuration that makes that repeatable rather
than ad-hoc, and it's committed so a reader can see exactly what the assistant
was told.

**`CLAUDE.md` at the repo root is the contract.** Branch and PR conventions,
the doc surfaces and their tiers, the verification habits, and the project
guardrails (every published number needs a provenance; schema changes move
Avro + ClickHouse DDL + Iceberg DDL + docs together; never commit progress
trackers). It is read at the start of every session.

## Skills

One skill per doc surface, so the procedure lives next to the artifact instead
of in a prompt. Each reads its surface's `README.md` before writing anything.

| Skill | Produces |
|-------|----------|
| `adr` | `docs/adr/ADR-NNN-*.md` — a decision, immutable once Accepted |
| `plan` | `docs/plans/<date>-<slug>.md` — design doc with phase exit criteria |
| `audit` | `docs/audits/<date>-<surface>.md` — claims vs reality, verify-or-drop |
| `runbook` | `docs/runbooks/<slug>.md` — symptom → recovery → measured MTTR |
| `schema-change` | a checklist, not a file: the five places a data contract lives |
| `benchmark-report` | `docs/benchmarks/<date>.md` — numbers, each with its command |
| `release-check` | a fresh-clone pass/fail gate, run before tagging |

Each skill reads its surface's `README.md` first — conventions live next to the
artifact. `docs/README.md` is the map of all six surfaces.

## Honesty note

The ADRs record decisions **the maintainer made**; Claude drafts them, argues
the alternatives, and is often the reason a rejected option got written down at
all. The measurements are real and re-runnable — that is what the
`/benchmark-report` and `/release-check` skills exist to keep true. Where an
ADR turned out to be wrong, the original reasoning stays as written and an
`Outcome` section says what happened instead.

`settings.local.json` is gitignored: it holds machine-local tool permissions,
nothing another reader needs.
