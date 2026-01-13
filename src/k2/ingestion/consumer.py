"""Kafka consumer for market data ingestion to Iceberg.

This module provides a production-ready Kafka consumer that bridges the streaming
layer (Kafka) and the lakehouse layer (Iceberg). Features include:

- Avro deserialization with Schema Registry
- Batch processing with configurable batch size
- Manual commit after successful Iceberg write (at-least-once delivery)
- Sequence gap detection and logging
- Graceful shutdown with signal handling
- Comprehensive metrics and structured logging
- Daemon and batch execution modes
- Support for v1 (legacy) and v2 (industry-standard) schemas

Schema Evolution:
- v1: Legacy ASX-specific schemas → market_data.trades table
- v2: Industry-standard hybrid schemas → market_data.trades_v2 table

Architecture Decisions:
- #012: Data-type-based consumer group naming
- #013: Single-topic subscription (with pattern support)
- #014: Sequence gap logging with metrics tracking
- #015: Batch size 1000 with configurable override
- #016: Daemon mode with graceful shutdown

Usage (v2 schemas):
    # Daemon mode (runs until stopped) with v2 schemas
    consumer = MarketDataConsumer(
        topics=['market.equities.trades.asx'],
        consumer_group='k2-iceberg-writer-trades-v2',
        schema_version='v2',
    )
    consumer.run()

    # Batch mode (consume N messages) with v2 schemas
    consumer = MarketDataConsumer(
        topics=['market.equities.trades.asx'],
        consumer_group='k2-iceberg-writer-trades-v2',
        schema_version='v2',
        max_messages=1000,
    )
    consumer.run()

Usage (v1 schemas - backward compatibility):
    consumer = MarketDataConsumer(
        topics=['market.equities.trades.asx'],
        consumer_group='k2-iceberg-writer-trades',
        schema_version='v1',
    )
    consumer.run()
"""

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics
from k2.ingestion.dead_letter_queue import DeadLetterQueue
from k2.ingestion.sequence_tracker import SequenceTracker
from k2.storage.writer import IcebergWriter

logger = get_logger(__name__)
metrics = create_component_metrics("consumer")


# Define retryable exceptions (transient failures that should be retried)
RETRYABLE_ERRORS = (
    ConnectionError,  # Network issues
    TimeoutError,  # S3 timeouts, catalog timeouts
    OSError,  # File system errors (disk full, permissions)
)

# Permanent errors that should go to DLQ (no retry)
PERMANENT_ERRORS = (
    ValueError,  # Data validation failures
    TypeError,  # Schema mismatches
    KeyError,  # Missing required fields
)


@dataclass
class ConsumerStats:
    """Statistics from consumer operation."""

    messages_consumed: int = 0
    messages_written: int = 0
    errors: int = 0
    sequence_gaps: int = 0
    duplicates_skipped: int = 0
    start_time: float = 0.0

    @property
    def duration_seconds(self) -> float:
        """Calculate duration in seconds."""
        if self.start_time == 0:
            return 0.0
        return time.time() - self.start_time

    @property
    def throughput(self) -> float:
        """Calculate throughput in messages per second."""
        if self.duration_seconds == 0:
            return 0.0
        return self.messages_consumed / self.duration_seconds


