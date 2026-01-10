"""
Prometheus metrics registry for K2 Platform.

Pre-registers all metrics at module load time for better performance
and fail-fast behavior on duplicate metric names.

Metrics follow Prometheus naming conventions:
- snake_case names
- Base unit suffixes (_seconds, _bytes, _total)
- Descriptive help text
"""

from prometheus_client import Counter, Gauge, Histogram, Info
from typing import Dict

# ==============================================================================
# Configuration
# ==============================================================================

# Standard labels applied to all metrics
STANDARD_LABELS = ["service", "environment", "component"]

# Exchange-specific labels
EXCHANGE_LABELS = STANDARD_LABELS + ["exchange", "asset_class"]

# API-specific labels
API_LABELS = STANDARD_LABELS + ["method", "endpoint", "status_code"]

# Histogram buckets optimized for HFT latency (<500ms p99 target)
LATENCY_BUCKETS = [
    0.001,  # 1ms
    0.005,  # 5ms
    0.010,  # 10ms
    0.025,  # 25ms
    0.050,  # 50ms
    0.100,  # 100ms
    0.250,  # 250ms
    0.500,  # 500ms (p99 target)
    1.000,  # 1s
    2.500,  # 2.5s
    5.000,  # 5s
]

# Storage-specific buckets (writes are slower)
STORAGE_BUCKETS = [
    0.010,  # 10ms
    0.050,  # 50ms
    0.100,  # 100ms
    0.200,  # 200ms (p99 target for storage)
    0.500,  # 500ms
    1.000,  # 1s
    2.000,  # 2s
    5.000,  # 5s
    10.000, # 10s
]

# ==============================================================================
# Platform Information
# ==============================================================================

PLATFORM_INFO = Info(
    "k2_platform",
    "K2 Market Data Platform build information",
)

# ==============================================================================
# Ingestion Layer Metrics
# ==============================================================================

# Kafka Producer
KAFKA_MESSAGES_PRODUCED_TOTAL = Counter(
    "k2_kafka_messages_produced_total",
    "Total messages produced to Kafka",
    EXCHANGE_LABELS + ["topic", "data_type"],
)

KAFKA_PRODUCE_ERRORS_TOTAL = Counter(
    "k2_kafka_produce_errors_total",
    "Total Kafka produce errors",
    EXCHANGE_LABELS + ["topic", "error_type"],
)

KAFKA_PRODUCE_DURATION_SECONDS = Histogram(
    "k2_kafka_produce_duration_seconds",
    "Kafka produce latency in seconds",
    EXCHANGE_LABELS + ["topic"],
    buckets=LATENCY_BUCKETS,
)

# Kafka Consumer
KAFKA_MESSAGES_CONSUMED_TOTAL = Counter(
    "k2_kafka_messages_consumed_total",
    "Total messages consumed from Kafka",
    EXCHANGE_LABELS + ["topic", "consumer_group"],
)

KAFKA_CONSUMER_LAG_SECONDS = Gauge(
    "k2_kafka_consumer_lag_seconds",
    "Consumer lag in seconds",
    EXCHANGE_LABELS + ["topic", "partition", "consumer_group"],
)

KAFKA_CONSUMER_LAG_MESSAGES = Gauge(
    "k2_kafka_consumer_lag_messages",
    "Consumer lag in message count",
    EXCHANGE_LABELS + ["topic", "partition", "consumer_group"],
)

KAFKA_CONSUMER_OFFSET = Gauge(
    "k2_kafka_consumer_offset",
    "Current consumer offset",
    EXCHANGE_LABELS + ["topic", "partition", "consumer_group"],
)

# Sequence Tracking
SEQUENCE_GAPS_DETECTED_TOTAL = Counter(
    "k2_sequence_gaps_detected_total",
    "Total sequence gaps detected (potential data loss)",
    EXCHANGE_LABELS + ["symbol", "severity"],  # severity: warning|critical
)

SEQUENCE_RESETS_DETECTED_TOTAL = Counter(
    "k2_sequence_resets_detected_total",
    "Total sequence resets detected (session restarts)",
    EXCHANGE_LABELS,
)

OUT_OF_ORDER_MESSAGES_TOTAL = Counter(
    "k2_out_of_order_messages_total",
    "Total out-of-order messages detected",
    EXCHANGE_LABELS,
)

# Deduplication
DUPLICATE_MESSAGES_DETECTED_TOTAL = Counter(
    "k2_duplicate_messages_detected_total",
    "Total duplicate messages detected and dropped",
    EXCHANGE_LABELS,
)

