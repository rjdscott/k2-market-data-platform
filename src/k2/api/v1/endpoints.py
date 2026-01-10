"""V1 API endpoints for K2 Market Data Platform.

This module implements the REST API endpoints for querying market data:

GET Endpoints (Simple Queries):
- /v1/trades - Query trade records with filters
- /v1/quotes - Query quote records with filters
- /v1/summary/{symbol}/{date} - Daily OHLCV market summary
- /v1/symbols - List available symbols
- /v1/stats - Database statistics
- /v1/snapshots - List Iceberg table snapshots

POST Endpoints (Complex Queries):
- /v1/trades/query - Multi-symbol trades with field selection
- /v1/quotes/query - Multi-symbol quotes with field selection
- /v1/replay - Historical data replay with pagination
- /v1/snapshots/{id}/query - Point-in-time queries
- /v1/aggregations - Custom VWAP, TWAP, OHLCV aggregations

All endpoints:
- Require X-API-Key authentication
- Include correlation ID in response
- Support JSON, CSV, and Parquet output formats (POST)
- Return standardized response models
"""

from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response

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
    DataType,
    OutputFormat,
    # POST request models
    TradeQueryRequest,
    QuoteQueryRequest,
    ReplayRequest,
    ReplayResponse,
    SnapshotQueryRequest,
    SnapshotQueryResponse,
    AggregationRequest,
    AggregationResponse,
    AggregationBucket,
    AggregationMetric,
    AggregationInterval,
)
from k2.api.deps import get_query_engine, get_replay_engine
from k2.api.middleware import verify_api_key, correlation_id_var
from k2.api.formatters import format_response, encode_cursor, decode_cursor
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


# =============================================================================
# POST Endpoints (Complex Queries)
# =============================================================================


@router.post(
    "/trades/query",
    summary="Advanced trade query",
    description="""
    Advanced trade query with multi-symbol support, field selection, and multiple output formats.

    **Use this endpoint for:**
    - Batch queries across multiple symbols (portfolio analytics)
    - Reducing payload size with field selection
    - Exporting data to CSV or Parquet
    - Applying advanced filters (price range, volume)

    **Use GET /v1/trades for:**
    - Simple single-symbol lookups
    - Quick interactive queries

    Output format is controlled by the `format` field in the request body.
    """,
    responses={
        200: {"description": "Query successful"},
        400: {"description": "Invalid request parameters"},
        401: {"description": "Missing API key"},
        403: {"description": "Invalid API key"},
        500: {"description": "Query execution failed"},
    },
)
async def query_trades_advanced(
    request: TradeQueryRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> Response:
    """
    Advanced trade query with multi-symbol support and field selection.

    Returns JSON, CSV, or Parquet based on request.format.
    """
    try:
        logger.info(
            "Advanced trade query",
            symbols=len(request.symbols),
            format=request.format.value,
            limit=request.limit,
        )

        all_trades: List[Dict[str, Any]] = []

        # Multi-symbol query: iterate over symbols or query all
        symbols_to_query = request.symbols if request.symbols else [None]
        exchanges_to_query = request.exchanges if request.exchanges else [None]

        for symbol in symbols_to_query:
            for exchange in exchanges_to_query:
                trades = engine.query_trades(
                    symbol=symbol,
                    exchange=exchange,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    limit=request.limit,
                )

                # Apply advanced filters
                for trade in trades:
                    # Price filter
                    if request.min_price is not None and trade.get("price", 0) < request.min_price:
                        continue
                    if request.max_price is not None and trade.get("price", 0) > request.max_price:
                        continue
                    # Volume filter
                    if request.min_volume is not None and trade.get("volume", 0) < request.min_volume:
                        continue
                    all_trades.append(trade)

                    # Stop if we've hit the limit
                    if len(all_trades) >= request.limit:
                        break

            if len(all_trades) >= request.limit:
                break

        # Apply offset and limit
        all_trades = all_trades[request.offset:request.offset + request.limit]

        return format_response(
            data=all_trades,
            output_format=request.format,
            resource_name="trades",
            fields=request.fields,
            meta=_get_meta(),
            pagination={
                "limit": request.limit,
                "offset": request.offset,
                "has_more": len(all_trades) == request.limit,
            },
        )

    except ValueError as e:
        # Field validation errors
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR", "message": str(e)},
        )
    except Exception as e:
        logger.error("Advanced trade query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"error": "QUERY_FAILED", "message": f"Query failed: {e!s}"},
        )