class MarketDataConsumer:
    """Kafka consumer with Iceberg writer integration.

    Consumes messages from Kafka topics and writes them to Iceberg tables
    using batch processing with manual offset commits for at-least-once
    delivery guarantees.

    Args:
        topics: List of Kafka topics to subscribe to
        consumer_group: Consumer group name (e.g., 'k2-iceberg-writer-trades')
        bootstrap_servers: Kafka bootstrap servers
        schema_registry_url: Schema Registry URL
        batch_size: Number of messages to batch before writing to Iceberg
        max_messages: Maximum messages to consume (None = infinite, for daemon mode)
        iceberg_writer: Optional IcebergWriter instance
        sequence_tracker: Optional SequenceTracker instance

    Example:
        >>> consumer = MarketDataConsumer(
        ...     topics=['market.equities.trades.asx'],
        ...     consumer_group='k2-iceberg-writer-trades',
        ... )
        >>> consumer.run()
    """

    def __init__(
        self,
        topics: list[str] | None = None,
        topic_pattern: str | None = None,
        consumer_group: str | None = None,
        bootstrap_servers: str | None = None,
        schema_registry_url: str | None = None,
        schema_version: str = "v2",
        batch_size: int | None = None,
        max_messages: int | None = None,
        iceberg_writer: IcebergWriter | None = None,
        sequence_tracker: SequenceTracker | None = None,
        dlq_path: Path | str | None = None,
    ):
        """Initialize consumer with configuration.

        Args:
            dlq_path: Path for Dead Letter Queue files (for permanent failures)
        """
        # Validate topic subscription
        if not topics and not topic_pattern:
            raise ValueError("Must specify either topics or topic_pattern")
        if topics and topic_pattern:
            raise ValueError("Specify either topics or topic_pattern, not both")

        # Validate schema version
        if schema_version not in ("v1", "v2"):
            raise ValueError(f"Invalid schema_version: {schema_version}. Must be 'v1' or 'v2'")

        # Load configuration
        self.topics = topics
        self.topic_pattern = topic_pattern
        self.consumer_group = consumer_group or os.getenv("K2_CONSUMER_GROUP", "k2-iceberg-writer")
        self.bootstrap_servers = bootstrap_servers or config.kafka.bootstrap_servers
        self.schema_registry_url = schema_registry_url or config.kafka.schema_registry_url
        self.schema_version = schema_version
        self.batch_size = batch_size or int(os.getenv("K2_CONSUMER_BATCH_SIZE", "1000"))
        self.max_messages = max_messages

        # Processing state
        self.running = True
        self.stats = ConsumerStats(start_time=time.time())
        self._current_batch: list[dict[str, Any]] = []

        # Initialize components
        self._init_schema_registry()
        self._init_consumer()
        self.iceberg_writer = iceberg_writer or IcebergWriter(schema_version=self.schema_version)
        self.sequence_tracker = sequence_tracker or SequenceTracker()

        # Initialize Dead Letter Queue for permanent failures
        dlq_default_path = Path("/var/k2/dlq") if dlq_path is None else Path(dlq_path)
        self.dlq = DeadLetterQueue(path=dlq_default_path, max_size_mb=100)

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

        logger.info(
            "Consumer initialized",
            consumer_group=self.consumer_group,
            topics=self.topics,
            topic_pattern=self.topic_pattern,
            schema_version=self.schema_version,
            batch_size=self.batch_size,
            mode="daemon" if self.max_messages is None else "batch",
            max_messages=self.max_messages,
        )

    def _init_schema_registry(self):
        """Initialize Schema Registry client and deserializer."""
        try:
            self.schema_registry_client = SchemaRegistryClient({"url": self.schema_registry_url})

            logger.info(
                "Schema Registry client initialized",
                url=self.schema_registry_url,
            )

        except Exception as err:
            logger.error(
                "Failed to initialize Schema Registry client",
                url=self.schema_registry_url,
                error=str(err),
            )
            metrics.increment(
                "consumer_init_errors_total",
                labels={"error_type": "schema_registry", "consumer_group": self.consumer_group},
            )
            raise

    def _init_consumer(self):
        """Initialize Kafka consumer with configuration."""
        # Consumer configuration (Decision #003: At-least-once with manual commit)
        consumer_config = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.consumer_group,
            "auto.offset.reset": "earliest",  # Start from beginning for new consumer groups
            "enable.auto.commit": False,  # Manual commit after Iceberg write
            "max.poll.interval.ms": 300000,  # 5 minutes for slow Iceberg writes
            "session.timeout.ms": 45000,  # 45 seconds
            "heartbeat.interval.ms": 3000,  # 3 seconds
        }

        try:
            self.consumer = Consumer(consumer_config)

            # Subscribe to topics or pattern (Decision #013)
            if self.topics:
                self.consumer.subscribe(self.topics)
                logger.info("Subscribed to topics", topics=self.topics)
            elif self.topic_pattern:
                self.consumer.subscribe(pattern=self.topic_pattern)
                logger.info("Subscribed to topic pattern", pattern=self.topic_pattern)

        except KafkaException as err:
            logger.error(
                "Failed to initialize Kafka consumer",
                bootstrap_servers=self.bootstrap_servers,
                error=str(err),
            )
            metrics.increment(
                "consumer_init_errors_total",
                labels={"error_type": "kafka_consumer", "consumer_group": self.consumer_group},
            )
            raise

    def _get_deserializer(self, subject: str) -> AvroDeserializer:
        """Get or create Avro deserializer for subject.

        Deserializers are cached per subject for performance.

        Args:
            subject: Schema Registry subject (e.g., 'market.equities.trades.asx-value')

        Returns:
            AvroDeserializer instance
        """
        if not hasattr(self, "_deserializers"):
            self._deserializers: dict[str, AvroDeserializer] = {}

        if subject not in self._deserializers:
            try:
                # Get latest schema from Schema Registry
                schema_info = self.schema_registry_client.get_latest_version(subject)

                # Create deserializer
                self._deserializers[subject] = AvroDeserializer(
                    schema_registry_client=self.schema_registry_client,
                    schema_str=schema_info.schema.schema_str,
                )

                logger.debug(
                    "Created Avro deserializer",
                    subject=subject,
                    schema_id=schema_info.schema_id,
                    version=schema_info.version,
                )

            except Exception as err:
                logger.error(
                    "Failed to create deserializer",
                    subject=subject,
                    error=str(err),
                )
                metrics.increment(
                    "deserializer_errors_total",
                    labels={"subject": subject, "consumer_group": self.consumer_group},
                )
                raise

        return self._deserializers[subject]

    def _shutdown_handler(self, signum, frame):
        """Handle graceful shutdown signals (Decision #016)."""
        logger.info("Shutdown signal received", signal=signum)
        self.running = False

    def run(self):
        """Main consumer loop (Decision #016: Daemon or batch mode).

        Runs indefinitely in daemon mode (max_messages=None) or until
        max_messages consumed in batch mode.
        """
        logger.info(
            "Consumer starting",
            mode="daemon" if self.max_messages is None else "batch",
            max_messages=self.max_messages,
            batch_size=self.batch_size,
        )

        try:
            while self.running:
                # Check message limit (batch mode)
                if self.max_messages and self.stats.messages_consumed >= self.max_messages:
                    logger.info(
                        "Max messages reached",
                        count=self.stats.messages_consumed,
                        max_messages=self.max_messages,
                    )
                    break

                # Consume batch
                self._consume_batch()

        except KeyboardInterrupt:
            logger.info("Consumer interrupted by user")
        except Exception as err:
            logger.error("Consumer error", error=str(err), exc_info=True)
            # TODO: Add consumer_errors_total metric to registry
            # metrics.increment(
            #     "consumer_errors_total",
            #     labels={"error_type": "unexpected", "consumer_group": self.consumer_group},
            # )
            raise
        finally:
            # Graceful shutdown
            self._shutdown()

    def _consume_batch(self):
        """Consume a batch of messages and write to Iceberg (Decision #015)."""
        batch: list[dict[str, Any]] = []
        batch_start_time = time.time()

        # Poll messages until batch is full or timeout
        while len(batch) < self.batch_size:
            msg = self.consumer.poll(timeout=0.1)

            if msg is None:
                # No more messages available, process what we have
                if batch:
                    break
                # No messages at all, continue polling
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    # End of partition, not an error
                    logger.debug("Reached end of partition", partition=msg.partition())
                    if batch:
                        break
                    continue
                # Real error
                logger.error(
                    "Consumer error",
                    error=msg.error(),
                    topic=msg.topic(),
                    partition=msg.partition(),
                )
                self.stats.errors += 1
                # TODO: Add consumer_errors_total metric to registry
                # metrics.increment(
                #     "consumer_errors_total",
                #     labels={"error_type": "poll_error", "consumer_group": self.consumer_group},
                # )
                continue

            # Deserialize message
            try:
                record = self._deserialize_message(msg)
                if record:
                    batch.append(record)
                    self.stats.messages_consumed += 1

                    # Update consumer metrics
                    # Standard labels (service, environment, component) are added automatically by component metrics
                    metrics.increment(
                        "kafka_messages_consumed_total",
                        labels={
                            "exchange": record.get("exchange", "unknown"),
                            "asset_class": record.get("asset_class", "unknown"),
                            "topic": msg.topic(),
                            "consumer_group": self.consumer_group,
                        },
                    )

                    # Check sequence (Decision #014: Sequence gap logging)
                    # V2 schema uses "source_sequence" instead of "sequence_number"
                    seq_field = "source_sequence" if self.schema_version == "v2" else "sequence_number"
                    if "symbol" in record and seq_field in record:
                        gap = self.sequence_tracker.check_sequence(
                            record["symbol"], record[seq_field],
                        )
                        if gap:
                            self.stats.sequence_gaps += 1

            except Exception as err:
                logger.error(
                    "Failed to deserialize message",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    error=str(err),
                )
                self.stats.errors += 1
                # TODO: Add consumer_errors_total metric to registry
                # metrics.increment(
                #     "consumer_errors_total",
                #     labels={"error_type": "deserialization", "consumer_group": self.consumer_group},
                # )
                continue

        # Write batch to Iceberg if we have messages
        if batch:
            try:
                # Use retry logic for transient failures
                self._write_batch_with_retry(batch)
                self.stats.messages_written += len(batch)

                # Commit offsets after successful write (at-least-once)
                self.consumer.commit(asynchronous=False)

                # Log batch statistics
                batch_duration = time.time() - batch_start_time
                logger.info(
                    "Batch processed",
                    batch_size=len(batch),
                    duration_seconds=batch_duration,
                    throughput=len(batch) / batch_duration if batch_duration > 0 else 0,
                    total_consumed=self.stats.messages_consumed,
                    total_written=self.stats.messages_written,
                )

                # Track batch processing metrics
                sample = batch[0] if batch else {}
                metrics.histogram(
                    "iceberg_batch_size",
                    value=len(batch),
                    labels={
                        "exchange": sample.get("exchange", "unknown"),
                        "asset_class": sample.get("asset_class", "unknown"),
                        "table": "trades_v2" if self.schema_version == "v2" else "trades",
                    },
                )

            except RetryError as err:
                # Exhausted retries for transient error - CRASH and restart
                logger.critical(
                    "Iceberg write failed after 3 retries - CRASH",
                    error=str(err.last_attempt.exception()),
                    error_type=type(err.last_attempt.exception()).__name__,
                    batch_size=len(batch),
                    consumer_group=self.consumer_group,
                )
                self.stats.errors += 1
                metrics.increment(
                    "consumer_errors_total",
                    labels={"error_type": "exhausted_retries", "consumer_group": self.consumer_group},
                )
                # Don't commit offsets - will reprocess after restart
                raise

            except PERMANENT_ERRORS as err:
                # Permanent error (validation, schema mismatch) - send to DLQ
                logger.error(
                    "Permanent write error - sending to DLQ",
                    error=str(err),
                    error_type=type(err).__name__,
                    batch_size=len(batch),
                    consumer_group=self.consumer_group,
                )

                # Write to DLQ for manual review
                self.dlq.write(
                    messages=batch,
                    error=str(err),
                    error_type=type(err).__name__,
                    metadata={
                        "consumer_group": self.consumer_group,
                        "topic": batch[0].get("topic", "unknown") if batch else "unknown",
                        "schema_version": self.schema_version,
                    },
                )

                self.stats.errors += 1
                metrics.increment(
                    "consumer_errors_total",
                    labels={"error_type": "permanent", "consumer_group": self.consumer_group},
                )

                # Commit offsets to skip bad messages (already in DLQ)
                self.consumer.commit(asynchronous=False)

            except Exception as err:
                # Unexpected error - log and crash for investigation
                logger.critical(
                    "Unexpected error during batch processing",
                    error=str(err),
                    error_type=type(err).__name__,
                    batch_size=len(batch),
                )
                self.stats.errors += 1
                metrics.increment(
                    "consumer_errors_total",
                    labels={"error_type": "unexpected", "consumer_group": self.consumer_group},
                )
                raise

    def _deserialize_message(self, msg) -> dict[str, Any] | None:
        """Deserialize Avro message from Kafka.

        Args:
            msg: Kafka message

        Returns:
            Deserialized record as dictionary, or None if deserialization fails
        """
        # Get deserializer for this topic
        subject = f"{msg.topic()}-value"
        deserializer = self._get_deserializer(subject)

        # Deserialize value
        value = deserializer(msg.value(), SerializationContext(msg.topic(), MessageField.VALUE))

        return value

    @retry(
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _write_batch_with_retry(self, batch: list[dict[str, Any]]) -> None:
        """Write batch to Iceberg with automatic retry for transient failures.

        Retries on transient errors (connection, timeout, OSError) with exponential backoff:
        - Attempt 1: Immediate
        - Attempt 2: 2 seconds wait
        - Attempt 3: 4 seconds wait

        Raises:
            RetryError: If all retry attempts exhausted (transient error)
            Other exceptions: Permanent errors (schema mismatch, validation, etc.)

        Security:
            Prevents data loss by retrying transient failures (S3 throttling,
            network timeouts) while properly handling permanent errors via DLQ.
        """
        try:
            self.iceberg_writer.write_trades(batch)

        except RETRYABLE_ERRORS as err:
            logger.warning(
                "Transient Iceberg write failure, will retry",
                error=str(err),
                error_type=type(err).__name__,
                batch_size=len(batch),
            )
            raise  # Let tenacity handle retry

    def _write_batch_to_iceberg(self, batch: list[dict[str, Any]]):
        """Write batch of records to Iceberg.

        Args:
            batch: List of deserialized records
        """
        start_time = time.time()

        try:
            # Write to Iceberg (IcebergWriter handles transaction)
            # Assuming trades for now - TODO: detect message type from topic or schema
            self.iceberg_writer.write_trades(batch)

            # Track write duration
            duration = time.time() - start_time
            # Extract exchange/asset_class from first record in batch (all should be same topic)
            sample = batch[0] if batch else {}
            metrics.histogram(
                "iceberg_write_duration_seconds",
                value=duration,
                labels={
                    "exchange": sample.get("exchange", "unknown"),
                    "asset_class": sample.get("asset_class", "unknown"),
                    "table": "trades_v2" if self.schema_version == "v2" else "trades",
                    "operation": "append",
                },
            )

            logger.debug(
                "Batch written to Iceberg",
                batch_size=len(batch),
                duration_seconds=duration,
            )

        except Exception as err:
            # Extract exchange/asset_class from first record in batch
            sample = batch[0] if batch else {}
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": sample.get("exchange", "unknown"),
                    "asset_class": sample.get("asset_class", "unknown"),
                    "table": "trades_v2" if self.schema_version == "v2" else "trades",
                    "error_type": str(type(err).__name__),
                },
            )
            raise

    def _shutdown(self):
        """Clean shutdown: flush, commit, log stats (Decision #016)."""
        logger.info("Consumer shutting down")

        try:
            # Iceberg writes are atomic - no flush needed
            # Final commit
            self.consumer.commit()

            # Close consumer
            self.consumer.close()

            # Log final statistics
            logger.info(
                "Consumer stopped",
                messages_consumed=self.stats.messages_consumed,
                messages_written=self.stats.messages_written,
                errors=self.stats.errors,
                sequence_gaps=self.stats.sequence_gaps,
                duration_seconds=self.stats.duration_seconds,
                throughput=self.stats.throughput,
            )

        except Exception as err:
            logger.error("Error during shutdown", error=str(err))

    def close(self):
        """Close consumer and cleanup resources."""
        self.running = False
        self._shutdown()


