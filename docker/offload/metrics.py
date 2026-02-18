#!/usr/bin/env python3
"""
K2 Market Data Platform - Prometheus Metrics for Iceberg Offload
Purpose: Export metrics for monitoring and alerting
Version: v1.0
Last Updated: 2026-02-12
"""

from prometheus_client import Counter, Gauge, Histogram, Summary, Info, start_http_server
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Metric Definitions
# ============================================================================

# Counter: Cumulative metrics (always increasing)
offload_rows_total = Counter(
    'offload_rows_total',
    'Total number of rows offloaded to Iceberg',
    ['table', 'layer']
)

offload_cycles_total = Counter(
    'offload_cycles_total',
    'Total number of offload cycles executed',
    ['status']  # success, failed, timeout
)

offload_errors_total = Counter(
    'offload_errors_total',
    'Total number of offload errors',
    ['table', 'error_type']
)

# Gauge: Point-in-time metrics (can go up or down)
offload_lag_minutes = Gauge(
    'offload_lag_minutes',
    'Time since last successful offload (minutes)',
    ['table']
)

watermark_timestamp_seconds = Gauge(
    'watermark_timestamp_seconds',
    'Unix timestamp of last successful watermark update',
    ['table']
)

offload_tables_configured = Gauge(
    'offload_tables_configured',
    'Number of tables configured for offload'
)

offload_last_success_timestamp = Gauge(
    'offload_last_success_timestamp',
    'Unix timestamp of last successful offload cycle'
)

offload_last_failure_timestamp = Gauge(
    'offload_last_failure_timestamp',
    'Unix timestamp of last failed offload cycle'
)

# Histogram: Distribution of values (with buckets)
offload_duration_seconds = Histogram(
    'offload_duration_seconds',
    'Duration of offload operations in seconds',
    ['table', 'layer'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]  # 1s, 5s, 10s, 30s, 1m, 2m, 5m, 10m
)

offload_cycle_duration_seconds = Histogram(
    'offload_cycle_duration_seconds',
    'Duration of complete offload cycles in seconds',
    buckets=[5, 10, 15, 30, 60, 120, 300, 600, 900]  # 5s to 15m
)

# Summary: Similar to histogram but with quantiles
offload_rows_per_second = Summary(
    'offload_rows_per_second',
    'Throughput of offload operations (rows/second)',
    ['table']
)

# Info: Static metadata
offload_info = Info(
    'offload_info',
    'Offload pipeline metadata'
)


# ============================================================================
# Metric Recording Functions
# ============================================================================

def record_offload_start(table: str, layer: str = "bronze"):
    """
    Record the start of an offload operation.

    Args:
        table: Source table name
        layer: Data layer (bronze, silver, gold)
    """
    logger.debug(f"Recording offload start: {table} ({layer})")


def record_offload_success(
    table: str,
    layer: str,
    rows: int,
    duration: float,
    watermark_timestamp: datetime = None
):
    """
    Record a successful offload operation.

    Args:
        table: Source table name
        layer: Data layer
        rows: Number of rows offloaded
        duration: Duration in seconds
        watermark_timestamp: Updated watermark timestamp
    """
    # Counter metrics
    offload_rows_total.labels(table=table, layer=layer).inc(rows)

    # Histogram metrics
    offload_duration_seconds.labels(table=table, layer=layer).observe(duration)

    # Summary metrics
    if duration > 0:
        throughput = rows / duration
        offload_rows_per_second.labels(table=table).observe(throughput)

    # Gauge metrics
    if watermark_timestamp:
        watermark_timestamp_seconds.labels(table=table).set(watermark_timestamp.timestamp())

    logger.info(f"Recorded offload success: {table} ({rows:,} rows in {duration:.1f}s)")


def record_offload_failure(
    table: str,
    layer: str,
    error_type: str,
    duration: float
):
    """
    Record a failed offload operation.

    Args:
        table: Source table name
        layer: Data layer
        error_type: Type of error (timeout, connection, crash, etc.)
        duration: Duration before failure (seconds)
    """
    # Counter metrics
    offload_errors_total.labels(table=table, error_type=error_type).inc()

    # Histogram metrics (record duration even on failure)
    offload_duration_seconds.labels(table=table, layer=layer).observe(duration)

    logger.warning(f"Recorded offload failure: {table} ({error_type}) after {duration:.1f}s")


def record_cycle_start():
    """Record the start of an offload cycle."""
    logger.debug("Recording cycle start")


