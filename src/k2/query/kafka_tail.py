"""KafkaTail - In-memory buffer for recent Kafka messages.

Maintains a sliding window of recent messages for hybrid queries that merge
Kafka (uncommitted, last 2-5 minutes) with Iceberg (committed, historical).

Architecture:
- Background thread consumes from Kafka starting at latest offset
- Messages stored in memory (dict: symbol -> list of messages)
- Thread-safe operations with mutex lock
- Automatic trimming by time window and message count limits
- Query interface returns messages by symbol and time range

Usage:
    tail = KafkaTail(bootstrap_servers="localhost:9092")
    tail.start()

    # Query recent messages
    messages = tail.query(
        symbol="BTCUSDT",
        exchange="binance",
        start_time=datetime.now() - timedelta(minutes=5),
        end_time=datetime.now()
    )

    tail.stop()
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException

from k2.common.config import config
from k2.common.logging import get_logger

logger = get_logger(__name__, component="kafka_tail")


@dataclass
class TailMessage:
    """Message stored in the tail buffer."""

    symbol: str
    exchange: str
    timestamp: datetime
    message_id: str
    data: dict[str, Any]  # Full message data


class KafkaTail:
    """In-memory buffer for recent Kafka messages.

    Maintains a sliding window (default 5 minutes) of recent messages
    for hybrid queries. Background consumer thread reads from Kafka
    starting at the latest offset.

    Thread Safety:
    - All buffer operations protected by mutex lock
    - Background consumer runs in separate thread
    - Query operations are thread-safe

    Memory Management:
    - Automatic trimming by time window (buffer_minutes)
    - Per-symbol message count limit (max_messages_per_symbol)
    - Typical memory: 5 min × 1K msg/sec × 500 bytes = 150 MB

    Performance:
    - Query: O(n) where n = messages in time window for symbol
    - Trim: O(n) where n = total messages (runs every 10 seconds)
    - Lock contention minimal (background thread holds lock <1ms)
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topic: str = "market-data.trades.v2",
        group_id: str = "k2-kafka-tail",
        buffer_minutes: int = 5,
        max_messages_per_symbol: int = 10000,
        trim_interval_seconds: int = 10,
    ):
        """Initialize Kafka tail buffer.

        Args:
            bootstrap_servers: Kafka bootstrap servers (defaults to config)
            topic: Kafka topic to consume from
            group_id: Consumer group ID
            buffer_minutes: Time window for message buffer (minutes)
            max_messages_per_symbol: Max messages per symbol in buffer
            trim_interval_seconds: How often to trim old messages
        """
        self.bootstrap_servers = bootstrap_servers or config.kafka.bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.buffer_minutes = buffer_minutes
        self.max_messages_per_symbol = max_messages_per_symbol
        self.trim_interval_seconds = trim_interval_seconds

        # In-memory buffer: symbol -> list of TailMessage
        self._buffer: dict[str, list[TailMessage]] = defaultdict(list)
        self._lock = threading.Lock()

        # Consumer state
        self._consumer: Consumer | None = None
        self._consumer_thread: threading.Thread | None = None
        self._running = False
        self._last_trim_time = time.time()

        # Stats
        self._stats = {
            "messages_received": 0,
            "messages_trimmed": 0,
            "buffer_size": 0,
            "symbols_tracked": 0,
        }

        logger.info(
            "KafkaTail initialized",
            topic=topic,
            buffer_minutes=buffer_minutes,
            max_messages_per_symbol=max_messages_per_symbol,
        )

    def start(self) -> None:
        """Start the background consumer thread."""
        if self._running:
            logger.warning("KafkaTail already running")
            return

        self._running = True

        # Initialize Kafka consumer
        self._consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": self.group_id,
                "auto.offset.reset": "latest",  # Start from latest (most recent)
                "enable.auto.commit": True,
                "auto.commit.interval.ms": 5000,
                "session.timeout.ms": 30000,
            }
        )

        # Subscribe to topic
        self._consumer.subscribe([self.topic])

        # Start consumer thread
        self._consumer_thread = threading.Thread(
            target=self._consume_loop,
            name="kafka-tail-consumer",
            daemon=True,
        )
        self._consumer_thread.start()

        logger.info("KafkaTail started", topic=self.topic)

    def stop(self) -> None:
        """Stop the background consumer thread."""
        if not self._running:
            return

        self._running = False

        # Wait for consumer thread to finish
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5.0)

        # Close consumer
        if self._consumer:
            self._consumer.close()
            self._consumer = None

        logger.info("KafkaTail stopped", stats=self._stats)

    def _consume_loop(self) -> None:
        """Background consumer loop (runs in separate thread)."""
        if not self._consumer:
            logger.error("Consumer not initialized")
            return

        logger.info("Consumer loop started")

        while self._running:
            try:
                # Poll for messages (1 second timeout)
                msg = self._consumer.poll(timeout=1.0)

                if msg is None:
                    # No message, check if we need to trim
                    self._maybe_trim()
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition, not an error
                        continue
                    logger.error("Kafka error", error=msg.error())
                    continue

                # Process message
                self._process_message(msg)

            except KafkaException as e:
                logger.error("Kafka exception in consumer loop", error=str(e))
                time.sleep(1)
            except Exception as e:
                logger.error("Unexpected error in consumer loop", error=str(e))
                time.sleep(1)

        logger.info("Consumer loop stopped")

    def _process_message(self, msg: Any) -> None:
        """Process a message from Kafka.

        Args:
            msg: Kafka message
        """
        try:
            # Parse message (simplified - in production would use Avro schema)
            value = msg.value()
            if not value:
                return

            # Extract fields (assuming JSON for now)
            import json

            data = json.loads(value.decode("utf-8"))

            symbol = data.get("symbol")
            exchange = data.get("exchange")
            message_id = data.get("message_id")

            if not symbol or not exchange or not message_id:
                logger.warning("Message missing required fields", data=data)
                return

            # Parse timestamp
            timestamp_str = data.get("timestamp")
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                # Use message timestamp if not in payload
                timestamp = datetime.fromtimestamp(msg.timestamp()[1] / 1000, tz=UTC)

            # Create TailMessage
            tail_msg = TailMessage(
                symbol=symbol,
                exchange=exchange,
                timestamp=timestamp,
                message_id=message_id,
                data=data,
            )

            # Add to buffer (thread-safe)
            with self._lock:
                self._buffer[symbol].append(tail_msg)
                self._stats["messages_received"] += 1

                # Enforce per-symbol limit
                if len(self._buffer[symbol]) > self.max_messages_per_symbol:
                    # Remove oldest message
                    self._buffer[symbol].pop(0)
                    self._stats["messages_trimmed"] += 1

        except Exception as e:
            logger.error("Error processing message", error=str(e))

    def _maybe_trim(self) -> None:
        """Trim old messages if trim interval elapsed."""
        now = time.time()
        if now - self._last_trim_time < self.trim_interval_seconds:
            return

        self._trim_old_messages()
        self._last_trim_time = now

    def _trim_old_messages(self) -> None:
        """Remove messages older than buffer_minutes."""
        cutoff_time = datetime.now(UTC) - timedelta(minutes=self.buffer_minutes)

        with self._lock:
            trimmed = 0

            for symbol in list(self._buffer.keys()):
                messages = self._buffer[symbol]

                # Find first message within time window
                # (messages are in chronological order)
                keep_from = 0
                for i, msg in enumerate(messages):
                    if msg.timestamp >= cutoff_time:
                        keep_from = i
                        break

                # Trim old messages
                if keep_from > 0:
                    self._buffer[symbol] = messages[keep_from:]
                    trimmed += keep_from

                # Remove empty symbol entries
                if not self._buffer[symbol]:
                    del self._buffer[symbol]

            self._stats["messages_trimmed"] += trimmed
            self._stats["buffer_size"] = sum(len(msgs) for msgs in self._buffer.values())
            self._stats["symbols_tracked"] = len(self._buffer)

        if trimmed > 0:
            logger.debug(
                "Trimmed old messages",
                trimmed=trimmed,
                buffer_size=self._stats["buffer_size"],
            )

    def query(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Query messages from buffer for given symbol and time range.

        Args:
            symbol: Symbol to query (e.g., "BTCUSDT")
            exchange: Exchange name (e.g., "binance")
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)

        Returns:
            List of messages as dicts, sorted by timestamp
        """
        with self._lock:
            messages = self._buffer.get(symbol, [])

            # Filter by time range and exchange
            results = []
            for msg in messages:
                if msg.exchange == exchange and start_time <= msg.timestamp <= end_time:
                    results.append(msg.data)

            return results

    def get_stats(self) -> dict[str, Any]:
        """Get buffer statistics."""
        with self._lock:
            return dict(self._stats)

    def clear(self) -> None:
        """Clear all messages from buffer (for testing)."""
        with self._lock:
            self._buffer.clear()
            self._stats = {
                "messages_received": 0,
                "messages_trimmed": 0,
                "buffer_size": 0,
                "symbols_tracked": 0,
            }

        logger.info("Buffer cleared")


def create_kafka_tail(
    bootstrap_servers: str | None = None,
    buffer_minutes: int = 5,
) -> KafkaTail:
    """Factory function to create and start KafkaTail.

    Usage:
        tail = create_kafka_tail()
        # tail is already started
        messages = tail.query(...)

    Args:
        bootstrap_servers: Kafka bootstrap servers (optional)
        buffer_minutes: Time window for buffer (default 5 minutes)

    Returns:
        Started KafkaTail instance
    """
    tail = KafkaTail(
        bootstrap_servers=bootstrap_servers,
        buffer_minutes=buffer_minutes,
    )
    tail.start()
    return tail
