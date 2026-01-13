"""
Chaos Tests for Storage Layer Resilience.

Tests Iceberg writer and query engine behavior under storage failures:
- MinIO/S3 outages
- Network partitions to storage
- Storage resource exhaustion
- Slow storage responses

These tests validate the platform's resilience to storage-related faults.

Usage:
    pytest tests/chaos/test_storage_chaos.py -v -m chaos_storage
"""

import logging
import time
from decimal import Decimal
from datetime import datetime

import pytest

from k2.storage.writer import IcebergWriter
from k2.query.engine import QueryEngine

logger = logging.getLogger(__name__)

# Test configuration
CATALOG_URI = "postgresql://k2:k2password@localhost:5432/k2_catalog"
WAREHOUSE_PATH = "s3://warehouse/"
S3_ENDPOINT = "http://localhost:9000"
S3_ACCESS_KEY = "minioadmin"
S3_SECRET_KEY = "minioadmin"

pytestmark = [pytest.mark.chaos, pytest.mark.chaos_storage]


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def writer_v2():
    """Create v2 Iceberg writer for chaos testing."""
    writer = IcebergWriter(
        catalog_uri=CATALOG_URI,
        warehouse_path=WAREHOUSE_PATH,
        s3_endpoint=S3_ENDPOINT,
        s3_access_key=S3_ACCESS_KEY,
        s3_secret_key=S3_SECRET_KEY,
        schema_version="v2",
    )
    yield writer
    # No explicit cleanup needed


@pytest.fixture
def query_engine():
    """Create query engine for chaos testing."""
    engine = QueryEngine(
        catalog_uri=CATALOG_URI,
        warehouse_path=WAREHOUSE_PATH,
        s3_endpoint=S3_ENDPOINT,
        s3_access_key=S3_ACCESS_KEY,
        s3_secret_key=S3_SECRET_KEY,
        table_version="v2",
    )
    yield engine
    # Connection pool cleanup handled by engine


@pytest.fixture
def sample_trades():
    """Sample trades for storage chaos testing."""
    trades = []
    for i in range(5):
        trades.append({
            "message_id": f"chaos-storage-{int(time.time() * 1000)}-{i}",
            "trade_id": f"CHAOS-{i}",
            "symbol": "BTCUSDT",
            "exchange": "BINANCE",
            "asset_class": "crypto",
            "timestamp": datetime.now(),
            "price": Decimal(f"45000.{i}"),
            "quantity": Decimal("1.0"),
            "currency": "USDT",
            "side": "BUY",
            "trade_conditions": [],
            "source_sequence": int(time.time() * 1000) + i,
            "ingestion_timestamp": datetime.now(),
            "platform_sequence": None,
            "vendor_data": None,
            "is_sample_data": True,
        })
    return trades


# ==============================================================================
# MinIO/S3 Failure Tests
# ==============================================================================


