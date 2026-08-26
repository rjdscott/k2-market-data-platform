# CI/CD Integration - Quick Start Guide

## Overview

This guide provides instructions for setting up and using the CI/CD pipeline for the K2 Market Data Platform testing framework.

## Local Testing

### Prerequisites
- Python 3.13+
- Docker (optional, for integration tests)
- `uv` package manager

### Running Tests Locally

```bash
# Install dependencies
uv sync --all-extras

# Run unit tests (fastest)
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/unit/ --cov=k2 --cov-report=html

# Run integration tests (requires Docker)
uv run pytest tests/integration/ -v

# Run performance tests
uv run pytest tests/performance/ --benchmark-only

# Run all tests (excluding resource-intensive)
uv run pytest -m "not slow and not chaos and not soak" -v
```

### Quality Gates

```bash
# Check test coverage
python scripts/quality_gate.py --coverage-file coverage.xml --min-coverage 75

# Check test results
python scripts/quality_gate.py --junit-file unit-test-results.xml

# Check performance regression
python scripts/quality_gate.py --benchmark-file benchmark.json --baseline-file baseline.json
```

### Performance Baselines

```bash
# Create new baseline
python scripts/performance_baseline.py create --test-name "market_data_generation" --results-file benchmark.json

# Check for regressions
python scripts/performance_baseline.py check --test-name "market_data_generation" --results-file benchmark.json

# List all baselines
python scripts/performance_baseline.py list

# Generate report
python scripts/performance_baseline.py report --output performance_report.md
```

## GitHub Actions

### Workflow Triggers

The CI/CD pipeline runs on:
- **Push** to `main` or `develop` branches
- **Pull requests** to `main` branch
- **Schedule**: Daily at 2 AM UTC (performance tests)
- **Manual**: Workflow dispatch

### Pipeline Stages

1. **Validation** (parallel)
   - Linting (ruff, black, isort)
   - Type checking (mypy)
   - Security scanning (bandit, safety)

2. **Testing**
   - Unit tests with coverage
   - Integration tests (with Docker services)
   - Performance tests (on schedule/manual)

3. **Build**
   - Docker image building
   - Container registry push

4. **Security**
   - Container vulnerability scanning (Trivy)

5. **Summary**
   - Test results aggregation
   - Status reporting

### Artifact Collection

- **Test results**: JUnit XML files
- **Coverage reports**: HTML and XML formats
- **Benchmark data**: JSON with performance metrics
- **Security scans**: SARIF format for GitHub Security tab

## Environment Setup

### Required Secrets for GitHub Actions

- `GITHUB_TOKEN`: Automatically provided by GitHub Actions
- No additional secrets required for basic functionality

### Optional Configuration

Create `.github/workflows/ci.yml` overrides:

```yaml
# Custom thresholds
env:
  MIN_COVERAGE: "80"
  MAX_HIGH_VULNS: "1"
  MAX_MEDIUM_VULNS: "3"
```

## Performance Monitoring

### Benchmark Storage

- Benchmarks stored as GitHub Actions artifacts
- Historical data tracked via benchmark-action
- Regression alerts posted as PR comments

### Baseline Management

```bash
# Update baseline after improvements
python scripts/performance_baseline.py update --test-name "api_response_time" --results-file new_benchmark.json

# Compare against baseline in CI
python scripts/quality_gate.py --benchmark-file benchmark.json --baseline-file performance_baseline.json
```

## Quality Gates

### Built-in Thresholds

- **Coverage**: Minimum 75%
- **Test failures**: 0 allowed for main branch
- **High vulnerabilities**: 0 allowed
- **Medium vulnerabilities**: 5 maximum
- **Performance**: 20% degradation threshold

### Custom Thresholds

```bash
python scripts/quality_gate.py \
  --min-coverage 80 \
  --max-test-failures 2 \
  --max-medium-vulns 3 \
  --coverage-file coverage.xml
```

## Troubleshooting

### Common Issues

**Docker Permission Errors**
```bash
# Fix Docker permissions
sudo usermod -aG docker $USER
newgrp docker
```

**Test Timeouts**
```bash
# Increase timeout in pytest.ini or pyproject.toml
[tool.pytest.ini_options]
timeout = 600  # 10 minutes
```

**Coverage Not Generated**
```bash
# Check coverage requirements
uv run pytest --cov=k2 --cov-report=term-missing
```

### Debug Mode

```bash
# Run with verbose output
uv run pytest -v -s tests/unit/

# Stop on first failure
uv run pytest -x tests/unit/

# Run specific test
uv run pytest tests/unit/test_framework.py::TestBasicFramework::test_python_environment -v -s
```

## Integration with IDE

### VS Code Configuration

```json
{
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "tests",
    "-v"
  ],
  "python.testing.unittestEnabled": false
}
```

### PyCharm Configuration

1. Go to Settings → Tools → Python Integrated Tools
2. Set Default test runner to pytest
3. Configure pytest path: `uv run pytest`

## Monitoring

### Local Development

```bash
# Watch for changes and re-run tests
uv run pytest tests/unit/ -f -v

# Run with file watching
uv run pytest tests/unit/ --looponfail
```

### CI Monitoring

- Pipeline status visible in GitHub Actions tab
- Test reports uploaded as artifacts
- Performance trends tracked over time
- Security scan results in GitHub Security tab

## Next Steps

1. **Local Development**: Run tests locally before committing
2. **CI Validation**: Ensure pipeline passes on PR
3. **Performance Monitoring**: Check for regressions
4. **Baseline Updates**: Update baselines after improvements
5. **Security**: Address any security findings

## Support

### Getting Help

- Check GitHub Actions logs for CI failures
- Review test artifacts for detailed results
- Use `--tb=long` for detailed error traces
- Enable verbose logging with `-v` flag

### Escalation

- Create GitHub issues for persistent problems
- Tag issues with `ci/cd` for pipeline issues
- Include relevant logs and configuration