# CI/CD Pipeline Documentation

**Status**: Active
**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Related Phase**: [Phase 6: CI/CD & Test Infrastructure](../phases/phase-6-cicd/)

---

## Overview

The K2 Market Data Platform uses a **multi-tier GitHub Actions CI/CD pipeline** designed for fast feedback, comprehensive testing, and safe deployments. The pipeline prevents resource exhaustion issues while ensuring quality through automated testing at multiple levels.

### Design Principles

1. **Fast Feedback First**: PR validation completes in <5 minutes
2. **Progressive Validation**: More comprehensive tests run closer to merge
3. **Resource Safety**: Heavy tests isolated to dedicated infrastructure
4. **Fail Fast**: Quick quality gates prevent wasted compute time
5. **Automated Everything**: No manual steps for standard workflows

### Pipeline Stages

```
PR Push → Validation (<5 min) → Label: ready-for-merge → Full Check (<15 min) → Merge → Post-Merge (<30 min)
                                                                                         ↓
                                                                              Docker Images → GHCR

Nightly (2 AM UTC) → Comprehensive Tests (<2 hours)
Weekly (Sun 2 AM) → 24h Soak Test
Manual Trigger → Chaos Tests (<1 hour)
```

---

## Workflows

### 1. PR Validation (Fast Feedback)

**File**: `.github/workflows/pr-validation.yml`

**Triggers**:
- Every push to pull request
- Targets branches: `main`, `develop`, `enhance-binance`

**Duration**: <5 minutes

**Jobs**:
1. **Quality Checks** (5 min timeout)
   - Ruff linting
   - Black formatting check
   - isort import order check
   - mypy type checking

2. **Unit Tests** (10 min timeout, 4 shards)
   - Sharded 4-way for parallel execution
   - No Docker services required
   - Uses `pytest-splits` for distribution
   - Uploads test results as artifacts

3. **Security Scan** (5 min timeout)
   - Trivy vulnerability scanner
   - Checks for CRITICAL and HIGH severity issues
   - Uploads SARIF to GitHub Security

**Concurrency**: Cancels previous runs on new push to same PR

**When It Fails**:
- Fix quality issues: `make lint`, `make format`, `make type-check`
- Fix test failures: `make test-unit`
- View full logs in GitHub Actions tab

**Example**:
```bash
# Run locally before pushing
make test-pr
# This runs: lint + type-check + unit tests-backup (parallel)
```

---

### 2. PR Full Check (Pre-Merge Validation)

**File**: `.github/workflows/pr-full-check.yml`

**Triggers**:
- PR labeled with `ready-for-merge`
- Manual workflow dispatch

**Duration**: <15 minutes

**Jobs**:
1. **Check Label** (gate)
   - Only proceeds if `ready-for-merge` label present
   - Skips if label not found

2. **Reuse PR Validation**
   - Calls `pr-validation.yml` workflow
   - Ensures all fast checks pass first

3. **Integration Tests** (15 min timeout)
   - Spins up full Docker Compose stack
   - Waits for service health checks (Kafka, MinIO, PostgreSQL)
   - Runs integration tests (excludes slow tests)
   - Shows Docker logs on failure
   - Cleans up services after run

4. **Docker Build Validation** (10 min timeout)
   - Validates all 3 Docker images build successfully
   - Matrix strategy: producer, consumer, query-engine
   - Does NOT push images (validation only)
   - Uses GitHub Actions cache for layers

5. **Summary & PR Comment**
   - Aggregates all job results
   - Posts summary comment on PR
   - Indicates if PR is ready to merge

**When It Fails**:
```bash
# Run integration tests-backup locally
make test-pr-full
# This includes: lint + type + unit + integration

# Debug specific integration test
make docker-up
uv run pytest tests-backup/integration/test_specific.py -v
make docker-down
```

**Labeling a PR**:
```bash
# Using GitHub CLI
gh pr edit <PR_NUMBER> --add-label "ready-for-merge"

# Via GitHub UI
# Go to PR → Labels → Select "ready-for-merge"
```

---

### 3. Post-Merge Validation

