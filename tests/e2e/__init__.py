"""End-to-end test suite for K2 Market Data Platform.

This module provides comprehensive end-to-end tests that validate the complete
data pipeline from Binance WebSocket through the FastAPI service.

Test Coverage:
- Complete data pipeline validation (Binance → Kafka → Consumer → Iceberg → API)
- Docker Compose service orchestration and health checks
- Data freshness and end-to-end latency validation
- Performance testing under realistic load conditions

Architecture:
- Tests run against real Docker Compose stack
- Production-like environment with actual services
- Proper resource management and cleanup
- Integration with existing pytest framework

Usage:
    pytest tests/e2e/ -v -m e2e
    pytest tests/e2e/test_complete_pipeline.py -v
"""

from .utils.docker_manager import E2EDockerManager
from .utils.data_validator import DataValidator
from .utils.performance_monitor import PerformanceMonitor

__all__ = [
    "E2EDockerManager",
    "DataValidator",
    "PerformanceMonitor",
]
