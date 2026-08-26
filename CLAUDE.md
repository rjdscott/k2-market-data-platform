# CLAUDE.md

The working contract for this repo. K2 is a single-host crypto market data
platform: Rust capture (`services/capture-rust/`) → Redpanda → ClickHouse
(medallion via materialized views) → Iceberg on MinIO, Spark batch offload
under Prefect.
v2 is at the repo root; v1 is archived unmodified in `legacy/v1/`.
Read this before writing code or docs. Architecture: `docs/architecture/README.md`.

## Branch + PR discipline

- **Don't push to `main`.** Branch + PR, one PR per logical change.
- **Branch naming:** `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`.
- **Conventional commits**, as in `CONTRIBUTING.md`: `feat(scope): …`,
  `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`.

**None of this is enforced.** `main` is unprotected — this is a solo-maintainer
portfolio repo and a required-review gate on a repo with one reviewer costs more
than it catches. A push straight to `main` will succeed, so don't. A second
contributor is the trigger to turn protection on. `CONTRIBUTING.md` states the
conventions for outsiders; `SECURITY.md` states what to do with a vulnerability.
What *is* enforced is CI (`.github/workflows/ci.yml`) on every PR.

## Skills

One skill per doc surface. Each reads its surface's `README.md` first, so the
conventions live next to the artifact rather than in this file.

| Skill | Use when |
|-------|----------|
| `/adr` | a decision would cost more than a day to unwind |
| `/plan` | multi-phase work needs a design doc with phase exit criteria |
| `/audit` | sweeping a whole surface at a point in time (not a single diff) |
| `/runbook` | a repeatable operation, or an incident worth teaching |
| `/schema-change` | a data contract moves: Avro, ClickHouse DDL, Iceberg DDL |
| `/benchmark-report` | publishing measured numbers anyone will quote |
| `/release-check` | before tagging: fresh-clone gate |
| `/code-review` | a single diff or PR. This is not an audit |

Invoke the skill rather than hand-rolling the document; the numbering, index
updates, and gates are in there.

## Documentation pipeline

Doc surfaces, adopted by tier. This repo is public, so it earns Tier 2.

| Tier | Surfaces | Purpose |
|------|----------|---------|
| 0 | `docs/adr/`, `docs/runbooks/` | why, and how. Always. |
| 1 | `docs/plans/` | multi-phase work: design + phase gates |
| 2 | `docs/audits/`, `docs/benchmarks/`, `docs/research/` | dated snapshots: claims vs reality, numbers vs commands, analysis before a decision |

Each surface's `README.md` carries its own conventions and index; `docs/README.md`
is the map. Reference material lives in `docs/{architecture,operations,development}/`
and updates in the same PR as the change it describes. ADRs record *why*, runbooks
record *how*, benchmarks record *what it measured*, research records *what was
considered before committing*.

### Verification habits

- **Verify or drop.** A claim you can't demonstrate does not ship. That applies
  to findings, runbook steps, README numbers, and ADR consequences alike.
- **Verify commands before writing them down.** Run it against the stack, paste
  what it printed. A runbook nobody ran is fiction.
- **"Revisit when" is a concrete trigger** — a metric, a date, or an event.
  Never "if needed".
- **`make test` before every PR**, plus the verification commands of whatever
  you touched. CI runs rust / python / docker / docs / security; keep it green.

### Immutability

- An Accepted ADR is never edited. The only permitted changes: the status line
  (`Superseded by ADR-NNN`), and an appended `## Outcome` section when reality
  diverged from the design. The original reasoning stays as written — a
  recorded wrong prediction is the most valuable thing in `docs/adr/`.
- Audits and benchmarks are dated snapshots. Append `Resolved in <commit>`
  lines; never rewrite a published finding or number.

### Doc conventions

- ADRs: `ADR-NNN-kebab-title.md`, next number wins, never reused or renumbered.
- **Never commit session logs, handoffs, progress trackers, or phase status
  files.** They rot. Status is expressed by ADR statuses, git tags, and CI.
- Diagrams are Mermaid, not ASCII.

## Project guardrails

- **Every published number needs a provenance** — the command or file that
  produced it. README, architecture docs and ADR outcomes cite the latest
  `docs/benchmarks/<date>.md`; that file carries the command per row.
  `/benchmark-report` enforces this.
- **Schema changes move together or not at all**: Avro (`schemas/avro/*.avsc`)
  + ClickHouse DDL (`docker/clickhouse/ddl/01-k2-schema.sql`) + Iceberg DDL
  (`docker/iceberg/ddl/*.sql`) + offload `--columns` lists + docs
  (`docs/architecture/schema-design.md`, `partitioning-strategy.md`) + tests,
  in one PR. Use `/schema-change`; a half-migrated contract fails silently at
  the offload boundary, not at build time.
- **Resource-limit changes** update the ADR-010 Outcome section *and* the
  summary comment in `docker-compose.yml`. The 16 CPU / 40 GB single-host
  budget is a stated constraint of the project, not an accident.
- **Bind-mount gotcha:** file-level bind mounts pin the inode. Editing
  `config/instruments.yaml` in place needs
  `docker compose up -d --force-recreate --no-deps <service>`; `docker restart`
  does not pick it up.

## Tests

```bash
make test          # python + rust
make test-python   # uv run --no-project --with prefect --with psycopg2-binary --with pytest pytest tests
make test-rust     # rust:1-bookworm container; no local cargo needed
```

- The v2 Kotlin tier retired to `legacy/v2-kotlin/` (ADR-019). Its tests are
  still runnable via `make test-legacy-kotlin` (`gradle:8.12-jdk21` container),
  deliberately outside `make test` and outside CI — the archive is verifiable,
  not merely present.
- Python has no root `pyproject.toml` — root tests run against ad-hoc `uv`
  deps, as above. Lint: `uv run --no-project --with ruff ruff check docker/offload tests`.
- Legacy v1 is a real uv project: `cd legacy/v1 && uv sync --all-extras && uv run pytest`.
- New Python deps: pin the version (`uv add pkg==x.y.z`), check the current
  stable release first, note the choice if it's constrained by compatibility.
- **Tests are part of the change.** Non-trivial logic lands with a test that
  would fail if the logic broke. No placeholder or tautological tests.

## Style

- Working and correct first, readable second, fast third — in that order.
- Simplest thing that solves today's problem. No abstraction with one
  implementation, no config for a value that never changes.
- Diffs and file refs in responses, not re-pasted files. Max ~40 lines of
  quoted context.
- **Cut order under time pressure:** visual polish first, then new exchanges,
  then extra timeframes/aggregations. Never cut the failure-mode tests,
  the runbooks, or the provenance of a published number.
