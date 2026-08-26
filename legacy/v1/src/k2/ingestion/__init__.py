"""K2 ingestion package.

Provides utilities for ingesting market data into the K2 platform:
- MarketDataProducer: Kafka producer with Schema Registry integration
- MarketDataConsumer: Kafka consumer with Iceberg writer integration
- SequenceTracker: Sequence gap detection and monitoring
- DeduplicationCache: Message deduplication for at-least-once semantics

Usage:
    from k2.ingestion import MarketDataProducer, MarketDataConsumer

    # Single message production
    producer = MarketDataProducer()
    producer.produce_trade(
        asset_class='crypto',
        exchange='binance',
        record={'symbol': 'BTCUSDT', 'price': 45000.50, ...}
    )
    producer.close()

    # Kafka to Iceberg consumption
    consumer = MarketDataConsumer(
        topics=['market.crypto.trades.binance'],
        consumer_group='k2-iceberg-writer-trades',
    )
    consumer.run()
"""

from k2.ingestion.consumer import ConsumerStats, MarketDataConsumer, create_consumer
from k2.ingestion.producer import MarketDataProducer, create_producer
from k2.ingestion.sequence_tracker import (
    DeduplicationCache,
    SequenceEvent,
    SequenceTracker,
)

__all__ = [
    "ConsumerStats",
    "DeduplicationCache",
    "MarketDataConsumer",
    "MarketDataProducer",
    "SequenceEvent",
    "SequenceTracker",
    "create_consumer",
    "create_producer",
]
