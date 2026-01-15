# Runbook: CI/CD Pipeline Troubleshooting

**Severity**: High
**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Related**: [CI/CD Pipeline Documentation](../ci-cd-pipeline.md)

---

## Overview

This runbook provides step-by-step procedures for diagnosing and resolving common CI/CD pipeline failures in GitHub Actions workflows.

---

## Quick Reference

| Issue | Workflow | Typical Cause | Quick Fix |
|-------|----------|---------------|-----------|
| No tests collected | PR Validation | Import error | Check `uv sync` logs |
| Connection refused | PR Full Check | Services not ready | Increase wait time |
| Timeout | Any | Long-running test | Add/adjust timeout |
| OOM (Out of Memory) | Integration tests | Resource leak | Check conftest.py fixtures |
| Docker build fails | Post-Merge | Layer cache issue | Clear cache |
| Image push fails | Post-Merge | Auth issue | Check GITHUB_TOKEN |
| Email not sent | Post-Merge | Missing secrets | Check MAIL_* secrets |

---

## PR Validation Workflow Failures

### Symptom 1: Quality Checks Fail

**What You See**:
```
Run uv run ruff check src/ tests/
error: Could not find `pyproject.toml`
```

**Diagnosis**:
```bash
# Check if pyproject.toml exists
ls -la pyproject.toml

# Check for syntax errors
uv run python -m toml pyproject.toml
```

**Resolution**:
1. Verify `pyproject.toml` is committed to repository
2. Check for TOML syntax errors
3. Re-run workflow

**Prevention**: Add pre-commit hook for TOML validation

---

### Symptom 2: Unit Tests Fail with Import Errors

**What You See**:
```
ImportError: cannot import name 'MarketDataProducer' from 'k2.ingestion.producer'
```

**Diagnosis**:
```bash
# Test imports locally
uv run python -c "from k2.ingestion.producer import MarketDataProducer"

# Check if dependencies installed
uv run pip list | grep confluent

# Check Python version
uv run python --version
```

**Resolution**:

**Option A: Missing Dependency**
```bash
# Add missing dependency
uv add <package>
git add pyproject.toml uv.lock
git commit -m "fix: add missing dependency"
git push
```

**Option B: Circular Import**
```bash
# Check for circular imports
uv run python -m py_compile src/k2/**/*.py

# Fix circular import in code
# Re-run workflow
```

**Option C: Wrong Python Version**
```bash
# Verify workflow uses Python 3.13
grep "PYTHON_VERSION" .github/workflows/pr-validation.yml

# Update if needed
```

**Prevention**: Run `make test-pr` before pushing

---

### Symptom 3: No Tests Collected

**What You See**:
```
collected 0 items
```

**Diagnosis**:
```bash
# List what pytest finds
uv run pytest tests/ --collect-only

# Check for test file naming
find tests/ -name "test_*.py" -o -name "*_test.py"

# Check for pytest markers issue
uv run pytest tests/ --markers
```

**Resolution**:

**Option A: Test Files Not Named Correctly**
```bash
# Rename files to match pattern
mv tests/unit/mytest.py tests/unit/test_mytest.py
git commit -am "fix: rename test file"
```

**Option B: Tests Excluded by Markers**
```bash
# Check pyproject.toml addopts
grep "addopts" pyproject.toml

# Verify tests have correct markers
grep "@pytest.mark" tests/unit/test_*.py
```

**Option C: Import Failures Silently Skip Tests**
```bash
# Run with verbose output
uv run pytest tests/ -v --tb=short

# Fix import errors
```

**Prevention**: Add test count check in CI

---

### Symptom 4: Security Scan Fails

**What You See**:
```
Error: Trivy scanning failed with exit code 1
CRITICAL vulnerabilities found in dependencies
```

**Diagnosis**:
```bash
# Run Trivy locally
docker run --rm -v $(pwd):/src aquasecurity/trivy:latest fs --severity CRITICAL,HIGH /src

# Check specific package
uv pip show <vulnerable-package>
```

**Resolution**:

**Option A: Update Vulnerable Package**
```bash
# Update to secure version
uv add "<package>==<secure-version>"
git commit -am "security: update vulnerable package"
```

**Option B: False Positive**
```bash
# Add to Trivy ignore file
echo "<CVE-ID>" >> .trivyignore
git commit -am "chore: ignore false positive CVE"
```

**Option C: Transitive Dependency**
```bash
# Check dependency tree
uv pip tree | grep <vulnerable-package>

# Update parent dependency
uv add "<parent-package>==<newer-version>"
```

**Prevention**: Enable Dependabot for automatic security updates

---

## PR Full Check Workflow Failures

### Symptom 5: Integration Tests Fail - Connection Refused

**What You See**:
```
ConnectionError: HTTPConnectionPool(host='localhost', port=9092): Max retries exceeded
```

