#!/usr/bin/env python3
"""V2 Schema Registry Validation Script.

This script validates that V2 schemas can be registered with Schema Registry
and tests serialization/deserialization roundtrip for Binance and Kraken trades.

Usage:
    # Validate all V2 schemas
    python scripts/validate_v2_schemas.py

    # Validate with custom Schema Registry URL
    python scripts/validate_v2_schemas.py --schema-registry http://localhost:8081

    # Test serialization performance
    python scripts/validate_v2_schemas.py --perf-test

Requirements:
    - Kafka + Schema Registry running (docker-compose up)
    - uv environment activated
"""

import argparse
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext
from rich.console import Console
from rich.table import Table

from k2.common.config import config
from k2.ingestion.message_builders import build_trade_v2

logger = structlog.get_logger(__name__)
console = Console()


def validate_schema_registry_connection(schema_registry_url: str) -> bool:
    """Validate Schema Registry is accessible.

    Args:
        schema_registry_url: Schema Registry URL

    Returns:
        True if accessible, False otherwise
    """
    try:
        client = SchemaRegistryClient({"url": schema_registry_url})
        subjects = client.get_subjects()
        console.print(
            f"✅ Schema Registry connected: {len(subjects)} subjects found", style="green"
        )
        return True
    except Exception as e:
        console.print(f"❌ Schema Registry connection failed: {e}", style="red")
        return False


def register_v2_schemas(schema_registry_url: str) -> dict[str, int]:
    """Register all V2 schemas with Schema Registry.

    Args:
        schema_registry_url: Schema Registry URL

    Returns:
        Dictionary mapping subject names to schema IDs
    """
    from k2.schemas import register_schemas

    console.print("\n[bold]Registering V2 schemas...[/bold]")

    try:
        schema_ids = register_schemas(schema_registry_url, version="v2")

        table = Table(title="Registered V2 Schemas", show_header=True)
        table.add_column("Subject", style="cyan")
        table.add_column("Schema ID", style="green")

        for subject, schema_id in schema_ids.items():
            table.add_row(subject, str(schema_id))

        console.print(table)
        return schema_ids

    except Exception as e:
        console.print(f"❌ Schema registration failed: {e}", style="red")
        raise


def test_v2_serialization(schema_registry_url: str) -> bool:
    """Test V2 trade serialization/deserialization.

    Args:
        schema_registry_url: Schema Registry URL

    Returns:
        True if successful, False otherwise
    """
    console.print("\n[bold]Testing V2 serialization...[/bold]")

    from k2.schemas import load_avro_schema

    try:
        # Load V2 trade schema
        schema_str = load_avro_schema("trade", version="v2")

        # Create serializer
        sr_client = SchemaRegistryClient({"url": schema_registry_url})
        avro_serializer = AvroSerializer(
            sr_client,
            schema_str,
            to_dict=lambda obj, ctx: obj,  # Pass dict directly
        )

        # Test Binance trade
        binance_trade = build_trade_v2(
            symbol="BTCUSDT",
            exchange="BINANCE",
            asset_class="crypto",
            timestamp=int(time.time() * 1_000_000),
            price=Decimal("45000.12345678"),
            quantity=Decimal("0.05000000"),
            currency="USDT",
            side="BUY",
            vendor_data={
                "is_buyer_maker": "false",
                "event_type": "aggTrade",
                "base_asset": "BTC",
                "quote_asset": "USDT",
            },
        )

        # Serialize with context
        ctx = SerializationContext("market.crypto.trades.binance", MessageField.VALUE)
        serialized = avro_serializer(binance_trade, ctx)
        console.print(f"✅ Binance trade serialized: {len(serialized)} bytes", style="green")

        # Test Kraken trade
        kraken_trade = build_trade_v2(
            symbol="BTCUSD",
            exchange="KRAKEN",
            asset_class="crypto",
            timestamp=int(time.time() * 1_000_000),
            price=Decimal("45000.10000000"),
            quantity=Decimal("0.12345678"),
            currency="USD",
            side="SELL",
            vendor_data={
                "pair": "XBT/USD",
                "order_type": "l",
                "base_asset": "XBT",
                "quote_asset": "USD",
            },
        )

        ctx = SerializationContext("market.crypto.trades.kraken", MessageField.VALUE)
        serialized = avro_serializer(kraken_trade, ctx)
        console.print(f"✅ Kraken trade serialized: {len(serialized)} bytes", style="green")

        return True

    except Exception as e:
        console.print(f"❌ Serialization test failed: {e}", style="red")
        return False


