"""Sequence number tracking and gap detection for market data.

Ensures ordering guarantees are maintained from exchange through storage.
See docs/MARKET_DATA_GUARANTEES.md for design rationale.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from k2.common.logging import get_logger
from k2.common.metrics import metrics

logger = get_logger(__name__)


@dataclass
class SequenceState:
    """Tracks last seen sequence number and timestamp for a symbol."""

    last_sequence: int
    last_timestamp: datetime
    total_messages: int
    gap_count: int
    out_of_order_count: int


class SequenceTracker:
    """Tracks exchange sequence numbers to detect gaps and out-of-order delivery.

    Market data exchanges provide monotonically increasing sequence numbers per symbol.
    This tracker detects:
    - Sequence gaps (missed messages, potential data loss)
    - Sequence resets (daily session starts)
    - Out-of-order delivery (Kafka partition reassignment)

    Usage:
        tracker = SequenceTracker(gap_alert_threshold=10)

        for message in kafka_consumer:
            event = tracker.check_sequence(
                exchange=message['exchange'],
                symbol=message['symbol'],
                sequence=message['sequence_number'],
                timestamp=message['exchange_timestamp']
            )

            if event == SequenceEvent.LARGE_GAP:
                # Page on-call, potential data loss
                alert_large_gap(...)
    """

    def __init__(
        self,
        gap_alert_threshold: int = 10,
        reset_detection_window: timedelta = timedelta(hours=1),
    ):
        """Initialize sequence tracker.

        Args:
            gap_alert_threshold: Alert if gap exceeds this size
            reset_detection_window: Time window to detect session resets
        """
        self.gap_threshold = gap_alert_threshold
        self.reset_window = reset_detection_window

        # State per (exchange, symbol)
        self._state: dict[tuple[str, str], SequenceState] = {}

    def check_sequence(
        self,
        exchange: str,
        symbol: str,
        sequence: int,
        timestamp: datetime,
    ) -> "SequenceEvent":
        """Validate sequence number and detect anomalies.

        Args:
            exchange: Exchange identifier (e.g., "ASX", "Chi-X")
            symbol: Trading symbol (e.g., "BHP")
            sequence: Exchange-provided sequence number
            timestamp: Exchange timestamp

        Returns:
            SequenceEvent indicating what happened (OK, GAP, RESET, etc.)
        """
        key = (exchange, symbol)

        # First message for this symbol
        if key not in self._state:
            self._state[key] = SequenceState(
                last_sequence=sequence,
                last_timestamp=timestamp,
                total_messages=1,
                gap_count=0,
                out_of_order_count=0,
            )
            logger.debug(f"First message for {exchange}.{symbol} at sequence {sequence}")
            return SequenceEvent.OK

        state = self._state[key]
        expected_sequence = state.last_sequence + 1

        # Case 1: Expected sequence (happy path)
        if sequence == expected_sequence:
            state.last_sequence = sequence
            state.last_timestamp = timestamp
            state.total_messages += 1
            return SequenceEvent.OK

        # Case 2: Sequence jumped forward (gap)
        if sequence > expected_sequence:
            gap_size = sequence - expected_sequence

            # Check if this is a session reset (sequence dropped significantly)
            if self._is_session_reset(state, sequence, timestamp):
                logger.info(
                    f"Session reset detected: {exchange}.{symbol} "
                    f"{state.last_sequence} → {sequence}",
                )
                metrics.increment(
                    "sequence_resets_detected_total",
                    labels={"exchange": exchange, "symbol": symbol},
                )

                # Reset state for new session
                state.last_sequence = sequence
                state.last_timestamp = timestamp
                state.total_messages += 1
                return SequenceEvent.RESET

            # Not a reset, this is a real gap
            state.gap_count += 1
            logger.warning(
                f"Sequence gap detected: {exchange}.{symbol} "
                f"expected {expected_sequence}, got {sequence} (gap: {gap_size})",
            )

            metrics.increment(
                "sequence_gaps_detected_total",
                labels={
                    "exchange": exchange,
                    "symbol": symbol,
                    "gap_size": str(gap_size),
                },
            )

            # Update state (we can't wait for missing messages)
            state.last_sequence = sequence
            state.last_timestamp = timestamp
            state.total_messages += 1

            # Large gaps may indicate data loss
            if gap_size > self.gap_threshold:
                logger.error(
                    f"LARGE GAP: {exchange}.{symbol} gap of {gap_size} "
                    f"(threshold: {self.gap_threshold})",
                )
                return SequenceEvent.LARGE_GAP

            return SequenceEvent.SMALL_GAP

        # Case 3: Sequence went backwards (out-of-order or duplicate)
        if sequence <= state.last_sequence:
            state.out_of_order_count += 1
            logger.warning(
                f"Out-of-order message: {exchange}.{symbol} "
                f"sequence {sequence} received after {state.last_sequence}",
            )

            metrics.increment(
                "out_of_order_messages_total",
                labels={"exchange": exchange, "symbol": symbol},
            )

            # Don't update last_sequence (preserve high watermark)
            return SequenceEvent.OUT_OF_ORDER

        # Should never reach here
        return SequenceEvent.OK

    def _is_session_reset(
        self, state: SequenceState, new_sequence: int, new_timestamp: datetime,
    ) -> bool:
        """Detect if sequence number reset is due to session restart.

        Heuristics:
        1. Sequence dropped by >50% (e.g., 1M → 100)
        2. Timestamp jumped forward by >1 hour (overnight reset)

        Args:
            state: Current state for this symbol
            new_sequence: New sequence number
            new_timestamp: New timestamp

        Returns:
            True if reset detected
        """
        # Sequence dropped significantly
        sequence_dropped = new_sequence < state.last_sequence * 0.5

        # Time jumped forward (session boundary)
        time_jumped = (new_timestamp - state.last_timestamp) > self.reset_window

        return sequence_dropped and time_jumped

    def get_stats(self, exchange: str, symbol: str) -> SequenceState | None:
        """Get tracking statistics for a symbol."""
        return self._state.get((exchange, symbol))

    def reset(self, exchange: str, symbol: str):
        """Reset tracking state for a symbol (use after manual recovery)."""
        key = (exchange, symbol)
        if key in self._state:
            del self._state[key]
            logger.info(f"Reset tracking state for {exchange}.{symbol}")


class SequenceEvent:
    """Events returned by SequenceTracker."""

    OK = "ok"
    SMALL_GAP = "small_gap"
    LARGE_GAP = "large_gap"
    RESET = "reset"
    OUT_OF_ORDER = "out_of_order"


class DeduplicationCache:
    """Message deduplication based on unique message IDs.

    Uses time-windowed cache to detect duplicate delivery.
    See docs/CORRECTNESS_TRADEOFFS.md for at-least-once semantics.

    Usage:
        dedup = DeduplicationCache(window_hours=24)

        for message in kafka_consumer:
            message_id = f"{message['exchange']}.{message['symbol']}.{message['sequence']}"

            if dedup.is_duplicate(message_id):
                continue  # Skip duplicate

            process_message(message)
    """

    def __init__(self, window_hours: int = 24):
        """Initialize deduplication cache.

        Args:
            window_hours: How long to remember message IDs
        """
        # In production, use Redis with TTL
        # For now, simple in-memory cache
        self._cache: dict[str, datetime] = {}
        self._window = timedelta(hours=window_hours)
        self._last_cleanup = datetime.utcnow()

    def is_duplicate(self, message_id: str) -> bool:
        """Check if message ID was seen recently.

        Args:
            message_id: Unique message identifier

        Returns:
            True if duplicate (seen within window)
        """
        now = datetime.utcnow()

        # Periodic cleanup of expired entries
        if now - self._last_cleanup > timedelta(minutes=10):
            self._cleanup(now)

        # Check if message was seen
        if message_id in self._cache:
            metrics.increment("duplicates_detected_total")
            return True

        # First time seeing this message
        self._cache[message_id] = now
        return False

    def _cleanup(self, now: datetime):
        """Remove expired entries from cache."""
        expired_threshold = now - self._window

        expired_keys = [
            msg_id for msg_id, timestamp in self._cache.items() if timestamp < expired_threshold
        ]

        for key in expired_keys:
            del self._cache[key]

        self._last_cleanup = now

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired deduplication entries")
