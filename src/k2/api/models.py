"""Pydantic V2 models for API request/response validation.

This module defines strongly-typed request and response models for the K2 API,
ensuring data validation and automatic OpenAPI documentation generation.

Models follow trading firm conventions:
- Explicit decimal handling for prices (no floating point errors)
- ISO 8601 timestamps
- Clear field descriptions for API consumers
"""

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =============================================================================
# Enums
# =============================================================================


class DataType(str, Enum):
    """Market data type classification."""

    TRADES = "trades"
    QUOTES = "quotes"


class HealthStatus(str, Enum):
    """Service health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


# =============================================================================
# Base Models
# =============================================================================


class APIResponse(BaseModel):
    """Base response model with metadata."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "data": {},
                "meta": {"correlation_id": "abc-123"},
            },
        },
    )

    success: bool = Field(default=True, description="Request success status")
    meta: dict = Field(default_factory=dict, description="Response metadata")


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": False,
                "error": {
                    "code": "INVALID_SYMBOL",
                    "message": "Symbol 'INVALID' not found",
                    "details": {},
                },
            },
        },
    )

    success: bool = Field(default=False)
    error: dict = Field(description="Error details")


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses."""

    limit: int = Field(description="Maximum items per page")
    offset: int = Field(default=0, description="Number of items skipped")
    total: int | None = Field(default=None, description="Total items available")
    has_more: bool = Field(default=False, description="More items available")


# =============================================================================
# Trade Models
# =============================================================================


class Trade(BaseModel):
    """Individual trade record (V2 schema)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-123",
                "trade_id": "trade-456",
                "symbol": "BTCUSDT",
                "exchange": "BINANCE",
                "asset_class": "CRYPTO",
                "timestamp": "2024-01-15T10:30:00.123456",
                "price": 45004.12,
                "quantity": 0.5,
                "currency": "USDT",
                "side": "BUY",
                "trade_conditions": None,
                "source_sequence": 12345,
                "ingestion_timestamp": "2024-01-15T10:30:00.123456",
                "platform_sequence": 67890,
                "vendor_data": None,
            },
        },
        arbitrary_types_allowed=True,
    )

    message_id: str = Field(description="Unique message identifier")
    trade_id: str = Field(description="Trade identifier")
    symbol: str = Field(description="Security symbol (e.g., BTCUSDT)")
    exchange: str = Field(description="Exchange code (e.g., BINANCE)")
    asset_class: str = Field(description="Asset class (EQUITY, CRYPTO, etc)")
    timestamp: Any = Field(description="Trade execution time (microseconds precision)")
    price: float = Field(description="Trade price")
    quantity: float = Field(description="Trade quantity (Decimal precision)")
    currency: str = Field(description="Currency code (e.g., USDT, USD)")
    side: str = Field(description="Trade side (BUY, SELL)")
    trade_conditions: str | None = Field(default=None, description="Trade conditions (JSON)")
    source_sequence: int | None = Field(default=None, description="Source sequence number")
    ingestion_timestamp: Any = Field(description="Time data was ingested")
    platform_sequence: int | None = Field(default=None, description="Platform sequence number")
    vendor_data: str | None = Field(default=None, description="Vendor-specific data (JSON)")

    @field_validator("timestamp", "ingestion_timestamp", mode="before")
    @classmethod
    def convert_timestamp(cls, v: Any) -> Any:
        """Convert Pandas Timestamp to ISO string for JSON serialization."""
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)


class TradesResponse(APIResponse):
    """Response model for trades query."""

    data: list[Trade] = Field(default_factory=list, description="List of trades")
    pagination: PaginationMeta | None = Field(default=None, description="Pagination info")


# =============================================================================
# Quote Models
# =============================================================================


