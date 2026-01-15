"""Prometheus metrics registry for K2 Platform.

Pre-registers all metrics at module load time for better performance
and fail-fast behavior on duplicate metric names.

Metrics follow Prometheus naming conventions:
- snake_case names
- Base unit suffixes (_seconds, _bytes, _total)
- Descriptive help text
"""


from prometheus_client import Counter, Gauge, Histogram, Info

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
    10.000,  # 10s
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

KAFKA_PRODUCE_RETRIES_TOTAL = Counter(
    "k2_kafka_produce_retries_total",
    "Total number of produce retries",
    EXCHANGE_LABELS + ["topic", "error_type"],
)

KAFKA_PRODUCE_MAX_RETRIES_EXCEEDED_TOTAL = Counter(
    "k2_kafka_produce_max_retries_exceeded_total",
    "Total messages that exceeded max retry attempts",
    EXCHANGE_LABELS + ["topic", "error_type"],
)

# Producer Lifecycle
PRODUCER_INITIALIZED_TOTAL = Counter(
    "k2_producer_initialized_total",
    "Total number of producer instances initialized",
    STANDARD_LABELS + ["component_type"],  # component_type for specific component identification
)

PRODUCER_INIT_ERRORS_TOTAL = Counter(
    "k2_producer_init_errors_total",
    "Total producer initialization errors",
    STANDARD_LABELS + ["component_type", "error"],
)

SERIALIZER_ERRORS_TOTAL = Counter(
    "k2_serializer_errors_total",
    "Total Avro serializer errors",
    STANDARD_LABELS + ["component_type", "subject"],
)

SERIALIZER_CACHE_SIZE = Gauge(
    "k2_serializer_cache_size",
    "Current number of cached Avro serializers",
    STANDARD_LABELS + ["component_type"],
)

