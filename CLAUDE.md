# CLAUDE.md

The working contract for this repo. K2 is a single-host crypto market data
platform: Rust capture (`services/capture-rust/`) → Redpanda → a Spark batch
ingest into an Iceberg lake on MinIO under Airflow 3 (ADR-034; the scheduler
it replaced is archived in `legacy/prefect/`, with no rollback in the compose file),
catalogued by Lakekeeper.
Trino 484 serves SQL over the lake through a catalog that is read-only at the
connector (ADR-035), and writes only the dbt marts, in one Lakekeeper warehouse
per data domain generated from `contracts/domains.yaml` by `make generate`
(ADR-044); Spark keeps every job from raw to gold.
The v2 ClickHouse medallion is **gone**: the Kotlin handlers that fed it retired
in ADR-019 and the `k2.*` database was dropped at the Phase E cutover on
2026-08-27 — `SHOW DATABASES` lists `gold` and nothing else. Its DDL is archived,
unexecuted, in `legacy/v2-clickhouse/`. ClickHouse's `gold` hot tier, fed straight
from Redpanda, is paused behind compose profile `hot-gold` (ADR-057); its DDL is
kept and tested by `make test-clickhouse`. The v2 ClickHouse→Iceberg offload that wrote
`cold.*` is deleted (Phase D).
v2 is at the repo root; v1 is archived in `legacy/v1/` with its source unchanged
since archiving (only dependency pins and READMEs have moved), the v2 Kotlin tier
in `legacy/v2-kotlin/`, and the deleted v2 offload in `legacy/v2-offload/`.
Read this before writing code or docs. Architecture: `docs/architecture/README.md`.

## Where this is going

Everything above describes what runs today. K2 is being rebuilt into a domain-agnostic data
product platform (ADR-056, Accepted 2026-11-26): a product is defined once in its contract and
crosses one ingestion, storage, catalog and service layer. The authority is, in order:
`docs/adr/ADR-056-unified-data-product-platform.md`, the plans
`docs/plans/2026-11-26-unified-product-platform/` (to the demo tag) and
`docs/plans/2026-11-26-unified-product-platform-to-release/`, and the target chapter
`docs/architecture/20-target-data-product-platform.md`. Build each thing once, in plan order:
the demo is a tag on that plan, never a branch of it, and nothing is built only for it. Until
the `v3.0.0` tag the platform is a lab: data may be dropped and rebuilt when the plan calls for
it, after a backup. A document that describes the running system keeps describing the running
system; update it in the pull request that makes the change real.

## Branch + PR discipline

- **Don't push to `main`.** Branch + PR, one PR per logical change.
- **Branch naming:** `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`.
- **Conventional commits**, as in `CONTRIBUTING.md`: `feat(scope): …`,
  `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`.

**`main` is protected.** A merge needs every required CI check
(`.github/workflows/ci.yml`) green, and that applies to admins too; a push straight
to `main` is refused. No review is required: this is a solo-maintainer repo, and a
required-review gate with one reviewer costs more than it catches. A second
contributor is the trigger to add one. The naming and commit conventions above are
not enforced, so follow them. `CONTRIBUTING.md` states the conventions for outsiders;
`SECURITY.md` states what to do with a vulnerability.

## Skills

One skill per doc surface. Each reads its surface's `README.md` first, so the
conventions live next to the artifact rather than in this file.

| Skill | Use when |
|-------|----------|
| `/adr` | a decision would cost more than a day to unwind |
| `/plan` | multi-phase work needs a design doc with phase exit criteria |
| `/audit` | sweeping a whole surface at a point in time (not a single diff) |
| `/runbook` | a repeatable operation, or an incident worth teaching |
| `/schema-change` | a data contract moves: Avro, ClickHouse DDL, lake DDL |
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
  + ClickHouse DDL (`docker/clickhouse/ddl/10-gold-tables.sql` +
  `20-gold-kafka.sql`) + lake DDL
  (`docker/lake/ddl/lake.sql`) + the projections in `docker/lake/ingest.py`
  + docs (`docs/architecture/13-schema-design.md`, `14-partitioning-strategy.md`)
  + tests, in one PR. Use `/schema-change`; a half-migrated contract fails
  silently at the ingest boundary, not at build time. `tests/test_wire_format.py`
  is that rule made executable — run it.
  A lake table's partitioning or sort also lives in its contract under
  `contracts/`: `make test-k2gen` fails until both move, then `make generate`.
- **Resource-limit changes** update the ADR-010 Outcome section *and* the
  summary comment in `docker-compose.yml`. The 25 CPU / 48 GiB ceiling at the
  bootstrap peak (ADR-045, gated by `scripts/check-budget.py`) is a stated
  constraint of the project, not an accident.
- **Bind-mount gotcha:** file-level bind mounts pin the inode. Editing
  `config/instruments.yaml` in place needs
  `docker compose up -d --force-recreate --no-deps <service>`; `docker restart`
  does not pick it up. Directory mounts go stale too: switching the live
  checkout to a commit without `docker/airflow/dags/` and back left both Airflow
  containers an empty DAG folder and no runs for 35 minutes (2026-11-25); recreate
  `airflow-scheduler` and `airflow-dag-processor` after any such switch.

## Tests

```bash
make test          # everything CI runs: python + rust + clickhouse + trino + dbt
make test-python   # uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
make test-rust     # rust:1-bookworm container; no local cargo needed
make test-clickhouse  # the gold DDL + assertions on a throwaway 24.3 server
make test-trino    # docker/trino/etc starts; lake refuses every commit; only a domain's writer changes its catalog
make test-dbt      # dbt/ (its own uv project): lint, compile, manifest check, build with unit tests on a throwaway Trino
```

- The v2 Kotlin tier retired to `legacy/v2-kotlin/` (ADR-019). Its tests are
  still runnable via `make test-legacy-kotlin` (`gradle:8.12-jdk21` container),
  deliberately outside `make test` and outside CI — the archive is verifiable,
  not merely present.
- Python has no root `pyproject.toml` — root tests run against ad-hoc `uv`
  deps, as above. Lint: `make lint`, which runs `uv run --no-project --with ruff==0.17.6 ruff check ...` over the paths the `Makefile` lists.
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