class Quote(BaseModel):
    """Individual quote record with bid/ask (V2 schema)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-123",
                "quote_id": "quote-456",
                "symbol": "BTCUSDT",
                "exchange": "BINANCE",
                "asset_class": "CRYPTO",
                "timestamp": "2024-01-15T10:30:00.123456",
                "bid_price": 45000.00,
                "bid_quantity": 1.5,
                "ask_price": 45005.00,
                "ask_quantity": 2.0,
                "currency": "USDT",
                "source_sequence": 12345,
                "ingestion_timestamp": "2024-01-15T10:30:00.123456",
                "platform_sequence": 67890,
                "vendor_data": None,
            },
        },
        arbitrary_types_allowed=True,
    )

    message_id: str = Field(description="Unique message identifier")
    quote_id: str = Field(description="Quote identifier")
    symbol: str = Field(description="Security symbol")
    exchange: str = Field(description="Exchange code")
    asset_class: str = Field(description="Asset class (EQUITY, CRYPTO, etc)")
    timestamp: Any = Field(description="Quote time (microseconds precision)")
    bid_price: float | None = Field(default=None, description="Best bid price")
    bid_quantity: float | None = Field(
        default=None, description="Best bid quantity (Decimal precision)"
    )
    ask_price: float | None = Field(default=None, description="Best ask price")
    ask_quantity: float | None = Field(
        default=None, description="Best ask quantity (Decimal precision)"
    )
    currency: str = Field(description="Currency code (e.g., USDT, USD)")
    source_sequence: int | None = Field(default=None, description="Source sequence number")
    ingestion_timestamp: Any = Field(description="Time data was ingested")
    platform_sequence: int | None = Field(default=None, description="Platform sequence number")
    vendor_data: str | None = Field(default=None, description="Vendor-specific data (JSON)")

    @field_validator("timestamp", "ingestion_timestamp", mode="before")
    @classmethod
    def convert_timestamp(cls, v: Any) -> Any:
        """Convert Pandas Timestamp to ISO string for JSON serialization."""
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)


class QuotesResponse(APIResponse):
    """Response model for quotes query."""

    data: list[Quote] = Field(default_factory=list, description="List of quotes")
    pagination: PaginationMeta | None = Field(default=None, description="Pagination info")


# =============================================================================
# Market Summary Models
# =============================================================================


class MarketSummaryData(BaseModel):
    """OHLCV daily market summary."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "BHP",
                "summary_date": "2024-01-15",
                "open_price": "45.20",
                "high_price": "46.10",
                "low_price": "45.00",
                "close_price": "45.80",
                "volume": 2500000,
                "trade_count": 15432,
                "vwap": "45.55",
            },
        },
    )

    symbol: str = Field(description="Security symbol")
    summary_date: date = Field(alias="date", description="Trading date")
    open_price: float = Field(description="Opening price")
    high_price: float = Field(description="Highest price")
    low_price: float = Field(description="Lowest price")
    close_price: float = Field(description="Closing price")
    volume: int = Field(description="Total volume traded")
    trade_count: int = Field(description="Number of trades")
    vwap: float = Field(description="Volume-weighted average price")


class SummaryResponse(APIResponse):
    """Response model for market summary."""

    data: MarketSummaryData | None = Field(default=None, description="Market summary")


# =============================================================================
# Symbols Models
# =============================================================================


class SymbolsResponse(APIResponse):
    """Response model for symbols list."""

    data: list[str] = Field(default_factory=list, description="List of symbols")


# =============================================================================
# Stats Models
# =============================================================================


class TableStats(BaseModel):
    """Statistics for a single table."""

    table_name: str = Field(description="Table name")
    row_count: int = Field(description="Number of rows")


class StatsData(BaseModel):
    """Database statistics."""

    s3_endpoint: str = Field(description="S3/MinIO endpoint")
    warehouse_path: str = Field(description="Iceberg warehouse path")
    connection_active: bool = Field(description="DuckDB connection status")
    trades_count: int | None = Field(default=None, description="Trade records")
    quotes_count: int | None = Field(default=None, description="Quote records")
    error: str | None = Field(default=None, description="Error if stats failed")


class StatsResponse(APIResponse):
    """Response model for stats endpoint."""

    data: StatsData = Field(description="Database statistics")