DEDUPLICATION_CACHE_SIZE = Gauge(
    "k2_deduplication_cache_size",
    "Current size of deduplication cache",
    STANDARD_LABELS,
)

# ==============================================================================
# Storage Layer Metrics (Iceberg)
# ==============================================================================

ICEBERG_ROWS_WRITTEN_TOTAL = Counter(
    "k2_iceberg_rows_written_total",
    "Total rows written to Iceberg",
    EXCHANGE_LABELS + ["table"],
)

ICEBERG_WRITE_DURATION_SECONDS = Histogram(
    "k2_iceberg_write_duration_seconds",
    "Iceberg write operation duration in seconds",
    EXCHANGE_LABELS + ["table", "operation"],  # operation: append|commit|rollback
    buckets=STORAGE_BUCKETS,
)

ICEBERG_WRITE_ERRORS_TOTAL = Counter(
    "k2_iceberg_write_errors_total",
    "Total Iceberg write errors",
    EXCHANGE_LABELS + ["table", "error_type"],
)

ICEBERG_COMMIT_FAILURES_TOTAL = Counter(
    "k2_iceberg_commit_failures_total",
    "Total Iceberg transaction commit failures",
    EXCHANGE_LABELS + ["table"],
)

ICEBERG_BATCH_SIZE = Histogram(
    "k2_iceberg_batch_size",
    "Size of batches written to Iceberg",
    EXCHANGE_LABELS + ["table"],
    buckets=[10, 50, 100, 500, 1000, 5000, 10000],
)

ICEBERG_TABLE_SIZE_BYTES = Gauge(
    "k2_iceberg_table_size_bytes",
    "Current size of Iceberg table in bytes",
    STANDARD_LABELS + ["table"],
)

ICEBERG_SNAPSHOT_COUNT = Gauge(
    "k2_iceberg_snapshot_count",
    "Total number of snapshots for table",
    STANDARD_LABELS + ["table"],
)

# ==============================================================================
# Query Layer Metrics (DuckDB)
# ==============================================================================

QUERY_EXECUTIONS_TOTAL = Counter(
    "k2_query_executions_total",
    "Total query executions",
    STANDARD_LABELS + ["query_type", "status"],  # query_type: realtime|historical|hybrid
)

QUERY_DURATION_SECONDS = Histogram(
    "k2_query_duration_seconds",
    "Query execution duration in seconds",
    STANDARD_LABELS + ["query_type"],
    buckets=LATENCY_BUCKETS,
)

QUERY_ROWS_SCANNED = Histogram(
    "k2_query_rows_scanned",
    "Number of rows scanned by query",
    STANDARD_LABELS + ["query_type"],
    buckets=[100, 1000, 10000, 100000, 1000000, 10000000],
)

QUERY_CACHE_HITS_TOTAL = Counter(
    "k2_query_cache_hits_total",
    "Total query cache hits",
    STANDARD_LABELS + ["cache_tier"],  # tier: L1|L2|L3
)

QUERY_CACHE_MISSES_TOTAL = Counter(
    "k2_query_cache_misses_total",
    "Total query cache misses",
    STANDARD_LABELS + ["cache_tier"],
)

# ==============================================================================
# API Layer Metrics (FastAPI)
# ==============================================================================

HTTP_REQUESTS_TOTAL = Counter(
    "k2_http_requests_total",
    "Total HTTP requests",
    API_LABELS,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "k2_http_request_duration_seconds",
    "HTTP request duration in seconds",
    API_LABELS,
    buckets=LATENCY_BUCKETS,
)

HTTP_REQUEST_ERRORS_TOTAL = Counter(
    "k2_http_request_errors_total",
    "Total HTTP request errors",
    STANDARD_LABELS + ["method", "endpoint", "error_type"],
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "k2_http_requests_in_progress",
    "Current number of HTTP requests in progress",
    STANDARD_LABELS + ["method", "endpoint"],
)

# ==============================================================================
# System Metrics
# ==============================================================================

CIRCUIT_BREAKER_STATE = Gauge(
    "k2_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    STANDARD_LABELS + ["breaker_name"],
)

CIRCUIT_BREAKER_FAILURES_TOTAL = Counter(
    "k2_circuit_breaker_failures_total",
    "Total circuit breaker failures",
    STANDARD_LABELS + ["breaker_name"],
)

