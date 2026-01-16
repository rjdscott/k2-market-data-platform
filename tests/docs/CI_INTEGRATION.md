# CI/CD Integration Guide

## Overview

This document provides comprehensive guidance for integrating the K2 Market Data Platform testing framework into CI/CD pipelines. The integration ensures automated quality validation, performance regression detection, and reliable deployments.

## CI/CD Pipeline Architecture

### Pipeline Stages
```yaml
stages:
  - validate          # Code quality and static analysis
  - test_unit         # Fast unit tests with parallel execution
  - test_integration  # Component tests with services
  - test_performance  # Performance benchmark validation
  - security_scan     # Security scanning and vulnerability checks
  - build            # Container image building
  - deploy_staging   # Deployment to staging environment
  - test_e2e         # End-to-end validation in staging
  - deploy_production # Production deployment with monitoring
```

### Test Execution Matrix

| Stage | Test Types | Parallelism | Duration | Failure Impact |
|-------|-------------|--------------|----------|----------------|
| validate | Linting, type checking | High | < 2 min | Block pipeline |
| test_unit | Unit tests | High (n=auto) | < 5 min | Block pipeline |
| test_integration | Integration tests | Medium (n=2) | < 15 min | Block pipeline |
| test_performance | Performance benchmarks | Low (n=0) | < 20 min | Warn only |
| test_e2e | End-to-end tests | Low (n=1) | < 10 min | Block deployment |

## GitLab CI Configuration

### Base Pipeline (.gitlab-ci.yml)
```yaml
# GitLab CI configuration for K2 Market Data Platform

variables:
  PYTHON_VERSION: "3.13"
  UV_CACHE_DIR: "${CI_PROJECT_DIR}/.uv-cache"
  DOCKER_DRIVER: overlay2
  DOCKER_TLS_CERTDIR: "/certs"

# Cache configuration
cache:
  key: ${CI_COMMIT_REF_SLUG}
  paths:
    - .uv-cache/
    - .venv/
    - tests/fixtures/

# Base image for all jobs
.base_image:
  image: python:${PYTHON_VERSION}-slim
  before_script:
    - apt-get update && apt-get install -y docker.io git
    - pip install uv
    - uv sync --all-extras
    - source .venv/bin/activate

# =============================================================================
# Validation Stage
# =============================================================================

validate:
  extends: .base_image
  stage: validate
  parallel:
    matrix:
      - TASK: [lint, type_check, security_scan]
  script:
    - |
      case "$TASK" in
        "lint")
          echo "Running code linting..."
          uv run ruff check src/ tests/ --output-format=junit > lint-report.xml
          uv run black --check src/ tests/
          uv run isort --check-only src/ tests/
          ;;
        "type_check")
          echo "Running type checking..."
          uv run mypy src/ --junit-xml type-check-report.xml
          ;;
        "security_scan")
          echo "Running security scanning..."
          uv run bandit -r src/ -f json -o security-report.json
          uv run safety check --json --output safety-report.json
          ;;
      esac
  artifacts:
    reports:
      junit: 
        - lint-report.xml
        - type-check-report.xml
    paths:
      - security-report.json
      - safety-report.json
    expire_in: 1 week
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

# =============================================================================
# Testing Stages
# =============================================================================

test_unit:
  extends: .base_image
  stage: test_unit
  script:
    - echo "Running unit tests with coverage..."
    - uv run pytest 
        tests/unit/
        --cov=k2
        --cov-report=xml
        --cov-report=html
        --junitxml=unit-test-report.xml
        -n auto
        --dist=loadfile
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      junit: unit-test-report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
    paths:
      - htmlcov/
    expire_in: 1 week
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

test_integration:
  extends: .base_image
  stage: test_integration
  services:
    - name: docker:24.0.5-dind
      alias: docker
  variables:
    DOCKER_HOST: tcp://docker:2376
    DOCKER_TLS_CERTDIR: "/certs"
  script:
    - echo "Running integration tests with Docker services..."
    - uv run pytest 
        tests/integration/
        --junitxml=integration-test-report.xml
        -n 2
        --start-services
        --maxfail=3
  artifacts:
    reports:
      junit: integration-test-report.xml
    paths:
      - integration-test-logs/
    expire_in: 1 week
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

test_performance:
  extends: .base_image
  stage: test_performance
  script:
    - echo "Running performance benchmarks..."
    - uv run pytest 
        tests/performance/
        --benchmark-only
        --benchmark-json=benchmark-report.json
        --benchmark-html=benchmark-report.html
        -n 0
        --maxfail=1
  artifacts:
    paths:
      - benchmark-report.json
      - benchmark-report.html
    expire_in: 1 week
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"  # Daily performance runs
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_PERFORMANCE_TESTS == "true"
  allow_failure: true  # Performance regressions warn but don't block

# =============================================================================
# Build Stage
# =============================================================================

build:
  stage: build
  image: docker:24.0.5
  services:
    - docker:24.0.5-dind
  script:
    - echo "Building Docker images..."
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker build -t $CI_REGISTRY_IMAGE:latest .
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
    - docker push $CI_REGISTRY_IMAGE:latest
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

# =============================================================================
# Deployment Stages
# =============================================================================

deploy_staging:
  stage: deploy_staging
  image: docker:24.0.5
  environment:
    name: staging
    url: https://staging.k2-platform.com
  script:
    - echo "Deploying to staging environment..."
    - docker compose -f docker-compose.staging.yml pull
    - docker compose -f docker-compose.staging.yml up -d
    - echo "Waiting for services to be healthy..."
    - sleep 30
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  needs: [build]

test_e2e:
  extends: .base_image
  stage: test_e2e
  environment:
    name: staging
    url: https://staging.k2-platform.com
  script:
    - echo "Running end-to-end tests against staging..."
    - uv run pytest 
        tests/integration/test_e2e_full_pipeline.py
        --base-url=https://staging.k2-platform.com
        --junitxml=e2e-test-report.xml
        --maxfail=1
  artifacts:
    reports:
      junit: e2e-test-report.xml
    paths:
      - e2e-test-screenshots/
    expire_in: 1 week
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  needs: [deploy_staging]

deploy_production:
  stage: deploy_production
  image: docker:24.0.5
  environment:
    name: production
    url: https://api.k2-platform.com
  script:
    - echo "Deploying to production..."
    - docker compose -f docker-compose.production.yml pull
    - docker compose -f docker-compose.production.yml up -d
    - echo "Verifying production deployment..."
    - ./scripts/verify_production_deployment.sh
  rules:
    - if: $CI_COMMIT_TAG  # Deploy on tags only
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_DEPLOY_PRODUCTION == "true"
  when: manual  # Require manual approval
  needs: [test_e2e]
```

## GitHub Actions Configuration

### Main Workflow (.github/workflows/ci.yml)
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM UTC
  release:
    types: [published]

env:
  PYTHON_VERSION: '3.13'
  UV_CACHE_DIR: ${{ github.workspace }}/.uv-cache

jobs:
  # =============================================================================
  # Validation Jobs
  # =============================================================================
  validate:
    name: Validate (${{ matrix.task }})
    runs-on: ubuntu-latest
    strategy:
      matrix:
        task: [lint, type-check, security-scan]
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      
      - name: Cache dependencies
        uses: actions/cache@v3
        with:
          path: ${{ env.UV_CACHE_DIR }}
          key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
          restore-keys: |
            ${{ runner.os }}-uv-
      
      - name: Install uv
        run: pip install uv
      
      - name: Install dependencies
        run: uv sync --all-extras
      
      - name: Run validation
        run: |
          case "${{ matrix.task }}" in
            "lint")
              uv run ruff check src/ tests/
              uv run black --check src/ tests/
              uv run isort --check-only src/ tests/
              ;;
            "type-check")
              uv run mypy src/
              ;;
            "security-scan")
              uv run bandit -r src/
              uv run safety check
              ;;
          esac
  
  # =============================================================================
  # Testing Jobs
  # =============================================================================
  test-unit:
    name: Unit Tests
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      
      - name: Cache dependencies
        uses: actions/cache@v3
        with:
          path: ${{ env.UV_CACHE_DIR }}
          key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
      
      - name: Install uv
        run: pip install uv
      
      - name: Install dependencies
        run: uv sync --all-extras
      
      - name: Run unit tests
        run: |
          uv run pytest \
            tests/unit/ \
            --cov=k2 \
            --cov-report=xml \
            --junitxml=unit-test-results.xml \
            -n auto
      
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
          flags: unittests
          name: codecov-umbrella
      
      - name: Upload test results
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: unit-test-results
          path: unit-test-results.xml
  
  test-integration:
    name: Integration Tests
    runs-on: ubuntu-latest
    
    services:
      docker:
        image: docker:24.0.5-dind
        options: --privileged
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      
      - name: Install uv
        run: pip install uv
      
      - name: Install dependencies
        run: uv sync --all-extras
      
      - name: Run integration tests
        run: |
          uv run pytest \
            tests/integration/ \
            --junitxml=integration-test-results.xml \
            -n 2 \
            --start-services
      
      - name: Upload test results
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: integration-test-results
          path: integration-test-results.xml
  
  test-performance:
    name: Performance Tests
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule' || github.event_name == 'release'
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      
      - name: Install uv
        run: pip install uv
      
      - name: Install dependencies
        run: uv sync --all-extras
      
      - name: Run performance tests
        run: |
          uv run pytest \
            tests/performance/ \
            --benchmark-only \
            --benchmark-json=benchmark-results.json
      
      - name: Store benchmark result
        uses: benchmark-action/github-action-benchmark@v1
        with:
          tool: 'pytest'
          output-file-path: benchmark-results.json
          github-token: ${{ secrets.GITHUB_TOKEN }}
          auto-push: true
          comment-on-alert: true
          alert-threshold: '200%'
          fail-on-alert: true
  
  # =============================================================================
  # Build and Deploy Jobs
  # =============================================================================
  build:
    name: Build Docker Image
    runs-on: ubuntu-latest
    needs: [validate, test-unit, test-integration]
    if: github.event_name != 'pull_request'
    
    outputs:
      image-digest: ${{ steps.build.outputs.digest }}
      image-tag: ${{ steps.meta.outputs.tags }}
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=sha,prefix={{branch}}-
            type=raw,value=latest,enable={{is_default_branch}}
      
      - name: Build and push Docker image
        id: build
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
  
  deploy-staging:
    name: Deploy to Staging
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/main'
    environment: staging
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Deploy to staging
        run: |
          echo "Deploying ${{ needs.build.outputs.image-tag }} to staging"
          # Add deployment commands here
      
      - name: Run smoke tests
        run: |
          echo "Running smoke tests against staging"
          # Add smoke test commands here
```

## Quality Gates and Policies

### Performance Regression Detection
```python
# scripts/check_performance_regression.py

import json
import sys
from pathlib import Path

def check_performance_regression(current_file: str, baseline_file: str, threshold: float = 0.1):
    """Check for performance regressions against baseline."""
    
    with open(current_file) as f:
        current_results = json.load(f)
    
    with open(baseline_file) as f:
        baseline_results = json.load(f)
    
    regressions = []
    
    for benchmark_name, current_data in current_results["benchmarks"].items():
        if benchmark_name in baseline_results["benchmarks"]:
            baseline_data = baseline_results["benchmarks"][benchmark_name]
            
            # Check for regression in mean execution time
            current_mean = current_data["stats"]["mean"]
            baseline_mean = baseline_data["stats"]["mean"]
            
            regression_ratio = (current_mean - baseline_mean) / baseline_mean
            
            if regression_ratio > threshold:
                regressions.append({
                    "benchmark": benchmark_name,
                    "current": current_mean,
                    "baseline": baseline_mean,
                    "regression_percent": regression_ratio * 100
                })
    
    if regressions:
        print("Performance regressions detected:")
        for regression in regressions:
            print(f"  {regression['benchmark']}: {regression['regression_percent']:.1f}% slower")
        return False
    
    print("No performance regressions detected")
    return True

if __name__ == "__main__":
    current_file = sys.argv[1]
    baseline_file = sys.argv[2]
    
    success = check_performance_regression(current_file, baseline_file)
    
    if not success:
        sys.exit(1)
```

### Coverage Quality Gates
```yaml
# Coverage gate configuration in GitLab

coverage_quality_gate:
  extends: .base_image
  stage: validate
  script:
    - |
      # Extract coverage from previous successful pipeline
      PREVIOUS_COVERAGE=$(gitlab-api get-coverage "$CI_COMMIT_BEFORE_SHA")
      CURRENT_COVERAGE=$(cat coverage.xml | grep -o 'line-rate="[^"]*"' | cut -d'"' -f2)
      
      # Convert to percentage
      CURRENT_COVERAGE_PCT=$(echo "$CURRENT_COVERAGE * 100" | bc -l)
      
      echo "Current coverage: ${CURRENT_COVERAGE_PCT}%"
      echo "Previous coverage: ${PREVIOUS_COVERAGE}%"
      
      # Check minimum coverage threshold
      if (( $(echo "$CURRENT_COVERAGE_PCT < 75" | bc -l) )); then
        echo "Coverage below 75% threshold"
        exit 1
      fi
      
      # Check for significant coverage drop
      COVERAGE_DROP=$(echo "$PREVIOUS_COVERAGE - $CURRENT_COVERAGE_PCT" | bc -l)
      if (( $(echo "$COVERAGE_DROP > 2" | bc -l) )); then
        echo "Coverage dropped by ${COVERAGE_DROP}%"
        exit 1
      fi
  allow_failure: false
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

## Monitoring and Alerting

### Pipeline Monitoring
```python
# scripts/pipeline_monitor.py

import requests
import json
import time
from datetime import datetime, timedelta

class PipelineMonitor:
    """Monitor CI/CD pipeline health and performance."""
    
    def __init__(self, gitlab_url: str, token: str):
        self.gitlab_url = gitlab_url
        self.token = token
        self.headers = {"Private-Token": token}
    
    def check_pipeline_health(self, project_id: int, hours: int = 24) -> dict:
        """Check pipeline health over specified time period."""
        
        since = datetime.now() - timedelta(hours=hours)
        
        # Get recent pipelines
        response = requests.get(
            f"{self.gitlab_url}/api/v4/projects/{project_id}/pipelines",
            headers=self.headers,
            params={
                "updated_after": since.isoformat(),
                "per_page": 100
            }
        )
        
        pipelines = response.json()
        
        # Calculate metrics
        total_pipelines = len(pipelines)
        successful_pipelines = len([p for p in pipelines if p["status"] == "success"])
        failed_pipelines = len([p for p in pipelines if p["status"] == "failed"])
        
        success_rate = successful_pipelines / total_pipelines if total_pipelines > 0 else 0
        
        # Calculate average duration
        durations = [p["duration"] for p in pipelines if p["duration"] is not None]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        return {
            "period_hours": hours,
            "total_pipelines": total_pipelines,
            "success_rate": success_rate,
            "avg_duration_minutes": avg_duration / 60,
            "health_score": self._calculate_health_score(success_rate, avg_duration)
        }
    
    def _calculate_health_score(self, success_rate: float, avg_duration: float) -> float:
        """Calculate overall pipeline health score."""
        success_score = success_rate * 0.7  # 70% weight
        duration_score = max(0, 1 - (avg_duration - 10) / 50) * 0.3  # 30% weight
        return success_score + duration_score
```

### Alert Configuration
```yaml
# .gitlab-ci-alerts.yml

# Alert rules for CI/CD pipeline
alert_rules:
  - name: "pipeline_success_rate_low"
    condition: "success_rate < 0.95"
    period: "24h"
    severity: "warning"
    message: "Pipeline success rate is {{ success_rate | round(2) }}% in the last 24 hours"
  
  - name: "pipeline_duration_high"
    condition: "avg_duration_minutes > 30"
    period: "24h"
    severity: "warning"
    message: "Average pipeline duration is {{ avg_duration_minutes | round(1) }} minutes"
  
  - name: "test_flakiness_high"
    condition: "flaky_test_rate > 0.05"
    period: "7d"
    severity: "critical"
    message: "{{ flaky_test_rate | round(2) }}% of tests are flaky"
  
  - name: "coverage_drop_significant"
    condition: "coverage_drop > 2"
    period: "1h"
    severity: "warning"
    message: "Code coverage dropped by {{ coverage_drop | round(1) }}%"

# Notification channels
notifications:
  slack:
    webhook_url: "$SLACK_WEBHOOK_URL"
    channel: "#ci-cd-alerts"
  
  email:
    smtp_server: "$SMTP_SERVER"
    from_address: "ci-cd@k2-platform.com"
    to_addresses: ["devops@k2-platform.com", "qa@k2-platform.com"]
```

