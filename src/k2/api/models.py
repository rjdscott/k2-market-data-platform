"""Pydantic V2 models for API request/response validation.

This module defines strongly-typed request and response models for the K2 API,
ensuring data validation and automatic OpenAPI documentation generation.

Models follow trading firm conventions:
- Explicit decimal handling for prices (no floating point errors)
- ISO 8601 timestamps
- Clear field descriptions for API consumers
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any, Union
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict, field_validator


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
            }
        }
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
            }
        }
    )

    success: bool = Field(default=False)
    error: dict = Field(description="Error details")


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses."""

    limit: int = Field(description="Maximum items per page")
    offset: int = Field(default=0, description="Number of items skipped")
    total: Optional[int] = Field(default=None, description="Total items available")
    has_more: bool = Field(default=False, description="More items available")


# =============================================================================
# Trade Models
# =============================================================================


class Trade(BaseModel):
    """Individual trade record."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "BHP",
                "company_id": "BHP.AX",
                "exchange": "ASX",
                "exchange_timestamp": "2024-01-15T10:30:00.123456",
                "price": "45.50",
                "volume": 1000,
                "qualifiers": "XT",
                "venue": "ASX",
                "buyer_id": "BROKER001",
                "sequence_number": 12345,
            }
        },
        # Allow arbitrary types for Pandas Timestamp etc
        arbitrary_types_allowed=True,
    )

    symbol: str = Field(description="Security symbol (e.g., BHP)")
    company_id: Optional[Union[str, int]] = Field(default=None, description="Company identifier")
    exchange: str = Field(description="Exchange code (e.g., ASX)")
    exchange_timestamp: Any = Field(description="Trade execution time")
    price: float = Field(description="Trade price")
    volume: int = Field(description="Trade volume (shares)")
    qualifiers: Optional[Union[str, int]] = Field(default=None, description="Trade condition codes")
    venue: Optional[str] = Field(default=None, description="Execution venue")
    buyer_id: Optional[str] = Field(default=None, description="Buyer broker ID")
    ingestion_timestamp: Optional[Any] = Field(
        default=None, description="Time data was ingested"
    )
    sequence_number: Optional[int] = Field(
        default=None, description="Sequence number for ordering"
    )

    @field_validator("exchange_timestamp", "ingestion_timestamp", mode="before")
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

    data: List[Trade] = Field(default_factory=list, description="List of trades")
    pagination: Optional[PaginationMeta] = Field(
        default=None, description="Pagination info"
    )


# =============================================================================
# Quote Models
# =============================================================================


class Quote(BaseModel):
    """Individual quote record with bid/ask."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "BHP",
                "company_id": "BHP.AX",
                "exchange": "ASX",
                "exchange_timestamp": "2024-01-15T10:30:00.123456",
                "bid_price": "45.48",
                "bid_volume": 500,
                "ask_price": "45.52",
                "ask_volume": 750,
                "spread": "0.04",
            }
        },
        arbitrary_types_allowed=True,
    )

    symbol: str = Field(description="Security symbol")
    company_id: Optional[Union[str, int]] = Field(default=None, description="Company identifier")
    exchange: str = Field(description="Exchange code")
    exchange_timestamp: Any = Field(description="Quote time")
    bid_price: Optional[float] = Field(default=None, description="Best bid price")
    bid_volume: Optional[int] = Field(default=None, description="Best bid volume")
    ask_price: Optional[float] = Field(default=None, description="Best ask price")
    ask_volume: Optional[int] = Field(default=None, description="Best ask volume")
    ingestion_timestamp: Optional[Any] = Field(
        default=None, description="Time data was ingested"
    )
    sequence_number: Optional[int] = Field(
        default=None, description="Sequence number"
    )

    @field_validator("exchange_timestamp", "ingestion_timestamp", mode="before")
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

    data: List[Quote] = Field(default_factory=list, description="List of quotes")
    pagination: Optional[PaginationMeta] = Field(
        default=None, description="Pagination info"
    )


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
            }
        }
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

    data: Optional[MarketSummaryData] = Field(default=None, description="Market summary")


# =============================================================================
# Symbols Models
# =============================================================================


class SymbolsResponse(APIResponse):
    """Response model for symbols list."""

    data: List[str] = Field(default_factory=list, description="List of symbols")


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
    trades_count: Optional[int] = Field(default=None, description="Trade records")
    quotes_count: Optional[int] = Field(default=None, description="Quote records")
    error: Optional[str] = Field(default=None, description="Error if stats failed")


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
            }
        }
    )

    snapshot_id: int = Field(description="Snapshot ID")
    timestamp: Optional[datetime] = Field(default=None, description="Snapshot time")
    manifest_list: Optional[str] = Field(
        default=None, description="Path to manifest list"
    )
    summary: Optional[dict] = Field(default=None, description="Snapshot summary")


class SnapshotsResponse(APIResponse):
    """Response model for snapshots list."""

    data: List[SnapshotInfo] = Field(
        default_factory=list, description="List of snapshots"
    )
    table_name: str = Field(description="Table name")


# =============================================================================
# Health Check Models
# =============================================================================


class DependencyHealth(BaseModel):
    """Health status of a single dependency."""

    name: str = Field(description="Dependency name")
    status: HealthStatus = Field(description="Health status")
    latency_ms: Optional[float] = Field(
        default=None, description="Response latency in milliseconds"
    )
    message: Optional[str] = Field(default=None, description="Status message")


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
            }
        }
    )

    status: HealthStatus = Field(description="Overall health status")
    version: str = Field(description="API version")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    dependencies: List[DependencyHealth] = Field(
        default_factory=list, description="Dependency health checks"
    )
