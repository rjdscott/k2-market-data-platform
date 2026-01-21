"""K2 Spark Streaming Jobs.

This package contains Spark Structured Streaming jobs for the Medallion architecture:

Bronze Layer (Kafka → Bronze):
- bronze_binance_ingestion.py: Raw Binance trade ingestion
- bronze_kraken_ingestion.py: Raw Kraken trade ingestion

Silver Layer (Bronze → Silver):
- silver_binance_transformation.py: Binance trade validation & transformation
- silver_kraken_transformation.py: Kraken trade validation & transformation

Gold Layer (Silver → Gold):
- gold_aggregation.py: Multi-exchange unified analytics layer

Architecture Pattern:
- Per-exchange Bronze/Silver for isolation
- Unified Gold for analytics
- Independent checkpoints per job
- Raw bytes in Bronze, deserialized in Silver
"""

__version__ = "1.0.0"
