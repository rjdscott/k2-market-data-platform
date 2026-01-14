"""Circuit breaker pattern implementation for fault tolerance.

Implements the circuit breaker pattern to prevent cascading failures
and allow systems to recover gracefully from transient errors.

Circuit Breaker States:
- CLOSED: Normal operation, requests pass through
- OPEN: Too many failures, requests fail fast without attempting
- HALF_OPEN: Testing if service recovered, limited requests allowed

Usage:
    from k2.common.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(
        name="binance_websocket",
        failure_threshold=5,
        success_threshold=2,
        timeout_seconds=60,
    )

    # Execute with circuit breaker protection
    result = breaker.call(risky_operation, arg1, arg2)

    # Or use as context manager
    with breaker:
        risky_operation()
"""

import time
from enum import Enum
from typing import Any, Callable

import structlog

from k2.common.metrics import create_component_metrics

logger = structlog.get_logger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""

    pass


class CircuitBreaker:
    """Circuit breaker for fault tolerance.

    Tracks failures and opens circuit after threshold is exceeded.
    Automatically attempts to close circuit after timeout period.

    Attributes:
        name: Circuit breaker name (for logging and metrics)
        failure_threshold: Number of consecutive failures to open circuit
        success_threshold: Number of consecutive successes to close circuit from half-open
        timeout_seconds: Seconds to wait before attempting recovery (half-open)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Initialize circuit breaker.

        Args:
            name: Circuit breaker name (for logging and metrics)
            failure_threshold: Consecutive failures before opening circuit (default: 5)
            success_threshold: Consecutive successes to close from half-open (default: 2)
            timeout_seconds: Seconds before attempting recovery (default: 60)
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds

        # State
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None

        # Metrics
        self._metrics = create_component_metrics("circuit_breaker")
        self._update_state_metric()

        logger.info(
            "circuit_breaker_initialized",
            name=self.name,
            failure_threshold=self.failure_threshold,
            success_threshold=self.success_threshold,
            timeout_seconds=self.timeout_seconds,
        )

    @property
    def state(self) -> CircuitBreakerState:
        """Get current circuit breaker state."""
        return self._state

    def _update_state_metric(self) -> None:
        """Update Prometheus metric for circuit breaker state."""
        self._metrics.record_circuit_breaker_state(
            breaker_name=self.name,
            state=self._state.value,
        )

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        """Transition to a new state.

        Args:
            new_state: Target state
        """
        old_state = self._state
        self._state = new_state
        self._update_state_metric()

        logger.info(
            "circuit_breaker_state_transition",
            name=self.name,
            old_state=old_state.value,
            new_state=new_state.value,
            failure_count=self._failure_count,
            success_count=self._success_count,
        )

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset.

        Returns:
            True if timeout has elapsed since last failure
        """
        if self._last_failure_time is None:
            return False

        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.timeout_seconds

    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function

        Returns:
            Function result

        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Original exception from function if circuit allows execution
        """
        # Check circuit state
        if self._state == CircuitBreakerState.OPEN:
            # Check if we should attempt reset
            if self._should_attempt_reset():
                logger.info(
                    "circuit_breaker_attempting_reset",
                    name=self.name,
                    timeout_elapsed_seconds=time.time() - (self._last_failure_time or 0),
                )
                self._transition_to(CircuitBreakerState.HALF_OPEN)
                self._success_count = 0
            else:
                # Still open, fail fast
                logger.warning(
                    "circuit_breaker_open_rejecting_call",
                    name=self.name,
                    time_until_reset=self.timeout_seconds
                    - (time.time() - (self._last_failure_time or 0)),
                )
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"Wait {self.timeout_seconds}s before retry.",
                )

        # Attempt to execute function
        try:
            result = func(*args, **kwargs)

            # Success! Record it
            self._on_success()
            return result

        except Exception as e:
            # Failure! Record it
            self._on_failure(e)
            raise

    def _on_success(self) -> None:
        """Handle successful execution."""
        self._failure_count = 0  # Reset failure count

        if self._state == CircuitBreakerState.HALF_OPEN:
            # Increment success count in half-open state
            self._success_count += 1

            logger.debug(
                "circuit_breaker_half_open_success",
                name=self.name,
                success_count=self._success_count,
                success_threshold=self.success_threshold,
            )

            # Check if we should close circuit
            if self._success_count >= self.success_threshold:
                logger.info(
                    "circuit_breaker_closing",
                    name=self.name,
                    success_count=self._success_count,
                )
                self._transition_to(CircuitBreakerState.CLOSED)
                self._success_count = 0

    def _on_failure(self, error: Exception) -> None:
        """Handle failed execution.

        Args:
            error: Exception that caused failure
        """
        self._failure_count += 1
        self._last_failure_time = time.time()

        # Record metric
        self._metrics.increment(
            "circuit_breaker_failures_total",
            labels={"breaker_name": self.name},
        )

        logger.warning(
            "circuit_breaker_failure",
            name=self.name,
            failure_count=self._failure_count,
            failure_threshold=self.failure_threshold,
            error=str(error),
            error_type=type(error).__name__,
        )

        # Check if we should open circuit
        if self._state == CircuitBreakerState.HALF_OPEN:
            # Single failure in half-open → back to open
            logger.warning(
                "circuit_breaker_opening_from_half_open",
                name=self.name,
                error=str(error),
            )
            self._transition_to(CircuitBreakerState.OPEN)
            self._success_count = 0

        elif self._state == CircuitBreakerState.CLOSED:
            # Check failure threshold
            if self._failure_count >= self.failure_threshold:
                logger.error(
                    "circuit_breaker_opening",
                    name=self.name,
                    failure_count=self._failure_count,
                    failure_threshold=self.failure_threshold,
                )
                self._transition_to(CircuitBreakerState.OPEN)

    def __enter__(self) -> "CircuitBreaker":
        """Enter context manager.

        Raises:
            CircuitBreakerError: If circuit is open
        """
        if self._state == CircuitBreakerState.OPEN and not self._should_attempt_reset():
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' is OPEN. "
                f"Wait {self.timeout_seconds}s before retry.",
            )

        if self._state == CircuitBreakerState.OPEN and self._should_attempt_reset():
            self._transition_to(CircuitBreakerState.HALF_OPEN)
            self._success_count = 0

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager.

        Args:
            exc_type: Exception type (None if no exception)
            exc_val: Exception value
            exc_tb: Exception traceback
        """
        if exc_type is None:
            # Success
            self._on_success()
        else:
            # Failure
            self._on_failure(exc_val)

        # Don't suppress exception
        return False

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state.

        Use with caution - typically circuit breaker should recover automatically.
        """
        logger.info("circuit_breaker_manual_reset", name=self.name)
        self._transition_to(CircuitBreakerState.CLOSED)
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics.

        Returns:
            Dictionary with state, failure_count, success_count, last_failure_time
        """
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "timeout_seconds": self.timeout_seconds,
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
        }