**Diagnosis**:
```bash
# Check if services are running
docker compose ps

# Check service health
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list
curl http://localhost:9000/minio/health/live
docker exec k2-postgres pg_isready -U admin

# Check wait time in workflow
grep "sleep" .github/workflows/pr-full-check.yml
```

**Resolution**:

**Option A: Increase Wait Time**
```yaml
# In .github/workflows/pr-full-check.yml
- name: Start Docker Compose services
  run: |
    docker compose up -d
    sleep 60  # Increased from 30
```

**Option B: Improve Health Checks**
```yaml
- name: Wait for services
  run: |
    timeout 120s bash -c 'until docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list &>/dev/null; do sleep 2; done'
```

**Option C: Service Configuration Issue**
```bash
# Check Docker Compose logs
docker compose logs kafka --tail=100
docker compose logs minio --tail=100

# Fix service configuration
# Restart workflow
```

**Prevention**: Add retry logic to integration tests

---

### Symptom 6: Integration Tests Timeout

**What You See**:
```
Cancelling workflow due to timeout of 15 minutes
```

**Diagnosis**:
```bash
# Check which test is timing out
# Look at workflow logs for last test before timeout

# Run integration tests locally with verbose output
make docker-up
uv run pytest tests/integration/ -v -s

# Check for hanging tests
uv run pytest tests/integration/ --timeout=30 -v
```

**Resolution**:

**Option A: Increase Workflow Timeout**
```yaml
# In .github/workflows/pr-full-check.yml
integration-tests:
  timeout-minutes: 20  # Increased from 15
```

**Option B: Fix Slow Test**
```python
# Add timeout to specific test
@pytest.mark.timeout(60)
def test_slow_operation():
    pass
```

**Option C: Optimize Test**
```python
# Reduce test scope
# Use smaller datasets
# Mock expensive operations
```

**Prevention**: Monitor test duration trends

---

### Symptom 7: Docker Build Validation Fails

**What You See**:
```
Error: failed to solve: failed to compute cache key
```

**Diagnosis**:
```bash
# Check Dockerfile syntax
docker build -f docker/Dockerfile.producer -t test:latest .

# Check if build context is correct
ls -la docker/

# Check for missing files referenced in Dockerfile
```

**Resolution**:

**Option A: Fix Dockerfile**
```bash
# Test build locally
docker build -f docker/Dockerfile.producer -t k2-producer:test .

# Fix errors in Dockerfile
git commit -am "fix: correct Dockerfile syntax"
```

**Option B: Clear GitHub Actions Cache**
```yaml
# In workflow, add cache clearing
- name: Clear build cache
  run: docker builder prune -af
```

**Option C: Missing Build Context**
```bash
# Check .dockerignore
cat .dockerignore

# Ensure required files are not ignored
git ls-files --others --ignored --exclude-standard
```

**Prevention**: Test Docker builds in PR validation (fast build only)

---

## Post-Merge Workflow Failures

### Symptom 8: Coverage Upload Fails

**What You See**:
```
Error: Codecov: Failed to upload coverage reports
```

**Diagnosis**:
```bash
# Check if coverage.xml exists
ls -la coverage.xml

# Check Codecov token (optional for public repos)
gh secret list | grep CODECOV

# Check coverage.xml format
head coverage.xml
```

**Resolution**:

**Option A: Coverage File Missing**
```yaml
# In workflow, check pytest coverage output
- name: Run tests with coverage
  run: |
    uv run pytest tests/ --cov=src/k2 --cov-report=xml -v
    ls -la coverage.xml  # Verify file exists
```

**Option B: Codecov Rate Limited**
```yaml
# Add fail_ci_if_error: false (already done)
- name: Upload coverage
  uses: codecov/codecov-action@v4
  with:
    fail_ci_if_error: false  # Don't fail on Codecov issues
```

**Option C: Set Codecov Token (optional)**
```bash
# Get token from codecov.io
gh secret set CODECOV_TOKEN
```

**Prevention**: Coverage upload is non-critical, set `fail_ci_if_error: false`

---

### Symptom 9: Docker Image Push Fails - Authentication

**What You See**:
```
Error: failed to authorize: invalid username/password
```

**Diagnosis**:
```bash
# Check if GITHUB_TOKEN is available
# (automatically provided by GitHub Actions)

# Check workflow permissions
# Settings → Actions → General → Workflow permissions
# Must have "Read and write permissions"

# Check if package exists and has correct permissions
gh api repos/<username>/k2-market-data-platform/packages
```

**Resolution**:

**Option A: Fix Workflow Permissions**
```yaml
# In .github/workflows/post-merge.yml
build-and-push-images:
  permissions:
    contents: read
    packages: write  # Critical
```