# =============================================================================
# Snapshot Models
# =============================================================================


class SnapshotInfo(BaseModel):
    """Iceberg table snapshot information."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "snapshot_id": 1234567890,
                "timestamp": "2024-01-15T10:30:00.000000",
                "manifest_list": "s3://warehouse/market_data/trades/metadata/snap-123.avro",
                "summary": {"operation": "append", "added-records": "1000"},
            },
        },
    )

    snapshot_id: int = Field(description="Snapshot ID")
    timestamp: datetime | None = Field(default=None, description="Snapshot time")
    manifest_list: str | None = Field(default=None, description="Path to manifest list")
    summary: dict | None = Field(default=None, description="Snapshot summary")


class SnapshotsResponse(APIResponse):
    """Response model for snapshots list."""

    data: list[SnapshotInfo] = Field(default_factory=list, description="List of snapshots")
    table_name: str = Field(description="Table name")


# =============================================================================
# Health Check Models
# =============================================================================


class DependencyHealth(BaseModel):
    """Health status of a single dependency."""

    name: str = Field(description="Dependency name")
    status: HealthStatus = Field(description="Health status")
    latency_ms: float | None = Field(
        default=None,
        description="Response latency in milliseconds",
    )
    message: str | None = Field(default=None, description="Status message")


class HealthResponse(BaseModel):
    """Health check response."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "version": "0.1.0",
                "dependencies": [
                    {"name": "duckdb", "status": "healthy", "latency_ms": 5.2},
                    {"name": "iceberg", "status": "healthy", "latency_ms": 12.3},
                ],
            },
        },
    )

    status: HealthStatus = Field(description="Overall health status")
    version: str = Field(description="API version")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    dependencies: list[DependencyHealth] = Field(
        default_factory=list,
        description="Dependency health checks",
    )


# =============================================================================
# POST Request Models (Complex Queries)
# =============================================================================


class OutputFormat(str, Enum):
    """Output format for query results."""

    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"


class AggregationMetric(str, Enum):
    """Available aggregation metrics."""

    VWAP = "vwap"  # Volume-weighted average price
    TWAP = "twap"  # Time-weighted average price
    OHLCV = "ohlcv"  # Open, High, Low, Close, Volume
    VOLUME_PROFILE = "volume_profile"  # Volume distribution by price
    TRADE_COUNT = "trade_count"  # Number of trades


class AggregationInterval(str, Enum):
    """Time intervals for aggregations."""

    ONE_MIN = "1min"
    FIVE_MIN = "5min"
    FIFTEEN_MIN = "15min"
    THIRTY_MIN = "30min"
    ONE_HOUR = "1hour"
    ONE_DAY = "1day"


# Allowlist of valid fields for field selection (security measure)
VALID_TRADE_FIELDS = frozenset(
    [
        "message_id",
        "trade_id",
        "symbol",
        "exchange",
        "asset_class",
        "timestamp",
        "price",
        "quantity",
        "currency",
        "side",
        "trade_conditions",
        "source_sequence",
        "ingestion_timestamp",
        "platform_sequence",
        "vendor_data",
    ],
)

VALID_QUOTE_FIELDS = frozenset(
    [
        "message_id",
        "quote_id",
        "symbol",
        "exchange",
        "asset_class",
        "timestamp",
        "bid_price",
        "bid_quantity",
        "ask_price",
        "ask_quantity",
        "currency",
        "source_sequence",
        "ingestion_timestamp",
        "platform_sequence",
        "vendor_data",
    ],
)


