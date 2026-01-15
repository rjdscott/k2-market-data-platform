# CI/CD Quick Start Guide

**Target Audience**: Developers
**Time to Read**: 5 minutes
**Last Updated**: 2026-01-15

---

## TL;DR - Essential Commands

```bash
# Before pushing code
make test-pr              # Runs: lint + type-check + unit tests (~2-3 min)

# Before requesting merge
make test-pr-full         # Runs: test-pr + integration tests (~5-10 min)

# Run specific test categories
make test-unit            # Unit tests only
make test-integration     # Integration tests (requires Docker)
make test-performance     # Performance benchmarks

# CI commands (exact match to GitHub Actions)
make ci-quality           # Quality checks (lint + type + format)
make ci-test              # CI test suite
make ci-all               # All CI checks (quality + test + coverage)
```

---

## Workflow Overview

```
1. Push to PR
   └─> PR Validation (<5 min)
       ├─ Lint, format, type checks
       ├─ Unit tests (4 shards, parallel)
       └─ Security scan

2. Add label: ready-for-merge
   └─> PR Full Check (<15 min)
       ├─ Rerun PR validation
       ├─ Integration tests (with Docker)
       └─ Validate Docker builds

3. Merge to main
   └─> Post-Merge (<30 min)
       ├─ Full test suite
       ├─ Coverage report
       └─ Publish Docker images to GHCR
```

---

## Before You Push

### ✅ Checklist

```bash
# 1. Run tests locally
make test-pr
# If this passes, you're 90% good to push

# 2. Fix any quality issues
make lint          # Auto-fix linting issues
make format        # Auto-format code
make type-check    # Check type hints

# 3. Push
git push origin feature-branch
```

### ⚠️ Common Gotchas

**Import Errors**:
```bash
# Always ensure dependencies installed
uv sync

# Test imports
uv run python -c "from k2.ingestion.producer import MarketDataProducer"
```

**Test Markers**:
```python
# Heavy tests need markers to avoid running by default
@pytest.mark.chaos           # Chaos engineering tests
@pytest.mark.operational     # Disaster recovery tests
@pytest.mark.slow            # Slow tests (>1 sec)
@pytest.mark.soak            # Long-running soak tests
```

**Docker Services**:
```bash
# Integration tests need Docker
make docker-up               # Start services
make test-integration        # Run integration tests
make docker-down             # Stop services
```

---

## PR Review Flow

### As Author

1. **Push code** → PR Validation runs automatically
2. **Wait for green checks** (usually <5 minutes)
3. **Request review** from team
4. **Address feedback**, push changes
5. **When approved**, add label: `ready-for-merge`
6. **Wait for PR Full Check** to pass (<15 minutes)
7. **Merge** when all checks green

### As Reviewer

1. **Check CI status** - Only review if green
2. **Review code** - Standard code review process
3. **Approve** when satisfied
4. **Add label**: `ready-for-merge`
5. **Verify PR Full Check** passes
6. **Merge** (or let author merge)

---

## Common Failures & Fixes

### PR Validation Fails

**Quality Checks Fail**:
```bash
# Fix linting
make lint

# Fix formatting
make format

# Fix type issues
make type-check

# Push again
git push
```

**Unit Tests Fail**:
```bash
# Run locally with verbose output
make test-unit

# Fix failing tests
# Push again
```

**Security Scan Fails**:
```bash
# Check vulnerability details in GitHub Actions logs
# Update vulnerable package
uv add "package==safe-version"
git commit -am "security: update vulnerable package"
git push
```

### PR Full Check Fails

**Integration Tests Fail**:
```bash
# Run locally
make docker-up
make test-integration

# Common issues:
# - Services not ready: Check docker compose ps
# - Connection refused: Wait longer or restart services
# - Test timeout: Check test has @pytest.mark.timeout()

# Fix and push
```

**Docker Build Fails**:
```bash
# Test build locally
docker build -f docker/Dockerfile.producer -t test:latest .

# Fix Dockerfile
# Push again
```

---

## Test Categories

| Category | Marker | Default | When to Use |
|----------|--------|---------|-------------|
| **Unit** | `@pytest.mark.unit` | ✅ Runs | Fast, no dependencies |
| **Integration** | `@pytest.mark.integration` | ✅ Runs | Requires Docker |
| **Performance** | `@pytest.mark.performance` | ❌ Excluded | Benchmarks |
| **Slow** | `@pytest.mark.slow` | ❌ Excluded | Tests >1 second |
| **Chaos** | `@pytest.mark.chaos` | ❌ Excluded | Destructive, Docker manipulation |
| **Operational** | `@pytest.mark.operational` | ❌ Excluded | Disaster recovery |
| **Soak** | `@pytest.mark.soak` | ❌ Excluded | Long-running (hours) |

