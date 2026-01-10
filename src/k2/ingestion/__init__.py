"""K2 ingestion package.

Provides utilities for ingesting market data into the K2 platform:
- MarketDataProducer: Kafka producer with Schema Registry integration
- MarketDataConsumer: Kafka consumer with Iceberg writer integration
- BatchLoader: CSV batch loader for bulk data ingestion
- SequenceTracker: Sequence gap detection and monitoring
- DeduplicationCache: Message deduplication for at-least-once semantics

Usage:
    from k2.ingestion import MarketDataProducer, MarketDataConsumer, BatchLoader

    # Single message production
    producer = MarketDataProducer()
    producer.produce_trade(
        asset_class='equities',
        exchange='asx',
        record={'symbol': 'BHP', 'price': 45.50, ...}
    )
    producer.close()

    # Kafka to Iceberg consumption
    consumer = MarketDataConsumer(
        topics=['market.equities.trades.asx'],
        consumer_group='k2-iceberg-writer-trades',
    )
    consumer.run()

    # Batch loading from CSV
    loader = BatchLoader('equities', 'asx', 'trades')
    stats = loader.load_csv(Path('data/trades.csv'))
    loader.close()
"""

from k2.ingestion.producer import MarketDataProducer, create_producer
from k2.ingestion.consumer import MarketDataConsumer, ConsumerStats, create_consumer
from k2.ingestion.batch_loader import BatchLoader, LoadStats, create_loader
from k2.ingestion.sequence_tracker import (
    SequenceTracker,
    SequenceEvent,
    DeduplicationCache,
)

__all__ = [
    "MarketDataProducer",
    "create_producer",
    "MarketDataConsumer",
    "ConsumerStats",
    "create_consumer",
    "BatchLoader",
    "LoadStats",
    "create_loader",
    "SequenceTracker",
    "SequenceEvent",
    "DeduplicationCache",
]
