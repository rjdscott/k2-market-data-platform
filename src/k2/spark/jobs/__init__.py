"""Spark streaming jobs for Medallion architecture.

Jobs:
    - Bronze ingestion: Kafka → Bronze (raw bytes)
    - Silver transformation: Bronze → Silver (validated, per-exchange)
    - Gold aggregation: Silver → Gold (unified, analytics-ready)
"""

__all__ = []