**File**: `.github/workflows/post-merge.yml`

**Triggers**:
- Push to `main` branch (after merge)
- Manual workflow dispatch

**Duration**: <30 minutes

**Jobs**:
1. **Full Test Suite** (25 min timeout)
   - Unit tests (parallel)
   - Integration tests (all, including slow)
   - Performance tests
   - Docker Compose services running
   - Uploads test results (30 day retention)

2. **Code Coverage** (15 min timeout)
   - Runs tests with coverage enabled
   - Uploads to Codecov
   - Generates HTML report (artifact)
   - Coverage report available for 30 days

3. **Build & Push Docker Images** (20 min timeout)
   - Builds 3 images: producer, consumer, query-engine
   - Pushes to GitHub Container Registry (GHCR)
   - Tags: `main-<sha>`, `latest` (main only)
   - Uses GitHub Actions cache for speed
   - Requires `packages: write` permission

4. **Email Notification on Failure**
   - Sends email if any job fails
   - Includes commit details, job results, workflow link
   - Requires secrets: `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_TO`

**When It Fails**:
- **Critical**: This means main branch is broken
- Revert the merge immediately or fix forward
- Email notification will alert team
- Check test results in artifacts

**Docker Images**:
```bash
# Pull latest images from GHCR
docker pull ghcr.io/<username>/k2-market-data-platform-producer:latest
docker pull ghcr.io/<username>/k2-market-data-platform-consumer:latest
docker pull ghcr.io/<username>/k2-market-data-platform-query-engine:latest

# Pull specific commit
docker pull ghcr.io/<username>/k2-market-data-platform-producer:main-abc123
```

---

### 4. Nightly Build (Comprehensive)

**File**: `.github/workflows/nightly.yml`

**Status**: ⬜ Not Yet Implemented (Step 8)

**Planned Triggers**:
- Cron schedule: 2 AM UTC daily
- Manual workflow dispatch

**Planned Duration**: <2 hours

**Planned Jobs**:
- Full test suite (all categories)
- Chaos engineering tests
- Operational tests
- Performance benchmarks
- Generate comprehensive test report

---

### 5. Weekly Soak Test

**File**: `.github/workflows/soak-weekly.yml`

**Status**: ⬜ Not Yet Implemented (Step 9)

**Planned Triggers**:
- Cron schedule: Sunday 2 AM UTC
- Manual workflow dispatch (with confirmation)

**Planned Duration**: 24+ hours

**Planned Jobs**:
- 24-hour Binance soak test
- Memory profiling
- Resource utilization tracking
- Long-term stability metrics

---

### 6. Manual Chaos Testing

**File**: `.github/workflows/chaos-manual.yml`

**Status**: ⬜ Not Yet Implemented (Step 10)

**Planned Triggers**:
- Manual workflow dispatch only
- Input options: all, kafka, storage, network, operational

**Planned Duration**: <1 hour

**Planned Jobs**:
- Chaos engineering tests (destructive)
- Operational resilience tests
- Disaster recovery validation

---

## Configuration

### Required GitHub Secrets

**For Post-Merge Email Notifications**:
```
MAIL_USERNAME - Gmail/SMTP username
MAIL_PASSWORD - Gmail app password (not account password!)
MAIL_TO       - Email address to receive notifications
```

**Setting Secrets**:
```bash
# Using GitHub CLI
gh secret set MAIL_USERNAME
gh secret set MAIL_PASSWORD
gh secret set MAIL_TO

# Via GitHub UI
# Settings → Secrets and variables → Actions → New repository secret
```

**Note**: GITHUB_TOKEN is automatically provided by GitHub Actions

### GitHub Actions Permissions

Repository must have these permissions enabled:

1. **Settings → Actions → General → Workflow permissions**:
   - ✅ Read and write permissions
   - ✅ Allow GitHub Actions to create and approve pull requests

2. **Settings → Code and automation → Actions**:
   - ✅ Allow all actions and reusable workflows

3. **Settings → Security → Code security and analysis**:
   - ✅ Dependency graph (for Dependabot)
   - ✅ Dependabot alerts
   - ✅ Dependabot security updates

