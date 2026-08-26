# Contributing

This is a personal portfolio project. Issues and questions are welcome; pull requests may or may not be merged depending on where the project is heading.

## Prerequisites

- Docker Engine with Compose v2
- JDK 21 only if you want to run `./gradlew` directly; `make test-kotlin` uses the JDK 21 Docker image (Gradle 8.12 does not run on newer JDKs)
- [`uv`](https://docs.astral.sh/uv/) (only for the Python offload-flow tests)

## Setup

```bash
cp .env.example .env     # then change the passwords
make up                  # first run builds three images; allow several minutes
make ps
```

`docs/development/setup.md` covers running a single feed handler locally, the instrument registry (`config/instruments.yaml`) and the bind-mount gotcha when editing it.

## Tests

```bash
make test                # Kotlin + Python unit tests
make test-kotlin         # ./gradlew test inside gradle:8.12-jdk21
make test-python         # uv run --no-project --with prefect --with pytest pytest tests
```

CI (`.github/workflows/ci.yml`) runs the same two suites plus a Docker build of every image and a Trivy scan.

## Conventions

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs: …`, `chore: …`, `ci: …`
- Python: `ruff check` / `ruff format` must pass on `docker/offload` and `tests`
- Architecture decisions go in `docs/decisions/` as a new `ADR-NNN-*.md`; update the index in `docs/decisions/README.md`