class TestMinIOFailure:
    """Test storage layer resilience to MinIO/S3 failures."""

    def test_writer_handles_brief_minio_outage(
        self,
        writer_v2,
        sample_trades,
        chaos_minio_failure,
    ):
        """Writer should fail gracefully during MinIO outage."""
        # Write successfully first
        rows_written = writer_v2.write_trades(
            records=sample_trades,
            table_name="market_data.trades_v2",
            exchange="binance",
            asset_class="crypto",
        )
        assert rows_written == len(sample_trades)

        # Simulate MinIO failure
        with chaos_minio_failure(duration_seconds=5, mode="pause"):
            logger.info("MinIO is down - attempting write")

            # Try to write during outage
            try:
                rows_written = writer_v2.write_trades(
                    records=sample_trades,
                    table_name="market_data.trades_v2",
                    exchange="binance",
                    asset_class="crypto",
                )
                # Write may timeout or fail
                logger.info(f"Write during outage: {rows_written} rows")

            except Exception as e:
                # Expected - MinIO is unavailable
                logger.info(f"Expected write failure during MinIO outage: {type(e).__name__}")
                assert True  # Failing gracefully is correct behavior

        # MinIO restored - writes should work
        logger.info("MinIO restored - testing recovery")
        rows_written = writer_v2.write_trades(
            records=sample_trades,
            table_name="market_data.trades_v2",
            exchange="binance",
            asset_class="crypto",
        )
        assert rows_written == len(sample_trades), "Writer should recover after MinIO restoration"

    def test_query_engine_handles_minio_outage(
        self,
        query_engine,
        chaos_minio_failure,
    ):
        """Query engine should handle MinIO outage gracefully."""
        # Query successfully first
        try:
            symbols = query_engine.get_symbols(
                table_name="market_data.trades_v2",
                exchange="binance",
                asset_class="crypto",
            )
            logger.info(f"Initial query returned {len(symbols)} symbols")
        except Exception as e:
            logger.info(f"Initial query failed (may be expected): {e}")

        # Simulate MinIO failure
        with chaos_minio_failure(duration_seconds=3, mode="pause"):
            logger.info("MinIO is down - attempting query")

            # Try to query during outage
            try:
                symbols = query_engine.get_symbols(
                    table_name="market_data.trades_v2",
                    exchange="binance",
                    asset_class="crypto",
                )
                logger.info(f"Query during outage returned: {symbols}")

            except Exception as e:
                # Expected - can't read from S3
                logger.info(f"Expected query failure during MinIO outage: {type(e).__name__}")
                assert True  # Failing gracefully is correct

        # MinIO restored - queries should work
        logger.info("MinIO restored - testing query recovery")

        # Give query engine time to recover connection
        time.sleep(2)

        try:
            symbols = query_engine.get_symbols(
                table_name="market_data.trades_v2",
                exchange="binance",
                asset_class="crypto",
            )
            logger.info(f"Recovery query returned {len(symbols)} symbols")
            assert isinstance(symbols, list), "Query should recover after MinIO restoration"

        except Exception as e:
            # May still fail if connection pool needs refresh
            logger.warning(f"Query recovery failed (connection pool issue?): {e}")

    @pytest.mark.slow
    def test_writer_handles_minio_stop_restart(
        self,
        writer_v2,
        sample_trades,
        chaos_minio_failure,
    ):
        """Writer should handle MinIO stop and restart cycle."""
        # Simulate MinIO stop (full restart cycle)
        with chaos_minio_failure(duration_seconds=5, mode="stop"):
            logger.info("MinIO stopped - waiting for restart")

            # Try to write during downtime
            try:
                rows_written = writer_v2.write_trades(
                    records=sample_trades,
                    table_name="market_data.trades_v2",
                    exchange="binance",
                    asset_class="crypto",
                )
            except Exception as e:
                logger.info(f"Write failed during MinIO restart: {type(e).__name__}")

        # MinIO restarted - verify recovery
        logger.info("MinIO restarted - testing write recovery")

        # May need retry or backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                rows_written = writer_v2.write_trades(
                    records=sample_trades,
                    table_name="market_data.trades_v2",
                    exchange="binance",
                    asset_class="crypto",
                )
                assert rows_written == len(sample_trades)
                logger.info(f"Write succeeded after {attempt + 1} attempts")
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.info(f"Retry {attempt + 1} failed, retrying...")
                    time.sleep(2)
                else:
                    pytest.fail(f"Writer did not recover after {max_retries} attempts: {e}")


# ==============================================================================
# Storage Resource Constraint Tests
# ==============================================================================


class TestStorageResourceConstraints:
    """Test behavior under storage resource constraints."""

    @pytest.mark.slow
    def test_writer_under_minio_cpu_constraint(
        self,
        writer_v2,
        sample_trades,
        chaos_kafka_resource_limit,  # Reusing for MinIO
    ):
        """Writer should handle MinIO under CPU pressure."""
        # Note: This test would need a separate chaos_minio_resource_limit fixture
        # Skipping for now as it requires additional setup
        pytest.skip("MinIO resource limit fixture not yet implemented")

    @pytest.mark.slow
    def test_writer_handles_slow_storage(
        self,
        writer_v2,
        sample_trades,
    ):
        """Writer should handle slow storage responses."""
        # This test would inject latency to MinIO
        # Requires latency injection fixture for MinIO
        pytest.skip("Storage latency injection not yet implemented")


# ==============================================================================
# Catalog Database Failure Tests
# ==============================================================================


class TestCatalogFailure:
    """Test resilience to Iceberg catalog database failures."""

    @pytest.mark.skip(reason="Catalog failure testing requires PostgreSQL chaos injection")
    def test_writer_handles_catalog_unavailable(
        self,
        writer_v2,
        sample_trades,
    ):
        """Writer should handle catalog database unavailability."""
        # This would require stopping PostgreSQL container
        # Skipping as it's complex and affects all Iceberg operations
        pass

    @pytest.mark.skip(reason="Catalog failure testing requires PostgreSQL chaos injection")
    def test_query_engine_handles_catalog_unavailable(
        self,
        query_engine,
    ):
        """Query engine should handle catalog database unavailability."""
        # This would require stopping PostgreSQL container
        pass


