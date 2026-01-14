# Step 02: Circuit Breaker Implementation

**Status**: ✅ Complete (2026-01-13)
**Assignee**: Implementation Team
**Issue**: #2a - No Demonstration of Backpressure (RESOLVED)

---

## Dependencies
- **Requires**: Phase 1 complete
- **Blocks**: Step 03 (Degradation Demo)

---

## Goal

Implement a working circuit breaker with 5-level degradation cascade. This transforms documented design into demonstrable resilience—a key differentiator for Principal Engineer level work.

---

## Overview

The platform documents a 4-level degradation cascade in `LATENCY_BACKPRESSURE.md` but has no implementation. Production systems need graceful degradation under load, and demonstrating this shows operational maturity.

### Degradation Levels

| Level | Name | Trigger | Behavior |
|-------|------|---------|----------|
| 0 | NORMAL | Default | Full processing |
| 1 | SOFT | Lag > 100K or Heap > 70% | Skip enrichment |
| 2 | GRACEFUL | Lag > 500K or Heap > 80% | Drop low-priority symbols |
| 3 | AGGRESSIVE | Lag > 1M or Heap > 90% | Spill to disk |
| 4 | CIRCUIT_BREAK | Lag > 5M or Heap > 95% | Stop accepting new data |

---

## Deliverables

### 1. Circuit Breaker Module

Create `src/k2/common/circuit_breaker.py`:

```python
"""
Circuit breaker for graceful degradation under load.

Implements 5-level degradation cascade:
- NORMAL: Full processing
- SOFT: Skip enrichment
- GRACEFUL: Drop low-priority symbols
- AGGRESSIVE: Spill to disk
- CIRCUIT_BREAK: Stop accepting new data
"""

from enum import IntEnum
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
import threading
import psutil

from k2.common.logging import get_logger
from k2.common.metrics_registry import METRICS

logger = get_logger(__name__, component="circuit_breaker")


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


class CircuitBreaker:
    """
    Circuit breaker for graceful degradation.

    Thread-safe implementation with hysteresis to prevent flapping.

    Usage:
        breaker = CircuitBreaker()

        # Check current level
        level = breaker.check_and_degrade(lag=current_lag, heap_pct=heap_usage)

        if level >= DegradationLevel.GRACEFUL:
            # Skip non-critical symbols
            if not breaker.should_process_symbol(symbol):
                continue
    """

    def __init__(
        self,
        thresholds: Optional[DegradationThresholds] = None,
        critical_symbols: Optional[list[str]] = None,
    ):
        self.thresholds = thresholds or DegradationThresholds()
        self.critical_symbols = set(critical_symbols or [
            # Top ASX symbols by volume
            'BHP', 'CBA', 'CSL', 'NAB', 'WBC', 'ANZ', 'WES', 'MQG', 'RIO', 'TLS',
            'WOW', 'FMG', 'NCM', 'STO', 'WPL', 'AMC', 'GMG', 'TCL', 'COL', 'ALL',
        ])

        self._level = DegradationLevel.NORMAL
        self._lock = threading.Lock()
        self._last_degradation_time: Optional[datetime] = None
        self._degradation_history: list[tuple[datetime, DegradationLevel]] = []

        # Initialize metrics
        self._init_metrics()

        logger.info(
            "Circuit breaker initialized",
            thresholds=str(self.thresholds),
            critical_symbols_count=len(self.critical_symbols),
        )

    def _init_metrics(self):
        """Initialize Prometheus metrics."""
        # Update degradation level gauge
        METRICS['k2_degradation_level'].labels(
            service='k2-platform',
            environment='dev',
            component='circuit_breaker',
        ).set(0)

    @property
    def level(self) -> DegradationLevel:
        """Current degradation level (thread-safe)."""
        with self._lock:
            return self._level

    def check_and_degrade(
        self,
        lag: int,
        heap_pct: Optional[float] = None,
    ) -> DegradationLevel:
        """
        Check system health and update degradation level.

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
        elif lag >= t.lag_aggressive or heap_pct >= t.heap_aggressive:
            return DegradationLevel.AGGRESSIVE
        elif lag >= t.lag_graceful or heap_pct >= t.heap_graceful:
            return DegradationLevel.GRACEFUL
        elif lag >= t.lag_soft or heap_pct >= t.heap_soft:
            return DegradationLevel.SOFT
        else:
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
            return (lag < t.lag_circuit_break * recovery_factor_lag and
                    heap_pct < t.heap_circuit_break * recovery_factor_heap)
        elif current_level == DegradationLevel.AGGRESSIVE:
            return (lag < t.lag_aggressive * recovery_factor_lag and
                    heap_pct < t.heap_aggressive * recovery_factor_heap)
        elif current_level == DegradationLevel.GRACEFUL:
            return (lag < t.lag_graceful * recovery_factor_lag and
                    heap_pct < t.heap_graceful * recovery_factor_heap)
        elif current_level == DegradationLevel.SOFT:
            return (lag < t.lag_soft * recovery_factor_lag and
                    heap_pct < t.heap_soft * recovery_factor_heap)

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
        METRICS['k2_degradation_level'].labels(
            service='k2-platform',
            environment='dev',
            component='circuit_breaker',
        ).set(new_level.value)

        # Log transition
        direction = "degraded" if new_level > old_level else "recovered"
        log_func = logger.warning if new_level > old_level else logger.info

        log_func(
            f"Circuit breaker {direction}",
            old_level=old_level.name,
            new_level=new_level.name,
            lag=lag,
            heap_pct=heap_pct,
        )

    def should_process_symbol(self, symbol: str) -> bool:
        """
        Check if symbol should be processed at current degradation level.

        At GRACEFUL and above, only critical symbols are processed.
        """
        if self._level < DegradationLevel.GRACEFUL:
            return True
        return symbol in self.critical_symbols

    def is_accepting_data(self) -> bool:
        """Check if circuit breaker is accepting new data."""
        return self._level < DegradationLevel.CIRCUIT_BREAK

    def should_skip_enrichment(self) -> bool:
        """Check if enrichment should be skipped (SOFT mode and above)."""
        return self._level >= DegradationLevel.SOFT

    def should_spill_to_disk(self) -> bool:
        """Check if spill to disk is needed (AGGRESSIVE mode)."""
        return self._level >= DegradationLevel.AGGRESSIVE

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        with self._lock:
            return {
                'level': self._level.name,
                'level_value': self._level.value,
                'is_accepting_data': self.is_accepting_data(),
                'critical_symbols_count': len(self.critical_symbols),
                'last_degradation': self._last_degradation_time.isoformat()
                    if self._last_degradation_time else None,
                'history_count': len(self._degradation_history),
            }

    def reset(self):
        """Reset circuit breaker to NORMAL (for testing/admin)."""
        with self._lock:
            old_level = self._level
            self._level = DegradationLevel.NORMAL
            self._last_degradation_time = None

            METRICS['k2_degradation_level'].labels(
                service='k2-platform',
                environment='dev',
                component='circuit_breaker',
            ).set(0)

            logger.info(
                "Circuit breaker reset",
                old_level=old_level.name,
            )
```

