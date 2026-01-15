"""HybridQueryEngine - Unified query interface merging Iceberg + Kafka.

The hybrid engine provides seamless queries spanning both committed historical
data (Iceberg) and recent uncommitted data (Kafka tail buffer). This is the
core value proposition of a lakehouse architecture.

Architecture:
- Query routing based on time windows
- Automatic source selection (Iceberg, Kafka, or both)
- Deduplication by message_id for overlapping data
- Consistent ordering by timestamp

Use Cases:
- "Give me last 15 minutes of BTCUSDT trades" → queries both Kafka + Iceberg
- "Give me yesterday's trades" → queries only Iceberg (all committed)
- "Give me last 30 seconds of trades" → queries only Kafka (uncommitted)

Performance:
- Iceberg query: ~200-500ms for time range
- Kafka tail query: <50ms (in-memory)
- Deduplication: O(n) where n = result count
- Total: <500ms p99 for 15-minute window

Usage:
    engine = HybridQueryEngine(
        iceberg_engine=QueryEngine(),
        kafka_tail=create_kafka_tail()
    )

    # Query last 15 minutes (automatic Kafka + Iceberg merge)
    trades = engine.query_trades(
        symbol="BTCUSDT",
        exchange="binance",
        start_time=datetime.now() - timedelta(minutes=15),
        end_time=datetime.now()
    )
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from k2.common.logging import get_logger
from k2.query.engine import QueryEngine
from k2.query.kafka_tail import KafkaTail

logger = get_logger(__name__, component="hybrid_query")


class HybridQueryEngine:
    """Unified query interface merging Iceberg (historical) + Kafka (real-time).

    Query Routing Logic:
    - If end_time > (now - commit_lag) → Query both Kafka + Iceberg
    - Otherwise → Query only Iceberg (all data committed)

    Commit Lag:
    Time between message arrival and Iceberg commit (typically 1-2 minutes).
    This accounts for:
    - Consumer processing time
    - Iceberg write batching
    - Catalog update latency

    Example Timeline:
        Now:        14:15:00
        Commit lag: 2 minutes
        Query:      14:00 to 14:15 (15-minute window)

        Iceberg query:  14:00 to 14:13 (committed)
        Kafka query:    14:13 to 14:15 (uncommitted)
        Merge & deduplicate by message_id

    Deduplication:
    Messages may appear in both Iceberg and Kafka during the overlap window.
    We deduplicate by message_id, preferring Iceberg data (committed source).
    """

    def __init__(
        self,
        iceberg_engine: QueryEngine,
        kafka_tail: KafkaTail,
        commit_lag_seconds: int = 120,  # 2 minutes default
    ):
        """Initialize hybrid query engine.

        Args:
            iceberg_engine: QueryEngine for Iceberg queries
            kafka_tail: KafkaTail for recent Kafka messages
            commit_lag_seconds: Time between message arrival and Iceberg commit
        """
        self.iceberg = iceberg_engine
        self.kafka_tail = kafka_tail
        self.commit_lag = timedelta(seconds=commit_lag_seconds)

        logger.info(
            "HybridQueryEngine initialized",
            commit_lag_seconds=commit_lag_seconds,
        )

    def query_trades(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query trades with automatic Kafka + Iceberg merging.

        The query is automatically routed to the appropriate source(s):
        - Recent data (< commit_lag from now): Kafka + Iceberg
        - Historical data (> commit_lag from now): Iceberg only

        Args:
            symbol: Symbol to query (e.g., "BTCUSDT")
            exchange: Exchange name (e.g., "binance")
            start_time: Start of time range (UTC)
            end_time: End of time range (UTC, defaults to now)
            limit: Maximum number of results

        Returns:
            List of trade records, sorted by timestamp, deduplicated
        """
        # Default end_time to now if not provided
        if end_time is None:
            end_time = datetime.now(UTC)

        # Ensure times are timezone-aware
        if start_time and start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        kafka_cutoff = now - self.commit_lag

        # Determine query strategy
        needs_kafka = end_time > kafka_cutoff
        needs_iceberg = start_time is None or start_time < kafka_cutoff

        logger.debug(
            "Query routing",
            symbol=symbol,
            exchange=exchange,
            start_time=start_time,
            end_time=end_time,
            needs_kafka=needs_kafka,
            needs_iceberg=needs_iceberg,
            kafka_cutoff=kafka_cutoff,
        )

        # Execute queries
        iceberg_results = []
        kafka_results = []

        if needs_iceberg:
            iceberg_results = self._query_iceberg(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=min(end_time, kafka_cutoff) if needs_kafka else end_time,
                limit=limit,
            )

        if needs_kafka:
            kafka_results = self._query_kafka(
                symbol=symbol,
                exchange=exchange,
                start_time=max(start_time, kafka_cutoff) if start_time else kafka_cutoff,
                end_time=end_time,
                limit=limit,
            )

        # Merge and deduplicate
        merged_results = self._merge_results(
            iceberg_results=iceberg_results,
            kafka_results=kafka_results,
            limit=limit,
        )

        # Record metrics
        # TODO: Re-enable once METRICS is properly imported
        # METRICS["k2_hybrid_queries_total"].labels(
        #     service="k2-platform",
        #     environment="dev",
        #     component="hybrid_query",
        #     source="both" if (needs_kafka and needs_iceberg) else ("kafka" if needs_kafka else "iceberg"),
        # ).inc()

        # METRICS["k2_hybrid_query_results"].labels(
        #     service="k2-platform",
        #     environment="dev",
        #     component="hybrid_query",
        #     symbol=symbol,
        # ).observe(len(merged_results))

        logger.info(
            "Hybrid query completed",
            symbol=symbol,
            exchange=exchange,
            iceberg_count=len(iceberg_results),
            kafka_count=len(kafka_results),
            merged_count=len(merged_results),
        )

        return merged_results

    def _query_iceberg(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime | None,
        end_time: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Query Iceberg for historical trades.

        Args:
            symbol: Symbol to query
            exchange: Exchange name
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum results

        Returns:
            List of trade records from Iceberg
        """
        try:
            results = self.iceberg.query_trades(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            # Tag source for debugging
            for record in results:
                record["_source"] = "iceberg"

            return results

        except Exception as e:
            logger.error(
                "Iceberg query failed",
                error=str(e),
                symbol=symbol,
                exchange=exchange,
            )
            # Return empty results on error (degraded mode)
            return []

    def _query_kafka(
        self,
        symbol: str,
        exchange: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Query Kafka tail buffer for recent trades.

        Args:
            symbol: Symbol to query
            exchange: Exchange name
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum results

        Returns:
            List of trade records from Kafka
        """
        try:
            results = self.kafka_tail.query(
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
            )

            # Tag source for debugging
            for record in results:
                record["_source"] = "kafka"

            # Apply limit
            return results[:limit]

        except Exception as e:
            logger.error(
                "Kafka tail query failed",
                error=str(e),
                symbol=symbol,
                exchange=exchange,
            )
            # Return empty results on error (degraded mode)
            return []

    def _merge_results(
        self,
        iceberg_results: list[dict[str, Any]],
        kafka_results: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Merge results from Iceberg and Kafka, deduplicating by message_id.

        Deduplication Strategy:
        - Prefer Iceberg data over Kafka (committed source of truth)
        - Use message_id as deduplication key
        - Handle records without message_id (include all)

        Args:
            iceberg_results: Results from Iceberg query
            kafka_results: Results from Kafka query
            limit: Maximum results to return

        Returns:
            Merged and deduplicated results, sorted by timestamp
        """
        # Combine all results
        all_results = iceberg_results + kafka_results

        # Deduplicate by message_id
        seen_ids = set()
        deduped_results = []

        for record in all_results:
            message_id = record.get("message_id")

            if message_id:
                if message_id not in seen_ids:
                    seen_ids.add(message_id)
                    deduped_results.append(record)
                # else: Skip duplicate (already seen)
            else:
                # No message_id, include anyway (shouldn't happen in practice)
                deduped_results.append(record)

        # Sort by timestamp
        deduped_results.sort(key=lambda r: r.get("timestamp", ""))

        # Apply limit
        final_results = deduped_results[:limit]

        # Remove _source tag before returning
        for record in final_results:
            record.pop("_source", None)

        return final_results

    def get_query_stats(self) -> dict[str, Any]:
        """Get query statistics from both sources.

        Returns:
            Statistics dict with Iceberg and Kafka tail stats
        """
        return {
            "iceberg": {
                "engine": "duckdb",
                "pool_active": (
                    self.iceberg.pool._active_count if hasattr(self.iceberg, "pool") else 0
                ),
            },
            "kafka_tail": self.kafka_tail.get_stats(),
            "commit_lag_seconds": int(self.commit_lag.total_seconds()),
        }


def create_hybrid_engine(
    commit_lag_seconds: int = 120,
) -> HybridQueryEngine:
    """Factory function to create HybridQueryEngine with default components.

    This creates and starts all required components:
    - QueryEngine for Iceberg queries
    - KafkaTail for recent Kafka messages
    - HybridQueryEngine to merge both

    Args:
        commit_lag_seconds: Time between message arrival and Iceberg commit

    Returns:
        Initialized HybridQueryEngine ready to use

    Usage:
        engine = create_hybrid_engine()
        trades = engine.query_trades(symbol="BTCUSDT", ...)
    """
    from k2.query.kafka_tail import create_kafka_tail

    # Create Iceberg query engine
    iceberg_engine = QueryEngine()

    # Create and start Kafka tail buffer
    kafka_tail = create_kafka_tail(buffer_minutes=5)

    # Create hybrid engine
    hybrid_engine = HybridQueryEngine(
        iceberg_engine=iceberg_engine,
        kafka_tail=kafka_tail,
        commit_lag_seconds=commit_lag_seconds,
    )

    logger.info("HybridQueryEngine created and ready")

    return hybrid_engine
