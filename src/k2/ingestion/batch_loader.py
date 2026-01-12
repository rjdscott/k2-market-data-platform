"""CSV Batch Loader for market data ingestion.

This module provides a CLI tool to load CSV files into Kafka topics using the
MarketDataProducer. Features include:
- Chunked CSV reading for memory efficiency
- Progress bar with rich library
- Schema validation and error handling
- Dead Letter Queue (DLQ) for invalid records
- Summary report with statistics
- v1 and v2 schema support with automatic message building

Usage:
    # Load trades from CSV (v2 schema with currency)
    python -m k2.ingestion.batch_loader load \
        --csv data/asx_trades.csv \
        --asset-class equities \
        --exchange asx \
        --data-type trades \
        --currency AUD

    # Load with custom batch size and DLQ
    python -m k2.ingestion.batch_loader load \
        --csv data/asx_trades.csv \
        --asset-class equities \
        --exchange asx \
        --data-type trades \
        --batch-size 5000 \
        --currency AUD \
        --dlq-file errors.csv

    # Load v1 legacy schema
    python -m k2.ingestion.batch_loader load \
        --csv data/asx_trades.csv \
        --asset-class equities \
        --exchange asx \
        --data-type trades \
        --schema-version v1

Example CSV format (v2 trades - minimal):
    symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id
    BHP,2026-01-10T10:30:00.123Z,45.50,1000,buy,12345,T-001
    RIO,2026-01-10T10:30:00.456Z,120.75,500,sell,12346,T-002

Example CSV format (v2 trades - with ASX vendor_data):
    symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id,company_id,qualifiers,venue
    BHP,2026-01-10T10:30:00.123Z,45.50,1000,buy,12345,T-001,123,0,X
    RIO,2026-01-10T10:30:00.456Z,120.75,500,sell,12346,T-002,456,0,X

V2 Schema Features:
- Auto-generates message_id (UUID) for deduplication
- Maps side: buy → BUY, sell → SELL (enum)
- Supports vendor_data: company_id, qualifiers, venue → vendor_data map
- Decimal precision: price/quantity as Decimal(18,8)
- Timestamp: microseconds precision
"""

import csv
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from k2.common.logging import get_logger
from k2.ingestion.message_builders import build_quote_v2, build_trade_v2
from k2.ingestion.producer import MarketDataProducer

logger = get_logger(__name__, component="batch_loader")
console = Console()

# CLI app
app = typer.Typer(help="K2 CSV Batch Loader - Load market data from CSV to Kafka")


