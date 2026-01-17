"""Unit tests-backup for Circuit Breaker pattern.

Tests cover:
- State transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
- Failure threshold behavior
- Success threshold behavior
- Timeout and automatic recovery
- Context manager usage
- Function call usage
- Metrics recording
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from k2.common.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitBreakerState


class TestCircuitBreakerInitialization:
    """Test circuit breaker initialization."""

    def test_default_initialization(self):
        """Test circuit breaker initializes with default values."""
        breaker = CircuitBreaker(name="test_breaker")

        assert breaker.name == "test_breaker"
        assert breaker.failure_threshold == 5
        assert breaker.success_threshold == 2
        assert breaker.timeout_seconds == 60.0
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker._failure_count == 0
        assert breaker._success_count == 0
        assert breaker._last_failure_time is None

    def test_custom_initialization(self):
        """Test circuit breaker accepts custom parameters."""
        breaker = CircuitBreaker(
            name="custom_breaker",
            failure_threshold=3,
            success_threshold=1,
            timeout_seconds=30.0,
        )

        assert breaker.name == "custom_breaker"
        assert breaker.failure_threshold == 3
        assert breaker.success_threshold == 1
        assert breaker.timeout_seconds == 30.0


class TestCircuitBreakerStateTransitions:
    """Test circuit breaker state transitions."""

    def test_closed_to_open_on_failure_threshold(self):
        """Test that breaker opens after reaching failure threshold."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        # Simulate 3 failures (should open circuit)
        for i in range(3):
            try:
                breaker.call(self._failing_function)
            except ValueError:
                pass

        assert breaker.state == CircuitBreakerState.OPEN
        assert breaker._failure_count == 3

    def test_open_rejects_calls(self):
        """Test that open circuit rejects calls without executing."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Open the circuit
        try:
            breaker.call(self._failing_function)
        except ValueError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Next call should raise CircuitBreakerError without calling function
        call_count = [0]

        def tracked_function():
            call_count[0] += 1
            return "success"

        with pytest.raises(CircuitBreakerError, match="Circuit breaker .* is OPEN"):
            breaker.call(tracked_function)

        # Function should not have been called
        assert call_count[0] == 0

    def test_open_to_half_open_after_timeout(self):
        """Test that breaker transitions to half-open after timeout."""
        breaker = CircuitBreaker(name="test", failure_threshold=1, timeout_seconds=0.1)

        # Open the circuit
        try:
            breaker.call(self._failing_function)
        except ValueError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # Next call should transition to HALF_OPEN
        try:
            breaker.call(self._succeeding_function)
        except Exception:
            pass

        assert breaker.state == CircuitBreakerState.HALF_OPEN

    def test_half_open_to_closed_on_success_threshold(self):
        """Test that breaker closes after success threshold in half-open state."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.1,
        )

        # Open the circuit
        try:
            breaker.call(self._failing_function)
        except ValueError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # First success → HALF_OPEN
        result1 = breaker.call(self._succeeding_function)
        assert result1 == "success"
        assert breaker.state == CircuitBreakerState.HALF_OPEN
        assert breaker._success_count == 1

        # Second success → CLOSED
        result2 = breaker.call(self._succeeding_function)
        assert result2 == "success"
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker._success_count == 0  # Reset

    def test_half_open_to_open_on_failure(self):
        """Test that single failure in half-open returns to open."""
        breaker = CircuitBreaker(name="test", failure_threshold=1, timeout_seconds=0.1)

        # Open the circuit
        try:
            breaker.call(self._failing_function)
        except ValueError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # Try success → HALF_OPEN
        breaker.call(self._succeeding_function)
        assert breaker.state == CircuitBreakerState.HALF_OPEN

        # Failure in half-open → back to OPEN
        try:
            breaker.call(self._failing_function)
        except ValueError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

    def test_closed_success_resets_failure_count(self):
        """Test that success in closed state resets failure count."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        # 2 failures (not enough to open)
        for i in range(2):
            try:
                breaker.call(self._failing_function)
            except ValueError:
                pass

        assert breaker._failure_count == 2
        assert breaker.state == CircuitBreakerState.CLOSED

        # 1 success resets failure count
        breaker.call(self._succeeding_function)
        assert breaker._failure_count == 0
        assert breaker.state == CircuitBreakerState.CLOSED

    # Helper functions
    @staticmethod
    def _failing_function():
        """Function that always fails."""
        raise ValueError("Simulated failure")

    @staticmethod
    def _succeeding_function():
        """Function that always succeeds."""
        return "success"


class TestCircuitBreakerContextManager:
    """Test circuit breaker as context manager."""

    def test_context_manager_success(self):
        """Test that context manager handles successful execution."""
        breaker = CircuitBreaker(name="test")

        with breaker:
            result = "success"

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker._failure_count == 0
        assert result == "success"

    def test_context_manager_failure(self):
        """Test that context manager handles failed execution."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        # First failure
        with pytest.raises(ValueError), breaker:
            raise ValueError("Test error")

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker._failure_count == 1

    def test_context_manager_open_rejects(self):
        """Test that context manager rejects when circuit is open."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Open the circuit
        with pytest.raises(ValueError), breaker:
            raise ValueError("Test error")

        assert breaker.state == CircuitBreakerState.OPEN

        # Next attempt should raise CircuitBreakerError
        with pytest.raises(CircuitBreakerError, match="Circuit breaker .* is OPEN"):
            with breaker:
                pass


class TestCircuitBreakerFunctionCall:
    """Test circuit breaker with function calls."""

    def test_call_with_args(self):
        """Test that function arguments are passed through."""
        breaker = CircuitBreaker(name="test")

        def add(a, b):
            return a + b

        result = breaker.call(add, 2, 3)
        assert result == 5

    def test_call_with_kwargs(self):
        """Test that keyword arguments are passed through."""
        breaker = CircuitBreaker(name="test")

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = breaker.call(greet, "Alice", greeting="Hi")
        assert result == "Hi, Alice!"

    def test_call_preserves_exception(self):
        """Test that original exception is raised after recording."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        def custom_error():
            raise RuntimeError("Custom error message")

        with pytest.raises(RuntimeError, match="Custom error message"):
            breaker.call(custom_error)

        assert breaker._failure_count == 1


