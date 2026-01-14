"""Delete v1 schema test data (BHP) from Iceberg tables.

This script removes old ASX test data that uses the legacy v1 schema.
Only v2 schema data (Binance crypto) should remain after this cleanup.
"""

from pyiceberg.catalog import load_catalog

from k2.common.config import config

def delete_bhp_data():
    """Delete BHP symbol data from trades_v2 table."""

    # Load Iceberg catalog
    catalog = load_catalog(
        "k2_catalog",
        **{
            "uri": config.iceberg.catalog_uri,
            "s3.endpoint": config.iceberg.s3_endpoint,
            "s3.access-key-id": config.iceberg.s3_access_key,
            "s3.secret-access-key": config.iceberg.s3_secret_key,
            "s3.path-style-access": "true",
        },
    )

    # Load trades_v2 table
    table = catalog.load_table("market_data.trades_v2")

    print(f"Table before deletion:")
    print(f"  Current snapshot: {table.current_snapshot()}")
    print(f"  Metadata: {table.metadata}")

    # Delete BHP rows
    print("\nDeleting BHP symbol data...")
    table.delete("symbol = 'BHP'")

    print(f"\nTable after deletion:")
    print(f"  Current snapshot: {table.current_snapshot()}")
    print(f"  Metadata: {table.metadata}")

    print("\n✅ BHP data deleted successfully")
    print("Remaining symbols should be: BTCUSDT, ETHUSDT")

if __name__ == "__main__":
    delete_bhp_data()