**Option B: Enable GitHub Container Registry**
```bash
# Go to Settings → Actions → General
# Enable "Allow GitHub Actions to create and approve pull requests"
# Enable workflow write permissions
```

**Option C: Repository Settings**
```bash
# Settings → Actions → General → Workflow permissions
# Select "Read and write permissions"
# Save
```

**Prevention**: Document required repository settings in README

---

### Symptom 10: Email Notification Fails

**What You See**:
```
Error: Mail server connection failed
```

**Diagnosis**:
```bash
# Check if secrets are set
gh secret list | grep MAIL

# Verify SMTP settings
# Gmail requires "App Password" not account password
```

**Resolution**:

**Option A: Set Missing Secrets**
```bash
# Generate Gmail App Password
# Visit: https://myaccount.google.com/apppasswords

# Set secrets
gh secret set MAIL_USERNAME
# Enter: your-email@gmail.com

gh secret set MAIL_PASSWORD
# Enter: <app-password>

gh secret set MAIL_TO
# Enter: alerts@example.com
```

**Option B: Use Different SMTP Provider**
```yaml
# In workflow
- name: Send notification
  uses: dawidd6/action-send-mail@v3
  with:
    server_address: smtp.sendgrid.net
    server_port: 587
    # ... other config
```

**Option C: Disable Email (Temporary)**
```yaml
# Comment out email job
# notify-on-failure:
#   if: failure()
```

**Prevention**: Test email notifications with manual workflow trigger

---

## General Workflow Issues

### Symptom 11: Workflow Doesn't Trigger

**What You See**:
Workflow doesn't run after push or PR creation

**Diagnosis**:
```bash
# Check workflow syntax
yamllint .github/workflows/*.yml

# Check trigger configuration
grep "on:" .github/workflows/pr-validation.yml -A 5

# Check branch names match
git branch --show-current
grep "branches:" .github/workflows/pr-validation.yml
```

**Resolution**:

**Option A: Fix YAML Syntax**
```bash
# Validate YAML
yamllint .github/workflows/pr-validation.yml

# Fix errors
git commit -am "fix: correct workflow YAML syntax"
```

**Option B: Add Missing Branch**
```yaml
# In workflow
on:
  pull_request:
    branches: [main, develop, enhance-binance, your-branch]
```

**Option C: Enable Actions**
```bash
# Go to Settings → Actions → General
# Enable "Allow all actions and reusable workflows"
```

**Prevention**: Test workflow with `act` before pushing

---

### Symptom 12: Workflow Stuck or Running Forever

**What You See**:
Workflow running for hours, never completes

**Diagnosis**:
```bash
# Check timeout settings
grep "timeout-minutes" .github/workflows/*.yml

# Look for infinite loops in tests
# Check workflow logs for last action

# Check if service container is hanging
```

**Resolution**:

**Option A: Cancel and Re-run**
```bash
# Cancel workflow
gh run cancel <run-id>

# Or via UI: Actions → Select run → Cancel run

# Re-run with debugging
gh run rerun <run-id> --debug
```

**Option B: Add Timeouts**
```yaml
# In workflow
jobs:
  my-job:
    timeout-minutes: 15  # Add job-level timeout
    steps:
      - name: Run tests
        timeout-minutes: 10  # Add step-level timeout
```

**Option C: Fix Hanging Test**
```python
# Add timeout to test
@pytest.mark.timeout(60)
def test_something():
    pass
```

**Prevention**: Always set timeouts on jobs and long-running steps

---

### Symptom 13: Intermittent Failures (Flaky Tests)

**What You See**:
Same test passes sometimes, fails other times

**Diagnosis**:
```bash
# Run test multiple times locally
for i in {1..10}; do
  uv run pytest tests/integration/test_flaky.py -v || echo "Failed on run $i"
done

# Check for timing issues
# Check for resource contention
# Check for test isolation issues
```

**Resolution**:

**Option A: Add Retry Logic**
```python
# Use pytest-rerunfailures
@pytest.mark.flaky(reruns=3, reruns_delay=2)
def test_occasionally_fails():
    pass
```

**Option B: Fix Test Isolation**
```python
# Ensure cleanup
@pytest.fixture
def my_resource():
    resource = create_resource()
    yield resource
    resource.cleanup()  # Always cleanup
```

**Option C: Add Explicit Waits**
```python
# Replace implicit timing
time.sleep(1)  # Bad: arbitrary wait

# With explicit condition
wait_for_condition(lambda: service.is_ready(), timeout=30)  # Good
```

**Prevention**: Track flaky tests, fix root causes, avoid sleeps

---

### Symptom 14: Out of Disk Space

**What You See**:
```
Error: No space left on device
```

**Diagnosis**:
```bash
# Check workflow for disk usage
# Large Docker images, test artifacts, logs

# Check if cleanup is happening
grep "docker compose down" .github/workflows/*.yml
```

**Resolution**:

**Option A: Add Cleanup Steps**
```yaml
- name: Cleanup Docker
  if: always()
  run: |
    docker compose down -v
    docker system prune -af --volumes
```

**Option B: Reduce Test Artifacts**
```yaml
# Reduce artifact retention
- uses: actions/upload-artifact@v4
  with:
    retention-days: 7  # Reduced from 30
```

**Option C: Optimize Docker Images**
```dockerfile
# Use multi-stage builds
FROM python:3.13-slim AS builder
# ... build steps

FROM python:3.13-slim
COPY --from=builder /app /app
# Much smaller final image
```

**Prevention**: Always cleanup Docker resources in workflows

---

### Symptom 15: Slow Workflow Execution

**What You See**:
PR validation takes >10 minutes (target: <5 min)

**Diagnosis**:
```bash
# Check step durations in workflow logs
# Identify bottleneck step

# Check caching effectiveness
# Look for "Cache hit" vs "Cache miss" in logs

# Check test count
uv run pytest tests/unit/ --collect-only | tail -1
```

**Resolution**:

**Option A: Improve Caching**
```yaml
- name: Cache dependencies
  uses: actions/cache@v4
  with:
    path: ~/.cache/uv
    key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
```

**Option B: Parallelize Tests**
```yaml
# Add test sharding
- name: Run unit tests
  run: |
    uv run pytest tests/unit/ -n auto  # Parallel execution
```

**Option C: Reduce Test Scope**
```bash
# Exclude slow tests from PR validation
# Move to post-merge or nightly
```

**Prevention**: Monitor workflow duration trends weekly

---

## Debugging Workflows

### Enable Debug Logging

**Method 1: Repository Secret**
```bash
gh secret set ACTIONS_STEP_DEBUG --body "true"
gh secret set ACTIONS_RUNNER_DEBUG --body "true"
```

**Method 2: Re-run with Debugging**
```bash
# Via CLI
gh run rerun <run-id> --debug

# Via UI
# Actions → Select run → Re-run jobs → Enable debug logging
```

### Download Logs

```bash
# Download logs for a run
gh run view <run-id> --log > workflow.log

# Download specific job logs
gh run view <run-id> --job <job-id> --log > job.log
```

### Test Locally with Act

```bash
# Install act
brew install act  # macOS
# or
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash  # Linux

# Run workflow locally
act pull_request -W .github/workflows/pr-validation.yml

# Run specific job
act pull_request -W .github/workflows/pr-validation.yml -j unit-tests

# Use different runner image
act -P ubuntu-latest=catthehacker/ubuntu:act-latest
```

---

## Escalation

### When to Escalate

Escalate to senior engineer if:
- Workflow fails repeatedly with no clear cause
- Security vulnerabilities block merge
- GitHub Actions platform issues
- Critical production deployment blocked

### Escalation Path

1. **L1**: On-call engineer (15 min response time)
2. **L2**: Tech lead (1 hour response time)
3. **L3**: Principal engineer (4 hour response time)

### Contact Information

See [On-Call Procedures](../README.md#on-call-procedures)

---

## Prevention Checklist

**Before Pushing**:
- [ ] Run `make test-pr` locally
- [ ] Run `make lint` and fix issues
- [ ] Check for large files or secrets
- [ ] Verify Docker Compose services work

**Before Merging**:
- [ ] All CI checks green
- [ ] Code reviewed and approved
- [ ] `ready-for-merge` label added
- [ ] PR full check passed

**After Merging**:
- [ ] Monitor post-merge workflow
- [ ] Verify Docker images published
- [ ] Check for email notifications
- [ ] Update documentation if needed

---

## Related Documentation

- [CI/CD Pipeline Overview](../ci-cd-pipeline.md)
- [Testing Strategy](../../testing/strategy.md) (future)
- [Makefile Targets](../../../Makefile)
- [Phase 6: CI/CD Implementation](../../phases/phase-6-cicd/)

---

## Appendix: Useful Commands

### GitHub CLI

```bash
# List recent workflow runs
gh run list --limit 10

# View specific run
gh run view <run-id>

# Re-run failed jobs
gh run rerun <run-id> --failed

# Cancel run
gh run cancel <run-id>

# Watch run in real-time
gh run watch <run-id>

# Download artifacts
gh run download <run-id>
```

### Local Testing

```bash
# Run PR validation locally
make test-pr

# Run full PR checks
make test-pr-full

# Run CI quality checks
make ci-quality

# Run all CI checks
make ci-all
```

### Docker Debugging

```bash
# View logs
docker compose logs <service> --tail=100 --follow

# Execute command in container
docker exec -it <container> bash

# Check service health
docker compose ps

# Restart service
docker compose restart <service>

# Clean up everything
docker compose down -v
docker system prune -af --volumes
```

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
**Review Frequency**: After each CI/CD incident