@dataclass
class LoadStats:
    """Statistics for batch load operation."""

    total_rows: int = 0
    success_count: int = 0
    error_count: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None

    @property
    def duration_seconds(self) -> float:
        """Calculate duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    @property
    def throughput(self) -> float:
        """Calculate rows per second."""
        if self.duration_seconds > 0:
            return self.success_count / self.duration_seconds
        return 0.0

    @property
    def error_rate(self) -> float:
        """Calculate error percentage."""
        if self.total_rows > 0:
            return (self.error_count / self.total_rows) * 100
        return 0.0


class BatchLoader:
    """CSV batch loader for market data.

    Reads CSV files in chunks, validates records, and produces to Kafka
    using MarketDataProducer.

    Example:
        >>> loader = BatchLoader(
        ...     asset_class='equities',
        ...     exchange='asx',
        ...     data_type='trades',
        ... )
        >>> stats = loader.load_csv('data/trades.csv', batch_size=1000)
        >>> print(f"Loaded {stats.success_count} records")
    """

    def __init__(
        self,
        asset_class: str,
        exchange: str,
        data_type: str,
        producer: MarketDataProducer | None = None,
        dlq_file: Path | None = None,
        schema_version: str = "v2",
        currency: str = "USD",
    ):
        """Initialize batch loader.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')
            data_type: Data type ('trades', 'quotes', 'reference_data')
            producer: Optional MarketDataProducer instance (default: creates new)
            dlq_file: Optional path to dead letter queue CSV file
            schema_version: Schema version - 'v1' or 'v2' (default: 'v2')
            currency: Default currency code (default: 'USD', ASX typically 'AUD')

        Raises:
            ValueError: If data_type or schema_version is invalid
        """
        self.asset_class = asset_class
        self.exchange = exchange
        self.data_type = data_type.lower()
        self.schema_version = schema_version
        self.currency = currency

        # Validate data type
        valid_data_types = ["trades", "quotes", "reference_data"]
        if self.data_type not in valid_data_types:
            raise ValueError(f"Invalid data_type '{data_type}'. Must be one of: {valid_data_types}")

        # Validate schema version
        if schema_version not in ("v1", "v2"):
            raise ValueError(f"Invalid schema_version '{schema_version}'. Must be 'v1' or 'v2'")

        # Create or use provided producer (with schema_version)
        self.producer = producer or MarketDataProducer(schema_version=schema_version)

        # Dead Letter Queue setup
        self.dlq_file = dlq_file
        self._dlq_writer = None
        if self.dlq_file:
            self._init_dlq()

        logger.info(
            "Batch loader initialized",
            asset_class=asset_class,
            exchange=exchange,
            data_type=data_type,
            schema_version=schema_version,
            currency=currency,
            dlq_enabled=dlq_file is not None,
        )

    def _init_dlq(self):
        """Initialize Dead Letter Queue CSV writer."""
        if not self.dlq_file:
            return

        self.dlq_file.parent.mkdir(parents=True, exist_ok=True)

        # Open DLQ file in append mode
        self._dlq_handle = open(self.dlq_file, "a", newline="", encoding="utf-8")
        self._dlq_writer = csv.DictWriter(
            self._dlq_handle,
            fieldnames=["row_number", "error", "raw_record"],
            extrasaction="ignore",
        )

        # Write header if file is new
        if self.dlq_file.stat().st_size == 0:
            self._dlq_writer.writeheader()

        logger.info("Dead Letter Queue initialized", dlq_file=str(self.dlq_file))

    def _write_to_dlq(self, row_number: int, error: str, record: dict):
        """Write failed record to Dead Letter Queue.

        Args:
            row_number: CSV row number
            error: Error message
            record: Failed record dict
        """
        if not self._dlq_writer:
            return

        try:
            self._dlq_writer.writerow(
                {
                    "row_number": row_number,
                    "error": error,
                    "raw_record": str(record),
                },
            )
            self._dlq_handle.flush()

        except Exception as e:
            logger.error(
                "Failed to write to DLQ",
                row_number=row_number,
                error=str(e),
            )

    def _parse_csv_row(self, row: dict) -> dict:
        """Parse CSV row and convert to appropriate types.

        Args:
            row: Raw CSV row dict (all string values)

        Returns:
            Parsed record dict with correct types

        Raises:
            ValueError: If row is missing required fields or has invalid values
        """
        # Clean whitespace from all fields
        row = {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}

        # Type conversions based on data type
        if self.data_type == "trades":
            return self._parse_trade_row(row)
        if self.data_type == "quotes":
            return self._parse_quote_row(row)
        if self.data_type == "reference_data":
            return self._parse_reference_data_row(row)
        raise ValueError(f"Unsupported data_type: {self.data_type}")

    def _parse_trade_row(self, row: dict) -> dict:
        """Parse trade CSV row.

        V2 schema:
        Required fields: symbol, exchange_timestamp, price, quantity, side,
                        sequence_number, trade_id
        Optional v2 fields: company_id, qualifiers, venue (mapped to vendor_data)

        V1 schema (legacy):
        Same required fields with simplified structure

        Args:
            row: Raw CSV row

        Returns:
            Parsed trade record (v2 format if schema_version='v2')

        Raises:
            ValueError: If required fields missing or invalid
        """
        required_fields = [
            "symbol",
            "exchange_timestamp",
            "price",
            "quantity",
            "side",
            "sequence_number",
            "trade_id",
        ]

        # Check required fields
        missing = [f for f in required_fields if f not in row or not row[f]]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        # Parse timestamp (ISO format string → datetime)
        try:
            timestamp = datetime.fromisoformat(row["exchange_timestamp"].replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {row['exchange_timestamp']}") from e

        # Map side to enum (buy → BUY, sell → SELL)
        side_mapping = {
            "buy": "BUY",
            "sell": "SELL",
            "sell_short": "SELL_SHORT",
            "unknown": "UNKNOWN",
        }
        side_lower = row["side"].lower()
        side_enum = side_mapping.get(side_lower, "UNKNOWN")

        # Use v2 builder for v2 schema
        if self.schema_version == "v2":
            # Build vendor_data from ASX-specific fields (if present)
            vendor_data = None
            if any(k in row for k in ["company_id", "qualifiers", "venue"]):
                vendor_data = {}
                if row.get("company_id"):
                    vendor_data["company_id"] = str(row["company_id"])
                if row.get("qualifiers"):
                    vendor_data["qualifiers"] = str(row["qualifiers"])
                if row.get("venue"):
                    vendor_data["venue"] = str(row["venue"])

            # Build v2 trade message
            return build_trade_v2(
                symbol=row["symbol"],
                exchange=self.exchange.upper(),
                asset_class=self.asset_class,
                timestamp=timestamp,
                price=Decimal(str(row["price"])),
                quantity=Decimal(str(row["quantity"])),
                currency=self.currency,
                side=side_enum,
                trade_id=row["trade_id"],
                message_id=str(uuid.uuid4()),
                source_sequence=int(row["sequence_number"]),
                vendor_data=vendor_data,
            )

        # V1 legacy format
        return {
            "symbol": row["symbol"],
            "exchange_timestamp": row["exchange_timestamp"],
            "price": float(row["price"]),
            "quantity": int(row["quantity"]),
            "side": row["side"].lower(),
            "sequence_number": int(row["sequence_number"]),
            "trade_id": row["trade_id"],
        }

    def _parse_quote_row(self, row: dict) -> dict:
        """Parse quote CSV row.

        V2 schema:
        Required fields: symbol, exchange_timestamp, bid_price, ask_price,
                        bid_quantity, ask_quantity, sequence_number
        Optional v2 fields: company_id, qualifiers, venue (mapped to vendor_data)

        V1 schema (legacy):
        Same required fields with simplified structure

        Args:
            row: Raw CSV row

        Returns:
            Parsed quote record (v2 format if schema_version='v2')

        Raises:
            ValueError: If required fields missing or invalid
        """
        required_fields = [
            "symbol",
            "exchange_timestamp",
            "bid_price",
            "ask_price",
            "bid_size",
            "ask_size",
            "sequence_number",
        ]

        missing = [f for f in required_fields if f not in row or not row[f]]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        # Parse timestamp (ISO format string → datetime)
        try:
            timestamp = datetime.fromisoformat(row["exchange_timestamp"].replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {row['exchange_timestamp']}") from e

        # Use v2 builder for v2 schema
        if self.schema_version == "v2":
            # Build vendor_data from ASX-specific fields (if present)
            vendor_data = None
            if any(k in row for k in ["company_id", "qualifiers", "venue"]):
                vendor_data = {}
                if row.get("company_id"):
                    vendor_data["company_id"] = str(row["company_id"])
                if row.get("qualifiers"):
                    vendor_data["qualifiers"] = str(row["qualifiers"])
                if row.get("venue"):
                    vendor_data["venue"] = str(row["venue"])

            # Build v2 quote message
            return build_quote_v2(
                symbol=row["symbol"],
                exchange=self.exchange.upper(),
                asset_class=self.asset_class,
                timestamp=timestamp,
                bid_price=Decimal(str(row["bid_price"])),
                bid_quantity=Decimal(str(row["bid_size"])),
                ask_price=Decimal(str(row["ask_price"])),
                ask_quantity=Decimal(str(row["ask_size"])),
                currency=self.currency,
                message_id=str(uuid.uuid4()),
                source_sequence=int(row["sequence_number"]),
                vendor_data=vendor_data,
            )

        # V1 legacy format
        return {
            "symbol": row["symbol"],
            "exchange_timestamp": row["exchange_timestamp"],
            "bid_price": float(row["bid_price"]),
            "ask_price": float(row["ask_price"]),
            "bid_size": int(row["bid_size"]),
            "ask_size": int(row["ask_size"]),
            "sequence_number": int(row["sequence_number"]),
        }

    def _parse_reference_data_row(self, row: dict) -> dict:
        """Parse reference data CSV row.

        Required fields: company_id, symbol, company_name, sector

        Args:
            row: Raw CSV row

        Returns:
            Parsed reference data record

        Raises:
            ValueError: If required fields missing or invalid
        """
        required_fields = ["company_id", "symbol", "company_name", "sector"]

        missing = [f for f in required_fields if f not in row or not row[f]]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        record = {
            "company_id": row["company_id"],
            "symbol": row["symbol"],
            "company_name": row["company_name"],
            "sector": row["sector"],
        }

        # Optional fields with type conversions
        if row.get("market_cap"):
            record["market_cap"] = int(row["market_cap"])
        if row.get("employees"):
            record["employees"] = int(row["employees"])
        if row.get("founded_year"):
            record["founded_year"] = int(row["founded_year"])

        # Pass through other optional string fields
        optional_string_fields = [
            "industry",
            "headquarters",
            "description",
            "website",
            "last_updated",
        ]
        for field in optional_string_fields:
            if row.get(field):
                record[field] = row[field]

        return record

    def _produce_record(self, record: dict):
        """Produce record to Kafka using appropriate method.

        Args:
            record: Parsed record dict

        Raises:
            Exception: If production fails
        """
        if self.data_type == "trades":
            self.producer.produce_trade(self.asset_class, self.exchange, record)
        elif self.data_type == "quotes":
            self.producer.produce_quote(self.asset_class, self.exchange, record)
        elif self.data_type == "reference_data":
            self.producer.produce_reference_data(self.asset_class, self.exchange, record)
        else:
            raise ValueError(f"Unsupported data_type: {self.data_type}")

    def load_csv(
        self,
        csv_file: Path,
        batch_size: int = 1000,
        flush_interval: int = 100,
        show_progress: bool = True,
    ) -> LoadStats:
        """Load CSV file into Kafka.

        Args:
            csv_file: Path to CSV file
            batch_size: Number of rows to process per batch (for memory efficiency)
            flush_interval: Flush producer every N records
            show_progress: Show progress bar (default: True)

        Returns:
            LoadStats with success/error counts and timing

        Raises:
            FileNotFoundError: If CSV file doesn't exist
        """
        if not csv_file.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_file}")

        stats = LoadStats()
        stats.start_time = datetime.utcnow()

        logger.info(
            "Starting CSV load",
            csv_file=str(csv_file),
            asset_class=self.asset_class,
            exchange=self.exchange,
            data_type=self.data_type,
            batch_size=batch_size,
        )

        # Count total rows for progress bar
        with open(csv_file, encoding="utf-8") as f:
            total_rows = sum(1 for line in f) - 1  # Subtract header

        stats.total_rows = total_rows

        # Progress bar setup
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TextColumn("[cyan]{task.fields[success]} success"),
            TextColumn("[red]{task.fields[errors]} errors"),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console if show_progress else None,
            disable=not show_progress,
        )

        with progress:
            task = progress.add_task(
                f"Loading {csv_file.name}",
                total=total_rows,
                success=0,
                errors=0,
            )

            # Read and process CSV
            with open(csv_file, encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for row_num, row in enumerate(reader, start=2):  # Start at 2 (after header)
                    try:
                        # Parse and validate row
                        record = self._parse_csv_row(row)

                        # Produce to Kafka
                        self._produce_record(record)

                        stats.success_count += 1
                        progress.update(
                            task, advance=1, success=stats.success_count, errors=stats.error_count,
                        )

                        # Flush periodically
                        if stats.success_count % flush_interval == 0:
                            remaining = self.producer.flush(timeout=10.0)
                            if remaining > 0:
                                logger.warning(
                                    f"{remaining} messages not sent after flush",
                                    row_num=row_num,
                                )

                    except ValueError as e:
                        # Invalid record (schema validation failure)
                        stats.error_count += 1
                        progress.update(
                            task, advance=1, success=stats.success_count, errors=stats.error_count,
                        )

                        logger.warning(
                            "Invalid record",
                            row_num=row_num,
                            error=str(e),
                            record=row,
                        )

                        # Write to DLQ
                        self._write_to_dlq(row_num, str(e), row)

                    except Exception as e:
                        # Producer error (Kafka/network issue)
                        stats.error_count += 1
                        progress.update(
                            task, advance=1, success=stats.success_count, errors=stats.error_count,
                        )

                        logger.error(
                            "Failed to produce record",
                            row_num=row_num,
                            error=str(e),
                            record=row,
                        )

                        # Write to DLQ
                        self._write_to_dlq(row_num, str(e), row)

            # Final flush
            remaining = self.producer.flush(timeout=60.0)
            if remaining > 0:
                logger.error(f"{remaining} messages not delivered after final flush!")
                stats.error_count += remaining

        stats.end_time = datetime.utcnow()

        logger.info(
            "CSV load complete",
            total_rows=stats.total_rows,
            success_count=stats.success_count,
            error_count=stats.error_count,
            duration_seconds=stats.duration_seconds,
            throughput=stats.throughput,
        )

        return stats

    def close(self):
        """Close producer and DLQ resources."""
        if self.producer:
            self.producer.flush()
            self.producer.close()

        if self._dlq_writer:
            self._dlq_handle.close()

        logger.info("Batch loader closed")

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        self.close()
        return False


def print_summary(stats: LoadStats):
    """Print summary table to console.

    Args:
        stats: LoadStats from load operation
    """
    table = Table(title="Load Summary", show_header=False)

    table.add_row("Total Rows", str(stats.total_rows))
    table.add_row("[green]Success", f"[green]{stats.success_count}")
    table.add_row("[red]Errors", f"[red]{stats.error_count} ({stats.error_rate:.2f}%)")
    table.add_row("Duration", f"{stats.duration_seconds:.2f} seconds")
    table.add_row("Throughput", f"{stats.throughput:.0f} rows/sec")

    console.print(table)


@app.command()
def load(
    csv: Path = typer.Option(
        ..., help="Path to CSV file", exists=True, file_okay=True, dir_okay=False,
    ),
    asset_class: str = typer.Option(..., help="Asset class (e.g., 'equities', 'crypto')"),
    exchange: str = typer.Option(..., help="Exchange code (e.g., 'asx', 'binance')"),
    data_type: str = typer.Option(..., help="Data type ('trades', 'quotes', 'reference_data')"),
    batch_size: int = typer.Option(1000, help="Batch size for processing"),
    flush_interval: int = typer.Option(100, help="Flush producer every N records"),
    dlq_file: Path | None = typer.Option(None, help="Dead letter queue file for errors"),
    no_progress: bool = typer.Option(False, help="Disable progress bar"),
    schema_version: str = typer.Option("v2", help="Schema version ('v1' or 'v2')"),
    currency: str = typer.Option("USD", help="Currency code (e.g., 'USD', 'AUD')"),
):
    """Load CSV file into Kafka topics.

    Example (v2 schema):
        python -m k2.ingestion.batch_loader load \\
            --csv data/asx_trades.csv \\
            --asset-class equities \\
            --exchange asx \\
            --data-type trades \\
            --currency AUD \\
            --dlq-file errors.csv

    Example (v1 legacy):
        python -m k2.ingestion.batch_loader load \\
            --csv data/asx_trades.csv \\
            --asset-class equities \\
            --exchange asx \\
            --data-type trades \\
            --schema-version v1
    """
    console.print("[cyan]K2 Batch Loader[/cyan]")
    console.print(f"CSV: {csv}")
    console.print(f"Target: {asset_class}.{data_type}.{exchange}")
    console.print(f"Schema: {schema_version} | Currency: {currency}")
    console.print()

    loader = None
    try:
        # Create loader
        loader = BatchLoader(
            asset_class=asset_class,
            exchange=exchange,
            data_type=data_type,
            dlq_file=dlq_file,
            schema_version=schema_version,
            currency=currency,
        )

        # Load CSV
        stats = loader.load_csv(
            csv_file=csv,
            batch_size=batch_size,
            flush_interval=flush_interval,
            show_progress=not no_progress,
        )

        # Print summary
        console.print()
        print_summary(stats)

        # Exit with error code if any failures
        if stats.error_count > 0:
            console.print(f"\n[yellow]Warning: {stats.error_count} records failed[/yellow]")
            if dlq_file:
                console.print(f"[yellow]Check DLQ file: {dlq_file}[/yellow]")
            sys.exit(1)
        else:
            console.print("\n[green]✓ All records loaded successfully![/green]")
            sys.exit(0)

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        logger.error("Batch load failed", error=str(e), exc_info=True)
        sys.exit(1)

    finally:
        if loader:
            loader.close()


def create_loader(
    asset_class: str,
    exchange: str,
    data_type: str,
    producer: MarketDataProducer | None = None,
    dlq_file: Path | None = None,
    schema_version: str = "v2",
    currency: str = "USD",
) -> BatchLoader:
    """Factory function to create a BatchLoader instance.

    Args:
        asset_class: Asset class (e.g., 'equities', 'crypto')
        exchange: Exchange code (e.g., 'asx', 'binance')
        data_type: Data type ('trades', 'quotes', 'reference_data')
        producer: Optional MarketDataProducer instance (creates new if None)
        dlq_file: Optional path to dead letter queue file
        schema_version: Schema version - 'v1' or 'v2' (default: 'v2')
        currency: Currency code (default: 'USD')

    Returns:
        BatchLoader: Configured batch loader instance

    Example (v2 schema):
        >>> loader = create_loader('equities', 'asx', 'trades', currency='AUD')
        >>> stats = loader.load_csv(Path('data/trades.csv'))
        >>> loader.close()

    Example (v1 legacy):
        >>> loader = create_loader('equities', 'asx', 'trades', schema_version='v1')
        >>> stats = loader.load_csv(Path('data/trades.csv'))
        >>> loader.close()
    """
    return BatchLoader(
        asset_class=asset_class,
        exchange=exchange,
        data_type=data_type,
        producer=producer,
        dlq_file=dlq_file,
        schema_version=schema_version,
        currency=currency,
    )


if __name__ == "__main__":
    app()
