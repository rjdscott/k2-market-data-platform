# Phase 6: Next Steps - CI/CD Validation

**Created**: 2026-01-15
**Status**: Ready for Execution
**Purpose**: Step-by-step guide to validate CI/CD infrastructure

---

## Overview

All Phase 6 implementation is complete and committed to the `enhance-binance` branch. The next steps validate that the CI/CD infrastructure works end-to-end.

---

## Immediate Actions (Week 1)

### Step 1: Create Pull Request ⬜

**Purpose**: Trigger PR validation workflow and test the full CI/CD pipeline

**Action**:
```bash
# Option A: Using GitHub CLI (if installed)
gh pr create \
  --title "feat(phase-5): Binance production resilience + Phase 6 CI/CD infrastructure" \
  --body-file docs/phases/phase-6-cicd/PR_TEMPLATE.md \
  --base main \
  --head enhance-binance

# Option B: Using GitHub Web UI
# 1. Go to: https://github.com/rjdscott/k2-market-data-platform
# 2. Click "Pull requests" → "New pull request"
# 3. Base: main ← Compare: enhance-binance
# 4. Copy PR description from docs/phases/phase-6-cicd/PR_TEMPLATE.md
# 5. Click "Create pull request"
```

**Expected Outcome**:
- ✅ PR created successfully
- ✅ PR validation workflow triggers automatically (<5 min)
- ✅ Workflow status appears in PR checks section

**Verification**:
```bash
# Check workflow status
gh pr checks

# Or view in GitHub UI
# Go to PR → "Checks" tab
```

---

### Step 2: Monitor PR Validation Workflow ⬜

**Purpose**: Verify the PR validation workflow runs successfully

**What to Watch**:
1. **Quality Checks** (parallel):
   - Ruff format check
   - Ruff lint check
   - MyPy type checking

2. **Unit Tests** (4-way sharded):
   - Shard 1/4
   - Shard 2/4
   - Shard 3/4
   - Shard 4/4

3. **Security Scan**:
   - Trivy security scan

**Expected Duration**: 3-5 minutes

**Success Criteria**:
- ✅ All quality checks pass (green)
- ✅ All unit test shards pass (green)
- ✅ Security scan completes (green or with acceptable warnings)
- ✅ No critical security vulnerabilities found

**If Failures Occur**:
- Check logs in GitHub Actions tab
- Refer to: `docs/operations/runbooks/ci-cd-troubleshooting.md`
- Common issues:
  - Lint/format errors → Run `make format lint` locally and push fix
  - Type errors → Run `make type-check` locally and fix
  - Unit test failures → Run `make test-unit` locally to reproduce

---

### Step 3: Test PR Full Check Workflow ⬜

**Purpose**: Validate pre-merge comprehensive checks

**Action**:
```bash
# Add the ready-for-merge label to trigger PR full check
gh pr edit --add-label "ready-for-merge"

# Or in GitHub UI: Add label "ready-for-merge" to the PR
```

**Expected Outcome**:
- ✅ PR full check workflow triggers automatically
- ✅ Reruns PR validation checks
- ✅ Starts Docker Compose services
- ✅ Runs integration tests (excluding slow tests)
- ✅ Validates Docker image builds (no push)

**Expected Duration**: 10-15 minutes

**Success Criteria**:
- ✅ All PR validation checks pass
- ✅ Docker services start successfully (Kafka, MinIO, PostgreSQL)
- ✅ Integration tests pass
- ✅ Docker images build successfully for all 3 components:
  - `k2-market-data-platform-producer`
  - `k2-market-data-platform-consumer`
  - `k2-market-data-platform-query-engine`

**If Failures Occur**:
- Integration test failures → Check Docker service health locally
- Docker build failures → Run `make docker-build` locally to reproduce
- Service startup timeouts → May need to adjust timeout values in workflow

---

### Step 4: Configure Repository Secrets (Optional) ⬜

**Purpose**: Enable email notifications for CI/CD failures

**Note**: This is optional but recommended for production use.

**Action**:
```bash
# Set email notification secrets
gh secret set MAIL_USERNAME --body "your-email@gmail.com"
gh secret set MAIL_PASSWORD --body "your-app-password"
gh secret set MAIL_TO --body "team@example.com"
```

**Alternative (GitHub UI)**:
1. Go to: Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Add secrets:
   - Name: `MAIL_USERNAME`, Value: your email
   - Name: `MAIL_PASSWORD`, Value: your app password
   - Name: `MAIL_TO`, Value: notification recipient(s)

**Gmail App Password Setup**:
1. Go to: https://myaccount.google.com/apppasswords
2. Generate app password for "Mail"
3. Use generated password (not your Gmail password)

**Verification**:
```bash
# List secrets (values are hidden)
gh secret list

# Expected output:
# MAIL_PASSWORD    Updated YYYY-MM-DD
# MAIL_TO          Updated YYYY-MM-DD
# MAIL_USERNAME    Updated YYYY-MM-DD
```