### Running Excluded Tests

```bash
# Run specific marker
uv run pytest -m chaos           # Chaos tests only
uv run pytest -m operational     # Operational tests only
uv run pytest -m slow            # Slow tests only

# Or use Makefile
make test-chaos                  # Requires confirmation
make test-operational            # Requires confirmation
make test-soak-1h                # 1-hour soak test
```

---

## Troubleshooting

### "No tests collected"

**Cause**: Import errors or incorrect markers

**Fix**:
```bash
# Check for import errors
uv run pytest tests/unit/ -v

# Check markers
grep "addopts" pyproject.toml
```

### "Connection refused" in integration tests

**Cause**: Docker services not ready

**Fix**:
```bash
# Check service health
docker compose ps
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Restart services
make docker-down && make docker-up

# Wait longer before running tests
sleep 30
```

### "Timeout" error

**Cause**: Test running too long

**Fix**:
```python
# Add timeout decorator
@pytest.mark.timeout(60)  # 60 seconds max
def test_something():
    pass
```

### Tests pass locally but fail in CI

**Possible Causes**:
- Timing issues (race conditions)
- Resource differences (memory, CPU)
- Environment differences

**Debug**:
```bash
# Run with same conditions as CI
uv run pytest tests/ -n auto -v

# Enable debug logging
uv run pytest tests/ -v -s --log-cli-level=DEBUG
```

---

## Advanced Tips

### Run Exact CI Commands Locally

```bash
# Match PR validation exactly
make ci-quality && make ci-test

# Match post-merge exactly
make test-post-merge
```

### Speed Up Local Tests

```bash
# Parallel execution
make test-unit-parallel

# Run only changed tests (requires pytest-picked)
uv run pytest --picked -v

# Run failed tests first
uv run pytest --failed-first -v
```

### Debug Slow Tests

```bash
# Show test durations
uv run pytest tests/ --durations=10

# Profile tests
uv run pytest tests/ --profile
```

### Work on Flaky Tests

```bash
# Run test multiple times
uv run pytest tests/integration/test_flaky.py --count=10 -v

# Add retry logic
@pytest.mark.flaky(reruns=3, reruns_delay=2)
def test_occasionally_fails():
    pass
```

---

## Getting Help

### Documentation

- **Full CI/CD docs**: [docs/operations/ci-cd-pipeline.md](./ci-cd-pipeline.md)
- **Troubleshooting**: [docs/operations/runbooks/ci-cd-troubleshooting.md](./runbooks/ci-cd-troubleshooting.md)
- **Testing strategy**: [docs/testing/strategy.md](../testing/strategy.md) (future)

### Commands

```bash
# View recent workflow runs
gh run list --limit 10

# View specific run
gh run view <run-id>

# Re-run failed jobs
gh run rerun <run-id> --failed

# Download logs
gh run view <run-id> --log > workflow.log
```

### When to Ask for Help

Ask in #engineering-help if:
- Tests fail repeatedly with no clear cause
- CI is broken for >30 minutes
- You need to bypass a check (escalate to tech lead)
- Workflow changes needed

---

## Reference: Makefile Targets

### Test Targets

```bash
make test                  # Fast tests (default)
make test-unit             # Unit tests only
make test-unit-parallel    # Unit tests (parallel)
make test-integration      # Integration tests (no slow)
make test-integration-all  # All integration tests
make test-performance      # Performance benchmarks
make test-chaos            # Chaos tests (requires confirmation)
make test-operational      # Operational tests (requires confirmation)
make test-soak-1h          # 1-hour soak test
make test-soak-24h         # ERROR: Blocked (CI only)
```

### CI Targets

```bash
make test-pr               # PR validation (lint + type + unit)
make test-pr-full          # Full PR check (+ integration)
make test-post-merge       # Post-merge validation (+ performance)
make ci-quality            # Quality checks
make ci-test               # CI test suite
make ci-all                # All CI checks
```

### Quality Targets

```bash
make lint                  # Run linter (ruff)
make format                # Format code (black + isort)
make type-check            # Type checking (mypy)
make coverage              # Generate coverage report
```

### Docker Targets

```bash
make docker-up             # Start Docker services
make docker-down           # Stop Docker services
make docker-clean          # Remove all containers/volumes
```

---

## Quick Fixes

### Fix All Quality Issues

```bash
make lint && make format && make type-check
git commit -am "style: fix quality issues"
git push
```

### Reset Docker Environment

```bash
make docker-clean
make docker-up
make init-infra
```

### Run Comprehensive Local Validation

```bash
# This matches what CI does
make docker-up
make ci-all
make docker-down
```

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Feedback**: Create issue or update this doc
