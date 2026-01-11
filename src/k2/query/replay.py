"""Replay Engine for time-travel queries and historical data replay.

This module provides time-travel capabilities using Iceberg snapshots:
- Query data as of a specific snapshot (point-in-time)
- Replay historical data in chronological order (backtesting)
- List and manage table snapshots
- Stream data in memory-efficient batches

Use cases:
- Backtesting trading strategies with historical data
- Compliance auditing (query as-of specific date)
- Debugging by replaying event sequences
- Disaster recovery with snapshot rollback

Usage:
    from k2.query.replay import ReplayEngine

    engine = ReplayEngine()

    # List available snapshots
    snapshots = engine.list_snapshots()

    # Query at specific snapshot
    trades = engine.query_at_snapshot(
        snapshot_id=12345,
        symbol="BHP",
        limit=1000
    )

    # Replay historical data
    for batch in engine.cold_start_replay(
        symbol="BHP",
        start_time=datetime(2024, 1, 1),
        end_time=datetime(2024, 1, 31),
        batch_size=1000
    ):
        process_batch(batch)
"""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics
from k2.query.engine import QueryEngine

logger = get_logger(__name__, component="replay")
metrics = create_component_metrics("replay")


@dataclass
class SnapshotInfo:
    """Information about an Iceberg table snapshot."""

    snapshot_id: int
    timestamp_ms: int
    parent_id: int | None
    manifest_list: str
    summary: dict[str, str]

    @property
    def timestamp(self) -> datetime:
        """Convert timestamp_ms to datetime."""
        return datetime.fromtimestamp(self.timestamp_ms / 1000)

    @property
    def added_records(self) -> int:
        """Number of records added in this snapshot."""
        return int(self.summary.get("added-records", "0"))

    @property
    def total_records(self) -> int:
        """Total records after this snapshot."""
        return int(self.summary.get("total-records", "0"))