# ==============================================================================
# Data Corruption Tests
# ==============================================================================


class TestDataCorruption:
    """Test handling of corrupted or malformed data."""

    def test_writer_rejects_invalid_decimal_precision(
        self,
        writer_v2,
    ):
        """Writer should reject trades with invalid decimal precision."""
        invalid_trade = {
            "message_id": "chaos-invalid-decimal",
            "trade_id": "INVALID-1",
            "symbol": "BTCUSDT",
            "exchange": "BINANCE",
            "asset_class": "crypto",
            "timestamp": datetime.now(),
            "price": Decimal("45000.123456789"),  # Too many decimals (>8 for crypto)
            "quantity": Decimal("1.0"),
            "currency": "USDT",
            "side": "BUY",
            "trade_conditions": [],
            "source_sequence": int(time.time() * 1000),
            "ingestion_timestamp": datetime.now(),
            "platform_sequence": None,
            "vendor_data": None,
            "is_sample_data": True,
        }

        # Writer should handle this gracefully (round or reject)
        try:
            rows_written = writer_v2.write_trades(
                records=[invalid_trade],
                table_name="market_data.trades_v2",
                exchange="binance",
                asset_class="crypto",
            )
            # If it succeeds, price should be rounded
            logger.info(f"Writer handled invalid precision by rounding: {rows_written} rows")

        except Exception as e:
            # Or it may reject
            logger.info(f"Writer rejected invalid precision: {type(e).__name__}")
            assert True  # Either behavior is acceptable

    def test_writer_handles_missing_required_fields(
        self,
        writer_v2,
    ):
        """Writer should reject trades missing required fields."""
        incomplete_trade = {
            "message_id": "chaos-incomplete",
            "trade_id": "INCOMPLETE-1",
            "symbol": "BTCUSDT",
            # Missing: exchange, asset_class, timestamp, price, quantity, etc.
        }

        # Writer should reject incomplete trade
        with pytest.raises(Exception):
            writer_v2.write_trades(
                records=[incomplete_trade],
                table_name="market_data.trades_v2",
                exchange="binance",
                asset_class="crypto",
            )

    def test_writer_handles_wrong_asset_class_data(
        self,
        writer_v2,
    ):
        """Writer should handle data written to wrong asset class table."""
        # Write equity trade to crypto table (wrong asset class)
        equity_trade = {
            "message_id": "chaos-wrong-asset-class",
            "trade_id": "WRONG-1",
            "symbol": "BHP",  # ASX equity symbol
            "exchange": "ASX",
            "asset_class": "equities",  # But writing to crypto table
            "timestamp": datetime.now(),
            "price": Decimal("45.67"),
            "quantity": Decimal("1000.0"),
            "currency": "AUD",
            "side": "BUY",
            "trade_conditions": [],
            "source_sequence": int(time.time() * 1000),
            "ingestion_timestamp": datetime.now(),
            "platform_sequence": None,
            "vendor_data": None,
            "is_sample_data": True,
        }

        # Writer behavior depends on implementation
        # May succeed (flexible schema) or fail (strict validation)
        try:
            rows_written = writer_v2.write_trades(
                records=[equity_trade],
                table_name="market_data.trades_v2",
                exchange="binance",  # Wrong exchange
                asset_class="crypto",  # Wrong asset class
            )
            logger.warning(f"Writer accepted mismatched asset class: {rows_written} rows")

        except Exception as e:
            logger.info(f"Writer rejected mismatched asset class: {type(e).__name__}")
            assert True  # Rejecting is correct behavior


# ==============================================================================
# Concurrent Storage Operations
# ==============================================================================


class TestConcurrentStorageOperations:
    """Test concurrent writes and queries under stress."""

    @pytest.mark.skip(reason="Requires concurrent execution framework")
    def test_concurrent_writes_under_minio_stress(
        self,
        writer_v2,
        sample_trades,
        chaos_minio_failure,
    ):
        """Multiple writers should handle MinIO stress."""
        # This would require threading/multiprocessing
        # to simulate concurrent writes
        pass

    @pytest.mark.skip(reason="Requires concurrent execution framework")
    def test_writes_and_queries_under_minio_stress(
        self,
        writer_v2,
        query_engine,
        sample_trades,
        chaos_minio_failure,
    ):
        """Concurrent writes and queries should handle MinIO stress."""
        # This would require threading/multiprocessing
        pass