### 2. Load Shedder Module

Create `src/k2/common/load_shedder.py`:

```python
"""
Load shedder for priority-based message filtering.

Works with CircuitBreaker to implement graceful degradation.
"""

from enum import IntEnum
from dataclasses import dataclass
from typing import Optional

from k2.common.logging import get_logger

logger = get_logger(__name__, component="load_shedder")


class MessagePriority(IntEnum):
    """Message priority levels."""
    CRITICAL = 1    # Top symbols, trades
    HIGH = 2        # Top 100 symbols
    NORMAL = 3      # All other symbols
    LOW = 4         # Reference data updates


@dataclass
class SymbolTier:
    """Symbol classification by liquidity tier."""
    tier_1: set[str]  # Top 20 - Always process
    tier_2: set[str]  # Top 100 - Process at HIGH and above
    tier_3: set[str]  # All others - Process at NORMAL only


class LoadShedder:
    """
    Load shedder for priority-based message filtering.

    Usage:
        shedder = LoadShedder()

        # Check if message should be processed
        if shedder.should_process(symbol, degradation_level):
            process_message(msg)
        else:
            # Shed load - skip this message
            pass
    """

    # Default ASX tier classification
    DEFAULT_TIER_1 = {
        'BHP', 'CBA', 'CSL', 'NAB', 'WBC', 'ANZ', 'WES', 'MQG', 'RIO', 'TLS',
        'WOW', 'FMG', 'NCM', 'STO', 'WPL', 'AMC', 'GMG', 'TCL', 'COL', 'ALL',
    }

    DEFAULT_TIER_2 = {
        'REA', 'QAN', 'BXB', 'ORG', 'JHX', 'CPU', 'SHL', 'ASX', 'IAG', 'APA',
        'S32', 'MIN', 'SGP', 'AGL', 'RMD', 'SEK', 'ORI', 'QBE', 'MPL', 'AZJ',
        # ... (abbreviated - would include top 100)
    }

    def __init__(
        self,
        tier_1: Optional[set[str]] = None,
        tier_2: Optional[set[str]] = None,
    ):
        self.tier_1 = tier_1 or self.DEFAULT_TIER_1
        self.tier_2 = tier_2 or self.DEFAULT_TIER_2

        logger.info(
            "Load shedder initialized",
            tier_1_count=len(self.tier_1),
            tier_2_count=len(self.tier_2),
        )

    def get_priority(self, symbol: str, data_type: str = 'trades') -> MessagePriority:
        """
        Get message priority based on symbol and data type.

        Args:
            symbol: Trading symbol
            data_type: 'trades', 'quotes', or 'reference_data'

        Returns:
            MessagePriority level
        """
        # Reference data is always LOW priority
        if data_type == 'reference_data':
            return MessagePriority.LOW

        # Tier classification
        if symbol in self.tier_1:
            return MessagePriority.CRITICAL
        elif symbol in self.tier_2:
            return MessagePriority.HIGH
        else:
            return MessagePriority.NORMAL

    def should_process(
        self,
        symbol: str,
        degradation_level: int,
        data_type: str = 'trades',
    ) -> bool:
        """
        Check if message should be processed at current degradation level.

        Args:
            symbol: Trading symbol
            degradation_level: Current DegradationLevel value (0-4)
            data_type: 'trades', 'quotes', or 'reference_data'

        Returns:
            True if message should be processed, False to shed
        """
        priority = self.get_priority(symbol, data_type)

        # Level 0 (NORMAL): Process everything
        if degradation_level == 0:
            return True

        # Level 1 (SOFT): Still process everything (but skip enrichment)
        if degradation_level == 1:
            return True

        # Level 2 (GRACEFUL): Drop LOW priority
        if degradation_level == 2:
            return priority <= MessagePriority.NORMAL

        # Level 3 (AGGRESSIVE): Only CRITICAL and HIGH
        if degradation_level == 3:
            return priority <= MessagePriority.HIGH

        # Level 4 (CIRCUIT_BREAK): Only CRITICAL
        if degradation_level == 4:
            return priority == MessagePriority.CRITICAL

        return False

    def get_shed_stats(
        self,
        symbols: list[str],
        degradation_level: int,
    ) -> dict:
        """
        Get statistics on what would be shed at given degradation level.

        Useful for understanding impact of degradation.
        """
        processed = sum(1 for s in symbols if self.should_process(s, degradation_level))
        shed = len(symbols) - processed

        return {
            'total': len(symbols),
            'processed': processed,
            'shed': shed,
            'shed_percentage': (shed / len(symbols) * 100) if symbols else 0,
        }
```