class ReplayEngine:
    """Engine for time-travel queries and historical data replay.

    Built on top of QueryEngine, this class adds:
    - Snapshot management and enumeration
    - Point-in-time queries via Iceberg time-travel
    - Streaming historical data replay for backtesting

    Uses PyIceberg for snapshot metadata and DuckDB for queries.

    Usage:
        engine = ReplayEngine()

        # List snapshots
        snapshots = engine.list_snapshots()
        print(f"Latest: {snapshots[0].timestamp}")

        # Query at snapshot
        trades = engine.query_at_snapshot(
            snapshot_id=snapshots[0].snapshot_id,
            symbol="BHP"
        )

        # Replay data in batches
        for batch in engine.cold_start_replay("BHP", start, end):
            analyze(batch)
    """

    def __init__(
        self,
        catalog_uri: str | None = None,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        warehouse_path: str | None = None,
    ):
        """Initialize replay engine.

        Args:
            catalog_uri: Iceberg REST catalog URI (defaults to config)
            s3_endpoint: S3/MinIO endpoint (defaults to config)
            s3_access_key: S3 access key (defaults to config)
            s3_secret_key: S3 secret key (defaults to config)
            warehouse_path: Iceberg warehouse path (defaults to config)
        """
        self.catalog_uri = catalog_uri or config.iceberg.catalog_uri
        self.s3_endpoint = s3_endpoint or config.iceberg.s3_endpoint
        self.s3_access_key = s3_access_key or config.iceberg.s3_access_key
        self.s3_secret_key = s3_secret_key or config.iceberg.s3_secret_key
        self.warehouse_path = warehouse_path or config.iceberg.warehouse

        # Initialize QueryEngine for DuckDB queries
        self._query_engine = QueryEngine(
            s3_endpoint=self.s3_endpoint,
            s3_access_key=self.s3_access_key,
            s3_secret_key=self.s3_secret_key,
            warehouse_path=self.warehouse_path,
        )

        # Initialize PyIceberg catalog for snapshot management
        self._catalog = None
        self._init_catalog()

        logger.info(
            "Replay engine initialized",
            catalog_uri=self.catalog_uri,
            warehouse_path=self.warehouse_path,
        )

    def _init_catalog(self) -> None:
        """Initialize PyIceberg catalog for snapshot access."""
        try:
            self._catalog = load_catalog(
                "k2_replay_catalog",
                **{
                    "uri": self.catalog_uri,
                    "s3.endpoint": self.s3_endpoint,
                    "s3.access-key-id": self.s3_access_key,
                    "s3.secret-access-key": self.s3_secret_key,
                    "s3.path-style-access": "true",
                },
            )
            logger.debug("PyIceberg catalog initialized")
        except Exception as e:
            logger.error("Failed to initialize catalog", error=str(e))
            raise

    def _load_table(self, table_name: str) -> Table:
        """Load an Iceberg table.

        Args:
            table_name: Table name (e.g., "trades" or "market_data.trades")

        Returns:
            PyIceberg Table object
        """
        if "." not in table_name:
            table_name = f"market_data.{table_name}"

        try:
            return self._catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            raise

    def list_snapshots(
        self,
        table_name: str = "market_data.trades",
        limit: int | None = None,
    ) -> list[SnapshotInfo]:
        """List snapshots for a table, newest first.

        Args:
            table_name: Iceberg table name
            limit: Maximum snapshots to return (None = all)

        Returns:
            List of SnapshotInfo objects, newest first
        """
        table = self._load_table(table_name)

        snapshots = []
        for snapshot in table.metadata.snapshots:
            # Handle summary - it may be a Summary object or dict
            summary_dict = {}
            if snapshot.summary:
                try:
                    # Try to iterate as dict-like object
                    for key in [
                        "operation",
                        "added-records",
                        "total-records",
                        "added-data-files",
                        "deleted-records",
                    ]:
                        try:
                            val = snapshot.summary.get(key)
                            if val is not None:
                                summary_dict[key] = str(val)
                        except (KeyError, TypeError):
                            pass
                except Exception:
                    pass

            info = SnapshotInfo(
                snapshot_id=snapshot.snapshot_id,
                timestamp_ms=snapshot.timestamp_ms,
                parent_id=snapshot.parent_snapshot_id,
                manifest_list=snapshot.manifest_list,
                summary=summary_dict,
            )
            snapshots.append(info)

        # Sort by timestamp descending (newest first)
        snapshots.sort(key=lambda s: s.timestamp_ms, reverse=True)

        if limit:
            snapshots = snapshots[:limit]

        logger.debug(
            "Listed snapshots",
            table=table_name,
            count=len(snapshots),
        )

        return snapshots

    def get_snapshot(
        self,
        snapshot_id: int,
        table_name: str = "market_data.trades",
    ) -> SnapshotInfo | None:
        """Get information about a specific snapshot.

        Args:
            snapshot_id: Snapshot ID
            table_name: Iceberg table name

        Returns:
            SnapshotInfo or None if not found
        """
        snapshots = self.list_snapshots(table_name)
        for snapshot in snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        return None

    def get_current_snapshot(
        self,
        table_name: str = "market_data.trades",
    ) -> SnapshotInfo | None:
        """Get the current (latest) snapshot for a table.

        Args:
            table_name: Iceberg table name

        Returns:
            SnapshotInfo for current snapshot
        """
        table = self._load_table(table_name)

        if table.metadata.current_snapshot() is None:
            return None

        snapshot = table.metadata.current_snapshot()

        # Handle summary - it may be a Summary object or dict
        summary_dict = {}
        if snapshot.summary:
            try:
                for key in [
                    "operation",
                    "added-records",
                    "total-records",
                    "added-data-files",
                    "deleted-records",
                ]:
                    try:
                        val = snapshot.summary.get(key)
                        if val is not None:
                            summary_dict[key] = str(val)
                    except (KeyError, TypeError):
                        pass
            except Exception:
                pass

        return SnapshotInfo(
            snapshot_id=snapshot.snapshot_id,
            timestamp_ms=snapshot.timestamp_ms,
            parent_id=snapshot.parent_snapshot_id,
            manifest_list=snapshot.manifest_list,
            summary=summary_dict,
        )

    def query_at_snapshot(
        self,
        snapshot_id: int,
        symbol: str | None = None,
        exchange: str | None = None,
        limit: int = 1000,
        table_name: str = "market_data.trades",
    ) -> list[dict[str, Any]]:
        """Query trades as of a specific snapshot (time-travel).

        Args:
            snapshot_id: Iceberg snapshot ID to query
            symbol: Filter by symbol
            exchange: Filter by exchange
            limit: Maximum records to return
            table_name: Iceberg table name

        Returns:
            List of trade records as of the snapshot

        Note:
            DuckDB's iceberg_scan with version parameter queries at
            that specific snapshot, not the current table state.
        """
        table_path = self._query_engine._get_table_path(table_name)

        # Build WHERE clause
        conditions = []
        if symbol:
            conditions.append(f"symbol = '{symbol}'")
        if exchange:
            conditions.append(f"exchange = '{exchange}'")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Query at specific snapshot version
        query = f"""
            SELECT
                symbol,
                company_id,
                exchange,
                exchange_timestamp,
                price,
                volume,
                qualifiers,
                venue,
                buyer_id,
                ingestion_timestamp,
                sequence_number
            FROM iceberg_scan('{table_path}', version = '{snapshot_id}')
            {where_clause}
            ORDER BY exchange_timestamp
            LIMIT {limit}
        """

        try:
            result = self._query_engine.connection.execute(query).fetchdf()
            rows = result.to_dict(orient="records")

            logger.debug(
                "Snapshot query completed",
                snapshot_id=snapshot_id,
                symbol=symbol,
                row_count=len(rows),
            )

            metrics.increment(
                "query_executions_total", labels={"query_type": "snapshot", "status": "success"},
            )

            return rows

        except Exception as e:
            logger.error(
                "Snapshot query failed",
                snapshot_id=snapshot_id,
                error=str(e),
            )
            metrics.increment(
                "query_executions_total", labels={"query_type": "snapshot", "status": "error"},
            )
            raise

    def cold_start_replay(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        batch_size: int = 1000,
        table_name: str = "market_data.trades",
    ) -> Generator[list[dict[str, Any]]]:
        """Replay historical data in chronological order.

        Streams data in batches for memory efficiency. Use for:
        - Backtesting trading strategies
        - Rebuilding derived state
        - Data migration

        Args:
            symbol: Filter by symbol
            exchange: Filter by exchange
            start_time: Start of replay period
            end_time: End of replay period
            batch_size: Records per batch (default: 1000)
            table_name: Iceberg table name

        Yields:
            Batches of trade records in chronological order

        Example:
            for batch in engine.cold_start_replay(
                symbol="BHP",
                start_time=datetime(2024, 1, 1),
                batch_size=500
            ):
                for trade in batch:
                    strategy.process(trade)
        """
        table_path = self._query_engine._get_table_path(table_name)

        # Build WHERE clause
        conditions = []
        if symbol:
            conditions.append(f"symbol = '{symbol}'")
        if exchange:
            conditions.append(f"exchange = '{exchange}'")
        if start_time:
            conditions.append(f"exchange_timestamp >= '{start_time.isoformat()}'")
        if end_time:
            conditions.append(f"exchange_timestamp <= '{end_time.isoformat()}'")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # First, get total count for progress tracking
        count_query = f"""
            SELECT COUNT(*)
            FROM iceberg_scan('{table_path}')
            {where_clause}
        """
        total_count = self._query_engine.connection.execute(count_query).fetchone()[0]

        logger.info(
            "Starting cold start replay",
            symbol=symbol,
            start_time=str(start_time) if start_time else None,
            end_time=str(end_time) if end_time else None,
            total_records=total_count,
            batch_size=batch_size,
        )

        if total_count == 0:
            logger.info("No records to replay")
            return

        # Stream data in batches with OFFSET/LIMIT
        offset = 0
        batches_yielded = 0
        records_yielded = 0

        while offset < total_count:
            query = f"""
                SELECT
                    symbol,
                    company_id,
                    exchange,
                    exchange_timestamp,
                    price,
                    volume,
                    qualifiers,
                    venue,
                    buyer_id,
                    ingestion_timestamp,
                    sequence_number
                FROM iceberg_scan('{table_path}')
                {where_clause}
                ORDER BY exchange_timestamp ASC, sequence_number ASC
                LIMIT {batch_size}
                OFFSET {offset}
            """

            try:
                result = self._query_engine.connection.execute(query).fetchdf()
                batch = result.to_dict(orient="records")

                if not batch:
                    break

                batches_yielded += 1
                records_yielded += len(batch)
                offset += batch_size

                logger.debug(
                    "Replay batch",
                    batch_number=batches_yielded,
                    records=len(batch),
                    progress=f"{records_yielded}/{total_count}",
                )

                yield batch

            except Exception as e:
                logger.error(
                    "Replay batch failed",
                    batch_number=batches_yielded + 1,
                    offset=offset,
                    error=str(e),
                )
                raise

        logger.info(
            "Cold start replay completed",
            symbol=symbol,
            batches=batches_yielded,
            total_records=records_yielded,
        )

        metrics.increment(
            "query_executions_total", labels={"query_type": "replay", "status": "success"},
        )

    def rewind_to_timestamp(
        self,
        target_time: datetime,
        table_name: str = "market_data.trades",
    ) -> SnapshotInfo | None:
        """Find the snapshot closest to (but not after) a target timestamp.

        Useful for finding the correct snapshot for point-in-time queries.

        Args:
            target_time: Target timestamp
            table_name: Iceberg table name

        Returns:
            SnapshotInfo for the appropriate snapshot, or None
        """
        snapshots = self.list_snapshots(table_name)
        target_ms = int(target_time.timestamp() * 1000)

        # Find latest snapshot before or at target time
        for snapshot in snapshots:
            if snapshot.timestamp_ms <= target_ms:
                logger.debug(
                    "Found snapshot for timestamp",
                    target_time=str(target_time),
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_time=str(snapshot.timestamp),
                )
                return snapshot

        # No snapshot before target time
        logger.debug(
            "No snapshot found before timestamp",
            target_time=str(target_time),
        )
        return None

    def get_replay_stats(
        self,
        symbol: str | None = None,
        table_name: str = "market_data.trades",
    ) -> dict[str, Any]:
        """Get statistics for replay planning.

        Args:
            symbol: Optional symbol filter
            table_name: Iceberg table name

        Returns:
            Dictionary with record counts, date range, etc.
        """
        table_path = self._query_engine._get_table_path(table_name)

        where_clause = ""
        if symbol:
            where_clause = f"WHERE symbol = '{symbol}'"

        query = f"""
            SELECT
                COUNT(*) as total_records,
                MIN(exchange_timestamp) as min_timestamp,
                MAX(exchange_timestamp) as max_timestamp,
                COUNT(DISTINCT symbol) as unique_symbols,
                COUNT(DISTINCT exchange) as unique_exchanges
            FROM iceberg_scan('{table_path}')
            {where_clause}
        """

        result = self._query_engine.connection.execute(query).fetchone()

        return {
            "total_records": result[0],
            "min_timestamp": result[1],
            "max_timestamp": result[2],
            "unique_symbols": result[3],
            "unique_exchanges": result[4],
            "snapshot_count": len(self.list_snapshots(table_name)),
        }

    def close(self) -> None:
        """Close the replay engine and underlying connections."""
        if self._query_engine:
            self._query_engine.close()
        logger.debug("Replay engine closed")

    def __enter__(self) -> "ReplayEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
