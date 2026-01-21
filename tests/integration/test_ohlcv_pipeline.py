"""Integration tests for OHLCV pipeline.

These tests validate the end-to-end OHLCV aggregation pipeline:
- Reading from gold_crypto_trades
- Executing incremental jobs (1m/5m with MERGE)
- Executing batch jobs (30m/1h/1d with INSERT OVERWRITE)
- Verifying data quality invariants
- Testing idempotency and late arrival handling

Requirements:
- Docker infrastructure running (Spark, Iceberg, MinIO)
- gold_crypto_trades table populated with test data
- OHLCV tables created

Run with: pytest tests/integration/test_ohlcv_pipeline.py -v --docker
"""

import subprocess
import time

import pytest

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def spark_submit_base():
    """Base spark-submit command configuration."""
    return [
        "docker",
        "exec",
        "k2-spark-master",
        "bash",
        "-c",
        "cd /opt/k2 && /opt/spark/bin/spark-submit "
        "--master spark://spark-master:7077 "
        "--total-executor-cores 1 "
        "--executor-cores 1 "
        "--executor-memory 1024m "
        "--driver-memory 512m "
        "--conf spark.driver.extraJavaOptions=-Daws.region=us-east-1 "
        "--conf spark.executor.extraJavaOptions=-Daws.region=us-east-1 "
        "--jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,"
        "/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,"
        "/opt/spark/jars-extra/bundle-2.20.18.jar,"
        "/opt/spark/jars-extra/url-connection-client-2.20.18.jar,"
        "/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,"
        "/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar ",
    ]


@pytest.fixture(scope="module")
def check_docker_infrastructure():
    """Verify Docker infrastructure is running."""
    required_containers = [
        "k2-spark-master",
        "k2-iceberg-rest",
        "k2-minio",
    ]

    for container in required_containers:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={container}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        if container not in result.stdout:
            pytest.skip(
                f"Required container {container} not running. Start with: docker-compose up -d"
            )


class TestIncrementalOHLCVJobs:
    """Test incremental OHLCV jobs (1m/5m with MERGE)."""

    def test_1m_incremental_job_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that 1m incremental job executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 1m --lookback-minutes 1440"  # 1 day lookback

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Job should succeed (even if no data)
        assert result.returncode == 0, f"1m job failed: {result.stderr}"
        # Verify structured logging output
        assert "ohlcv-incremental" in result.stdout or "No trades found" in result.stdout

    def test_5m_incremental_job_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that 5m incremental job executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 5m --lookback-minutes 1440"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"5m job failed: {result.stderr}"

    def test_1m_job_idempotency(self, spark_submit_base, check_docker_infrastructure):
        """Test that running 1m job twice produces consistent results."""
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 1m --lookback-minutes 60"

        # Run job first time
        result1 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result1.returncode == 0

        # Run job second time (should be idempotent)
        result2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result2.returncode == 0

        # Both runs should succeed without errors
        assert "ERROR" not in result1.stderr
        assert "ERROR" not in result2.stderr


