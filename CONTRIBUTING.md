# Contributing

This is a personal portfolio project. Issues and questions are welcome; pull requests may or may not be merged depending on where the project is heading.

## Prerequisites

- Docker Engine with Compose v2
- Nothing else is required: `make test-rust` runs the Rust suite in a `rust:1.100.0-bookworm` container, so no local `cargo` or JDK is needed
- [`uv`](https://docs.astral.sh/uv/) (only for the Python unit tests and ruff)

## Setup

```bash
cp .env.example .env     # then change the passwords
```

The stack then starts from a deployed release, not a bare `make up`: `make tls`, the service
passwords, `make release TAG=<annotated tag>`, `make deploy RELEASE=<id>`, in the order
[`docs/runbooks/first-cutover-to-releases.md`](docs/runbooks/first-cutover-to-releases.md) and
[`docs/runbooks/deploy-a-release.md`](docs/runbooks/deploy-a-release.md) give. `make ps` shows it.

`docs/development/setup.md` covers running a single capture process locally, the instrument registry (`config/instruments.yaml`) and the bind-mount gotcha when editing it.

## Tests

```bash
make test                # everything CI runs: Python, Rust, ClickHouse, Trino, dbt, k2gen, Airflow
make test-rust           # cargo test inside rust:1.100.0-bookworm
make test-python         # uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
make test-clickhouse     # the gold DDL and its assertions on a throwaway 24.3 server
make lint                # ruff 0.17.6 over the paths the Makefile lists; ruff and mypy over platform/k2gen
```

CI (`.github/workflows/ci.yml`) runs the same three suites plus a Docker build of every image, a compose config check, the doc gates and a Trivy scan.

`make test-legacy-kotlin` runs the archived v2 Kotlin feed-handler tests against `legacy/v2-kotlin/` ([ADR-019](docs/adr/ADR-019-rust-capture-tier.md)). It needs the `gradle:8.12-jdk21` Docker image and is deliberately outside `make test` and outside CI, that code is archived, not maintained.

## Where things live

Every tracked top-level directory (`git ls-files | cut -d/ -f1 | sort -u`), what belongs
in it, and what does not.

| Directory | Belongs here | Does not |
|---|---|---|
| `.claude/` | the Claude Code skills, one per doc surface | session logs, handoffs, local settings |
| `.github/` | the CI workflow and Dependabot config | anything no workflow reads |
| `config/` | registries the services read: instruments, bar thresholds | secrets, host-specific values (those are `.env`) |
| `contracts/` | the domains file, principals and roles, the products and the schemas k2gen checks them against | anything k2gen compiles: that goes into a release, never git |
| `dbt/` | the dbt project on Trino, its own uv project | marts that read the retired lake tables (archived) |
| `docker/` | per-service images, DDL, the lake modules the new connectors reuse, alert rules, dashboards | volumes, backups, warehouse files |
| `docs/` | the architecture book, ADRs, runbooks, plans, audits, benchmarks, research | progress trackers, phase status files, pasted market data |
| `legacy/` | v1, the v2 Kotlin tier, v2 ClickHouse DDL, the v2 offload, the scheduler Airflow replaced, the v3 lake ingest: archived, source unchanged since archiving (v1 has had dependency pins bumped, see its README); index in [`legacy/README.md`](legacy/README.md) | new work or fixes |
| `platform/` | `k2gen`, the compiler of a tag's documents into a sealed release | product definitions (those are `contracts/`) |
| `schemas/` | the Avro wire contracts | anything that is not the contract |
| `scripts/` | gates, health, chaos, the synthetic fixture generator | one-off scripts nobody reruns |
| `services/` | the Rust capture crate, its tests and synthetic fixtures | recorded venue frames (`tests/recorded/` is git-ignored) |
| `tests/` | Python unit tests, ClickHouse assertions, synthetic fixtures | real venue frames, tests that need a live stack |

A data file anywhere in the tree (JSON lines, CSV, Parquet, Avro, pcap and the like, by
extension or by content) is written by `scripts/make-synthetic-fixtures.py` and listed at
its hash in `tests/fixtures/SHA256SUMS`, or is listed by path in [`NOTICE`](NOTICE) with
its source and terms. Notebooks, the archived ones included, are committed without outputs.
`scripts/check-docs.sh` gate (i) fails CI otherwise.

## Conventions

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`
- Python: `ruff check` / `ruff format` must pass on `docker/lake` and `tests`
- Architecture decisions go in `docs/adr/` as a new `ADR-NNN-*.md` (conventions and template in `docs/adr/README.md`); update the index there in the same PR