BACKPRESSURE_EVENTS_TOTAL = Counter(
    "k2_backpressure_events_total",
    "Total backpressure events",
    STANDARD_LABELS + ["level"],  # level: soft|graceful|spill|circuit_breaker
)

DEGRADATION_LEVEL = Gauge(
    "k2_degradation_level",
    "Current system degradation level (0=normal, 1=soft, 2=graceful, 3=spill, 4=circuit_breaker)",
    STANDARD_LABELS,
)

# ==============================================================================
# Registry Helper Functions
# ==============================================================================

_METRIC_REGISTRY: Dict[str, object] = {
    # Ingestion
    "kafka_messages_produced_total": KAFKA_MESSAGES_PRODUCED_TOTAL,
    "kafka_produce_errors_total": KAFKA_PRODUCE_ERRORS_TOTAL,
    "kafka_produce_duration_seconds": KAFKA_PRODUCE_DURATION_SECONDS,
    "kafka_messages_consumed_total": KAFKA_MESSAGES_CONSUMED_TOTAL,
    "kafka_consumer_lag_seconds": KAFKA_CONSUMER_LAG_SECONDS,
    "kafka_consumer_lag_messages": KAFKA_CONSUMER_LAG_MESSAGES,
    "sequence_gaps_detected_total": SEQUENCE_GAPS_DETECTED_TOTAL,
    "sequence_resets_detected_total": SEQUENCE_RESETS_DETECTED_TOTAL,
    "out_of_order_messages_total": OUT_OF_ORDER_MESSAGES_TOTAL,
    "duplicate_messages_detected_total": DUPLICATE_MESSAGES_DETECTED_TOTAL,

    # Storage
    "iceberg_rows_written_total": ICEBERG_ROWS_WRITTEN_TOTAL,
    "iceberg_write_duration_seconds": ICEBERG_WRITE_DURATION_SECONDS,
    "iceberg_write_errors_total": ICEBERG_WRITE_ERRORS_TOTAL,
    "iceberg_commit_failures_total": ICEBERG_COMMIT_FAILURES_TOTAL,
    "iceberg_batch_size": ICEBERG_BATCH_SIZE,

    # Query
    "query_executions_total": QUERY_EXECUTIONS_TOTAL,
    "query_duration_seconds": QUERY_DURATION_SECONDS,
    "query_rows_scanned": QUERY_ROWS_SCANNED,
    "query_cache_hits_total": QUERY_CACHE_HITS_TOTAL,
    "query_cache_misses_total": QUERY_CACHE_MISSES_TOTAL,

    # API
    "http_requests_total": HTTP_REQUESTS_TOTAL,
    "http_request_duration_seconds": HTTP_REQUEST_DURATION_SECONDS,
    "http_request_errors_total": HTTP_REQUEST_ERRORS_TOTAL,
    "http_requests_in_progress": HTTP_REQUESTS_IN_PROGRESS,

    # System
    "circuit_breaker_state": CIRCUIT_BREAKER_STATE,
    "circuit_breaker_failures_total": CIRCUIT_BREAKER_FAILURES_TOTAL,
    "backpressure_events_total": BACKPRESSURE_EVENTS_TOTAL,
    "degradation_level": DEGRADATION_LEVEL,
}


def get_metric(metric_name: str) -> object:
    """
    Get a pre-registered metric by name.

    Args:
        metric_name: Metric name (without k2_ prefix)

    Returns:
        Prometheus metric object (Counter, Gauge, or Histogram)

    Raises:
        KeyError: If metric name not found in registry
    """
    if metric_name not in _METRIC_REGISTRY:
        raise KeyError(
            f"Metric '{metric_name}' not found in registry. "
            f"Available metrics: {sorted(_METRIC_REGISTRY.keys())}"
        )
    return _METRIC_REGISTRY[metric_name]


def get_counter(metric_name: str) -> Counter:
    """Get a Counter metric."""
    return get_metric(metric_name)


def get_gauge(metric_name: str) -> Gauge:
    """Get a Gauge metric."""
    return get_metric(metric_name)


def get_histogram(metric_name: str) -> Histogram:
    """Get a Histogram metric."""
    return get_metric(metric_name)


def initialize_platform_info(version: str, environment: str, deployment: str):
    """
    Initialize platform information metric.

    Args:
        version: Platform version (e.g., "0.1.0")
        environment: Environment name (e.g., "dev", "staging", "prod")
        deployment: Deployment ID or timestamp
    """
    PLATFORM_INFO.info({
        "version": version,
        "environment": environment,
        "deployment": deployment,
    })
