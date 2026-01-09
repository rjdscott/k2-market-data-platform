"""
Prometheus metrics instrumentation.

Provides RED metrics (Rate, Errors, Duration) for all platform components.
"""

from typing import Dict, Optional


class MetricsClient:
    """
    Lightweight wrapper around Prometheus client.

    In production, use prometheus_client library.
    This stub allows code to import without dependencies.
    """

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None):
        """Increment a counter metric."""
        # TODO: Implement with prometheus_client.Counter
        pass

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Set a gauge metric."""
        # TODO: Implement with prometheus_client.Gauge
        pass

    def histogram(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a histogram observation."""
        # TODO: Implement with prometheus_client.Histogram
        pass


# Global metrics instance
metrics = MetricsClient()