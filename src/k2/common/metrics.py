"""
Prometheus metrics instrumentation.

Provides RED metrics (Rate, Errors, Duration) for all platform components.
This module provides a simple API wrapper around the metrics registry.

Usage:
    from k2.common.metrics import metrics

    # Counter
    metrics.increment(
        "kafka_messages_produced_total",
        labels={"exchange": "asx", "topic": "market.equities.trades.asx"}
    )

    # Gauge
    metrics.gauge(
        "kafka_consumer_lag_seconds",
        value=60.5,
        labels={"exchange": "asx", "topic": "market.equities.trades.asx"}
    )

    # Histogram
    metrics.histogram(
        "iceberg_write_duration_seconds",
        value=0.150,
        labels={"exchange": "asx", "table": "trades", "operation": "append"}
    )

    # Context manager for timing
    with metrics.timer("query_duration_seconds", labels={"query_type": "historical"}):
        result = execute_query()
"""

import time
from contextlib import contextmanager
from typing import Dict, Optional, Generator

from k2.common.metrics_registry import (
    get_counter,
    get_gauge,
    get_histogram,
    initialize_platform_info,
)


class MetricsClient:
    """
    Lightweight wrapper around Prometheus metrics registry.

    Provides a simple API for instrumenting code with counters, gauges,
    and histograms. All metrics are pre-registered in metrics_registry.py
    for better performance and fail-fast behavior.
    """

    def __init__(self, default_labels: Optional[Dict[str, str]] = None):
        """
        Initialize metrics client.

        Args:
            default_labels: Default labels to apply to all metrics
                (e.g., {"service": "k2-platform", "environment": "dev"})
        """
        self.default_labels = default_labels or {}

    def _merge_labels(self, labels: Optional[Dict[str, str]]) -> Dict[str, str]:
        """Merge provided labels with default labels."""
        merged = self.default_labels.copy()
        if labels:
            merged.update(labels)
        return merged

    def increment(
        self,
        metric_name: str,
        value: int = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Increment a counter metric.

        Args:
            metric_name: Name of the counter (without k2_ prefix)
            value: Amount to increment (default: 1)
            labels: Metric labels (merged with default labels)

        Example:
            metrics.increment(
                "kafka_messages_produced_total",
                labels={"exchange": "asx", "topic": "market.equities.trades.asx"}
            )
        """
        counter = get_counter(metric_name)
        merged_labels = self._merge_labels(labels)
        counter.labels(**merged_labels).inc(value)

    def gauge(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Set a gauge metric to a specific value.

        Args:
            metric_name: Name of the gauge (without k2_ prefix)
            value: Value to set
            labels: Metric labels (merged with default labels)

        Example:
            metrics.gauge(
                "kafka_consumer_lag_seconds",
                value=60.5,
                labels={"exchange": "asx", "partition": "0"}
            )
        """
        gauge_metric = get_gauge(metric_name)
        merged_labels = self._merge_labels(labels)
        gauge_metric.labels(**merged_labels).set(value)

    def histogram(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Record a histogram observation.

        Args:
            metric_name: Name of the histogram (without k2_ prefix)
            value: Observed value (typically duration in seconds)
            labels: Metric labels (merged with default labels)

        Example:
            metrics.histogram(
                "iceberg_write_duration_seconds",
                value=0.150,
                labels={"exchange": "asx", "table": "trades"}
            )
        """
        histogram_metric = get_histogram(metric_name)
        merged_labels = self._merge_labels(labels)
        histogram_metric.labels(**merged_labels).observe(value)

    @contextmanager
    def timer(
        self,
        metric_name: str,
        labels: Optional[Dict[str, str]] = None,
    ) -> Generator[None, None, None]:
        """
        Context manager for timing operations.

        Automatically records duration in a histogram metric.

        Args:
            metric_name: Name of the histogram (without k2_ prefix)
            labels: Metric labels (merged with default labels)

        Example:
            with metrics.timer("query_duration_seconds", labels={"query_type": "historical"}):
                result = execute_query()
        """
        start_time = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - start_time
            self.histogram(metric_name, duration, labels)

    def set_degradation_level(self, level: int) -> None:
        """
        Set current system degradation level.

        Levels:
            0: Normal operation
            1: Soft degradation (p99 latency increase)
            2: Graceful degradation (drop low-priority symbols)
            3: Spill to disk (buffer to NVMe)
            4: Circuit breaker (halt consumption)

        Args:
            level: Degradation level (0-4)
        """
        if not 0 <= level <= 4:
            raise ValueError(f"Degradation level must be 0-4, got {level}")

        self.gauge(
            "degradation_level",
            value=float(level),
            labels={"component": "system"},
        )

    def record_circuit_breaker_state(
        self,
        breaker_name: str,
        state: str,
    ) -> None:
        """
        Record circuit breaker state change.

        Args:
            breaker_name: Name of the circuit breaker
            state: State ("closed", "open", "half_open")
        """
        state_map = {
            "closed": 0.0,
            "open": 1.0,
            "half_open": 2.0,
        }

        if state not in state_map:
            raise ValueError(f"Invalid state: {state}. Must be closed, open, or half_open")

        self.gauge(
            "circuit_breaker_state",
            value=state_map[state],
            labels={"breaker_name": breaker_name},
        )


# Global metrics instance with no default labels
# Components should create their own instances with appropriate default labels
metrics = MetricsClient()


def initialize_metrics(version: str, environment: str) -> None:
    """
    Initialize platform metrics with version info.

    Should be called once at application startup.

    Args:
        version: Platform version (e.g., "0.1.0")
        environment: Environment name (e.g., "dev", "staging", "prod")
    """
    import datetime

    deployment = datetime.datetime.now(datetime.timezone.utc).isoformat()
    initialize_platform_info(version, environment, deployment)


def create_component_metrics(
    component: str,
    service: str = "k2-platform",
    environment: str = "dev",
) -> MetricsClient:
    """
    Create a metrics client with default labels for a component.

    Args:
        component: Component name (e.g., "ingestion", "storage", "query")
        service: Service name (default: "k2-platform")
        environment: Environment (default: "dev")

    Returns:
        MetricsClient instance with default labels

    Example:
        # In a consumer module
        metrics = create_component_metrics("ingestion", environment="prod")

        # All metrics will include these labels
        metrics.increment("kafka_messages_consumed_total", labels={"exchange": "asx"})
        # Results in: k2_kafka_messages_consumed_total{
        #     service="k2-platform",
        #     environment="prod",
        #     component="ingestion",
        #     exchange="asx"
        # }
    """
    return MetricsClient(
        default_labels={
            "service": service,
            "environment": environment,
            "component": component,
        }
    )