SERIALIZER_CACHE_EVICTIONS_TOTAL = Counter(
    "k2_serializer_cache_evictions_total",
    "Total number of serializer cache evictions (LRU)",
    STANDARD_LABELS + ["component_type"],
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

CONSUMER_ERRORS_TOTAL = Counter(
    "k2_consumer_errors_total",
    "Total consumer errors",
    STANDARD_LABELS + ["error_type", "consumer_group"],
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
# Binance WebSocket Streaming Metrics
# ==============================================================================

BINANCE_CONNECTION_STATUS = Gauge(
    "k2_binance_connection_status",
    "Binance WebSocket connection status (1=connected, 0=disconnected)",
    STANDARD_LABELS,  # Removed symbols label - use symbol label for per-symbol metrics
)

BINANCE_MESSAGES_RECEIVED_TOTAL = Counter(
    "k2_binance_messages_received_total",
    "Total trade messages received from Binance WebSocket",
    STANDARD_LABELS + ["symbol"],
)

BINANCE_MESSAGE_ERRORS_TOTAL = Counter(
    "k2_binance_message_errors_total",
    "Total message parsing/validation errors from Binance",
    STANDARD_LABELS + ["error_type"],
)

BINANCE_RECONNECTS_TOTAL = Counter(
    "k2_binance_reconnects_total",
    "Total Binance WebSocket reconnection attempts",
    STANDARD_LABELS + ["reason"],  # reason: connection_closed|websocket_error|unexpected_error
)

BINANCE_CONNECTION_ERRORS_TOTAL = Counter(
    "k2_binance_connection_errors_total",
    "Total Binance WebSocket connection errors",
    STANDARD_LABELS + ["error_type"],
)

BINANCE_LAST_MESSAGE_TIMESTAMP_SECONDS = Gauge(
    "k2_binance_last_message_timestamp_seconds",
    "Timestamp of last message received from Binance (for health check)",
    STANDARD_LABELS,  # Removed symbols label - aggregate metric across all symbols
)

BINANCE_RECONNECT_DELAY_SECONDS = Gauge(
    "k2_binance_reconnect_delay_seconds",
    "Current reconnection delay in seconds (exponential backoff)",
    STANDARD_LABELS,
)

BINANCE_CONNECTION_ROTATIONS_TOTAL = Counter(
    "k2_binance_connection_rotations_total",
    "Total scheduled connection rotations (periodic reconnects to prevent memory leaks)",
    STANDARD_LABELS + ["reason"],  # reason: scheduled_rotation|manual_rotation
)

BINANCE_CONNECTION_LIFETIME_SECONDS = Gauge(
    "k2_binance_connection_lifetime_seconds",
    "Current WebSocket connection lifetime in seconds",
    STANDARD_LABELS,
)

# Memory monitoring metrics
PROCESS_MEMORY_RSS_BYTES = Gauge(
    "k2_process_memory_rss_bytes",
    "Process Resident Set Size (RSS) memory usage in bytes",
    STANDARD_LABELS,
)

PROCESS_MEMORY_VMS_BYTES = Gauge(
    "k2_process_memory_vms_bytes",
    "Process Virtual Memory Size (VMS) in bytes",
    STANDARD_LABELS,
)

MEMORY_LEAK_DETECTION_SCORE = Gauge(
    "k2_memory_leak_detection_score",
    "Memory leak detection score (0-1, >0.8 indicates likely leak via linear regression)",
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

ICEBERG_TRANSACTIONS_TOTAL = Counter(
    "k2_iceberg_transactions_total",
    "Total Iceberg transactions committed",
    STANDARD_LABELS + ["table"],
)

ICEBERG_TRANSACTION_ROWS = Histogram(
    "k2_iceberg_transaction_rows",
    "Number of rows per Iceberg transaction",
    STANDARD_LABELS + ["table"],
    buckets=[10, 50, 100, 500, 1000, 5000, 10000, 50000],
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
# Connection Pool Metrics (DuckDB)
# ==============================================================================

CONNECTION_POOL_SIZE = Gauge(
    "k2_connection_pool_size",
    "Total size of connection pool (max concurrent connections)",
    STANDARD_LABELS,
)

CONNECTION_POOL_ACTIVE_CONNECTIONS = Gauge(
    "k2_connection_pool_active_connections",
    "Number of connections currently in use",
    STANDARD_LABELS,
)

CONNECTION_POOL_AVAILABLE_CONNECTIONS = Gauge(
    "k2_connection_pool_available_connections",
    "Number of connections available in pool",
    STANDARD_LABELS,
)

CONNECTION_POOL_WAIT_TIME_SECONDS = Histogram(
    "k2_connection_pool_wait_time_seconds",
    "Time spent waiting to acquire connection from pool",
    STANDARD_LABELS,
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0],
)

CONNECTION_POOL_ACQUISITION_TIMEOUTS_TOTAL = Counter(
    "k2_connection_pool_acquisition_timeouts_total",
    "Total number of connection acquisition timeouts",
    STANDARD_LABELS,
)

CONNECTION_POOL_CREATION_ERRORS_TOTAL = Counter(
    "k2_connection_pool_creation_errors_total",
    "Total number of connection creation errors",
    STANDARD_LABELS,
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

DEGRADATION_TRANSITIONS_TOTAL = Counter(
    "k2_degradation_transitions_total",
    "Total degradation level transitions",
    STANDARD_LABELS + ["from_level", "to_level"],
)

MESSAGES_SHED_TOTAL = Counter(
    "k2_messages_shed_total",
    "Total messages shed due to load shedding",
    STANDARD_LABELS + ["symbol_tier", "reason"],
)

# ==============================================================================
# Registry Helper Functions
# ==============================================================================

_METRIC_REGISTRY: dict[str, object] = {
    # Ingestion
    "kafka_messages_produced_total": KAFKA_MESSAGES_PRODUCED_TOTAL,
    "kafka_produce_errors_total": KAFKA_PRODUCE_ERRORS_TOTAL,
    "kafka_produce_duration_seconds": KAFKA_PRODUCE_DURATION_SECONDS,
    "kafka_produce_retries_total": KAFKA_PRODUCE_RETRIES_TOTAL,
    "kafka_produce_max_retries_exceeded_total": KAFKA_PRODUCE_MAX_RETRIES_EXCEEDED_TOTAL,
    "producer_initialized_total": PRODUCER_INITIALIZED_TOTAL,
    "producer_init_errors_total": PRODUCER_INIT_ERRORS_TOTAL,
    "serializer_errors_total": SERIALIZER_ERRORS_TOTAL,
    "serializer_cache_size": SERIALIZER_CACHE_SIZE,
    "serializer_cache_evictions_total": SERIALIZER_CACHE_EVICTIONS_TOTAL,
    "kafka_messages_consumed_total": KAFKA_MESSAGES_CONSUMED_TOTAL,
    "kafka_consumer_lag_seconds": KAFKA_CONSUMER_LAG_SECONDS,
    "kafka_consumer_lag_messages": KAFKA_CONSUMER_LAG_MESSAGES,
    "consumer_errors_total": CONSUMER_ERRORS_TOTAL,
    "sequence_gaps_detected_total": SEQUENCE_GAPS_DETECTED_TOTAL,
    "sequence_resets_detected_total": SEQUENCE_RESETS_DETECTED_TOTAL,
    "out_of_order_messages_total": OUT_OF_ORDER_MESSAGES_TOTAL,
    "duplicate_messages_detected_total": DUPLICATE_MESSAGES_DETECTED_TOTAL,
    "deduplication_cache_size": DEDUPLICATION_CACHE_SIZE,
    # Binance
    "binance_connection_status": BINANCE_CONNECTION_STATUS,
    "binance_messages_received_total": BINANCE_MESSAGES_RECEIVED_TOTAL,
    "binance_message_errors_total": BINANCE_MESSAGE_ERRORS_TOTAL,
    "binance_reconnects_total": BINANCE_RECONNECTS_TOTAL,
    "binance_connection_errors_total": BINANCE_CONNECTION_ERRORS_TOTAL,
    "binance_last_message_timestamp_seconds": BINANCE_LAST_MESSAGE_TIMESTAMP_SECONDS,
    "binance_reconnect_delay_seconds": BINANCE_RECONNECT_DELAY_SECONDS,
    "binance_connection_rotations_total": BINANCE_CONNECTION_ROTATIONS_TOTAL,
    "binance_connection_lifetime_seconds": BINANCE_CONNECTION_LIFETIME_SECONDS,
    # Memory Monitoring
    "process_memory_rss_bytes": PROCESS_MEMORY_RSS_BYTES,
    "process_memory_vms_bytes": PROCESS_MEMORY_VMS_BYTES,
    "memory_leak_detection_score": MEMORY_LEAK_DETECTION_SCORE,
    # Storage
    "iceberg_rows_written_total": ICEBERG_ROWS_WRITTEN_TOTAL,
    "iceberg_write_duration_seconds": ICEBERG_WRITE_DURATION_SECONDS,
    "iceberg_write_errors_total": ICEBERG_WRITE_ERRORS_TOTAL,
    "iceberg_commit_failures_total": ICEBERG_COMMIT_FAILURES_TOTAL,
    "iceberg_transactions_total": ICEBERG_TRANSACTIONS_TOTAL,
    "iceberg_transaction_rows": ICEBERG_TRANSACTION_ROWS,
    "iceberg_batch_size": ICEBERG_BATCH_SIZE,
    # Query
    "query_executions_total": QUERY_EXECUTIONS_TOTAL,
    "query_duration_seconds": QUERY_DURATION_SECONDS,
    "query_rows_scanned": QUERY_ROWS_SCANNED,
    "query_cache_hits_total": QUERY_CACHE_HITS_TOTAL,
    "query_cache_misses_total": QUERY_CACHE_MISSES_TOTAL,
    # Connection Pool
    "connection_pool_size": CONNECTION_POOL_SIZE,
    "connection_pool_active_connections": CONNECTION_POOL_ACTIVE_CONNECTIONS,
    "connection_pool_available_connections": CONNECTION_POOL_AVAILABLE_CONNECTIONS,
    "connection_pool_wait_time_seconds": CONNECTION_POOL_WAIT_TIME_SECONDS,
    "connection_pool_acquisition_timeouts_total": CONNECTION_POOL_ACQUISITION_TIMEOUTS_TOTAL,
    "connection_pool_creation_errors_total": CONNECTION_POOL_CREATION_ERRORS_TOTAL,
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
    "degradation_transitions_total": DEGRADATION_TRANSITIONS_TOTAL,
    "messages_shed_total": MESSAGES_SHED_TOTAL,
}


def get_metric(metric_name: str) -> object:
    """Get a pre-registered metric by name.

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
            f"Available metrics: {sorted(_METRIC_REGISTRY.keys())}",
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
    """Initialize platform information metric.

    Args:
        version: Platform version (e.g., "0.1.0")
        environment: Environment name (e.g., "dev", "staging", "prod")
        deployment: Deployment ID or timestamp
    """
    PLATFORM_INFO.info(
        {
            "version": version,
            "environment": environment,
            "deployment": deployment,
        },
    )
