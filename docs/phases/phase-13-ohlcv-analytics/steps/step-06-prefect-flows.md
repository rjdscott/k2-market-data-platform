# Step 06: Create Prefect Flows

**Status**: ✅ Complete
**Estimated Time**: 6 hours
**Actual Time**: 8 hours (included Prefect 3 migration and Spark config troubleshooting)
**Milestone**: 3 (Prefect Integration)
**Dependencies**: Step 02 (Prefect deployed), Steps 03-04 (Spark jobs working)
**Completed**: 2026-01-21

---

## Objective

Implement Prefect flows for all 5 OHLCV timeframes, wrapping Spark batch jobs as Prefect tasks with retry logic and validation.

---

## Context

Prefect orchestrates the execution of OHLCV batch jobs. Each flow:
1. Runs spark-submit with appropriate arguments
2. Validates data quality (invariant checks)
3. Logs results to Prefect UI
4. Retries on failure (2× with 60s delay)

**Flows to Create**: `ohlcv_1m_flow`, `ohlcv_5m_flow`, `ohlcv_30m_flow`, `ohlcv_1h_flow`, `ohlcv_1d_flow`

---

## Implementation

### 1. Create Prefect Pipeline Script

**File**: `src/k2/orchestration/flows/ohlcv_pipeline.py` (~400 lines)

### 2. Task Pattern (Spark-Submit Wrapper)

```python
from prefect import flow, task
import subprocess
import logging

logger = logging.getLogger(__name__)

@task(
    name="generate_ohlcv_1m",
    retries=2,
    retry_delay_seconds=60,
    timeout_seconds=300
)
def generate_ohlcv_1m():
    """Generate 1-minute OHLCV candles (incremental with MERGE)."""
    cmd = [
        "/opt/spark/bin/spark-submit",
        "--master", "spark://spark-master:7077",
        "--total-executor-cores", "1",
        "--executor-memory", "1g",
        "--driver-memory", "512m",
        "--jars", "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar",
        "/opt/k2/src/k2/spark/jobs/batch/ohlcv_incremental.py",
        "--timeframe", "1",
        "--lookback-minutes", "10"
    ]

    logger.info(f"Running command: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300
    )

    if result.returncode != 0:
        logger.error(f"1m OHLCV job failed: {result.stderr}")
        raise Exception(f"1m OHLCV failed with exit code {result.returncode}")

    logger.info(f"1m OHLCV job succeeded: {result.stdout}")
    return result.stdout

@task(name="validate_ohlcv_1m", retries=1, retry_delay_seconds=30)
def validate_ohlcv_1m():
    """Run data quality checks on 1m OHLCV."""
    # Import validation function from step 08
    from k2.spark.validation.ohlcv_validation import validate_invariants

    # Run validation (will be implemented in step 08)
    result = validate_invariants("1m")

    if not result["passed"]:
        raise Exception(f"1m OHLCV validation failed: {result['failures']}")

    logger.info(f"1m OHLCV validation passed: {result['summary']}")
    return result
```

### 3. Flow Definition

```python
@flow(name="OHLCV 1-Minute Pipeline", log_prints=True)
def ohlcv_1m_flow():
    """Run every 5 minutes via Prefect schedule."""
    logger.info("Starting 1m OHLCV flow")

    # Generate OHLCV
    output = generate_ohlcv_1m()

    # Validate data quality
    validation = validate_ohlcv_1m()

    logger.info("1m OHLCV flow complete")
    return {"output": output, "validation": validation}
```

### 4. All 5 Flows

**Pattern**: Same structure for each timeframe, different arguments

| Flow | Task Function | Arguments | Validation |
|------|---------------|-----------|------------|
| ohlcv_1m_flow | generate_ohlcv_1m | `--timeframe 1 --lookback-minutes 10` | validate_ohlcv_1m |
| ohlcv_5m_flow | generate_ohlcv_5m | `--timeframe 5 --lookback-minutes 20` | validate_ohlcv_5m |
| ohlcv_30m_flow | generate_ohlcv_30m | `--timeframe 30 --lookback-minutes 60` | validate_ohlcv_30m |
| ohlcv_1h_flow | generate_ohlcv_1h | `--timeframe 60 --lookback-hours 2` | validate_ohlcv_1h |
| ohlcv_1d_flow | generate_ohlcv_1d | `--timeframe 1440 --lookback-days 2` | validate_ohlcv_1d |

