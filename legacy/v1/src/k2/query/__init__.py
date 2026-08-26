"""K2 Query Layer - DuckDB query engine for Iceberg market data.

This module provides analytical query capabilities for market data stored
in Apache Iceberg tables. Built on DuckDB for sub-second query performance.

Components:
- QueryEngine: Main query interface with trade/quote queries and OHLCV summaries
- ReplayEngine: Time-travel and historical data replay
- CLI: Command-line query interface (k2-query command)

Usage:
    from k2.query import QueryEngine, ReplayEngine, MarketSummary

    # Create query engine
    engine = QueryEngine()

    # Query trades
    trades = engine.query_trades(symbol="BHP", limit=100)

    # Get market summary
    summary = engine.get_market_summary("BHP", date(2024, 1, 15))

    # Replay historical data
    replay = ReplayEngine()
    for batch in replay.cold_start_replay(symbol="BHP", batch_size=1000):
        process(batch)

    # Always close when done
    engine.close()
    replay.close()
"""

from k2.query.engine import MarketSummary, QueryEngine, QueryType
from k2.query.replay import ReplayEngine, SnapshotInfo

__all__ = [
    "MarketSummary",
    "QueryEngine",
    "QueryType",
    "ReplayEngine",
    "SnapshotInfo",
]