class TradeQueryRequest(BaseModel):
    """Complex trade query request for POST /v1/trades/query.

    Supports multi-symbol queries, field selection, advanced filters,
    and multiple output formats. Use this for analytical queries;
    use GET /v1/trades for simple single-symbol lookups.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbols": ["BHP", "RIO", "FMG"],
                "exchanges": ["ASX"],
                "start_time": "2024-01-01T09:00:00",
                "end_time": "2024-01-31T16:00:00",
                "fields": ["symbol", "timestamp", "price", "quantity"],
                "limit": 10000,
                "format": "json",
            },
        },
    )

    # Symbol filters (empty list = all symbols)
    symbols: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Filter by symbols (max 100). Empty = all symbols.",
    )
    exchanges: list[str] = Field(
        default_factory=list,
        description="Filter by exchanges. Empty = all exchanges.",
    )

    # Time filters
    start_time: datetime | None = Field(
        default=None,
        description="Filter trades after this time (ISO 8601)",
    )
    end_time: datetime | None = Field(
        default=None,
        description="Filter trades before this time (ISO 8601)",
    )

    # Field selection (None = all fields)
    fields: list[str] | None = Field(
        default=None,
        description="Fields to return. None = all fields. Reduces payload size.",
    )

    # Pagination
    limit: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="Maximum records to return (max 100,000)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of records to skip",
    )

    # Advanced filters
    min_price: float | None = Field(
        default=None,
        ge=0,
        description="Minimum trade price filter",
    )
    max_price: float | None = Field(
        default=None,
        ge=0,
        description="Maximum trade price filter",
    )
    min_quantity: float | None = Field(
        default=None,
        ge=0,
        description="Minimum trade quantity filter",
    )

    # Output options
    format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format: json, csv, or parquet",
    )

    @field_validator("fields", mode="before")
    @classmethod
    def validate_fields(cls, v: list[str] | None) -> list[str] | None:
        """Validate field names against allowlist to prevent SQL injection."""
        if v is None:
            return None
        invalid = set(v) - VALID_TRADE_FIELDS
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}. Valid: {VALID_TRADE_FIELDS}")
        return v


class QuoteQueryRequest(BaseModel):
    """Complex quote query request for POST /v1/quotes/query.

    Supports multi-symbol queries, field selection, and multiple output formats.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbols": ["BHP", "RIO"],
                "start_time": "2024-01-15T09:30:00",
                "end_time": "2024-01-15T16:00:00",
                "fields": ["symbol", "timestamp", "bid_price", "ask_price"],
                "limit": 5000,
                "format": "json",
            },
        },
    )

    symbols: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Filter by symbols (max 100)",
    )
    exchanges: list[str] = Field(
        default_factory=list,
        description="Filter by exchanges",
    )
    start_time: datetime | None = Field(
        default=None,
        description="Filter quotes after this time",
    )
    end_time: datetime | None = Field(
        default=None,
        description="Filter quotes before this time",
    )
    fields: list[str] | None = Field(
        default=None,
        description="Fields to return. None = all fields.",
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="Maximum records (max 100,000)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Records to skip",
    )
    format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format",
    )

    @field_validator("fields", mode="before")
    @classmethod
    def validate_fields(cls, v: list[str] | None) -> list[str] | None:
        """Validate field names against allowlist."""
        if v is None:
            return None
        invalid = set(v) - VALID_QUOTE_FIELDS
        if invalid:
            raise ValueError(f"Invalid fields: {invalid}. Valid: {VALID_QUOTE_FIELDS}")
        return v


