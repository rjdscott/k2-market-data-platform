"""Output format converters for API responses.

This module provides format conversion for query results:
- JSON: Default, standard REST response
- CSV: For analysts, Excel users, data export
- Parquet: For data scientists, efficient columnar storage

Trading firm design considerations:
- Memory-efficient streaming for large datasets
- Proper decimal handling for prices (no floating point errors)
- ISO 8601 timestamps with timezone awareness
- Consistent column ordering for reproducibility

Usage:
    from k2.api.formatters import format_response, OutputFormat

    # Return as FastAPI Response with correct content type
    response = format_response(data, OutputFormat.CSV, "trades")
"""

import base64
import io
import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from fastapi import Response
from fastapi.responses import JSONResponse

from k2.api.models import OutputFormat
from k2.common.logging import get_logger

logger = get_logger(__name__, component="api.formatters")


# Content types for each format
CONTENT_TYPES = {
    OutputFormat.JSON: "application/json",
    OutputFormat.CSV: "text/csv",
    OutputFormat.PARQUET: "application/octet-stream",
}

# File extensions for Content-Disposition header
FILE_EXTENSIONS = {
    OutputFormat.JSON: "json",
    OutputFormat.CSV: "csv",
    OutputFormat.PARQUET: "parquet",
}


def _convert_timestamps(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert any Pandas Timestamps to ISO strings for serialization.

    Args:
        data: List of record dictionaries

    Returns:
        Data with timestamps converted to strings
    """
    converted = []
    for row in data:
        new_row = {}
        for key, value in row.items():
            if hasattr(value, "isoformat"):
                new_row[key] = value.isoformat()
            elif pd.isna(value):
                new_row[key] = None
            else:
                new_row[key] = value
        converted.append(new_row)
    return converted


def _apply_field_selection(
    data: list[dict[str, Any]],
    fields: list[str] | None,
) -> list[dict[str, Any]]:
    """Apply field selection to filter columns.

    Args:
        data: List of record dictionaries
        fields: Fields to include (None = all fields)

    Returns:
        Filtered data with only requested fields
    """
    if not fields or not data:
        return data

    return [{k: v for k, v in row.items() if k in fields} for row in data]


def to_json_response(
    data: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format data as JSON response body.

    Args:
        data: List of record dictionaries
        meta: Response metadata
        pagination: Pagination info

    Returns:
        Response body dictionary
    """
    converted = _convert_timestamps(data)

    response = {
        "success": True,
        "data": converted,
        "meta": meta or {},
    }

    if pagination:
        response["pagination"] = pagination

    return response


def to_csv_response(
    data: list[dict[str, Any]],
    filename: str = "export",
) -> Response:
    """Format data as CSV file response.

    Args:
        data: List of record dictionaries
        filename: Base filename for download

    Returns:
        FastAPI Response with CSV content
    """
    if not data:
        # Empty CSV with headers from first row if available
        csv_content = ""
    else:
        # Convert to DataFrame for CSV export
        converted = _convert_timestamps(data)
        df = pd.DataFrame(converted)

        # Ensure consistent column ordering
        df = df.reindex(sorted(df.columns), axis=1)

        # Export to CSV
        buffer = io.StringIO()
        df.to_csv(buffer, index=False)
        csv_content = buffer.getvalue()

    return Response(
        content=csv_content,
        media_type=CONTENT_TYPES[OutputFormat.CSV],
        headers={
            "Content-Disposition": f"attachment; filename={filename}.csv",
            "X-Record-Count": str(len(data)),
        },
    )


def to_parquet_response(
    data: list[dict[str, Any]],
    filename: str = "export",
) -> Response:
    """Format data as Parquet file response.

    Parquet is optimal for:
    - Large datasets (columnar compression)
    - Data science workflows (pandas, polars, spark)
    - Type preservation (no string parsing needed)

    Args:
        data: List of record dictionaries
        filename: Base filename for download

    Returns:
        FastAPI Response with Parquet content
    """
    if not data:
        # Create empty DataFrame with schema inferred from data
        df = pd.DataFrame()
    else:
        # Convert to DataFrame
        converted = _convert_timestamps(data)
        df = pd.DataFrame(converted)

        # Convert timestamp strings back to datetime for Parquet
        for col in df.columns:
            if "timestamp" in col.lower():
                try:
                    df[col] = pd.to_datetime(df[col])
                except (ValueError, TypeError):
                    pass  # Keep as string if conversion fails

    # Export to Parquet
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    parquet_content = buffer.getvalue()

    return Response(
        content=parquet_content,
        media_type=CONTENT_TYPES[OutputFormat.PARQUET],
        headers={
            "Content-Disposition": f"attachment; filename={filename}.parquet",
            "X-Record-Count": str(len(data)),
        },
    )


def format_response(
    data: list[dict[str, Any]],
    output_format: OutputFormat,
    resource_name: str = "data",
    fields: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
) -> Response:
    """Format query results in the requested output format.

    This is the main entry point for format conversion. Use this function
    in endpoints to return data in JSON, CSV, or Parquet format.

    Args:
        data: List of record dictionaries
        output_format: Desired output format
        resource_name: Name for file download (e.g., "trades", "quotes")
        fields: Optional field selection (applied before formatting)
        meta: Response metadata (JSON only)
        pagination: Pagination info (JSON only)

    Returns:
        FastAPI Response with appropriate content type

    Example:
        @router.post("/trades/query")
        async def query_trades(request: TradeQueryRequest):
            data = engine.query_trades(...)
            return format_response(
                data,
                request.format,
                resource_name="trades",
                fields=request.fields,
            )
    """
    # Apply field selection
    filtered_data = _apply_field_selection(data, fields)

    # Generate timestamp for filename
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{resource_name}_{timestamp}"

    logger.debug(
        "Formatting response",
        format=output_format.value,
        record_count=len(filtered_data),
        fields=len(fields) if fields else "all",
    )

    if output_format == OutputFormat.JSON:
        body = to_json_response(filtered_data, meta, pagination)
        return JSONResponse(content=body)

    if output_format == OutputFormat.CSV:
        return to_csv_response(filtered_data, filename)

    if output_format == OutputFormat.PARQUET:
        return to_parquet_response(filtered_data, filename)

    # Fallback to JSON
    logger.warning("Unknown format, falling back to JSON", format=output_format)
    body = to_json_response(filtered_data, meta, pagination)
    return JSONResponse(content=body)


def encode_cursor(offset: int, total: int, batch_number: int) -> str:
    """Encode replay cursor as base64 string.

    Args:
        offset: Current offset position
        total: Total records
        batch_number: Current batch number

    Returns:
        Base64-encoded cursor string
    """
    cursor_data = {
        "o": offset,
        "t": total,
        "b": batch_number,
    }
    json_str = json.dumps(cursor_data)
    return base64.urlsafe_b64encode(json_str.encode()).decode()


def decode_cursor(cursor: str) -> dict[str, int]:
    """Decode replay cursor from base64 string.

    Args:
        cursor: Base64-encoded cursor string

    Returns:
        Dictionary with offset, total, batch_number

    Raises:
        ValueError: If cursor is invalid
    """
    try:
        json_str = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(json_str)
        return {
            "offset": data["o"],
            "total": data["t"],
            "batch_number": data["b"],
        }
    except Exception as e:
        raise ValueError(f"Invalid cursor: {e}")
