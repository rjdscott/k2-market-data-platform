"""Kafka producer for market data ingestion.

This module provides a production-ready Kafka producer with:
- Idempotent producer configuration (at-least-once with deduplication)
- Avro serialization with Schema Registry integration
- Partition by symbol for ordering guarantees
- Exponential backoff retry for transient failures
- Structured logging and Prometheus metrics

Architecture:
    The producer uses the exchange + asset class topic architecture via
    TopicNameBuilder. Each message is routed to the appropriate topic based
    on asset class, data type, and exchange.

    Topic naming: market.{asset_class}.{data_type}.{exchange}
    Example: market.equities.trades.asx

Usage:
    from k2.ingestion.producer import MarketDataProducer
    from k2.kafka import DataType

    producer = MarketDataProducer()

    # Produce a trade record
    producer.produce_trade(
        asset_class='equities',
        exchange='asx',
        record={'symbol': 'BHP', 'price': 45.50, ...}
    )

    # Flush pending messages
    producer.flush()
    producer.close()

Configuration:
    Producer behavior is controlled by K2 environment variables:
    - K2_KAFKA_BOOTSTRAP_SERVERS: Kafka brokers (default: localhost:9092)
    - K2_KAFKA_SCHEMA_REGISTRY_URL: Schema Registry URL (default: http://localhost:8081)

See Also:
    - Decision #009: Partition by symbol for ordering
    - Decision #010: At-least-once with idempotent producers
"""

import time
from typing import Any

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics
from k2.kafka import DataType, get_topic_builder

logger = get_logger(__name__, component="ingestion")
metrics = create_component_metrics("ingestion")


