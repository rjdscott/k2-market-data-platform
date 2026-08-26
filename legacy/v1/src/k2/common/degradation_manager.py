"""Degradation manager for graceful degradation under load.

Implements 5-level degradation cascade based on system load metrics:
- NORMAL: Full processing
- SOFT: Skip enrichment
- GRACEFUL: Drop low-priority symbols
- AGGRESSIVE: Spill to disk
- CIRCUIT_BREAK: Stop accepting new data

This is a load-based degradation system, distinct from the fault-tolerance
circuit breaker (CLOSED/OPEN/HALF_OPEN) in circuit_breaker.py.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

import psutil

from k2.common.logging import get_logger
from k2.common.metrics_registry import (
    DEGRADATION_LEVEL,
    get_metric,
)

logger = get_logger(__name__, component="degradation_manager")


class DegradationLevel(IntEnum):
    """Degradation levels from healthy to circuit break."""

    NORMAL = 0
    SOFT = 1
    GRACEFUL = 2
    AGGRESSIVE = 3
    CIRCUIT_BREAK = 4


@dataclass
class DegradationThresholds:
    """Configurable thresholds for degradation transitions."""

    # Lag thresholds (message count)
    lag_soft: int = 100_000
    lag_graceful: int = 500_000
    lag_aggressive: int = 1_000_000
    lag_circuit_break: int = 5_000_000

    # Heap thresholds (percentage)
    heap_soft: float = 70.0
    heap_graceful: float = 80.0
    heap_aggressive: float = 90.0
    heap_circuit_break: float = 95.0

    # Recovery thresholds (hysteresis to prevent flapping)
    recovery_lag_factor: float = 0.5  # Recover when lag < trigger * factor
    recovery_heap_factor: float = 0.9  # Recover when heap < trigger * factor

    # Cooldown before recovery
    recovery_cooldown_seconds: int = 30


class DegradationManager:
    """Degradation manager for graceful degradation.

    Thread-safe implementation with hysteresis to prevent flapping.

    Usage:
        manager = DegradationManager()

        # Check current level
        level = manager.check_and_degrade(lag=current_lag, heap_pct=heap_usage)

        if level >= DegradationLevel.GRACEFUL:
            # Skip non-critical symbols
            if not manager.should_process_symbol(symbol):
                continue
    """

    def __init__(
        self,
        thresholds: DegradationThresholds | None = None,
        critical_symbols: list[str] | None = None,
    ):
        self.thresholds = thresholds or DegradationThresholds()
        self.critical_symbols = set(
            critical_symbols
            or [
                # Top ASX symbols by volume
                "BHP",
                "CBA",
                "CSL",
                "NAB",
                "WBC",
                "ANZ",
                "WES",
                "MQG",
                "RIO",
                "TLS",
                "WOW",
                "FMG",
                "NCM",
                "STO",
                "WPL",
                "AMC",
                "GMG",
                "TCL",
                "COL",
                "ALL",
            ]
        )

        self._level = DegradationLevel.NORMAL
        self._lock = threading.Lock()
        self._last_degradation_time: datetime | None = None
        self._degradation_history: list[tuple[datetime, DegradationLevel]] = []

        # Initialize metrics
        self._init_metrics()

        logger.info(
            "Degradation manager initialized",
            thresholds=str(self.thresholds),
            critical_symbols_count=len(self.critical_symbols),
        )

    def _init_metrics(self):
        """Initialize Prometheus metrics."""
        # Update degradation level gauge
        DEGRADATION_LEVEL.labels(
            service="k2-platform",
            environment="dev",
            component="degradation_manager",
        ).set(0)

    @property
    def level(self) -> DegradationLevel:
        """Current degradation level (thread-safe)."""
        with self._lock:
            return self._level

    def check_and_degrade(
        self,
        lag: int,
        heap_pct: float | None = None,
    ) -> DegradationLevel:
        """Check system health and update degradation level.

        Args:
            lag: Current Kafka consumer lag (message count)
            heap_pct: Current heap usage percentage (0-100).
                     If None, will be measured automatically.

        Returns:
            Current degradation level after check.
        """
        if heap_pct is None:
            heap_pct = psutil.virtual_memory().percent

        with self._lock:
            old_level = self._level
            new_level = self._calculate_level(lag, heap_pct)

            # Apply hysteresis for recovery (prevent flapping)
            if new_level < old_level:
                if not self._can_recover(lag, heap_pct, old_level):
                    new_level = old_level

            if new_level != old_level:
                self._transition(old_level, new_level, lag, heap_pct)

            return self._level

    def _calculate_level(self, lag: int, heap_pct: float) -> DegradationLevel:
        """Calculate target degradation level based on metrics."""
        t = self.thresholds

        if lag >= t.lag_circuit_break or heap_pct >= t.heap_circuit_break:
            return DegradationLevel.CIRCUIT_BREAK
        if lag >= t.lag_aggressive or heap_pct >= t.heap_aggressive:
            return DegradationLevel.AGGRESSIVE
        if lag >= t.lag_graceful or heap_pct >= t.heap_graceful:
            return DegradationLevel.GRACEFUL
        if lag >= t.lag_soft or heap_pct >= t.heap_soft:
            return DegradationLevel.SOFT
        return DegradationLevel.NORMAL

    def _can_recover(
        self,
        lag: int,
        heap_pct: float,
        current_level: DegradationLevel,
    ) -> bool:
        """Check if recovery is allowed (cooldown + threshold check)."""
        # Check cooldown
        if self._last_degradation_time:
            elapsed = (datetime.utcnow() - self._last_degradation_time).total_seconds()
            if elapsed < self.thresholds.recovery_cooldown_seconds:
                return False

        # Check recovery thresholds
        t = self.thresholds
        recovery_factor_lag = t.recovery_lag_factor
        recovery_factor_heap = t.recovery_heap_factor

        if current_level == DegradationLevel.CIRCUIT_BREAK:
            return (
                lag < t.lag_circuit_break * recovery_factor_lag
                and heap_pct < t.heap_circuit_break * recovery_factor_heap
            )
        if current_level == DegradationLevel.AGGRESSIVE:
            return (
                lag < t.lag_aggressive * recovery_factor_lag
                and heap_pct < t.heap_aggressive * recovery_factor_heap
            )
        if current_level == DegradationLevel.GRACEFUL:
            return (
                lag < t.lag_graceful * recovery_factor_lag
                and heap_pct < t.heap_graceful * recovery_factor_heap
            )
        if current_level == DegradationLevel.SOFT:
            return (
                lag < t.lag_soft * recovery_factor_lag
                and heap_pct < t.heap_soft * recovery_factor_heap
            )

        return True

    def _transition(
        self,
        old_level: DegradationLevel,
        new_level: DegradationLevel,
        lag: int,
        heap_pct: float,
    ):
        """Handle level transition with logging and metrics."""
        self._level = new_level
        self._last_degradation_time = datetime.utcnow()
        self._degradation_history.append((datetime.utcnow(), new_level))

        # Trim history to last 100 entries
        if len(self._degradation_history) > 100:
            self._degradation_history = self._degradation_history[-100:]

        # Update metrics
        DEGRADATION_LEVEL.labels(
            service="k2-platform",
            environment="dev",
            component="degradation_manager",
        ).set(new_level.value)

        # Record transition metric
        try:
            transitions_metric = get_metric("degradation_transitions_total")
            transitions_metric.labels(
                service="k2-platform",
                environment="dev",
                component="degradation_manager",
                from_level=old_level.name.lower(),
                to_level=new_level.name.lower(),
            ).inc()
        except KeyError:
            # Metric not registered yet - will be added
            pass

        # Log transition
        direction = "degraded" if new_level > old_level else "recovered"
        log_func = logger.warning if new_level > old_level else logger.info

        log_func(
            f"System {direction}",
            old_level=old_level.name,
            new_level=new_level.name,
            lag=lag,
            heap_pct=heap_pct,
        )

    def should_process_symbol(self, symbol: str) -> bool:
        """Check if symbol should be processed at current degradation level.

        At GRACEFUL and above, only critical symbols are processed.
        """
        if self._level < DegradationLevel.GRACEFUL:
            return True
        return symbol in self.critical_symbols

    def is_accepting_data(self) -> bool:
        """Check if system is accepting new data."""
        return self._level < DegradationLevel.CIRCUIT_BREAK

    def should_skip_enrichment(self) -> bool:
        """Check if enrichment should be skipped (SOFT mode and above)."""
        return self._level >= DegradationLevel.SOFT

    def should_spill_to_disk(self) -> bool:
        """Check if spill to disk is needed (AGGRESSIVE mode)."""
        return self._level >= DegradationLevel.AGGRESSIVE

    def get_status(self) -> dict:
        """Get current degradation manager status."""
        with self._lock:
            return {
                "level": self._level.name,
                "level_value": self._level.value,
                "is_accepting_data": self.is_accepting_data(),
                "critical_symbols_count": len(self.critical_symbols),
                "last_degradation": (
                    self._last_degradation_time.isoformat() if self._last_degradation_time else None
                ),
                "history_count": len(self._degradation_history),
            }

    def reset(self):
        """Reset degradation manager to NORMAL (for testing/admin)."""
        with self._lock:
            old_level = self._level
            self._level = DegradationLevel.NORMAL
            self._last_degradation_time = None

            DEGRADATION_LEVEL.labels(
                service="k2-platform",
                environment="dev",
                component="degradation_manager",
            ).set(0)

            logger.info(
                "Degradation manager reset",
                old_level=old_level.name,
            )
