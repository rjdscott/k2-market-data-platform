#!/usr/bin/env python3
"""Register raw Avro schemas with Schema Registry.

This script registers the raw exchange schemas (Binance and Kraken) with Schema Registry
before starting producers. The schemas define the format for raw data in Bronze layer.

Usage:
    python scripts/register_raw_schemas.py
"""

import sys
from pathlib import Path

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema

from k2.common.config import config


def register_schema(client: SchemaRegistryClient, subject: str, schema_path: Path) -> int:
    """Register Avro schema with Schema Registry.

    Args:
        client: Schema Registry client
        subject: Schema subject name
        schema_path: Path to .avsc file

    Returns:
        Schema ID
    """
    print(f"\nRegistering schema: {subject}")
    print(f"  Schema file: {schema_path}")

    # Load schema
    with open(schema_path) as f:
        schema_str = f.read()

    # Validate JSON
    schema_json = json.loads(schema_str)
    print(f"  Schema name: {schema_json['name']}")
    print(f"  Fields: {len(schema_json['fields'])}")

    # Register with Schema Registry
    schema = Schema(schema_str, schema_type="AVRO")
    schema_id = client.register_schema(subject, schema)

    print(f"  ✓ Registered with ID: {schema_id}")
    return schema_id


def main():
    """Main entry point."""
    print("=" * 70)
    print("Register Raw Schemas with Schema Registry")
    print("=" * 70)
    print(f"Schema Registry: {config.kafka.schema_registry_url}")
    print("")

    # Initialize Schema Registry client
    client = SchemaRegistryClient({"url": config.kafka.schema_registry_url})

    # Schema paths
    schemas_dir = Path(__file__).parent.parent / "src" / "k2" / "schemas"

    schemas = [
        {
            "subject": "market.crypto.trades.binance.raw-value",
            "path": schemas_dir / "binance_raw_trade.avsc",
        },
        {
            "subject": "market.crypto.trades.kraken.raw-value",
            "path": schemas_dir / "kraken_raw_trade.avsc",
        },
    ]

    # Register schemas
    registered = []
    for schema_def in schemas:
        try:
            schema_id = register_schema(client, schema_def["subject"], schema_def["path"])
            registered.append((schema_def["subject"], schema_id))
        except Exception as e:
            print(f"  ✗ Failed to register: {e}")
            return 1

    # Summary
    print("\n" + "=" * 70)
    print(f"Summary: {len(registered)}/{len(schemas)} schemas registered")
    print("=" * 70)
    for subject, schema_id in registered:
        print(f"  • {subject}: ID {schema_id}")

    print("\n✓ Ready to start raw producers")
    print("=" * 70)
    print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