class TestCircuitBreakerReset:
    """Test manual circuit breaker reset."""

    def test_manual_reset(self):
        """Test that manual reset returns breaker to closed state."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Open the circuit
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN
        assert breaker._failure_count == 1

        # Manual reset
        breaker.reset()

        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker._failure_count == 0
        assert breaker._success_count == 0
        assert breaker._last_failure_time is None


class TestCircuitBreakerStatistics:
    """Test circuit breaker statistics."""

    def test_get_stats(self):
        """Test that get_stats returns correct information."""
        breaker = CircuitBreaker(
            name="test_stats",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=60.0,
        )

        # Trigger one failure
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        stats = breaker.get_stats()

        assert stats["name"] == "test_stats"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 1
        assert stats["success_count"] == 0
        assert stats["last_failure_time"] is not None
        assert stats["timeout_seconds"] == 60.0
        assert stats["failure_threshold"] == 5
        assert stats["success_threshold"] == 2


class TestCircuitBreakerMetrics:
    """Test circuit breaker metrics recording."""

    @patch("k2.common.circuit_breaker.create_component_metrics")
    def test_metrics_on_failure(self, mock_metrics_factory):
        """Test that metrics are recorded on failure."""
        mock_metrics = MagicMock()
        mock_metrics_factory.return_value = mock_metrics

        breaker = CircuitBreaker(name="test_metrics", failure_threshold=3)

        # Trigger failure
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        # Check that failure metric was incremented
        mock_metrics.increment.assert_called_with(
            "circuit_breaker_failures_total",
            labels={"breaker_name": "test_metrics"},
        )

    @patch("k2.common.circuit_breaker.create_component_metrics")
    def test_metrics_on_state_change(self, mock_metrics_factory):
        """Test that metrics are recorded on state change."""
        mock_metrics = MagicMock()
        mock_metrics_factory.return_value = mock_metrics

        breaker = CircuitBreaker(name="test_metrics", failure_threshold=1)

        # Open circuit (should record state change)
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        # Check that state metric was updated
        assert mock_metrics.record_circuit_breaker_state.call_count >= 2  # Initial + transition


class TestCircuitBreakerEdgeCases:
    """Test circuit breaker edge cases."""

    def test_timeout_not_elapsed(self):
        """Test that circuit remains open if timeout not elapsed."""
        breaker = CircuitBreaker(name="test", failure_threshold=1, timeout_seconds=10.0)

        # Open circuit
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

        # Try immediately (timeout not elapsed)
        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: "success")

        # Should still be open
        assert breaker.state == CircuitBreakerState.OPEN

    def test_success_threshold_one(self):
        """Test breaker with success_threshold=1."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=1,
            timeout_seconds=0.1,
        )

        # Open circuit
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        # Wait for timeout
        time.sleep(0.15)

        # Single success should close circuit
        breaker.call(lambda: "success")
        assert breaker.state == CircuitBreakerState.CLOSED

    def test_failure_threshold_one(self):
        """Test breaker with failure_threshold=1 (fail-fast)."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Single failure should open circuit
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        assert breaker.state == CircuitBreakerState.OPEN

    def test_multiple_failures_in_open_state(self):
        """Test that failures in open state don't execute function."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Open circuit
        try:
            breaker.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass

        call_count = [0]

        def tracked_function():
            call_count[0] += 1
            return "success"

        # Multiple attempts should all fail fast without calling function
        for _ in range(5):
            with pytest.raises(CircuitBreakerError):
                breaker.call(tracked_function)

        assert call_count[0] == 0  # Function never called