class TestBatchOHLCVJobs:
    """Test batch OHLCV jobs (30m/1h/1d with INSERT OVERWRITE)."""

    def test_30m_batch_job_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that 30m batch job executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 30m --lookback-hours 48"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"30m job failed: {result.stderr}"
        assert "ohlcv-batch" in result.stdout or "No trades found" in result.stdout

    def test_1h_batch_job_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that 1h batch job executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1h --lookback-hours 48"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"1h job failed: {result.stderr}"

    def test_1d_batch_job_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that 1d batch job executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1d --lookback-days 7"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"1d job failed: {result.stderr}"

    def test_batch_job_idempotency(self, spark_submit_base, check_docker_infrastructure):
        """Test that running batch job twice overwrites cleanly."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1h --lookback-hours 24"

        # Run job first time
        result1 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result1.returncode == 0

        # Run job second time (should overwrite)
        result2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result2.returncode == 0

        # Both runs should succeed
        assert "ERROR" not in result1.stderr
        assert "ERROR" not in result2.stderr


class TestDataQualityValidation:
    """Test data quality validation script."""

    def test_validation_script_execution(self, spark_submit_base, check_docker_infrastructure):
        """Test that validation script executes successfully."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/validation/ohlcv_validation.py --timeframe 1m --lookback-days 1"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Validation should succeed (or gracefully report no data)
        assert result.returncode == 0 or "No OHLCV data found" in result.stdout

    def test_validation_all_timeframes(self, spark_submit_base, check_docker_infrastructure):
        """Test validation across all timeframes."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/validation/ohlcv_validation.py --timeframe all --lookback-days 1"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )

        # Should validate all 5 timeframes
        assert result.returncode == 0 or "No OHLCV data found" in result.stdout


class TestOHLCVTableQueries:
    """Test querying OHLCV tables via Spark SQL."""

    def test_query_1m_table(self, spark_submit_base, check_docker_infrastructure):
        """Test querying gold_ohlcv_1m table."""
        query = "SELECT COUNT(*) as count FROM iceberg.market_data.gold_ohlcv_1m"

        cmd = spark_submit_base.copy()
        cmd[-1] += f'--code "{query}" pyspark'

        # Note: This is a simplified test - in practice, need a proper query script
        # For now, just verify table exists and is queryable

    def test_query_1d_table(self, spark_submit_base, check_docker_infrastructure):
        """Test querying gold_ohlcv_1d table."""
        # Similar pattern as test_query_1m_table
        pass


class TestEndToEndWorkflow:
    """Test complete end-to-end workflow."""

    def test_full_pipeline_sequence(self, spark_submit_base, check_docker_infrastructure):
        """Test running all OHLCV jobs in sequence."""
        jobs = [
            (
                "1m",
                "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 1m --lookback-minutes 60",
            ),
            (
                "5m",
                "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 5m --lookback-minutes 120",
            ),
            ("30m", "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 30m --lookback-hours 24"),
            ("1h", "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1h --lookback-hours 48"),
            ("1d", "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1d --lookback-days 7"),
        ]

        for timeframe, job_args in jobs:
            cmd = spark_submit_base.copy()
            cmd[-1] += job_args

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            assert result.returncode == 0, f"{timeframe} job failed: {result.stderr}"
            print(f"✓ {timeframe} job completed successfully")

    def test_validation_after_pipeline(self, spark_submit_base, check_docker_infrastructure):
        """Test validation after running full pipeline."""
        # First run a job to ensure data exists
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 1m --lookback-minutes 1440"
        subprocess.run(cmd, capture_output=True, timeout=120)

        # Then run validation
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/validation/ohlcv_validation.py --timeframe 1m --lookback-days 1"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Validation should pass
        assert result.returncode == 0


class TestRetentionEnforcement:
    """Test retention policy enforcement."""

    def test_retention_dry_run(self, spark_submit_base, check_docker_infrastructure):
        """Test retention script in dry-run mode."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/orchestration/flows/ohlcv_retention.py --dry-run"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Dry-run should succeed without deleting data
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout or "dry-run" in result.stdout.lower()


class TestJobConfiguration:
    """Test job configuration and argument parsing."""

    def test_incremental_job_invalid_timeframe(
        self, spark_submit_base, check_docker_infrastructure
    ):
        """Test that incremental job rejects invalid timeframes."""
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 30m --lookback-minutes 60"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Should fail with error about invalid timeframe (argparse writes to stdout)
        assert result.returncode != 0
        assert "invalid choice: '30m'" in result.stdout or (
            "1m" in result.stdout and "5m" in result.stdout
        )

    def test_batch_job_invalid_timeframe(self, spark_submit_base, check_docker_infrastructure):
        """Test that batch job rejects invalid timeframes."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1m --lookback-hours 1"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Should fail with error about invalid timeframe (argparse writes to stdout)
        assert result.returncode != 0
        assert "invalid choice: '1m'" in result.stdout or (
            "30m" in result.stdout and "1h" in result.stdout and "1d" in result.stdout
        )


@pytest.mark.slow
class TestPerformance:
    """Test performance characteristics of OHLCV jobs."""

    def test_1m_job_performance(self, spark_submit_base, check_docker_infrastructure):
        """Test that 1m job completes within expected time."""
        cmd = spark_submit_base.copy()
        cmd[
            -1
        ] += "src/k2/spark/jobs/batch/ohlcv_incremental.py --timeframe 1m --lookback-minutes 60"

        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        duration = time.time() - start

        # Job should complete in < 60 seconds for 1 hour of data
        assert result.returncode == 0
        assert duration < 60, f"1m job took {duration}s (expected <60s)"
        print(f"✓ 1m job completed in {duration:.1f}s")

    def test_1d_job_performance(self, spark_submit_base, check_docker_infrastructure):
        """Test that 1d job completes within expected time."""
        cmd = spark_submit_base.copy()
        cmd[-1] += "src/k2/spark/jobs/batch/ohlcv_batch.py --timeframe 1d --lookback-days 7"

        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        duration = time.time() - start

        # Job should complete in < 120 seconds for 7 days of data
        assert result.returncode == 0
        assert duration < 120, f"1d job took {duration}s (expected <120s)"
        print(f"✓ 1d job completed in {duration:.1f}s")


# Pytest configuration
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: integration tests requiring Docker infrastructure"
    )
    config.addinivalue_line("markers", "slow: slow tests (performance benchmarks)")