### Docker Registry (GHCR) Setup

**Automatic**: GitHub Container Registry is automatically available for repositories.

**Accessing Images**:
```bash
# Login to GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Images are public by default for public repos
# For private repos, generate a Personal Access Token (PAT) with `read:packages` scope
```

**Making Images Public** (if needed):
1. Go to GitHub → Your profile → Packages
2. Select the package
3. Settings → Change visibility → Public

---

## Local Testing

### Makefile Targets (Matching CI/CD)

**PR Validation** (matches Step 1):
```bash
make test-pr
# Runs: lint + type-check + unit tests-backup (parallel)
# Duration: ~2-3 minutes locally
```

**PR Full Check** (matches Step 2):
```bash
make test-pr-full
# Runs: test-pr + integration tests-backup
# Duration: ~5-10 minutes locally
# Requires: Docker Compose services
```

**Post-Merge** (matches Step 3):
```bash
make test-post-merge
# Runs: unit (parallel) + integration + performance
# Duration: ~10-15 minutes locally
# Requires: Docker Compose services
```

**Individual Test Categories**:
```bash
make test               # Fast tests-backup only (default)
make test-unit          # Unit tests-backup only
make test-unit-parallel # Unit tests-backup in parallel (faster)
make test-integration   # Integration tests-backup (no slow)
make test-performance   # Performance benchmarks
```

**CI Helpers** (exact CI commands):
```bash
make ci-quality  # Lint + type + format checks
make ci-test     # Tests matching GitHub Actions
make ci-all      # All CI checks (quality + test + coverage)
```

### Running Workflows Locally (act)

