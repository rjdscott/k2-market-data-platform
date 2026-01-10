"""V1 API endpoints for K2 Market Data Platform.

This module implements the REST API endpoints for querying market data:
- /v1/trades - Query trade records with filters
- /v1/quotes - Query quote records with filters
- /v1/summary/{symbol}/{date} - Daily OHLCV market summary
- /v1/symbols - List available symbols
- /v1/stats - Database statistics
- /v1/snapshots - List Iceberg table snapshots

All endpoints:
- Require X-API-Key authentication
- Include correlation ID in response
- Support query parameter filtering
- Return standardized response models
"""

from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Path

from k2.api.models import (
    TradesResponse,
    QuotesResponse,
    SummaryResponse,
    SymbolsResponse,
    StatsResponse,
    SnapshotsResponse,
    Trade,
    Quote,
    MarketSummaryData,
    StatsData,
    SnapshotInfo,
    PaginationMeta,
)
from k2.api.deps import get_query_engine, get_replay_engine
from k2.api.middleware import verify_api_key, correlation_id_var
from k2.query.engine import QueryEngine
from k2.query.replay import ReplayEngine
from k2.common.logging import get_logger

logger = get_logger(__name__, component="api")

# Create router with prefix and tags for OpenAPI
router = APIRouter(
    prefix="/v1",
    tags=["Market Data"],
    dependencies=[Depends(verify_api_key)],
)


def _get_meta() -> dict:
    """Get standard metadata for response."""
    return {
        "correlation_id": correlation_id_var.get() or "unknown",
        "timestamp": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Trades Endpoint
# =============================================================================


@router.get(
    "/trades",
    response_model=TradesResponse,
    summary="Query trade records",
    description="""
    Query trade records from the market data lakehouse.

    Supports filtering by:
    - **symbol**: Filter by security symbol (e.g., BHP, RIO)
    - **exchange**: Filter by exchange (e.g., ASX)
    - **start_time**: Filter trades after this timestamp (ISO 8601)
    - **end_time**: Filter trades before this timestamp (ISO 8601)
    - **limit**: Maximum records to return (default: 100, max: 10000)

    Results are ordered by exchange_timestamp descending (most recent first).
    """,
    responses={
        200: {"description": "Trades retrieved successfully"},
        401: {"description": "Missing API key"},
        403: {"description": "Invalid API key"},
        500: {"description": "Query execution failed"},
    },
)
async def get_trades(
    symbol: Optional[str] = Query(
        default=None,
        description="Filter by symbol (e.g., BHP)",
        example="BHP",
    ),
    exchange: Optional[str] = Query(
        default=None,
        description="Filter by exchange (e.g., ASX)",
        example="ASX",
    ),
    start_time: Optional[datetime] = Query(
        default=None,
        description="Filter trades after this time (ISO 8601)",
        example="2024-01-15T09:00:00",
    ),
    end_time: Optional[datetime] = Query(
        default=None,
        description="Filter trades before this time (ISO 8601)",
        example="2024-01-15T16:00:00",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=10000,
        description="Maximum records to return",
    ),
    engine: QueryEngine = Depends(get_query_engine),
) -> TradesResponse:
    """Query trade records with optional filters."""
    try:
        logger.debug(
            "Querying trades",
            symbol=symbol,
            exchange=exchange,
            limit=limit,
        )

        # Execute query
        rows = engine.query_trades(
            symbol=symbol,
            exchange=exchange,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        # Convert to response models
        trades = [Trade(**row) for row in rows]

        return TradesResponse(
            success=True,
            data=trades,
            pagination=PaginationMeta(
                limit=limit,
                offset=0,
                total=None,  # Total not computed for performance
                has_more=len(trades) == limit,
            ),
            meta=_get_meta(),
        )

    except Exception as e:
        logger.error("Trades query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to query trades: {e!s}",
            },
        )


# =============================================================================
# Quotes Endpoint
# =============================================================================


@router.get(
    "/quotes",
    response_model=QuotesResponse,
    summary="Query quote records",
    description="""
    Query quote records (bid/ask) from the market data lakehouse.

    Supports the same filtering as /trades endpoint.
    Returns bid price, bid volume, ask price, ask volume.
    """,
)
async def get_quotes(
    symbol: Optional[str] = Query(default=None, description="Filter by symbol"),
    exchange: Optional[str] = Query(default=None, description="Filter by exchange"),
    start_time: Optional[datetime] = Query(default=None, description="Filter after time"),
    end_time: Optional[datetime] = Query(default=None, description="Filter before time"),
    limit: int = Query(default=100, ge=1, le=10000, description="Max records"),
    engine: QueryEngine = Depends(get_query_engine),
) -> QuotesResponse:
    """Query quote records with optional filters."""
    try:
        rows = engine.query_quotes(
            symbol=symbol,
            exchange=exchange,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        quotes = [Quote(**row) for row in rows]

        return QuotesResponse(
            success=True,
            data=quotes,
            pagination=PaginationMeta(
                limit=limit,
                offset=0,
                has_more=len(quotes) == limit,
            ),
            meta=_get_meta(),
        )

    except Exception as e:
        logger.error("Quotes query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to query quotes: {e!s}",
            },
        )


# =============================================================================
# Market Summary Endpoint
# =============================================================================


@router.get(
    "/summary/{symbol}/{query_date}",
    response_model=SummaryResponse,
    summary="Get daily market summary (OHLCV)",
    description="""
    Get OHLCV (Open, High, Low, Close, Volume) summary for a symbol on a specific date.

    Returns:
    - **open_price**: First trade price of the day
    - **high_price**: Highest trade price
    - **low_price**: Lowest trade price
    - **close_price**: Last trade price
    - **volume**: Total volume traded
    - **trade_count**: Number of trades
    - **vwap**: Volume-weighted average price
    """,
    responses={
        200: {"description": "Summary retrieved successfully"},
        404: {"description": "No data found for symbol/date"},
    },
)
async def get_market_summary(
    symbol: str = Path(
        description="Security symbol (e.g., BHP)",
        example="BHP",
    ),
    query_date: date = Path(
        description="Trading date (YYYY-MM-DD)",
        example="2024-01-15",
    ),
    exchange: Optional[str] = Query(
        default=None,
        description="Optional exchange filter",
    ),
    engine: QueryEngine = Depends(get_query_engine),
) -> SummaryResponse:
    """Get OHLCV market summary for a symbol on a date."""
    try:
        summary = engine.get_market_summary(
            symbol=symbol.upper(),
            query_date=query_date,
            exchange=exchange,
        )

        if summary is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "NOT_FOUND",
                    "message": f"No trades found for {symbol} on {query_date}",
                },
            )

        return SummaryResponse(
            success=True,
            data=MarketSummaryData(
                symbol=summary.symbol,
                date=summary.date,
                open_price=summary.open_price,
                high_price=summary.high_price,
                low_price=summary.low_price,
                close_price=summary.close_price,
                volume=summary.volume,
                trade_count=summary.trade_count,
                vwap=summary.vwap,
            ),
            meta=_get_meta(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Summary query failed", symbol=symbol, date=str(query_date), error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to get market summary: {e!s}",
            },
        )


