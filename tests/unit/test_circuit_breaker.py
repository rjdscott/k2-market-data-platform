"""
Unit tests for K2 Market Data Platform circuit breaker functionality.

Tests cover:
- Circuit breaker state transitions
- Failure threshold detection
- Recovery mechanisms
- Configuration validation
- Performance impact under load
"""

import time
from unittest.mock import Mock, patch

import pytest

from k2.common.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerError,
    CircuitBreakerConfig,
)


class TestCircuitBreaker:
    """Test suite for circuit breaker functionality."""

    def test_initial_state(self):
        """Test circuit breaker starts in closed state."""
        breaker = CircuitBreaker("test_breaker")

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.last_failure_time is None
        assert breaker.is_closed() is True

    def test_successful_operation(self):
        """Test successful operation doesn't change state."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=3)

        # Successful operation
        result = breaker.call(lambda: "success")

        assert result == "success"
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0

    def test_failure_threshold_trips(self):
        """Test circuit breaker trips after failure threshold."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=2)

        # First failure
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 1

        # Second failure should trip
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN
        assert breaker.failure_count == 2

    def test_open_state_blocks_calls(self):
        """Test open state blocks calls with CircuitBreakerError."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=1)

        # Trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

        # Calls should be blocked
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.call(lambda: "blocked")

        assert "Circuit breaker is open" in str(exc_info.value)

    def test_half_open_state_recovery(self):
        """Test half-open state allows recovery."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=1, recovery_timeout=1.0)

        # Trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        time.sleep(1.1)

        # Next call should be allowed (half-open state)
        result = breaker.call(lambda: "recovered")

        assert result == "recovered"
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_state_failure_reopens(self):
        """Test half-open state reopens on failure."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=1, recovery_timeout=1.0)

        # Trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        time.sleep(1.1)

        # Failure in half-open state should reopen
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

    def test_reset_functionality(self):
        """Test manual reset functionality."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=1)

        # Trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

        # Reset the breaker
        breaker.reset()

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.last_failure_time is None

    def test_timeout_configuration(self):
        """Test operation timeout functionality."""
        breaker = CircuitBreaker("test_breaker", timeout=0.1)

        # Operation that takes too long should timeout
        def slow_operation():
            time.sleep(0.2)
            return "slow"

        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.call(slow_operation)

        assert "timeout" in str(exc_info.value).lower()

    def test_exception_filtering(self):
        """Test filtering specific exceptions."""
        # Only treat ValueError as failure
        breaker = CircuitBreaker(
            "test_breaker",
            failure_threshold=1,
            exception_filter=lambda e: isinstance(e, ValueError),
        )

        # TypeError should not trip the breaker
        with pytest.raises(TypeError):
            breaker.call(lambda: "string" + 1)

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0

        # ValueError should trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        assert breaker.state == CircuitBreakerState.OPEN

    def test_fallback_function(self):
        """Test fallback function when circuit is open."""
        fallback_result = "fallback_value"

        breaker = CircuitBreaker(
            "test_breaker", failure_threshold=1, fallback=lambda: fallback_result
        )

        # Trip the breaker
        with pytest.raises(ValueError):
            breaker.call(lambda: 1 / 0)

        # Should return fallback when open
        result = breaker.call(lambda: "blocked")
        assert result == fallback_result


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration validation."""

    def test_default_configuration(self):
        """Test default configuration values."""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60.0
        assert config.timeout == 30.0
        assert config.exception_filter is None

    def test_custom_configuration(self):
        """Test custom configuration values."""

        def custom_filter(e):
            return isinstance(e, ValueError)

        config = CircuitBreakerConfig(
            failure_threshold=3, recovery_timeout=30.0, timeout=10.0, exception_filter=custom_filter
        )

        assert config.failure_threshold == 3
        assert config.recovery_timeout == 30.0
        assert config.timeout == 10.0
        assert config.exception_filter == custom_filter

    def test_invalid_configuration(self):
        """Test invalid configuration validation."""
        # Negative failure threshold
        with pytest.raises(ValueError):
            CircuitBreakerConfig(failure_threshold=-1)

        # Negative recovery timeout
        with pytest.raises(ValueError):
            CircuitBreakerConfig(recovery_timeout=-1.0)

        # Negative timeout
        with pytest.raises(ValueError):
            CircuitBreakerConfig(timeout=-1.0)


class TestCircuitBreakerMetrics:
    """Test circuit breaker metrics and monitoring."""

    def test_metrics_collection(self):
        """Test metrics are collected properly."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=3)

        # Successful operations
        for _ in range(5):
            breaker.call(lambda: "success")

        # Failed operations
        for _ in range(2):
            try:
                breaker.call(lambda: 1 / 0)
            except ValueError:
                pass

        metrics = breaker.get_metrics()

        assert metrics["total_calls"] == 7
        assert metrics["successful_calls"] == 5
        assert metrics["failed_calls"] == 2
        assert metrics["current_state"] == CircuitBreakerState.CLOSED.value
        assert metrics["failure_count"] == 2

    def test_metrics_reset(self):
        """Test metrics reset functionality."""
        breaker = CircuitBreaker("test_breaker")

        # Generate some activity
        breaker.call(lambda: "success")
        try:
            breaker.call(lambda: 1 / 0)
        except ValueError:
            pass

        # Reset metrics
        breaker.reset_metrics()

        metrics = breaker.get_metrics()
        assert metrics["total_calls"] == 0
        assert metrics["successful_calls"] == 0
        assert metrics["failed_calls"] == 0

    def test_state_transition_history(self):
        """Test state transition history tracking."""
        breaker = CircuitBreaker("test_breaker", failure_threshold=1, recovery_timeout=0.1)

        # Generate state transitions
        try:
            breaker.call(lambda: 1 / 0)
        except ValueError:
            pass  # Trip to OPEN

        time.sleep(0.2)  # Allow recovery

        breaker.call(lambda: "recovered")  # Should go to CLOSED

        history = breaker.get_state_history()

        assert len(history) >= 2
        assert history[0]["from_state"] is None
        assert history[0]["to_state"] == CircuitBreakerState.OPEN.value
        assert history[-1]["to_state"] == CircuitBreakerState.CLOSED.value