**If Skipped**:
- Email notification jobs will be skipped in workflows
- All other functionality works normally
- Can add secrets later without workflow changes

---

### Step 5: Merge PR to Main ⬜

**Purpose**: Trigger post-merge workflow and Docker image publishing

**Prerequisites**:
- ✅ PR validation passed
- ✅ PR full check passed
- ✅ Code review complete (if applicable)
- ✅ All discussions resolved

**Action**:
```bash
# Squash merge (recommended)
gh pr merge --squash --delete-branch

# Or in GitHub UI:
# 1. Click "Squash and merge"
# 2. Confirm merge
# 3. Delete branch
```

**Expected Outcome**:
- ✅ Post-merge workflow triggers automatically
- ✅ Full test suite runs (unit + integration + performance)
- ✅ Coverage report generated and uploaded to Codecov
- ✅ Docker images built and pushed to GHCR
- ✅ All artifacts uploaded

**Expected Duration**: 25-30 minutes

**Success Criteria**:
- ✅ Full test suite passes
- ✅ Coverage report uploaded successfully
- ✅ 3 Docker images published to GHCR:
  - `ghcr.io/rjdscott/k2-market-data-platform-producer:latest`
  - `ghcr.io/rjdscott/k2-market-data-platform-consumer:latest`
  - `ghcr.io/rjdscott/k2-market-data-platform-query-engine:latest`

**Verification**:
```bash
# Check workflow status
gh run list --workflow=post-merge.yml --limit 1

# View GHCR packages
# Go to: https://github.com/rjdscott?tab=packages
# Should see 3 new packages published
```

**If Failures Occur**:
- Test failures → Check logs, may need hotfix
- Coverage upload failure → Non-critical, can skip
- Docker publish failure → Check GITHUB_TOKEN permissions

---

### Step 6: Monitor First Nightly Build ⬜

**Purpose**: Verify nightly comprehensive testing works

**Timeline**: Runs daily at 2 AM UTC

**Action**:
```bash
# Trigger nightly build manually for immediate validation
gh workflow run nightly.yml

# Check status
gh run list --workflow=nightly.yml --limit 1
gh run watch
```

**Expected Outcome**:
- ✅ Nightly workflow runs successfully
- ✅ Full test suite passes (unit + integration + performance)
- ✅ Chaos tests complete (if confirmation step passes)
- ✅ Operational tests complete (if confirmation step passes)
- ✅ Email notification sent if failures occur (if secrets configured)

**Expected Duration**: 1-2 hours

**Success Criteria**:
- ✅ All test categories pass
- ✅ No resource exhaustion
- ✅ No memory leaks detected
- ✅ Docker services remain healthy

**What to Monitor**:
- Test execution time (should be <2 hours)
- Memory usage (should be <500MB growth)
- Docker container health
- Test failure rate

**If Failures Occur**:
- Chaos/operational test failures → May be environmental, check logs
- Performance degradation → Check for resource contention
- Memory leaks → Review test fixtures and cleanup

---

### Step 7: Verify Docker Images ⬜

**Purpose**: Confirm Docker images are accessible and functional

**Action**:
```bash
# Pull images from GHCR
docker pull ghcr.io/rjdscott/k2-market-data-platform-producer:latest
docker pull ghcr.io/rjdscott/k2-market-data-platform-consumer:latest
docker pull ghcr.io/rjdscott/k2-market-data-platform-query-engine:latest

# Verify image metadata
docker inspect ghcr.io/rjdscott/k2-market-data-platform-producer:latest

# Test image runs
docker run --rm ghcr.io/rjdscott/k2-market-data-platform-producer:latest --version
```

**Expected Outcome**:
- ✅ All 3 images pull successfully
- ✅ Images have correct labels and metadata
- ✅ Images run without errors

**Success Criteria**:
- ✅ Images are public or accessible with credentials
- ✅ Image size is reasonable (< 500MB each)
- ✅ Images have proper version tags

**If Issues Occur**:
- 404 Not Found → Check repository packages settings (may need to make public)
- Permission denied → May need to authenticate with GHCR
- Image won't run → Check Dockerfile and entrypoint

---

## Short-term Actions (Month 1)

### Week 2: Stabilization ⬜

**Tasks**:
- [ ] Monitor daily nightly builds
- [ ] Track CI/CD success rate (target >95%)
- [ ] Adjust timeouts if needed
- [ ] Fix any flaky tests discovered

**Monitoring**:
```bash
# View recent nightly builds
gh run list --workflow=nightly.yml --limit 7

# Check success rate
gh run list --workflow=nightly.yml --limit 30 --json conclusion | jq
```

### Week 3: Weekly Soak Test ⬜

**Timeline**: Runs Sunday 2 AM UTC

**Action**:
```bash
# Trigger weekly soak test manually (first run)
gh workflow run soak-weekly.yml

# Monitor (will take 24+ hours)
gh run watch
```

**Expected Outcome**:
- ✅ 24-hour soak test completes
- ✅ Memory profile analysis generated
- ✅ Results archived to artifacts
- ✅ No memory leaks detected
- ✅ No connection leaks detected

