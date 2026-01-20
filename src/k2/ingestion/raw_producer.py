#!/usr/bin/env python3
"""Raw data producers for streaming exchange-native data to Kafka.

This module provides producers that send raw exchange data to Kafka without
any schema transformation. This implements the industry best practice of
storing raw data in Bronze layer before applying transformations.

Architecture:
- Binance WebSocket → RawBinanceProducer → Kafka (raw JSON)
- Kraken WebSocket → RawKrakenProducer → Kafka (raw JSON)
- Bronze: Store exchange-native schemas
- Silver: Apply V2 transformation (handled by Spark)

Benefits:
- True Bronze layer (preserves original source data)
- Schema evolution flexibility
- Reprocessing capability
- Complete audit trail
"""

import json
import time
from typing import Any

import structlog
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

from k2.common.config import config
from k2.common.metrics import create_component_metrics

logger = structlog.get_logger(__name__)
metrics = create_component_metrics("raw_producer")


class RawBinanceProducer:
    """Producer for raw Binance trade data.

    Sends Binance WebSocket messages to Kafka in their native format,
    adding only an ingestion timestamp.
    """

    def __init__(
        self,
        kafka_bootstrap_servers: str = None,
        schema_registry_url: str = None,
        topic: str = "market.crypto.trades.binance.raw",
    ):
        """Initialize raw Binance producer.

        Args:
            kafka_bootstrap_servers: Kafka brokers
            schema_registry_url: Schema Registry URL
            topic: Kafka topic for raw Binance data
        """
        self.kafka_bootstrap_servers = kafka_bootstrap_servers or config.kafka.bootstrap_servers
        self.schema_registry_url = schema_registry_url or config.kafka.schema_registry_url
        self.topic = topic

        # Initialize Schema Registry client
        self.schema_registry_client = SchemaRegistryClient({"url": self.schema_registry_url})

        # Load raw Binance schema
        from pathlib import Path
        schema_path = Path(__file__).parent.parent / "schemas" / "binance_raw_trade.avsc"
        with open(schema_path) as f:
            schema_str = f.read()

        # Create Avro serializer
        self.avro_serializer = AvroSerializer(
            self.schema_registry_client,
            schema_str,
            lambda obj, ctx: obj,  # Pass dict directly
        )

        # Create Kafka producer
        self.producer = Producer({
            "bootstrap.servers": self.kafka_bootstrap_servers,
            "compression.type": "lz4",
            "linger.ms": 10,
            "batch.size": 32768,
            "acks": "1",
        })

        logger.info(
            "raw_binance_producer_initialized",
            topic=self.topic,
            bootstrap_servers=self.kafka_bootstrap_servers,
        )

    def produce(self, raw_message: dict[str, Any]) -> None:
        """Produce raw Binance message to Kafka with field name mapping.

        Args:
            raw_message: Raw Binance WebSocket message (dict with e, E, s, t, p, q, T, m, M fields)
        """
        # Map Binance single-char fields to descriptive names
        mapped_message = {
            "event_type": raw_message["e"],
            "event_time_ms": raw_message["E"],
            "symbol": raw_message["s"],
            "trade_id": raw_message["t"],
            "price": raw_message["p"],
            "quantity": raw_message["q"],
            "trade_time_ms": raw_message["T"],
            "is_buyer_maker": raw_message["m"],
            "is_best_match": raw_message.get("M"),  # Optional field
            "ingestion_timestamp": int(time.time() * 1_000_000),
        }

        try:
            # Serialize with Avro
            serialized_value = self.avro_serializer(
                mapped_message,
                SerializationContext(self.topic, MessageField.VALUE)
            )

            # Produce to Kafka
            self.producer.produce(
                topic=self.topic,
                key=mapped_message["symbol"].encode("utf-8"),  # Symbol as key
                value=serialized_value,
                on_delivery=self._delivery_report,
            )

            # Poll for callbacks
            self.producer.poll(0)

            metrics.increment(
                "kafka_messages_produced_total",
                labels={
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "topic": self.topic,
                    "data_type": "raw",
                },
            )

        except Exception as e:
            logger.error(
                "raw_binance_produce_failed",
                error=str(e),
                symbol=raw_message.get("s"),
            )
            metrics.increment(
                "kafka_produce_errors_total",
                labels={
                    "exchange": "BINANCE",
                    "asset_class": "crypto",
                    "topic": self.topic,
                    "error_type": type(e).__name__,
                },
            )
            raise

    def _delivery_report(self, err, msg):
        """Kafka delivery callback."""
        if err:
            logger.error(
                "raw_binance_delivery_failed",
                error=str(err),
                topic=msg.topic(),
            )
        else:
            logger.info(
                "raw_binance_message_delivered",
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
            )

    def flush(self, timeout: float = 10.0):
        """Flush producer."""
        self.producer.flush(timeout)

    def close(self):
        """Close producer."""
        self.producer.flush()