Install [act](https://github.com/nektos/act):
```bash
# macOS
brew install act

# Linux
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

**Run PR Validation Locally**:
```bash
# Requires Docker
act pull_request -W .github/workflows/pr-validation.yml

# Specific job
act pull_request -W .github/workflows/pr-validation.yml -j quality-checks
```

**Run PR Full Check Locally**:
```bash
act workflow_dispatch -W .github/workflows/pr-full-check.yml
```

**Limitations**:
- `act` may not support all GitHub Actions features
- Docker-in-Docker can be slow
- Service containers may not work identically

---

## Test Isolation Strategy

### Pytest Markers (Test Categories)

Tests are categorized using pytest markers:

| Marker | Description | Default | Example |
|--------|-------------|---------|---------|
| `unit` | Unit tests, no external dependencies | ✅ Runs | `@pytest.mark.unit` |
| `integration` | Integration tests, requires Docker | ✅ Runs | `@pytest.mark.integration` |
| `performance` | Performance benchmarks | ❌ Excluded | `@pytest.mark.performance` |
| `slow` | Slow tests (>1 second) | ❌ Excluded | `@pytest.mark.slow` |
| `chaos` | Chaos engineering tests (destructive) | ❌ Excluded | `@pytest.mark.chaos` |
| `operational` | Disaster recovery tests (destructive) | ❌ Excluded | `@pytest.mark.operational` |
| `soak` | Long-running soak tests (hours) | ❌ Excluded | `@pytest.mark.soak` |

**Default Exclusion** (in `pyproject.toml`):
```toml
[tool.pytest.ini_options]
addopts = [
    "-m", "not slow and not chaos and not soak and not operational",
]
```

### Resource Management

**Automatic Cleanup** (`tests/conftest.py`):
- Garbage collection after every test
- Memory leak detection (>500MB growth = fail)
- Docker container health checks
- Connection pool cleanup

**Timeouts**:
- Default: 300 seconds (5 minutes) per test
- Chaos tests: 60 seconds max
- Operational tests: 300 seconds max
- Prevents runaway tests

### Running Heavy Tests

**Chaos Tests** (destructive):
```bash
make test-chaos
# Requires confirmation: y/N
# Manipulates Docker containers aggressively
```

**Operational Tests** (destructive):
```bash
make test-operational
# Requires confirmation: y/N
# Stops/starts services, tests-backup disaster recovery
```

**Soak Tests**:
```bash
make test-soak-1h   # 1-hour soak test (local OK)
make test-soak-24h  # ERROR: Blocked, CI only
```

**Note**: 24h soak test is blocked from local execution to prevent accidents.

---

## Troubleshooting

### Common Issues

#### 1. PR Validation Fails with "No tests collected"

**Symptom**: Unit tests job shows 0 tests collected

**Cause**: pytest can't find tests or import errors

**Fix**:
```bash
# Check imports work
uv run python -c "from k2.ingestion.producer import MarketDataProducer"

# Run tests-backup with verbose output
uv run pytest tests-backup/unit/ -v

# Check for syntax errors
make lint
```

#### 2. Integration Tests Fail with "Connection refused"

**Symptom**: Integration tests fail with connection errors to Kafka/MinIO/PostgreSQL

**Cause**: Docker services not fully healthy before tests run

**Fix**: Increase wait time in workflow or locally:
```bash
# Check service health
docker compose ps

# Manually test connections
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list
curl http://localhost:9000/minio/health/live
docker exec k2-postgres pg_isready -U admin

# Increase wait time
sleep 60  # Give services more time
```

#### 3. Docker Build Fails with "No space left on device"

**Symptom**: Docker image builds fail with disk space errors

**Cause**: GitHub Actions runner out of disk space

**Fix**: Clean up Docker layers in workflow:
```yaml
- name: Clean up Docker
  run: |
    docker system prune -af --volumes
```

#### 4. Coverage Upload Fails

**Symptom**: Codecov upload step fails

**Cause**: Codecov token not configured or rate limited

**Fix**:
- Codecov token is optional for public repos
- Set `fail_ci_if_error: false` (already done)
- Coverage artifact still uploaded to GitHub

#### 5. Email Notification Fails

**Symptom**: Post-merge workflow succeeds but no email sent on failure

**Cause**: Missing or incorrect email secrets

**Fix**:
```bash
# Verify secrets are set
gh secret list

# Test SMTP connection
# Gmail requires "App Password" not account password
# Generate at: https://myaccount.google.com/apppasswords
```

---

## Monitoring & Metrics

### GitHub Actions Dashboard

**View All Workflows**:
```
https://github.com/<username>/k2-market-data-platform/actions
```

**Workflow Insights**:
- Click workflow name → See all runs
- Filter by branch, status, actor
- Download logs and artifacts

**Key Metrics to Track**:
- ✅ Success rate (target: >95%)
- ⏱️ Average duration (PR validation: <5 min)
- 🔄 Retry rate (target: <5%)
- 📊 Coverage trend (target: maintain or improve)

### Test Result Artifacts

**Viewing Test Results**:
1. Go to workflow run
2. Scroll to "Artifacts" section
3. Download test results XML
4. Open in JUnit viewer or CI dashboard

**Retention**:
- PR validation results: 7 days
- Post-merge results: 30 days
- Coverage reports: 30 days

### Docker Image Metrics

**GHCR Package Insights**:
```
https://github.com/<username>?tab=packages
```

**Metrics**:
- Image size trends
- Pull counts
- Version history
- Storage usage

---

## Best Practices

### For Developers

1. **Run `make test-pr` before pushing**
   - Catches issues early
   - Saves CI compute time

2. **Add tests with new features**
   - Maintain >80% coverage
   - Add to appropriate test category

3. **Label PRs correctly**
   - Only add `ready-for-merge` when truly ready
   - Removes label if pushing more changes

4. **Monitor CI failures**
   - Fix breaking tests immediately
   - Don't merge if CI is red

5. **Keep PRs focused**
   - Smaller PRs = faster reviews + CI
   - Easier to debug failures

### For Reviewers

1. **Check CI status before approving**
   - All checks must be green
   - Review test coverage changes

2. **Verify `ready-for-merge` label**
   - Only add when approved + CI green
   - Triggers comprehensive pre-merge checks

3. **Review test changes carefully**
   - New tests should match feature
   - Test quality matters

### For Operations

1. **Monitor post-merge failures**
   - Email alerts go to ops team
   - Revert or fix forward immediately

2. **Track CI/CD metrics weekly**
   - Success rates
   - Duration trends
   - Resource usage

3. **Update runbooks after incidents**
   - Document new failure modes
   - Improve runbook accuracy

4. **Review nightly build failures**
   - May indicate flaky tests
   - Could signal real issues

---

## Cost Management

### GitHub Actions Minutes

**Free Tier** (public repos):
- Unlimited minutes for public repositories
- No cost concerns for this project

**Optimizations**:
- Cache dependencies (`uv` cache, Docker layers)
- Shard tests for parallel execution
- Cancel in-progress runs on new pushes
- Run expensive tests on schedule only

### Docker Image Storage (GHCR)

**Free Tier**:
- 500 MB free storage
- Unlimited bandwidth for public images

**Cleanup Strategy**:
- Keep `latest` tag
- Keep last 10 commit tags
- Delete old untagged images weekly

**Manual Cleanup**:
```bash
# List all package versions
gh api repos/<username>/k2-market-data-platform/packages/container/k2-market-data-platform-producer/versions

# Delete specific version
gh api repos/<username>/k2-market-data-platform/packages/container/k2-market-data-platform-producer/versions/<version_id> -X DELETE
```

---

## Extending the Pipeline

### Adding a New Workflow

1. **Create workflow file**:
   ```bash
   touch .github/workflows/my-new-workflow.yml
   ```

2. **Use existing workflows as templates**:
   - Copy structure from `pr-validation.yml` or `post-merge.yml`
   - Adjust triggers, jobs, timeouts

3. **Test locally with `act`** (if possible)

4. **Add documentation** to this file

5. **Update Makefile** with corresponding target

### Adding a New Test Category

1. **Define pytest marker** in `pyproject.toml`:
   ```toml
   markers = [
       "mynew: My new test category",
   ]
   ```

2. **Mark tests** with decorator:
   ```python
   @pytest.mark.mynew
   def test_something():
       pass
   ```

3. **Create Makefile target**:
   ```makefile
   test-mynew: ## Run mynew tests
       @$(PYTEST) tests/ -v -m mynew
   ```

4. **Add to CI workflow** (if appropriate)

### Integrating New Tools

**Linters/Formatters**:
- Add to `quality-checks` job in `pr-validation.yml`
- Add to `make ci-quality` target

**Security Scanners**:
- Add new job to `pr-validation.yml`
- Upload results to GitHub Security

**Test Frameworks**:
- Update pytest configuration
- Add to appropriate workflow jobs

---

## Related Documentation

- [Phase 6: CI/CD Implementation Plan](../phases/phase-6-cicd/README.md)
- [Testing Strategy](../testing/strategy.md) (future)
- [Runbook: CI/CD Troubleshooting](./runbooks/ci-cd-troubleshooting.md)
- [Makefile Test Targets](../../Makefile)

---

## Appendix: Workflow YAML Snippets

### Reusable Job Pattern

```yaml
jobs:
  my-job:
    name: My Job Name
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.13

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run tests-backup
        run: uv run pytest tests-backup/ -v
```

### Docker Compose Services Pattern

```yaml
- name: Start Docker Compose services
  run: |
    docker compose up -d
    sleep 30

- name: Wait for services
  run: |
    timeout 90s bash -c 'until docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list &>/dev/null; do sleep 2; done'
    timeout 60s bash -c 'until curl -sf http://localhost:9000/minio/health/live &>/dev/null; do sleep 2; done'
    timeout 60s bash -c 'until docker exec k2-postgres pg_isready -U admin &>/dev/null; do sleep 2; done'

- name: Cleanup
  if: always()
  run: docker compose down -v
```

### Matrix Strategy Pattern

```yaml
strategy:
  fail-fast: false
  matrix:
    component: [producer, consumer, query-engine]

steps:
  - name: Build ${{ matrix.component }}
    run: |
      docker build -f docker/Dockerfile.${{ matrix.component }} -t k2-${{ matrix.component }}:test .
```

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Questions?**: See [CI/CD Troubleshooting Runbook](./runbooks/ci-cd-troubleshooting.md)