class TestCircuitBreakerPerformance:
    """Test circuit breaker performance characteristics."""

    def test_overhead_measurement(self):
        """Test circuit breaker overhead is minimal."""
        breaker = CircuitBreaker("test_breaker")

        def simple_operation():
            return "result"

        # Measure time without circuit breaker
        start_time = time.time()
        for _ in range(10000):
            simple_operation()
        direct_time = time.time() - start_time

        # Measure time with circuit breaker
        start_time = time.time()
        for _ in range(10000):
            breaker.call(simple_operation)
        breaker_time = time.time() - start_time

        # Overhead should be minimal (< 50% increase)
        overhead_ratio = (breaker_time - direct_time) / direct_time
        assert overhead_ratio < 0.5

    def test_concurrent_access(self):
        """Test circuit breaker handles concurrent access."""
        import threading

        breaker = CircuitBreaker("test_breaker", failure_threshold=10)
        results = []
        errors = []

        def worker():
            try:
                result = breaker.call(lambda: "success")
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=worker)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Should handle concurrent access without errors
        assert len(errors) == 0
        assert len(results) == 10
        assert all(result == "success" for result in results)


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration scenarios."""

    @patch("requests.get")
    def test_http_service_integration(self, mock_get):
        """Test circuit breaker with HTTP service."""
        # Configure circuit breaker for HTTP calls
        breaker = CircuitBreaker(
            "http_service",
            failure_threshold=2,
            timeout=1.0,
            fallback=lambda: {"error": "service_unavailable"},
        )

        # Simulate successful response
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": "success"}

        result = breaker.call(lambda: mock_get("http://api.example.com/data").json())
        assert result == {"data": "success"}

        # Simulate failures
        mock_get.side_effect = Exception("Connection failed")

        # First failure
        try:
            breaker.call(lambda: mock_get("http://api.example.com/data"))
        except Exception:
            pass

        # Second failure should trip
        try:
            breaker.call(lambda: mock_get("http://api.example.com/data"))
        except Exception:
            pass

        # Should return fallback
        result = breaker.call(lambda: mock_get("http://api.example.com/data").json())
        assert result == {"error": "service_unavailable"}

    def test_database_connection_integration(self):
        """Test circuit breaker with database connections."""
        mock_db = Mock()

        breaker = CircuitBreaker("database", failure_threshold=2, recovery_timeout=0.1)

        # Simulate successful query
        mock_db.query.return_value = [{"id": 1, "name": "test"}]

        result = breaker.call(lambda: mock_db.query("SELECT * FROM users"))
        assert result == [{"id": 1, "name": "test"}]

        # Simulate database failure
        mock_db.query.side_effect = Exception("Database connection failed")

        # First failure
        try:
            breaker.call(lambda: mock_db.query("SELECT * FROM users"))
        except Exception:
            pass

        # Second failure should trip
        try:
            breaker.call(lambda: mock_db.query("SELECT * FROM users"))
        except Exception:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Should block subsequent calls
        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: mock_db.query("SELECT * FROM users"))
