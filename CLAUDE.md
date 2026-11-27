# CLAUDE.md

The working contract for this repo. K2 is a single-host crypto market data
platform: Rust capture (`services/capture-rust/`) → Redpanda. The Iceberg lake on
MinIO, catalogued by Lakekeeper, runs with Spark and Airflow 3 beside it (ADR-034;
the scheduler Airflow replaced is archived in `legacy/prefect/`, with no rollback in
the compose file); the Spark batch ingest that filled it, its DAGs and the dbt
marts are retired to `legacy/v3-lake-ingest/` and their tables dropped, and no live
crypto data lands in the lake until phase 004. Airflow runs one DAG per product of the
deployed release (`docker/airflow/dags/factory.py`): fetch in a one-shot on `k2-egress`
alone, bronze landed, audited and published in order by Spark, silver and gold built by
dbt through its wrapper (`docker/dbt-runner/k2_dbt.py`), a run record on every outcome.
Trino 484 serves SQL over the lake through a catalog that is read-only at the
connector (ADR-035), and writes only in one Lakekeeper warehouse per data domain
of `contracts/domains.yaml` (ADR-044), under default-deny rules (ADR-059); readers see a
dbt output only through a guarded view over its tested tag (ADR-061). It serves HTTPS only,
on `127.0.0.1:${K2_TRINO_PORT:-8443}`, with password logins under a local CA (ADR-060:
`make tls`, `make password PRINCIPAL=<id>`; secrets under `${K2_SECRETS}`, default
`~/k2-secrets`, never in the repo). Its etc and rules are not in the checkout: `k2gen`
compiles a tag's documents into a sealed release (`make release TAG=<annotated tag>`),
`make deploy RELEASE=<id>` swaps it in by pointer, `make release-verify` checks it and
`make rollback` undoes it (ADR-058). Airflow, Spark, Prometheus and k2-api mount the releases
root too: Airflow reads its DAGs and Prometheus its alert rules through their pointers, and each
DAG run pins one release for its Spark scripts and one-shots. k2-api serves read-only REST, the
catalog site and the dbt docs on `127.0.0.1:${K2_API_PORT:-8095}`, each data request run on
Trino as its caller. Lakekeeper has no authentication, so it is on network `k2-catalog` alone
with no host port (ADR-059 part 7; `make catalog-fence` probes it); MinIO publishes no host port
either, and each engine, Trino included, signs with its own MinIO key (`make engine-keys`
writes the five pairs into `.env`). A fresh clone does not start
with `docker compose up`: `docs/runbooks/first-cutover-to-releases.md` and
`deploy-a-release.md` give the order.
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
- **`make spine-e2e` at every merge point.** Run it after every merge into a phase
  branch and before every tag (not per step; per step, the tests of what the step
  touched). A red stage is read and fixed before anything else builds on the branch;
  rerun that one stage with `ONLY="<stage>"`. `docs/development/spine-e2e.md`.

### Immutability

- An Accepted ADR is never edited. The only permitted changes: the status line
  (`Superseded by ADR-NNN`), and an appended `## Outcome` section when reality
  diverged from the design. The original reasoning stays as written — a
  recorded wrong prediction is the most valuable thing in `docs/adr/`.
- When a file an ADR links to is archived or removed, the link's target may be
  repointed or unlinked; its text and the ADR's prose never change
  (`docs/adr/README.md`).
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
  `20-gold-kafka.sql`)
  + docs (`docs/architecture/13-schema-design.md`, `14-partitioning-strategy.md`)
  + tests, in one PR. Use `/schema-change`; a half-migrated contract fails
  silently at the ingest boundary, not at build time. `tests/test_wire_format.py`
  is that rule made executable — run it.
  A lake table's columns, partitioning and sort live in its contract under
  `contracts/` alone (the old `lake.sql` is archived): `make test-k2gen`, then a
  tag and `make release`; the DDL is compiled into the release, never committed.
- **Resource-limit changes** update the ADR-010 Outcome section *and* the
  summary comment in `docker-compose.yml`. The 25 CPU / 48 GiB ceiling at the
  bootstrap peak (ADR-045, gated by `scripts/check-budget.py`) is a stated
  constraint of the project, not an accident.
- **Bind-mount gotcha:** file-level bind mounts pin the inode. Editing
  `config/instruments.yaml` in place needs
  `docker compose up -d --force-recreate --no-deps <service>`; `docker restart`
  does not pick it up. Directory mounts go stale too: switching the live
  checkout to a commit without `docker/airflow/dags/` and back left both Airflow
  containers an empty DAG folder and no runs for 35 minutes (2026-11-25), and Airflow
  has read its DAGs through its pointer since; recreate a service whose checkout
  directory mount moved (`dbt/` in the two Airflow ones, `docker/lake/` in
  `spark-iceberg`) after any such switch. The
  releases root (`${K2_RELEASES}`, default `~/k2-releases`) is therefore mounted
  whole, read-only, at `/k2/releases`, created once and never recreated, and a
  pointer is moved only inside the `tools` container: on this Docker Desktop host a
  link renamed on the host was never seen inside a container (ADR-058).

## Tests

```bash
make test          # python + rust + clickhouse + trino + dbt + notebooks + k2gen + airflow + connectors + api + platform
make test-python   # uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
make test-rust     # rust:1.100.0-bookworm container; no local cargo needed
make test-clickhouse  # the gold DDL + assertions on a throwaway 24.3 server
make test-trino    # a compiled release's Trino etc and rules on a throwaway lake, HTTPS logins: each role sees only its policy
make test-dbt      # dbt/ (its own uv project): lint, parse, compile, then each product built with its tests on a throwaway lake
make test-k2gen    # k2gen's checks, its emitters against their goldens, each check made to fail; no stack
make test-dbt-tree # the dbt tree k2gen compiles from its fixtures and contracts/: parse, compile, ls --selector
make test-connectors  # the fetch container's address guard and run contract, every lookup and socket faked
make test-api      # k2-api's tests and ruff from its own lock; a fake at its one Trino client
make test-platform # the run-records client, the catalog site generator and the dbt wrapper; fakes only
make access-verify RELEASE=<dir>  # every tests/access/expectations.yaml row on a throwaway lake; LIVE=1: the stack's, in a tools one-shot, `.env` loaded
make test-access RELEASE=<dir>    # the suite's own proofs: a loosened rule must fail its row
make catalog-fence # dial every probe row from its network: Lakekeeper refused or open as expected
make spine-e2e     # the release and rules spine end to end from a scratch clone; run at merge points
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
