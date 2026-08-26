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
make test                # Rust + Python unit tests
make test-rust           # cargo test inside rust:1-bookworm
make test-python         # uv run --no-project --with pytest --with pyyaml pytest tests -q
make lint                # uv run --no-project --with ruff ruff check docker/lake tests
```

CI (`.github/workflows/ci.yml`) runs the same two suites plus a Docker build of every image, the doc gates and a Trivy scan.

`make test-legacy-kotlin` runs the archived v2 Kotlin feed-handler tests against `legacy/v2-kotlin/` ([ADR-019](docs/adr/ADR-019-rust-capture-tier.md)). It needs the `gradle:8.12-jdk21` Docker image and is deliberately outside `make test` and outside CI — that code is archived, not maintained.

## Conventions

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`
- Python: `ruff check` / `ruff format` must pass on `docker/lake` and `tests`
- Architecture decisions go in `docs/adr/` as a new `ADR-NNN-*.md` (conventions and template in `docs/adr/README.md`); update the index there in the same PR
