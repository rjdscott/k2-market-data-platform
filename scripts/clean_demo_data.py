"""Clean all demo data from K2 platform while keeping Docker containers running.

This script removes:
- All data from Iceberg tables (trades_v2, quotes_v2)
- All Kafka messages from topics
- Keeps Docker containers, schemas, and table definitions intact

Use this to get a clean slate without restarting the entire infrastructure.
"""

import subprocess
import sys

from pyiceberg.catalog import load_catalog

from k2.common.config import config


def clean_iceberg_tables():
    """Delete all data from Iceberg tables."""
    print("=" * 60)
    print("CLEANING ICEBERG TABLES")
    print("=" * 60)

    try:
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

        # Clean trades_v2 table
        print("\n📊 Cleaning market_data.trades_v2...")
        try:
            table = catalog.load_table("market_data.trades_v2")
            snapshot_before = table.current_snapshot()

            if snapshot_before:
                print(f"  Current snapshot: {snapshot_before.snapshot_id}")
                print("  Deleting all rows...")
                table.delete("true")  # Delete all rows
                print("  ✅ Trades table cleaned")
            else:
                print("  ℹ️ Table already empty")

        except Exception as e:
            print(f"  ⚠️ Could not clean trades table: {e}")

        # Clean quotes_v2 table
        print("\n📊 Cleaning market_data.quotes_v2...")
        try:
            table = catalog.load_table("market_data.quotes_v2")
            snapshot_before = table.current_snapshot()

            if snapshot_before:
                print(f"  Current snapshot: {snapshot_before.snapshot_id}")
                print("  Deleting all rows...")
                table.delete("true")  # Delete all rows
                print("  ✅ Quotes table cleaned")
            else:
                print("  ℹ️ Table already empty")

        except Exception as e:
            print(f"  ⚠️ Could not clean quotes table: {e}")

        print("\n✅ Iceberg tables cleaned successfully")
        return True

    except Exception as e:
        print(f"\n❌ Error cleaning Iceberg tables: {e}")
        return False


def clean_kafka_topics():
    """Delete all messages from Kafka topics."""
    print("\n" + "=" * 60)
    print("CLEANING KAFKA TOPICS")
    print("=" * 60)

    topics = [
        "market.crypto.trades.binance",
        # Add other topics as needed
    ]

    for topic in topics:
        print(f"\n📬 Cleaning topic: {topic}")
        try:
            # Use kafka-delete-records to set offset to latest (effectively deletes all)
            # Alternative: kafka-topics --delete and recreate (more aggressive)
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "k2-kafka",
                    "kafka-topics",
                    "--delete",
                    "--topic",
                    topic,
                    "--bootstrap-server",
                    "localhost:9092",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                print(f"  ✅ Topic {topic} deleted")

                # Recreate the topic
                print(f"  📬 Recreating topic: {topic}")
                create_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "k2-kafka",
                        "kafka-topics",
                        "--create",
                        "--topic",
                        topic,
                        "--bootstrap-server",
                        "localhost:9092",
                        "--partitions",
                        "32",
                        "--replication-factor",
                        "1",
                        "--config",
                        "compression.type=zstd",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if create_result.returncode == 0:
                    print(f"  ✅ Topic {topic} recreated")
                else:
                    print(f"  ⚠️ Could not recreate topic: {create_result.stderr}")

            else:
                print(f"  ⚠️ Could not delete topic: {result.stderr}")

        except subprocess.TimeoutExpired:
            print(f"  ⚠️ Timeout deleting topic {topic}")
        except Exception as e:
            print(f"  ⚠️ Error cleaning topic {topic}: {e}")

    print("\n✅ Kafka topics cleaned")


def verify_cleanup():
    """Verify that data has been cleaned."""
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    # Check Iceberg data via API
    print("\n🔍 Checking remaining data...")
    try:
        import requests

        response = requests.get(
            "http://localhost:8000/v1/symbols",
            headers={"X-API-Key": "k2-dev-api-key-2026"},
            timeout=5,
        )

        if response.status_code == 200:
            data = response.json()
            symbols = data.get("data", [])
            if symbols:
                print(f"  ⚠️ Still have symbols: {symbols}")
            else:
                print("  ✅ No symbols remaining (clean)")
        else:
            print(f"  ⚠️ Could not verify: HTTP {response.status_code}")

    except Exception as e:
        print(f"  ⚠️ Could not verify via API: {e}")


def main():
    """Main cleanup routine."""
    print("\n🧹 K2 DEMO DATA CLEANUP")
    print("=" * 60)
    print("This will remove ALL data from Iceberg and Kafka")
    print("Docker containers will remain running")
    print("=" * 60)

    # Confirm with user
    response = input("\n⚠️  Continue with cleanup? [y/N]: ")
    if response.lower() not in ["y", "yes"]:
        print("❌ Cleanup cancelled")
        sys.exit(0)

    # Clean Iceberg tables
    iceberg_success = clean_iceberg_tables()

    # Clean Kafka topics
    clean_kafka_topics()

    # Verify cleanup
    verify_cleanup()

    print("\n" + "=" * 60)
    if iceberg_success:
        print("✅ CLEANUP COMPLETE")
        print("\nAll demo data removed. Docker containers still running.")
        print("Ready for fresh data ingestion.")
    else:
        print("⚠️ CLEANUP COMPLETED WITH WARNINGS")
        print("\nSome data may not have been cleaned. Check logs above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