class ReplayRequest(BaseModel):
    """Historical data replay request for POST /v1/replay.

    Returns paginated batches of historical data in chronological order.
    Use for backtesting, rebuilding state, or data migration.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data_type": "trades",
                "symbol": "BHP",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-31T23:59:59",
                "batch_size": 1000,
                "snapshot_id": None,
            },
        },
    )

    data_type: DataType = Field(
        default=DataType.TRADES,
        description="Data type to replay: trades or quotes",
    )
    symbol: str | None = Field(
        default=None,
        description="Filter by symbol (None = all symbols)",
    )
    exchange: str | None = Field(
        default=None,
        description="Filter by exchange",
    )
    start_time: datetime = Field(
        description="Start of replay period (required)",
    )
    end_time: datetime = Field(
        description="End of replay period (required)",
    )
    batch_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Records per batch (100-10,000)",
    )
    snapshot_id: int | None = Field(
        default=None,
        description="Optional snapshot ID for point-in-time replay",
    )
    cursor: str | None = Field(
        default=None,
        description="Cursor from previous response for pagination",
    )
    format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format",
    )


class ReplayCursor(BaseModel):
    """Cursor for replay pagination (base64 encoded in API)."""

    offset: int = Field(description="Current offset position")
    total: int = Field(description="Total records in replay")
    batch_number: int = Field(description="Current batch number")


class ReplayResponse(APIResponse):
    """Response model for replay endpoint."""

    data: list[Trade | Quote] = Field(
        default_factory=list,
        description="Batch of records",
    )
    cursor: str | None = Field(
        default=None,
        description="Cursor for next batch (None if complete)",
    )
    batch_info: dict = Field(
        default_factory=dict,
        description="Batch metadata (number, size, progress)",
    )


class SnapshotQueryRequest(BaseModel):
    """Point-in-time query request for POST /v1/snapshots/{id}/query.

    Query data as it existed at a specific Iceberg snapshot.
    Use for compliance auditing, debugging, or disaster recovery.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data_type": "trades",
                "symbol": "BHP",
                "limit": 1000,
                "format": "json",
            },
        },
    )

    data_type: DataType = Field(
        default=DataType.TRADES,
        description="Data type: trades or quotes",
    )
    symbol: str | None = Field(
        default=None,
        description="Filter by symbol",
    )
    exchange: str | None = Field(
        default=None,
        description="Filter by exchange",
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="Maximum records",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Records to skip",
    )
    format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format",
    )


class SnapshotQueryResponse(APIResponse):
    """Response model for snapshot query endpoint."""

    data: list[Trade | Quote] = Field(
        default_factory=list,
        description="Records as of snapshot",
    )
    snapshot_id: int = Field(description="Queried snapshot ID")
    snapshot_timestamp: datetime | None = Field(
        default=None,
        description="Snapshot creation time",
    )
    pagination: PaginationMeta | None = Field(
        default=None,
        description="Pagination info",
    )


class AggregationRequest(BaseModel):
    """Custom aggregation request for POST /v1/aggregations.

    Compute VWAP, TWAP, OHLCV, and other metrics over time intervals.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbols": ["BHP", "RIO"],
                "metrics": ["vwap", "ohlcv"],
                "interval": "5min",
                "start_time": "2024-01-15T09:30:00",
                "end_time": "2024-01-15T16:00:00",
            },
        },
    )

    symbols: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Symbols to aggregate (1-50, required)",
    )
    metrics: list[AggregationMetric] = Field(
        min_length=1,
        description="Metrics to compute",
    )
    interval: AggregationInterval = Field(
        description="Time interval for aggregation",
    )
    start_time: datetime = Field(
        description="Start of aggregation period",
    )
    end_time: datetime = Field(
        description="End of aggregation period",
    )
    exchange: str | None = Field(
        default=None,
        description="Filter by exchange",
    )
    format: OutputFormat = Field(
        default=OutputFormat.JSON,
        description="Output format",
    )


class AggregationBucket(BaseModel):
    """Single time bucket in aggregation results."""

    symbol: str = Field(description="Symbol")
    interval_start: datetime = Field(description="Bucket start time")
    interval_end: datetime = Field(description="Bucket end time")

    # OHLCV fields
    open_price: float | None = Field(default=None)
    high_price: float | None = Field(default=None)
    low_price: float | None = Field(default=None)
    close_price: float | None = Field(default=None)
    volume: int | None = Field(default=None)
    trade_count: int | None = Field(default=None)

    # Weighted averages
    vwap: float | None = Field(default=None, description="Volume-weighted avg price")
    twap: float | None = Field(default=None, description="Time-weighted avg price")


class AggregationResponse(APIResponse):
    """Response model for aggregation endpoint."""

    data: list[AggregationBucket] = Field(
        default_factory=list,
        description="Aggregation results by time bucket",
    )
    request_summary: dict = Field(
        default_factory=dict,
        description="Summary of request parameters",
    )