### 5. Error Handling

```python
@task(retries=2, retry_delay_seconds=60)
def generate_ohlcv_with_error_handling(timeframe: str, args: dict):
    """Generic OHLCV generator with error handling."""
    try:
        result = subprocess.run(..., timeout=300)

        if result.returncode != 0:
            # Log to Prefect
            logger.error(f"Spark job failed: {result.stderr}")

            # Raise exception (triggers retry)
            raise Exception(f"OHLCV {timeframe} failed")

        return result.stdout

    except subprocess.TimeoutExpired:
        logger.error(f"Spark job timed out after 300s")
        raise

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise
```

---

## Testing

### 1. Manual Flow Execution

```bash
# From host (requires prefect installed)
uv run prefect deployment run "OHLCV 1-Minute Pipeline/ohlcv-1m"
uv run prefect deployment run "OHLCV 5-Minute Pipeline/ohlcv-5m"
```

### 2. Check Flow Status

```bash
# View recent flow runs
uv run prefect flow-run ls

# View flow logs
uv run prefect flow-run logs <flow-run-id>
```

### 3. Test Retry Logic

```bash
# Temporarily break job (e.g., invalid timeframe)
# Run flow - should retry 2× then fail
# Fix job, re-run - should succeed
```

### 4. Prefect UI

Navigate to http://localhost:4200:
- View all flows
- Check run history
- Inspect task logs
- Monitor retry behavior

---

## Acceptance Criteria

- [x] `ohlcv_pipeline.py` created (~334 lines)
- [x] 5 flows defined: 1m, 5m, 30m, 1h, 1d
- [x] Each flow has:
  - Generate task (docker exec spark-submit wrapper)
  - Retry logic (2× with 60s delay)
  - Timeout (300s)
- [x] Spark configuration matches Gold aggregation exactly (6 JARs)
- [x] Prefect 3 serve() pattern used (no separate agent)
- [x] Flows deployed and running automatically
- [x] 1m and 5m flows verified executing successfully
- [ ] Validation task integration: Pending (script exists but not yet integrated)
- [ ] All 5 flows fully verified: Pending (awaiting 30m/1h/1d scheduled runs)

---

## Deployment (Step 07)

Flows created in this step are NOT yet scheduled. Scheduling happens in Step 07 via `deployment.yaml`.

Manual execution only in this step.

---

## Files to Create

- `src/k2/orchestration/flows/ohlcv_pipeline.py` (~400 lines)

---

## Files to Reference

- `src/k2/spark/jobs/batch/ohlcv_incremental.py` (1m/5m jobs)
- `src/k2/spark/jobs/batch/ohlcv_batch.py` (30m/1h/1d jobs)
- `src/k2/spark/validation/ohlcv_validation.py` (validation, created in step 08)

---

## Notes

- **Prefect 3 Pattern**: Uses serve() API, no separate agent service needed
- **Docker Exec**: Spark-submit wrapped in `docker exec k2-spark-master bash -c`
- **Consistent Spark Config**: Matches Gold aggregation exactly (6 JARs, 1024m/512m memory)
- spark-submit must use absolute paths (`/opt/k2/src/...`)
- Retries help with transient failures (network, resource contention)
- Timeout prevents hung Spark jobs from blocking serve() process

---

## Troubleshooting

**Issue**: Spark-submit fails with "connection refused"
- **Cause**: Spark master not reachable from prefect-agent
- **Fix**: Ensure prefect-agent on k2-network, depends_on spark-master

**Issue**: Flow times out after 300s
- **Cause**: Spark job taking too long
- **Fix**: Increase timeout_seconds, check resource allocation

**Issue**: Validation fails but job succeeded
- **Cause**: Data quality issue (invariant violated)
- **Fix**: Investigate validation logs, check raw trades

---

**Last Updated**: 2026-01-21
**Owner**: Data Engineering Team
**Completion Notes**:
- Migrated to Prefect 3 serve() pattern (Decision #028)
- Implemented docker exec pattern for Spark submit (Decision #029)
- Standardized Spark configuration across all workflows (Decision #027)
- Resolved Prefect UI connectivity (PREFECT_API_URL = localhost:4200)
- All 5 flows deployed and scheduled with staggered execution