def record_cycle_complete(
    status: str,
    duration: float,
    tables_processed: int,
    successful: int,
    failed: int,
    total_rows: int
):
    """
    Record a complete offload cycle.

    Args:
        status: Cycle status (success, partial, failed)
        duration: Total cycle duration (seconds)
        tables_processed: Number of tables processed
        successful: Number of successful table offloads
        failed: Number of failed table offloads
        total_rows: Total rows offloaded in cycle
    """
    # Counter metrics
    offload_cycles_total.labels(status=status).inc()

    # Histogram metrics
    offload_cycle_duration_seconds.observe(duration)

    # Gauge metrics
    if successful == tables_processed:
        offload_last_success_timestamp.set(datetime.now().timestamp())
    if failed > 0:
        offload_last_failure_timestamp.set(datetime.now().timestamp())

    logger.info(f"Recorded cycle complete: {status} ({successful}/{tables_processed} tables, "
                f"{total_rows:,} rows, {duration:.1f}s)")


def update_offload_lag(table: str, lag_minutes: float):
    """
    Update the offload lag metric.

    Args:
        table: Table name
        lag_minutes: Time since last successful offload (minutes)
    """
    offload_lag_minutes.labels(table=table).set(lag_minutes)
    logger.debug(f"Updated offload lag: {table} = {lag_minutes:.1f} min")


def set_configured_tables(count: int):
    """
    Set the number of configured tables.

    Args:
        count: Number of tables configured for offload
    """
    offload_tables_configured.set(count)
    logger.debug(f"Set configured tables: {count}")


def set_pipeline_info(version: str, schedule_minutes: int, tables: list):
    """
    Set static pipeline metadata.

    Args:
        version: Pipeline version
        schedule_minutes: Schedule interval in minutes
        tables: List of table names
    """
    offload_info.info({
        'version': version,
        'schedule_minutes': str(schedule_minutes),
        'tables': ','.join(tables),
        'start_time': datetime.now().isoformat()
    })
    logger.info(f"Set pipeline info: v{version}, {schedule_minutes}min schedule, {len(tables)} tables")


# ============================================================================
# Metrics Server
# ============================================================================

def start_metrics_server(port: int = 8000):
    """
    Start Prometheus metrics HTTP server.

    Args:
        port: Port to listen on (default: 8000)
    """
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
        logger.info(f"Metrics endpoint: http://localhost:{port}/metrics")
        return True
    except OSError as e:
        if "Address already in use" in str(e):
            logger.warning(f"Metrics server already running on port {port}")
            return True
        else:
            logger.error(f"Failed to start metrics server: {e}")
            return False
    except Exception as e:
        logger.error(f"Failed to start metrics server: {e}")
        return False


# ============================================================================
# Utility Functions
# ============================================================================

def get_metrics_summary() -> dict:
    """
    Get summary of current metrics (for logging/debugging).

    Returns:
        Dict with current metric values
    """
    # Note: This is a simplified version for debugging
    # Production monitoring should query Prometheus directly
    return {
        "info": "Metrics are exposed at /metrics endpoint",
        "port": 8000,
        "endpoint": "http://localhost:8000/metrics"
    }


# ============================================================================
# Design Notes
# ============================================================================

# Metric Types Explained:
# ─────────────────────────
# - Counter: Cumulative count (monotonically increasing)
#   Examples: total_rows_offloaded, total_errors
#   Use: Rate calculations (rows/sec), error rates
#
# - Gauge: Point-in-time value (can go up or down)
#   Examples: lag_minutes, watermark_timestamp
#   Use: Current state, threshold alerts
#
# - Histogram: Distribution with predefined buckets
#   Examples: duration_seconds (buckets: 1s, 5s, 10s, ...)
#   Use: Percentiles (p50, p95, p99), SLO compliance
#
# - Summary: Distribution with calculated quantiles
#   Examples: rows_per_second (p50, p90, p99)
#   Use: Similar to histogram but with runtime quantiles
#
# - Info: Static metadata labels
#   Examples: version, configuration
#   Use: Context for metrics, debugging

# Prometheus Query Examples:
# ──────────────────────────
# 1. Offload rate (rows/second):
#    rate(offload_rows_total[5m])
#
# 2. Error rate (errors/minute):
#    rate(offload_errors_total[5m]) * 60
#
# 3. Average duration (last 5 minutes):
#    rate(offload_duration_seconds_sum[5m]) / rate(offload_duration_seconds_count[5m])
#
# 4. 95th percentile duration:
#    histogram_quantile(0.95, offload_duration_seconds_bucket)
#
# 5. Current lag (minutes):
#    offload_lag_minutes
#
# 6. Success rate (%):
#    (rate(offload_cycles_total{status="success"}[5m]) /
#     rate(offload_cycles_total[5m])) * 100