@router.post(
    "/quotes/query",
    summary="Advanced quote query",
    description="""
    Advanced quote query with multi-symbol support, field selection, and multiple output formats.

    Similar to POST /v1/trades/query but for bid/ask quote data.
    """,
)
async def query_quotes_advanced(
    request: QuoteQueryRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> Response:
    """Advanced quote query with multi-symbol support and field selection."""
    try:
        logger.info(
            "Advanced quote query",
            symbols=len(request.symbols),
            format=request.format.value,
            limit=request.limit,
        )

        all_quotes: List[Dict[str, Any]] = []

        symbols_to_query = request.symbols if request.symbols else [None]
        exchanges_to_query = request.exchanges if request.exchanges else [None]

        for symbol in symbols_to_query:
            for exchange in exchanges_to_query:
                quotes = engine.query_quotes(
                    symbol=symbol,
                    exchange=exchange,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    limit=request.limit,
                )
                all_quotes.extend(quotes)

                if len(all_quotes) >= request.limit:
                    break
            if len(all_quotes) >= request.limit:
                break

        # Apply offset and limit
        all_quotes = all_quotes[request.offset:request.offset + request.limit]

        return format_response(
            data=all_quotes,
            output_format=request.format,
            resource_name="quotes",
            fields=request.fields,
            meta=_get_meta(),
            pagination={
                "limit": request.limit,
                "offset": request.offset,
                "has_more": len(all_quotes) == request.limit,
            },
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR", "message": str(e)},
        )
    except Exception as e:
        logger.error("Advanced quote query failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"error": "QUERY_FAILED", "message": f"Query failed: {e!s}"},
        )


@router.post(
    "/replay",
    response_model=ReplayResponse,
    summary="Historical data replay",
    description="""
    Replay historical data in chronological order with cursor-based pagination.

    **Use cases:**
    - Backtesting trading strategies
    - Rebuilding derived state after failure
    - Data migration and synchronization
    - Compliance auditing with reproducible data access

    **Pagination:**
    - First request: Omit `cursor` field
    - Subsequent requests: Pass `cursor` from previous response
    - Replay complete when `cursor` is null in response

    **Point-in-time replay:**
    - Set `snapshot_id` to replay data as it existed at that snapshot
    - Useful for reproducing historical state exactly
    """,
)
async def replay_historical(
    request: ReplayRequest,
    engine: QueryEngine = Depends(get_query_engine),
    replay_engine: ReplayEngine = Depends(get_replay_engine),
) -> ReplayResponse:
    """
    Replay historical data with cursor-based pagination.

    Returns data in batches with a cursor for the next page.
    """
    try:
        logger.info(
            "Replay request",
            data_type=request.data_type.value,
            symbol=request.symbol,
            start=str(request.start_time),
            end=str(request.end_time),
            batch_size=request.batch_size,
        )

        # Determine table based on data type
        table_name = f"market_data.{request.data_type.value}"

        # Parse cursor or start fresh
        if request.cursor:
            cursor_data = decode_cursor(request.cursor)
            offset = cursor_data["offset"]
            total = cursor_data["total"]
            batch_number = cursor_data["batch_number"]
        else:
            offset = 0
            batch_number = 0
            # Get total count for progress tracking
            stats = replay_engine.get_replay_stats(
                symbol=request.symbol,
                table_name=table_name,
            )
            total = stats.get("total_records", 0)

        # Query data for this batch
        if request.data_type == DataType.TRADES:
            # Build conditions for query
            table_path = engine._get_table_path(table_name)
            conditions = []
            if request.symbol:
                conditions.append(f"symbol = '{request.symbol}'")
            if request.exchange:
                conditions.append(f"exchange = '{request.exchange}'")
            conditions.append(f"exchange_timestamp >= '{request.start_time.isoformat()}'")
            conditions.append(f"exchange_timestamp <= '{request.end_time.isoformat()}'")

            where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT *
                FROM iceberg_scan('{table_path}')
                {where_clause}
                ORDER BY exchange_timestamp ASC, sequence_number ASC
                LIMIT {request.batch_size}
                OFFSET {offset}
            """
            result = engine.connection.execute(query).fetchdf()
            batch_data = result.to_dict(orient="records")
        else:
            # Quotes
            table_path = engine._get_table_path(table_name)
            conditions = []
            if request.symbol:
                conditions.append(f"symbol = '{request.symbol}'")
            if request.exchange:
                conditions.append(f"exchange = '{request.exchange}'")
            conditions.append(f"exchange_timestamp >= '{request.start_time.isoformat()}'")
            conditions.append(f"exchange_timestamp <= '{request.end_time.isoformat()}'")

            where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT *
                FROM iceberg_scan('{table_path}')
                {where_clause}
                ORDER BY exchange_timestamp ASC, sequence_number ASC
                LIMIT {request.batch_size}
                OFFSET {offset}
            """
            result = engine.connection.execute(query).fetchdf()
            batch_data = result.to_dict(orient="records")

        # Convert to response models
        if request.data_type == DataType.TRADES:
            data = [Trade(**row) for row in batch_data]
        else:
            data = [Quote(**row) for row in batch_data]

        # Calculate next cursor
        new_offset = offset + len(batch_data)
        next_cursor = None
        if new_offset < total and batch_data:
            next_cursor = encode_cursor(new_offset, total, batch_number + 1)

        return ReplayResponse(
            success=True,
            data=data,
            cursor=next_cursor,
            batch_info={
                "batch_number": batch_number + 1,
                "batch_size": len(batch_data),
                "total_records": total,
                "progress_percent": round(new_offset / total * 100, 2) if total > 0 else 100,
            },
            meta=_get_meta(),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_CURSOR", "message": str(e)},
        )
    except Exception as e:
        logger.error("Replay failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"error": "REPLAY_FAILED", "message": f"Replay failed: {e!s}"},
        )


@router.post(
    "/snapshots/{snapshot_id}/query",
    response_model=SnapshotQueryResponse,
    summary="Point-in-time query",
    description="""
    Query data as it existed at a specific Iceberg snapshot (time-travel).

    **Use cases:**
    - Compliance auditing ("what data did we have on date X?")
    - Debugging ("reproduce the state before incident Y")
    - Disaster recovery ("rollback to known good state")
    - Backtesting with immutable historical state

    The snapshot ID can be obtained from GET /v1/snapshots.
    """,
)
async def query_at_snapshot(
    snapshot_id: int = Path(description="Iceberg snapshot ID"),
    request: SnapshotQueryRequest = ...,
    replay_engine: ReplayEngine = Depends(get_replay_engine),
) -> Response:
    """Query data as of a specific snapshot (time-travel)."""
    try:
        logger.info(
            "Snapshot query",
            snapshot_id=snapshot_id,
            data_type=request.data_type.value,
            symbol=request.symbol,
        )

        table_name = f"market_data.{request.data_type.value}"

        # Verify snapshot exists
        snapshot_info = replay_engine.get_snapshot(snapshot_id, table_name)
        if not snapshot_info:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "SNAPSHOT_NOT_FOUND",
                    "message": f"Snapshot {snapshot_id} not found for table {table_name}",
                },
            )

        # Query at snapshot
        rows = replay_engine.query_at_snapshot(
            snapshot_id=snapshot_id,
            symbol=request.symbol,
            exchange=request.exchange,
            limit=request.limit,
            table_name=table_name,
        )

        # Apply offset
        rows = rows[request.offset:request.offset + request.limit]

        # Return in requested format
        if request.format != OutputFormat.JSON:
            return format_response(
                data=rows,
                output_format=request.format,
                resource_name=f"snapshot_{snapshot_id}_{request.data_type.value}",
                meta=_get_meta(),
            )

        # JSON response with full model
        if request.data_type == DataType.TRADES:
            data = [Trade(**row) for row in rows]
        else:
            data = [Quote(**row) for row in rows]

        return SnapshotQueryResponse(
            success=True,
            data=data,
            snapshot_id=snapshot_id,
            snapshot_timestamp=snapshot_info.timestamp,
            pagination=PaginationMeta(
                limit=request.limit,
                offset=request.offset,
                has_more=len(rows) == request.limit,
            ),
            meta=_get_meta(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Snapshot query failed", snapshot_id=snapshot_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"error": "QUERY_FAILED", "message": f"Snapshot query failed: {e!s}"},
        )


# Interval to timedelta mapping for aggregations
INTERVAL_DURATIONS = {
    AggregationInterval.ONE_MIN: timedelta(minutes=1),
    AggregationInterval.FIVE_MIN: timedelta(minutes=5),
    AggregationInterval.FIFTEEN_MIN: timedelta(minutes=15),
    AggregationInterval.THIRTY_MIN: timedelta(minutes=30),
    AggregationInterval.ONE_HOUR: timedelta(hours=1),
    AggregationInterval.ONE_DAY: timedelta(days=1),
}

# SQL interval expressions for DuckDB
INTERVAL_SQL = {
    AggregationInterval.ONE_MIN: "INTERVAL '1 minute'",
    AggregationInterval.FIVE_MIN: "INTERVAL '5 minutes'",
    AggregationInterval.FIFTEEN_MIN: "INTERVAL '15 minutes'",
    AggregationInterval.THIRTY_MIN: "INTERVAL '30 minutes'",
    AggregationInterval.ONE_HOUR: "INTERVAL '1 hour'",
    AggregationInterval.ONE_DAY: "INTERVAL '1 day'",
}


@router.post(
    "/aggregations",
    response_model=AggregationResponse,
    summary="Custom aggregations",
    description="""
    Compute custom aggregations (VWAP, TWAP, OHLCV) over time intervals.

    **Available metrics:**
    - `vwap`: Volume-weighted average price
    - `twap`: Time-weighted average price (simple average)
    - `ohlcv`: Open, High, Low, Close, Volume
    - `trade_count`: Number of trades
    - `volume_profile`: Volume distribution (same as trade_count for now)

    **Time intervals:**
    - 1min, 5min, 15min, 30min, 1hour, 1day

    **Example use cases:**
    - Calculate 5-minute VWAP bars for backtesting
    - Generate OHLCV candlestick data for charting
    - Analyze intraday volume patterns
    """,
)
async def compute_aggregations(
    request: AggregationRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> Response:
    """Compute custom aggregations over time intervals."""
    try:
        logger.info(
            "Aggregation request",
            symbols=request.symbols,
            metrics=[m.value for m in request.metrics],
            interval=request.interval.value,
        )

        table_path = engine._get_table_path("market_data.trades")

        # Build symbols filter
        symbols_list = ", ".join(f"'{s}'" for s in request.symbols)
        exchange_filter = f"AND exchange = '{request.exchange}'" if request.exchange else ""

        # DuckDB time bucket aggregation
        interval_sql = INTERVAL_SQL[request.interval]

        query = f"""
            WITH bucketed AS (
                SELECT
                    symbol,
                    time_bucket({interval_sql}, exchange_timestamp) as bucket_start,
                    time_bucket({interval_sql}, exchange_timestamp) + {interval_sql} as bucket_end,
                    exchange_timestamp,
                    price,
                    volume
                FROM iceberg_scan('{table_path}')
                WHERE symbol IN ({symbols_list})
                  AND exchange_timestamp >= '{request.start_time.isoformat()}'
                  AND exchange_timestamp <= '{request.end_time.isoformat()}'
                  {exchange_filter}
            )
            SELECT
                symbol,
                bucket_start,
                bucket_end,
                FIRST(price ORDER BY exchange_timestamp ASC) as open_price,
                MAX(price) as high_price,
                MIN(price) as low_price,
                FIRST(price ORDER BY exchange_timestamp DESC) as close_price,
                SUM(volume) as volume,
                COUNT(*) as trade_count,
                SUM(price * volume) / NULLIF(SUM(volume), 0) as vwap,
                AVG(price) as twap
            FROM bucketed
            GROUP BY symbol, bucket_start, bucket_end
            ORDER BY symbol, bucket_start
        """

        result = engine.connection.execute(query).fetchdf()
        rows = result.to_dict(orient="records")

        # Build response buckets based on requested metrics
        buckets = []
        for row in rows:
            bucket = AggregationBucket(
                symbol=row["symbol"],
                interval_start=row["bucket_start"],
                interval_end=row["bucket_end"],
            )

            # Include metrics based on request
            if AggregationMetric.OHLCV in request.metrics:
                bucket.open_price = float(row["open_price"]) if row["open_price"] else None
                bucket.high_price = float(row["high_price"]) if row["high_price"] else None
                bucket.low_price = float(row["low_price"]) if row["low_price"] else None
                bucket.close_price = float(row["close_price"]) if row["close_price"] else None
                bucket.volume = int(row["volume"]) if row["volume"] else None

            if AggregationMetric.VWAP in request.metrics:
                bucket.vwap = float(row["vwap"]) if row["vwap"] else None

            if AggregationMetric.TWAP in request.metrics:
                bucket.twap = float(row["twap"]) if row["twap"] else None

            if AggregationMetric.TRADE_COUNT in request.metrics or AggregationMetric.VOLUME_PROFILE in request.metrics:
                bucket.trade_count = int(row["trade_count"]) if row["trade_count"] else None

            buckets.append(bucket)

        # Return in requested format
        if request.format != OutputFormat.JSON:
            # Convert buckets to dicts for format conversion
            bucket_dicts = [b.model_dump() for b in buckets]
            return format_response(
                data=bucket_dicts,
                output_format=request.format,
                resource_name="aggregations",
                meta=_get_meta(),
            )

        return AggregationResponse(
            success=True,
            data=buckets,
            request_summary={
                "symbols": request.symbols,
                "metrics": [m.value for m in request.metrics],
                "interval": request.interval.value,
                "start_time": request.start_time.isoformat(),
                "end_time": request.end_time.isoformat(),
                "bucket_count": len(buckets),
            },
            meta=_get_meta(),
        )

    except Exception as e:
        logger.error("Aggregation failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"error": "AGGREGATION_FAILED", "message": f"Aggregation failed: {e!s}"},
        )