# =============================================================================
# Symbols Endpoint
# =============================================================================


@router.get(
    "/symbols",
    response_model=SymbolsResponse,
    summary="List available symbols",
    description="Get a list of all symbols available in the trades table.",
)
async def get_symbols(
    exchange: Optional[str] = Query(default=None, description="Filter by exchange"),
    engine: QueryEngine = Depends(get_query_engine),
) -> SymbolsResponse:
    """List all available symbols."""
    try:
        symbols = engine.get_symbols(exchange=exchange)

        return SymbolsResponse(
            success=True,
            data=symbols,
            meta=_get_meta(),
        )

    except Exception as e:
        logger.error("Symbols query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to get symbols: {e!s}",
            },
        )


# =============================================================================
# Stats Endpoint
# =============================================================================


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get database statistics",
    description="""
    Get statistics about the market data database including:
    - Connection status
    - Row counts for trades and quotes tables
    - Storage configuration
    """,
)
async def get_stats(
    engine: QueryEngine = Depends(get_query_engine),
) -> StatsResponse:
    """Get database statistics."""
    try:
        stats = engine.get_stats()

        return StatsResponse(
            success=True,
            data=StatsData(
                s3_endpoint=stats.get("s3_endpoint", "unknown"),
                warehouse_path=stats.get("warehouse_path", "unknown"),
                connection_active=stats.get("connection_active", False),
                trades_count=stats.get("trades_count"),
                quotes_count=stats.get("quotes_count"),
                error=stats.get("error"),
            ),
            meta=_get_meta(),
        )

    except Exception as e:
        logger.error("Stats query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to get stats: {e!s}",
            },
        )


# =============================================================================
# Snapshots Endpoint
# =============================================================================


@router.get(
    "/snapshots",
    response_model=SnapshotsResponse,
    summary="List Iceberg table snapshots",
    description="""
    List snapshots for an Iceberg table. Snapshots enable time-travel queries.

    Each snapshot represents a point-in-time view of the table.
    """,
)
async def get_snapshots(
    table: str = Query(
        default="trades",
        description="Table name (trades or quotes)",
        example="trades",
    ),
    limit: int = Query(default=10, ge=1, le=100, description="Max snapshots to return"),
    replay_engine: ReplayEngine = Depends(get_replay_engine),
) -> SnapshotsResponse:
    """List Iceberg table snapshots."""
    try:
        # Validate table name
        if table not in ("trades", "quotes"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INVALID_TABLE",
                    "message": "Table must be 'trades' or 'quotes'",
                },
            )

        full_table_name = f"market_data.{table}"
        snapshots = replay_engine.list_snapshots(table_name=full_table_name, limit=limit)

        snapshot_list = [
            SnapshotInfo(
                snapshot_id=s.snapshot_id,
                timestamp=s.timestamp,
                manifest_list=s.manifest_list,
                summary=s.summary,
            )
            for s in snapshots
        ]

        return SnapshotsResponse(
            success=True,
            data=snapshot_list,
            table_name=full_table_name,
            meta=_get_meta(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Snapshots query failed", table=table, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "QUERY_FAILED",
                "message": f"Failed to get snapshots: {e!s}",
            },
        )