def create_consumer(
    topics: list[str] | None = None,
    topic_pattern: str | None = None,
    consumer_group: str | None = None,
    batch_size: int | None = None,
    max_messages: int | None = None,
) -> MarketDataConsumer:
    """Factory function to create a MarketDataConsumer instance.

    Args:
        topics: List of Kafka topics to subscribe to
        topic_pattern: Topic pattern to subscribe to (e.g., 'market\\..*\\.trades\\..*')
        consumer_group: Consumer group name
        batch_size: Number of messages to batch before writing
        max_messages: Maximum messages to consume (None = infinite)

    Returns:
        MarketDataConsumer: Configured consumer instance

    Example:
        >>> consumer = create_consumer(
        ...     topics=['market.equities.trades.asx'],
        ...     consumer_group='k2-iceberg-writer-trades',
        ...     batch_size=1000,
        ... )
        >>> consumer.run()
    """
    return MarketDataConsumer(
        topics=topics,
        topic_pattern=topic_pattern,
        consumer_group=consumer_group,
        batch_size=batch_size,
        max_messages=max_messages,
    )


# ==============================================================================
# CLI Interface
# ==============================================================================

try:
    import typer
    from rich.console import Console

    app = typer.Typer(
        name="consumer",
        help="Kafka consumer for market data ingestion to Iceberg",
        add_completion=False,
    )
    console = Console()

    @app.command()
    def consume(
        topic: str | None = typer.Option(
            None,
            "--topic",
            "-t",
            help="Kafka topic to consume from (e.g., market.equities.trades.asx)",
        ),
        topic_pattern: str | None = typer.Option(
            None,
            "--topic-pattern",
            "-p",
            help="Kafka topic pattern (e.g., 'market\\.equities\\.trades\\..*')",
        ),
        consumer_group: str | None = typer.Option(
            None,
            "--consumer-group",
            "-g",
            help="Consumer group name (default: k2-iceberg-writer)",
        ),
        batch_size: int = typer.Option(
            1000,
            "--batch-size",
            "-b",
            help="Batch size for Iceberg writes",
        ),
        max_messages: int | None = typer.Option(
            None,
            "--max-messages",
            "-n",
            help="Maximum messages to consume (None = daemon mode)",
        ),
    ):
        """Consume messages from Kafka and write to Iceberg.

        Examples:
            # Daemon mode (runs until stopped)
            python -m k2.ingestion.consumer consume --topic market.equities.trades.asx

            # Batch mode (consume 1000 messages then exit)
            python -m k2.ingestion.consumer consume --topic market.equities.trades.asx --max-messages 1000

            # Pattern matching (all equity trades)
            python -m k2.ingestion.consumer consume --topic-pattern "market\\.equities\\.trades\\..*"

            # Custom batch size
            python -m k2.ingestion.consumer consume --topic market.equities.trades.asx --batch-size 5000
        """
        # Validate input
        if not topic and not topic_pattern:
            console.print("[red]Error: Must specify either --topic or --topic-pattern[/red]")
            raise typer.Exit(1)

        if topic and topic_pattern:
            console.print("[red]Error: Specify either --topic or --topic-pattern, not both[/red]")
            raise typer.Exit(1)

        # Display configuration
        console.print("\n[bold cyan]K2 Market Data Consumer[/bold cyan]")
        console.print("=" * 60)

        if topic:
            console.print(f"[green]Topic:[/green] {topic}")
        else:
            console.print(f"[green]Topic Pattern:[/green] {topic_pattern}")

        console.print(f"[green]Consumer Group:[/green] {consumer_group or 'k2-iceberg-writer'}")
        console.print(f"[green]Batch Size:[/green] {batch_size}")
        console.print(
            f"[green]Mode:[/green] {'daemon' if max_messages is None else f'batch ({max_messages} messages)'}",
        )
        console.print("=" * 60)
        console.print()

        # Create and run consumer
        try:
            topics_list = [topic] if topic else None

            consumer = create_consumer(
                topics=topics_list,
                topic_pattern=topic_pattern,
                consumer_group=consumer_group,
                batch_size=batch_size,
                max_messages=max_messages,
            )

            console.print("[green]Starting consumer...[/green]")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")

            consumer.run()

            # Success
            console.print(
                f"\n[green]✓[/green] Consumed {consumer.stats.messages_consumed} messages",
            )
            console.print(
                f"[green]✓[/green] Written {consumer.stats.messages_written} records to Iceberg",
            )

            if consumer.stats.errors > 0:
                console.print(f"[yellow]⚠[/yellow]  {consumer.stats.errors} errors")

            if consumer.stats.sequence_gaps > 0:
                console.print(
                    f"[yellow]⚠[/yellow]  {consumer.stats.sequence_gaps} sequence gaps detected",
                )

        except KeyboardInterrupt:
            console.print("\n[yellow]Consumer stopped by user[/yellow]")
        except Exception as err:
            console.print(f"\n[red]Error: {err}[/red]")
            raise typer.Exit(1)

    if __name__ == "__main__":
        app()

except ImportError:
    # Typer not installed, skip CLI
    pass