class MarketDataProducer:
    """Production-ready Kafka producer for market data.

    Features:
    - Idempotent producer (enable.idempotence=True) prevents duplicates on retry
    - At-least-once delivery semantics with acks=all
    - Partition by symbol to preserve per-symbol ordering
    - Avro serialization with Schema Registry integration
    - Exponential backoff retry for transient failures
    - Structured logging with correlation IDs
    - Comprehensive Prometheus metrics

    Example:
        >>> producer = MarketDataProducer()
        >>> producer.produce_trade(
        ...     asset_class='equities',
        ...     exchange='asx',
        ...     record={'symbol': 'BHP', 'price': 45.50, 'quantity': 1000}
        ... )
        >>> producer.flush()  # Wait for all messages to be sent
        >>> producer.close()
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        schema_registry_url: str | None = None,
        max_retries: int = 3,
        initial_retry_delay: float = 0.1,
        retry_backoff_factor: float = 2.0,
        max_retry_delay: float = 10.0,
    ):
        """Initialize Kafka producer with idempotent configuration.

        Args:
            bootstrap_servers: Kafka bootstrap servers (default: from config)
            schema_registry_url: Schema Registry URL (default: from config)
            max_retries: Maximum retry attempts for transient failures (default: 3)
            initial_retry_delay: Initial retry delay in seconds (default: 0.1)
            retry_backoff_factor: Exponential backoff multiplier (default: 2.0)
            max_retry_delay: Maximum retry delay in seconds (default: 10.0)

        Raises:
            Exception: If producer initialization fails
        """
        self.bootstrap_servers = bootstrap_servers or config.kafka.bootstrap_servers
        self.schema_registry_url = schema_registry_url or config.kafka.schema_registry_url

        # Retry configuration (Decision #006: Exponential Backoff Retry)
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.retry_backoff_factor = retry_backoff_factor
        self.max_retry_delay = max_retry_delay

        # Initialize topic builder
        self.topic_builder = get_topic_builder()

        # Initialize Schema Registry client
        self._init_schema_registry()

        # Initialize Kafka producer with idempotent config
        self._init_producer()

        # Cache for Avro serializers (keyed by schema subject)
        self._serializers: dict[str, AvroSerializer] = {}

        # Statistics
        self._total_produced = 0
        self._total_errors = 0
        self._total_retries = 0

        logger.info(
            "Kafka producer initialized",
            bootstrap_servers=self.bootstrap_servers,
            schema_registry_url=self.schema_registry_url,
            max_retries=self.max_retries,
        )
        metrics.increment("producer_initialized_total", labels={"component_type": "producer"})

    def _init_schema_registry(self):
        """Initialize Schema Registry client."""
        try:
            self.schema_registry_client = SchemaRegistryClient({"url": self.schema_registry_url})
            logger.debug(
                "Schema Registry client initialized",
                url=self.schema_registry_url,
            )
        except Exception as e:
            logger.error(
                "Failed to initialize Schema Registry client",
                url=self.schema_registry_url,
                error=str(e),
            )
            metrics.increment(
                "producer_init_errors_total",
                labels={"component_type": "producer", "error": "schema_registry"},
            )
            raise

    def _init_producer(self):
        """Initialize Kafka producer with idempotent configuration.

        Producer Configuration (Decision #010: At-Least-Once with Idempotent Producers):
        - enable.idempotence=True: Prevents duplicates on retry
        - acks=all: Wait for all in-sync replicas to acknowledge
        - retries=3: Retry transient failures
        - max.in.flight.requests.per.connection=5: Required for idempotence
        - compression.type=snappy: Balance speed and compression ratio
        """
        producer_config = {
            "bootstrap.servers": self.bootstrap_servers,
            # Idempotency (Decision #010)
            "enable.idempotence": True,
            "acks": "all",  # Wait for all replicas
            "retries": self.max_retries,
            "max.in.flight.requests.per.connection": 5,  # Required for idempotence
            # Performance
            "compression.type": "snappy",  # Balance speed + compression
            "linger.ms": 10,  # Small batching delay for throughput
            "batch.size": 16384,  # 16KB batch size
            # Error handling
            "request.timeout.ms": 30000,  # 30 second timeout
            "delivery.timeout.ms": 120000,  # 2 minute total timeout
        }

        try:
            self.producer = Producer(producer_config)
            logger.debug(
                "Kafka producer created",
                config={k: v for k, v in producer_config.items() if "password" not in k.lower()},
            )
        except Exception as e:
            logger.error(
                "Failed to create Kafka producer",
                bootstrap_servers=self.bootstrap_servers,
                error=str(e),
            )
            metrics.increment(
                "producer_init_errors_total",
                labels={"component_type": "producer", "error": "kafka_connection"},
            )
            raise

    def _get_serializer(self, schema_subject: str) -> AvroSerializer:
        """Get or create Avro serializer for schema subject.

        Args:
            schema_subject: Schema Registry subject name (e.g., 'market.equities.trades-value')

        Returns:
            Cached AvroSerializer instance

        Raises:
            Exception: If schema cannot be loaded from Schema Registry
        """
        if schema_subject not in self._serializers:
            try:
                # Fetch latest schema from Schema Registry
                schema = self.schema_registry_client.get_latest_version(schema_subject)

                # Create Avro serializer
                serializer = AvroSerializer(
                    schema_registry_client=self.schema_registry_client,
                    schema_str=schema.schema.schema_str,
                )

                self._serializers[schema_subject] = serializer

                logger.debug(
                    "Avro serializer created",
                    subject=schema_subject,
                    schema_id=schema.schema_id,
                    schema_version=schema.version,
                )

            except Exception as e:
                logger.error(
                    "Failed to create Avro serializer",
                    subject=schema_subject,
                    error=str(e),
                )
                metrics.increment(
                    "serializer_errors_total",
                    labels={"component_type": "producer", "subject": schema_subject},
                )
                raise

        return self._serializers[schema_subject]

    def _delivery_callback(
        self,
        err,
        msg,
        context: dict[str, Any],
    ):
        """Callback invoked on message delivery.

        Args:
            err: Error if delivery failed, None if successful
            msg: Kafka message object
            context: Additional context (topic, partition_key, labels)
        """
        topic = context.get("topic", "unknown")
        partition_key = context.get("partition_key", "unknown")
        labels = context.get("labels", {})

        if err:
            self._total_errors += 1
            logger.error(
                "Message delivery failed",
                topic=topic,
                partition_key=partition_key,
                error=str(err),
                **labels,
            )
            metrics.increment(
                "kafka_produce_errors_total",
                labels={**labels, "error_type": str(err)[:50]},
            )
        else:
            self._total_produced += 1
            logger.debug(
                "Message delivered",
                topic=topic,
                partition=msg.partition(),
                offset=msg.offset(),
                partition_key=partition_key,
                **labels,
            )
            metrics.increment(
                "kafka_messages_produced_total",
                labels=labels,
            )

    def _produce_with_retry(
        self,
        topic: str,
        value: dict[str, Any],
        key: str,
        serializer: AvroSerializer,
        labels: dict[str, str],
    ):
        """Produce message with exponential backoff retry.

        Args:
            topic: Kafka topic name
            value: Message value (dict matching Avro schema)
            key: Partition key (symbol)
            serializer: Avro serializer for value
            labels: Metric labels (exchange, asset_class, data_type)

        Raises:
            Exception: If all retry attempts fail
        """
        retry_count = 0
        delay = self.initial_retry_delay

        while retry_count <= self.max_retries:
            try:
                # Serialize value to Avro bytes
                serialization_context = SerializationContext(topic, MessageField.VALUE)
                serialized_value = serializer(value, serialization_context)

                # Produce to Kafka (partition by key = symbol)
                self.producer.produce(
                    topic=topic,
                    value=serialized_value,
                    key=key.encode("utf-8"),  # Partition key (Decision #009)
                    on_delivery=lambda err, msg: self._delivery_callback(
                        err, msg, {"topic": topic, "partition_key": key, "labels": labels},
                    ),
                )

                # Poll to trigger callbacks
                self.producer.poll(0)

                # Success - return
                if retry_count > 0:
                    logger.info(
                        "Message produced after retry",
                        key=key,
                        retry_count=retry_count,
                        **labels,  # Includes topic
                    )
                return

            except BufferError:
                # Producer queue full - poll and retry
                logger.warning(
                    "Producer queue full, polling...",
                    key=key,
                    retry_count=retry_count,
                    **labels,  # Includes topic
                )
                self.producer.poll(1.0)  # Poll for 1 second
                retry_count += 1
                self._total_retries += 1

                if retry_count > self.max_retries:
                    logger.error(
                        "Max retries exceeded (BufferError)",
                        key=key,
                        max_retries=self.max_retries,
                        **labels,  # Includes topic
                    )
                    metrics.increment(
                        "kafka_produce_max_retries_exceeded_total",
                        labels={**labels, "error_type": "buffer_error"},
                    )
                    raise

                # Exponential backoff
                time.sleep(min(delay, self.max_retry_delay))
                delay *= self.retry_backoff_factor

            except Exception as e:
                # Other errors - log and retry with backoff
                logger.warning(
                    "Transient error producing message",
                    key=key,
                    error=str(e),
                    retry_count=retry_count,
                    **labels,  # Includes topic
                )
                retry_count += 1
                self._total_retries += 1

                if retry_count > self.max_retries:
                    logger.error(
                        "Max retries exceeded",
                        key=key,
                        error=str(e),
                        max_retries=self.max_retries,
                        **labels,  # Includes topic
                    )
                    metrics.increment(
                        "kafka_produce_max_retries_exceeded_total",
                        labels={**labels, "error_type": str(e)[:50]},
                    )
                    raise

                # Exponential backoff
                time.sleep(min(delay, self.max_retry_delay))
                delay *= self.retry_backoff_factor

    def produce_trade(
        self,
        asset_class: str,
        exchange: str,
        record: dict[str, Any],
    ):
        """Produce a trade record to Kafka.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')
            record: Trade record matching trade.avsc schema

        Raises:
            ValueError: If asset class or exchange is invalid
            Exception: If production fails after retries

        Example:
            >>> producer.produce_trade(
            ...     asset_class='equities',
            ...     exchange='asx',
            ...     record={
            ...         'symbol': 'BHP',
            ...         'exchange_timestamp': '2026-01-10T10:30:00Z',
            ...         'price': 45.50,
            ...         'quantity': 1000,
            ...         'side': 'buy',
            ...         'sequence_number': 12345,
            ...     }
            ... )
        """
        self._produce_message(
            asset_class=asset_class,
            data_type=DataType.TRADES,
            exchange=exchange,
            record=record,
        )

    def produce_quote(
        self,
        asset_class: str,
        exchange: str,
        record: dict[str, Any],
    ):
        """Produce a quote record to Kafka.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')
            record: Quote record matching quote.avsc schema

        Raises:
            ValueError: If asset class or exchange is invalid
            Exception: If production fails after retries
        """
        self._produce_message(
            asset_class=asset_class,
            data_type=DataType.QUOTES,
            exchange=exchange,
            record=record,
        )

    def produce_reference_data(
        self,
        asset_class: str,
        exchange: str,
        record: dict[str, Any],
    ):
        """Produce a reference data record to Kafka.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')
            record: Reference data record matching reference_data.avsc schema

        Raises:
            ValueError: If asset class or exchange is invalid
            Exception: If production fails after retries
        """
        self._produce_message(
            asset_class=asset_class,
            data_type=DataType.REFERENCE_DATA,
            exchange=exchange,
            record=record,
        )

    def _produce_message(
        self,
        asset_class: str,
        data_type: DataType,
        exchange: str,
        record: dict[str, Any],
    ):
        """Internal method to produce a message to Kafka.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            data_type: Data type (TRADES, QUOTES, REFERENCE_DATA)
            exchange: Exchange code (e.g., 'asx', 'binance')
            record: Record dict matching Avro schema

        Raises:
            ValueError: If required field 'symbol' is missing
            Exception: If production fails after retries
        """
        # Get topic configuration
        topic_config = self.topic_builder.get_topic_config(asset_class, data_type, exchange)
        topic = topic_config.topic_name
        schema_subject = topic_config.schema_subject
        partition_key_field = topic_config.partition_key_field

        # Extract partition key (symbol) from record
        partition_key = record.get(partition_key_field)
        if not partition_key:
            raise ValueError(
                f"Record missing partition key field '{partition_key_field}': {record}",
            )

        # Get Avro serializer
        serializer = self._get_serializer(schema_subject)

        # Metric labels
        labels = {
            "exchange": exchange,
            "asset_class": asset_class,
            "data_type": data_type.value,
            "topic": topic,  # Required by kafka_produce_duration_seconds
        }

        # Start metrics timer
        start_time = time.time()

        try:
            # Produce with retry
            self._produce_with_retry(
                topic=topic,
                value=record,
                key=partition_key,
                serializer=serializer,
                labels=labels,
            )

            # Record duration
            duration = time.time() - start_time
            metrics.histogram(
                "kafka_produce_duration_seconds",
                duration,
                labels={
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "topic": topic,
                },
            )

        except Exception as e:
            logger.error(
                "Failed to produce message",
                partition_key=partition_key,
                error=str(e),
                **labels,  # Includes topic, exchange, asset_class, data_type
            )
            raise

    def flush(self, timeout: float = 60.0):
        """Wait for all buffered messages to be sent.

        Args:
            timeout: Maximum time to wait in seconds (default: 60s)

        Returns:
            Number of messages still in queue after timeout

        Example:
            >>> producer.produce_trade(...)
            >>> remaining = producer.flush()
            >>> if remaining > 0:
            ...     print(f"Warning: {remaining} messages not sent")
        """
        logger.info("Flushing producer queue", timeout=timeout)
        start_time = time.time()

        remaining = self.producer.flush(timeout)

        duration = time.time() - start_time
        logger.info(
            "Producer flush complete",
            duration_seconds=duration,
            remaining_messages=remaining,
        )

        return remaining

    def close(self):
        """Close producer and cleanup resources.

        Flushes all pending messages before closing.

        Example:
            >>> producer.close()
        """
        logger.info(
            "Closing Kafka producer",
            total_produced=self._total_produced,
            total_errors=self._total_errors,
            total_retries=self._total_retries,
        )

        # Flush remaining messages
        self.flush()

        # No explicit close needed for confluent_kafka.Producer
        # It will be cleaned up by garbage collection

        logger.info("Kafka producer closed")

    def get_stats(self) -> dict[str, int]:
        """Get producer statistics.

        Returns:
            Dictionary with produced, errors, and retries counts

        Example:
            >>> stats = producer.get_stats()
            >>> print(f"Produced: {stats['produced']}, Errors: {stats['errors']}")
        """
        return {
            "produced": self._total_produced,
            "errors": self._total_errors,
            "retries": self._total_retries,
        }


# Convenience factory function
def create_producer(**kwargs) -> MarketDataProducer:
    """Create a MarketDataProducer with optional configuration overrides.

    Args:
        **kwargs: Optional arguments passed to MarketDataProducer constructor

    Returns:
        Configured MarketDataProducer instance

    Example:
        >>> producer = create_producer(max_retries=5)
        >>> producer.produce_trade(...)
        >>> producer.close()
    """
    return MarketDataProducer(**kwargs)