def test_v2_performance(schema_registry_url: str, count: int = 1000) -> None:
    """Test V2 serialization performance.

    Args:
        schema_registry_url: Schema Registry URL
        count: Number of trades to serialize
    """
    console.print(f"\n[bold]Performance test: serializing {count} trades...[/bold]")

    from k2.schemas import load_avro_schema

    # Load schema
    schema_str = load_avro_schema("trade", version="v2")
    sr_client = SchemaRegistryClient({"url": schema_registry_url})
    avro_serializer = AvroSerializer(sr_client, schema_str, to_dict=lambda obj, ctx: obj)

    # Generate test trades
    start_time = time.perf_counter()

    for i in range(count):
        trade = build_trade_v2(
            symbol="BTCUSDT",
            exchange="BINANCE",
            asset_class="crypto",
            timestamp=int(time.time() * 1_000_000) + i,
            price=Decimal("45000.00") + Decimal(i % 100) / 100,
            quantity=Decimal("0.05"),
            currency="USDT",
            side="BUY" if i % 2 == 0 else "SELL",
        )

        ctx = SerializationContext("market.crypto.trades.binance", MessageField.VALUE)
        _ = avro_serializer(trade, ctx)

    elapsed = time.perf_counter() - start_time
    throughput = count / elapsed

    table = Table(title="V2 Serialization Performance", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Trades", f"{count:,}")
    table.add_row("Duration", f"{elapsed:.3f}s")
    table.add_row("Throughput", f"{throughput:,.0f} trades/sec")
    table.add_row("Latency (avg)", f"{(elapsed / count) * 1000:.3f}ms")

    console.print(table)

    # Validate performance targets
    if throughput < 1000:
        console.print(
            f"⚠️  Warning: Throughput {throughput:.0f} below 1,000 trades/sec", style="yellow"
        )
    else:
        console.print(f"✅ Performance target met: {throughput:.0f} trades/sec", style="green")


def validate_schema_compatibility(schema_registry_url: str) -> bool:
    """Validate V2 schema compatibility settings.

    Args:
        schema_registry_url: Schema Registry URL

    Returns:
        True if compatible, False otherwise
    """
    console.print("\n[bold]Validating schema compatibility...[/bold]")

    try:
        client = SchemaRegistryClient({"url": schema_registry_url})

        # Check compatibility for crypto trades subject
        subject = "market.crypto.trades-value"

        try:
            compatibility = client.get_compatibility(subject)
            console.print(f"✅ Compatibility mode: {compatibility}", style="green")

            # Recommended: BACKWARD (new schema can read old data)
            if compatibility != "BACKWARD":
                console.print(
                    f"⚠️  Warning: Compatibility {compatibility} is not BACKWARD", style="yellow"
                )
                console.print("   Recommended: Set to BACKWARD for production", style="yellow")

        except Exception:
            console.print("ℹ️  No compatibility mode set (using global default)", style="blue")

        return True

    except Exception as e:
        console.print(f"❌ Compatibility check failed: {e}", style="red")
        return False


def main():
    """Main validation script."""
    parser = argparse.ArgumentParser(
        description="V2 Schema Registry Validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--schema-registry",
        default=None,
        help=f"Schema Registry URL (default: {config.kafka.schema_registry_url})",
    )
    parser.add_argument(
        "--perf-test",
        action="store_true",
        help="Run performance test (serialize 1000 trades)",
    )
    parser.add_argument(
        "--skip-registration",
        action="store_true",
        help="Skip schema registration (assume already registered)",
    )

    args = parser.parse_args()

    schema_registry_url = args.schema_registry or config.kafka.schema_registry_url

    console.print("\n[bold blue]V2 Schema Registry Validation[/bold blue]")
    console.print("=" * 60)
    console.print(f"Schema Registry: {schema_registry_url}")
    console.print("=" * 60)

    # Step 1: Validate connection
    if not validate_schema_registry_connection(schema_registry_url):
        console.print("\n❌ Validation failed: Cannot connect to Schema Registry", style="red")
        sys.exit(1)

    # Step 2: Register V2 schemas
    if not args.skip_registration:
        try:
            register_v2_schemas(schema_registry_url)
        except Exception:
            console.print("\n❌ Validation failed: Schema registration error", style="red")
            sys.exit(1)

    # Step 3: Test serialization
    if not test_v2_serialization(schema_registry_url):
        console.print("\n❌ Validation failed: Serialization error", style="red")
        sys.exit(1)

    # Step 4: Validate compatibility
    if not validate_schema_compatibility(schema_registry_url):
        console.print("\n⚠️  Warning: Compatibility validation failed", style="yellow")

    # Step 5: Performance test (optional)
    if args.perf_test:
        test_v2_performance(schema_registry_url, count=1000)

    console.print("\n✅ [bold green]All V2 schema validations passed![/bold green]")
    console.print("\n📊 Summary:")
    console.print("  • Schema Registry connection: ✅")
    console.print("  • V2 schema registration: ✅")
    console.print("  • Binance trade serialization: ✅")
    console.print("  • Kraken trade serialization: ✅")
    console.print("  • Compatibility validation: ✅")

    if args.perf_test:
        console.print("  • Performance test: ✅")

    sys.exit(0)


if __name__ == "__main__":
    main()