### 3. Prometheus Metrics

Add to `src/k2/common/metrics_registry.py`:

```python
# Circuit breaker metrics
'k2_degradation_level': Gauge(
    'k2_degradation_level',
    'Current degradation level (0=normal, 4=circuit_break)',
    ['service', 'environment', 'component'],
),
'k2_degradation_transitions_total': Counter(
    'k2_degradation_transitions_total',
    'Total degradation level transitions',
    ['service', 'environment', 'component', 'from_level', 'to_level'],
),
'k2_messages_shed_total': Counter(
    'k2_messages_shed_total',
    'Total messages shed due to load shedding',
    ['service', 'environment', 'component', 'symbol_tier', 'reason'],
),
```

### 4. Consumer Integration

Update `src/k2/ingestion/consumer.py` to use circuit breaker.

### 5. Unit Tests

Create `tests/unit/test_circuit_breaker.py` with 15+ tests.

---

## Validation

### Acceptance Criteria

1. [x] `src/k2/common/degradation_manager.py` created (Note: Named degradation_manager not circuit_breaker to distinguish from existing fault-tolerance circuit breaker)
2. [x] `src/k2/common/load_shedder.py` created
3. [x] All 5 degradation levels implemented (NORMAL/SOFT/GRACEFUL/AGGRESSIVE/CIRCUIT_BREAK)
4. [x] Hysteresis prevents flapping (recovery_lag_factor=0.5, recovery_heap_factor=0.9, cooldown=30s)
5. [x] Prometheus metrics exposed (degradation_level, degradation_transitions_total, messages_shed_total)
6. [x] Consumer uses degradation manager (integrated in consumer.py with lag calculation and load shedding)
7. [x] 64 unit tests passing (34 for degradation_manager + 30 for load_shedder, 94% and 98% coverage respectively)

### Verification Commands

```bash
# Run unit tests
uv run pytest tests/unit/test_degradation_manager.py tests/unit/test_load_shedder.py -v

# Check metrics exposed
curl http://localhost:8000/metrics | grep degradation

# Test imports
python -c "from k2.common.degradation_manager import DegradationManager, DegradationLevel; print('OK')"
python -c "from k2.common.load_shedder import LoadShedder, MessagePriority; print('OK')"
```

---

## Demo Talking Points

> "Let me show you the circuit breaker implementation.
>
> We have 5 degradation levels from NORMAL to CIRCUIT_BREAK.
> When consumer lag exceeds 100K messages or heap usage hits 70%,
> we degrade to SOFT mode—still processing everything but skipping enrichment.
>
> At GRACEFUL mode, we start shedding load—only processing top symbols.
> At CIRCUIT_BREAK, we stop accepting new data entirely.
>
> The key insight is hysteresis—we recover at lower thresholds than we degrade,
> with a cooldown period to prevent flapping. This is how production systems survive."

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete
