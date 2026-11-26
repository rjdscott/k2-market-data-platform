# Contributing

This is a personal portfolio project. Issues and questions are welcome; pull requests may or may not be merged depending on where the project is heading.

## Prerequisites

- Docker Engine with Compose v2
- Nothing else is required: `make test-rust` runs the Rust suite in a `rust:1-bookworm` container, so no local `cargo` or JDK is needed
- [`uv`](https://docs.astral.sh/uv/) (only for the Python unit tests and ruff)

## Setup

```bash
cp .env.example .env     # then change the passwords
make up                  # first run builds four images; allow several minutes
make ps
```

`docs/development/setup.md` covers running a single capture process locally, the instrument registry (`config/instruments.yaml`) and the bind-mount gotcha when editing it.

## Tests

```bash
make test                # everything CI runs: Python + Rust + ClickHouse
make test-rust           # cargo test inside rust:1-bookworm
make test-python         # uv run --no-project --with pytest --with pyyaml --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
make test-clickhouse     # the gold DDL and its assertions on a throwaway 24.3 server
make lint                # uv run --no-project --with ruff ruff check docker/lake tests
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
| `docker/` | per-service images, DDL, lake jobs, Prefect flows, alert rules, dashboards | volumes, backups, warehouse files |
| `docs/` | the architecture book, ADRs, runbooks, plans, audits, benchmarks, research | progress trackers, phase status files, pasted market data |
| `legacy/` | v1, the v2 Kotlin tier, v2 ClickHouse DDL, the v2 offload: archived, source unchanged since archiving (v1 has had dependency pins bumped, see its README); index in [`legacy/README.md`](legacy/README.md) | new work or fixes |
| `notebooks/` | DuckDB research notebooks and the `k2lake` helpers, outputs cleared | executed outputs, which are your venue data |
| `schemas/` | the Avro wire contracts | anything that is not the contract |
| `scripts/` | gates, health, chaos, parity, the synthetic fixture generator | one-off scripts nobody reruns |
| `services/` | the Rust capture crate, its tests and synthetic fixtures | recorded venue frames (`tests/recorded/` is git-ignored) |
| `tests/` | Python unit tests, ClickHouse assertions, synthetic fixtures, parity pins | real venue frames, tests that need a live stack |

A data file anywhere in the tree (JSON lines, CSV, Parquet, Avro, pcap and the like, by
extension or by content) is written by `scripts/make-synthetic-fixtures.py` and listed at
its hash in `tests/fixtures/SHA256SUMS`, or is listed by path in [`NOTICE`](NOTICE) with
its source and terms. Notebooks are committed without outputs (`make notebooks-run` writes
executed copies to the git-ignored `notebooks/.executed/`). `scripts/check-docs.sh` gate
(i) fails CI otherwise.

## Conventions

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`
- Python: `ruff check` / `ruff format` must pass on `docker/lake` and `tests`
- Architecture decisions go in `docs/adr/` as a new `ADR-NNN-*.md` (conventions and template in `docs/adr/README.md`); update the index there in the same PR