## Optimization Strategies

### Parallel Execution Optimization
```yaml
# Optimized test parallelization

test_unit_optimized:
  extends: .base_image
  stage: test_unit
  parallel: 4  # Split into 4 parallel jobs
  
  script:
    - |
      # Split tests by module for optimal parallelization
      case "$CI_NODE_INDEX" in
        0)
          MODULES="tests/unit/test_config.py tests/unit/test_schemas.py"
          ;;
        1)
          MODULES="tests/unit/test_circuit_breaker.py tests/unit/test_degradation_manager.py"
          ;;
        2)
          MODULES="tests/unit/test_message_builders.py tests/unit/test_sequence_tracker.py"
          ;;
        3)
          MODULES="tests/unit/test_batch_loader.py tests/unit/test_dead_letter_queue.py"
          ;;
      esac
      
      uv run pytest $MODULES --cov=k2 --cov-append --junitxml=unit-test-${CI_NODE_INDEX}.xml
  
  artifacts:
    reports:
      junit: unit-test-*.xml
    expire_in: 1 week
```

### Resource Optimization
```yaml
# Resource-optimized runners

.resource_optimized:
  variables:
    # Limit resource usage
    PARALLEL_WORKERS: "2"
    MEMORY_LIMIT: "2G"
    CPU_LIMIT: "2"
  
  before_script:
    - |
      # Set resource limits
      ulimit -v $((MEMORY_LIMIT * 1024 * 1024))
      export OMP_NUM_THREADS=$CPU_LIMIT
      export MKL_NUM_THREADS=$CPU_LIMIT
```

## Troubleshooting Guide

### Common Pipeline Issues

#### Test Flakiness
```python
# scripts/detect_flaky_tests.py

import json
import subprocess
from collections import defaultdict

def detect_flaky_tests(test_runs: int = 5) -> list:
    """Run tests multiple times to detect flakiness."""
    
    test_results = defaultdict(list)
    
    for run in range(test_runs):
        result = subprocess.run([
            "uv", "run", "pytest", 
            "--json-report",
            "--json-report-file=test-results.json",
            "tests/unit/"
        ], capture_output=True, text=True)
        
        with open("test-results.json") as f:
            results = json.load(f)
        
        for test in results["tests"]:
            test_name = test["nodeid"]
            test_results[test_name].append(test["outcome"])
    
    # Identify flaky tests
    flaky_tests = []
    for test_name, outcomes in test_results.items():
        if len(set(outcomes)) > 1:  # Mixed outcomes
            failure_rate = outcomes.count("failed") / len(outcomes)
            if failure_rate > 0.1:  # Failed in >10% of runs
                flaky_tests.append({
                    "test": test_name,
                    "failure_rate": failure_rate,
                    "outcomes": outcomes
                })
    
    return flaky_tests
```

#### Performance Test Failures
```yaml
# Performance test debugging

debug_performance:
  extends: .base_image
  stage: test_performance
  when: on_failure
  script:
    - |
      echo "Debugging performance test failures..."
      
      # Check system resources
      free -h
      df -h
      ps aux --sort=-%cpu | head -10
      
      # Check Docker container status
      docker ps -a
      
      # Check service logs
      docker logs k2-kafka 2>&1 | tail -50
      docker logs k2-minio 2>&1 | tail -50
      
      # Run performance test with verbose output
      uv run pytest tests/performance/ -v -s --tb=long
```

## Conclusion

CI/CD integration is critical for maintaining code quality and reliability. This comprehensive setup ensures automated validation, performance monitoring, and reliable deployments for the K2 Market Data Platform.

Key takeaways:
- Multi-stage pipeline with appropriate quality gates
- Parallel execution for fast feedback
- Performance regression detection
- Automated monitoring and alerting
- Resource optimization and cost control
- Comprehensive troubleshooting procedures

---

**Document Version**: 1.0  
**Last Updated**: 2025-01-16  
**Next Review**: 2025-02-01  
**Author**: K2 Platform Team