class RawKrakenProducer:
    """Producer for raw Kraken trade data.

    Sends Kraken WebSocket messages to Kafka in their native format,
    flattening the array structure and adding an ingestion timestamp.
    """

    def __init__(
        self,
        kafka_bootstrap_servers: str = None,
        schema_registry_url: str = None,
        topic: str = "market.crypto.trades.kraken.raw",
    ):
        """Initialize raw Kraken producer.

        Args:
            kafka_bootstrap_servers: Kafka brokers
            schema_registry_url: Schema Registry URL
            topic: Kafka topic for raw Kraken data
        """
        self.kafka_bootstrap_servers = kafka_bootstrap_servers or config.kafka.bootstrap_servers
        self.schema_registry_url = schema_registry_url or config.kafka.schema_registry_url
        self.topic = topic

        # Initialize Schema Registry client
        self.schema_registry_client = SchemaRegistryClient({"url": self.schema_registry_url})

        # Load raw Kraken schema
        from pathlib import Path
        schema_path = Path(__file__).parent.parent / "schemas" / "kraken_raw_trade.avsc"
        with open(schema_path) as f:
            schema_str = f.read()

        # Create Avro serializer
        self.avro_serializer = AvroSerializer(
            self.schema_registry_client,
            schema_str,
            lambda obj, ctx: obj,  # Pass dict directly
        )

        # Create Kafka producer
        self.producer = Producer({
            "bootstrap.servers": self.kafka_bootstrap_servers,
            "compression.type": "lz4",
            "linger.ms": 10,
            "batch.size": 32768,
            "acks": "1",
        })

        logger.info(
            "raw_kraken_producer_initialized",
            topic=self.topic,
            bootstrap_servers=self.kafka_bootstrap_servers,
        )

    def produce(self, raw_message: list[Any]) -> None:
        """Produce raw Kraken message to Kafka.

        Kraken format: [channelID, [[price, volume, timestamp, side, orderType, misc]], "trade", "pair"]

        Args:
            raw_message: Raw Kraken WebSocket message (array)
        """
        # Extract components from Kraken array format
        channel_id = raw_message[0]
        trades_array = raw_message[1]
        pair = raw_message[3]

        # Process first trade (typically only one trade per message)
        trade = trades_array[0]

        # Flatten to schema format
        flattened = {
            "channel_id": channel_id,
            "price": trade[0],
            "volume": trade[1],
            "timestamp": trade[2],
            "side": trade[3],
            "order_type": trade[4],
            "misc": trade[5] if len(trade) > 5 else "",
            "pair": pair,
            "ingestion_timestamp": int(time.time() * 1_000_000),
        }

        try:
            # Serialize with Avro
            serialized_value = self.avro_serializer(
                flattened,
                SerializationContext(self.topic, MessageField.VALUE)
            )

            # Produce to Kafka
            self.producer.produce(
                topic=self.topic,
                key=pair.encode("utf-8"),  # Pair as key
                value=serialized_value,
                on_delivery=self._delivery_report,
            )

            # Poll for callbacks
            self.producer.poll(0)

            metrics.increment(
                "kafka_messages_produced_total",
                labels={
                    "exchange": "KRAKEN",
                    "asset_class": "crypto",
                    "topic": self.topic,
                    "data_type": "raw",
                },
            )

        except Exception as e:
            logger.error(
                "raw_kraken_produce_failed",
                error=str(e),
                pair=pair,
            )
            metrics.increment(
                "kafka_produce_errors_total",
                labels={
                    "exchange": "KRAKEN",
                    "asset_class": "crypto",
                    "topic": self.topic,
                    "error_type": type(e).__name__,
                },
            )
            raise

    def _delivery_report(self, err, msg):
        """Kafka delivery callback."""
        if err:
            logger.error(
                "raw_kraken_delivery_failed",
                error=str(err),
                topic=msg.topic(),
            )
        else:
            logger.info(
                "raw_kraken_message_delivered",
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
            )

    def flush(self, timeout: float = 10.0):
        """Flush producer."""
        self.producer.flush(timeout)

    def close(self):
        """Close producer."""
        self.producer.flush()