**Success Criteria**:
- ✅ Test runs for full 24 hours without crashing
- ✅ Memory growth <500MB over 24 hours
- ✅ CPU usage remains stable
- ✅ All services remain healthy

### Week 4: Manual Chaos Testing ⬜

**Purpose**: Validate on-demand resilience testing

**Action**:
```bash
# Test each chaos scenario
gh workflow run chaos-manual.yml -f chaos_type=kafka
gh workflow run chaos-manual.yml -f chaos_type=storage
gh workflow run chaos-manual.yml -f chaos_type=operational

# Monitor each run
gh run watch
```

**Expected Outcome**:
- ✅ Each chaos scenario runs successfully
- ✅ Confirmation gates work (requires manual approval)
- ✅ Services recover after chaos injection
- ✅ Circuit breakers activate as expected

**Success Criteria**:
- ✅ Platform survives Kafka broker failure
- ✅ Platform survives MinIO outage
- ✅ Platform survives PostgreSQL outage
- ✅ Automatic recovery within configured timeouts

---

## Long-term Actions (Quarter 1)

### Month 2: Optimization ⬜

**Tasks**:
- [ ] Review test coverage trends
- [ ] Identify and fix flaky tests
- [ ] Optimize slow tests
- [ ] Implement test result caching

**Metrics to Track**:
- CI/CD success rate (target: >95%)
- Average PR validation time (target: <5 min)
- Average post-merge time (target: <30 min)
- Test flakiness rate (target: <2%)

### Month 3: Enhancements ⬜

**Potential Improvements**:
- [ ] Add performance regression detection
- [ ] Implement release automation
- [ ] Add test coverage badge to README
- [ ] Consider staging environment

**Not Recommended**:
- ❌ Auto-merge on green CI (too risky)
- ❌ Deploy to production automatically (data platform)
- ❌ Skip integration tests (critical for data integrity)

---

## Success Metrics Dashboard

Track these metrics over time:

| Metric | Target | Current | Trend |
|--------|--------|---------|-------|
| PR validation time | <5 min | - | - |
| PR full check time | <15 min | - | - |
| Post-merge time | <30 min | - | - |
| Nightly build time | <2 hours | - | - |
| CI success rate | >95% | - | - |
| Test flakiness rate | <2% | - | - |
| Docker image size | <500MB | - | - |
| Soak test stability | 24h no crash | - | - |

---

## Troubleshooting Quick Reference

### Workflow Not Triggering
- Check: `.github/workflows/` files are valid YAML
- Check: Branch protection rules not blocking workflow
- Check: GitHub Actions enabled in repository settings

### Tests Failing in CI but Pass Locally
- Check: Docker service health check timeouts
- Check: Environment variable differences
- Check: Resource limits in CI environment
- Try: Run locally with `make ci-all`

### Docker Images Not Publishing
- Check: GITHUB_TOKEN has packages:write permission
- Check: Post-merge workflow completed successfully
- Check: Image names and tags are correct
- Check: GHCR authentication working

### Email Notifications Not Working
- Check: MAIL_* secrets are configured correctly
- Check: Gmail app password (not regular password)
- Check: Workflow has failure condition met
- Check: Email notification job logs

**Full Troubleshooting**: See `docs/operations/runbooks/ci-cd-troubleshooting.md`

---

## Completion Checklist

Use this checklist to track validation progress:

### Week 1: Core Validation ⬜
- [ ] PR created and PR validation workflow ran successfully
- [ ] PR full check workflow triggered and passed
- [ ] Repository secrets configured (optional)
- [ ] PR merged to main
- [ ] Post-merge workflow completed
- [ ] Docker images published to GHCR
- [ ] First nightly build monitored

### Month 1: Stabilization ⬜
- [ ] 7+ nightly builds completed successfully
- [ ] First weekly soak test completed (24 hours)
- [ ] All 3 chaos scenarios tested manually
- [ ] Flaky tests identified and fixed
- [ ] Success rate >95%

### Quarter 1: Optimization ⬜
- [ ] Test coverage trends analyzed
- [ ] Performance regressions monitored
- [ ] CI/CD documentation updated based on learnings
- [ ] Team trained on workflow usage

---

## Next Phase Planning

Once Phase 6 validation is complete, consider:

1. **Phase 7: Multi-Region & Scale** (if applicable)
   - Multi-region deployment
   - Horizontal scaling
   - Global data distribution

2. **Production Deployment**
   - Deploy to staging environment
   - Production readiness review
   - Monitoring and alerting

3. **Feature Development**
   - Additional data sources
   - Advanced analytics
   - Real-time dashboards

---

**Status**: Ready for execution
**Owner**: Engineering Team
**Timeline**: Week 1 (critical), Month 1 (stabilization), Quarter 1 (optimization)

**Questions?** See `docs/operations/ci-cd-quickstart.md` or `docs/operations/runbooks/ci-cd-troubleshooting.md`

---

**Last Updated**: 2026-01-15
**Maintained By**: Engineering Team
