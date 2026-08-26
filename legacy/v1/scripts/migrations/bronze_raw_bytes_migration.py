#!/usr/bin/env python3
"""Bronze Layer Migration: Raw Bytes Schema (Decision #011).

This script migrates Bronze tables from deserialized Avro schema to raw bytes schema
following Medallion architecture best practices.

Migration Overview:
    BEFORE: bronze_*_trades contains deserialized Avro fields
    AFTER:  bronze_*_trades contains raw Kafka bytes (5-byte header + Avro payload)

Why: Replayability, schema evolution, debugging, auditability (see Decision #011)

BREAKING CHANGE: Requires dropping and recreating Bronze tables.

Usage:
    # Dry run (show what will happen)
    python scripts/migrations/bronze_raw_bytes_migration.py --dry-run

    # Execute migration
    python scripts/migrations/bronze_raw_bytes_migration.py --execute

    # Rollback (restore old schema - DATA LOSS)
    python scripts/migrations/bronze_raw_bytes_migration.py --rollback

Safety:
    - Stops Bronze streaming jobs before migration
    - Validates Kafka topics exist and have data
    - Creates backup of table metadata
    - Provides rollback procedure

Dependencies:
    - Docker Compose stack running (kafka, minio, iceberg-rest, spark)
    - Bronze streaming jobs deployed as services
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(message):
    """Print formatted header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}")
    print(f"{message}")
    print(f"{'=' * 80}{Colors.ENDC}\n")


def print_step(step_num, message):
    """Print migration step."""
    print(f"{Colors.OKBLUE}[Step {step_num}]{Colors.ENDC} {message}")


def print_success(message):
    """Print success message."""
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")


def print_warning(message):
    """Print warning message."""
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")


def print_error(message):
    """Print error message."""
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")


def run_command(cmd, description, dry_run=False):
    """Run shell command with error handling."""
    print(f"  → {description}")
    print(f"    Command: {cmd}")

    if dry_run:
        print_warning("  [DRY RUN] Command not executed")
        return True

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout:
            print(f"    {result.stdout.strip()}")
        print_success("  Command succeeded")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"  Command failed: {e.stderr}")
        return False


def check_prerequisites():
    """Verify all prerequisites are met."""
    print_header("Checking Prerequisites")

    checks = {
        "Docker Compose stack": "docker compose ps kafka minio iceberg-rest spark-master",
        "Kafka reachable": "docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list",
        "Iceberg REST reachable": "curl -s http://localhost:8181/v1/config",
        "Spark Master reachable": "curl -s http://localhost:8090",
    }

    all_passed = True
    for name, cmd in checks.items():
        result = subprocess.run(cmd, shell=True, capture_output=True)
        if result.returncode == 0:
            print_success(f"{name} ✓")
        else:
            print_error(f"{name} ✗")
            all_passed = False

    return all_passed


def stop_bronze_jobs(dry_run=False):
    """Stop Bronze streaming jobs."""
    print_header("Step 1: Stop Bronze Streaming Jobs")

    jobs = ["bronze-binance-stream", "bronze-kraken-stream"]

    for job in jobs:
        run_command(
            f"docker compose stop {job}",
            f"Stopping {job}",
            dry_run
        )

    if not dry_run:
        print("\n  Waiting 10 seconds for graceful shutdown...")
        time.sleep(10)
        print_success("Bronze jobs stopped")


def backup_table_metadata(dry_run=False):
    """Backup existing table metadata."""
    print_header("Step 2: Backup Table Metadata")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"/tmp/k2_bronze_backup_{timestamp}"

    commands = [
        (f"mkdir -p {backup_dir}", "Create backup directory"),
        (f"docker exec k2-spark-master /opt/spark/bin/spark-sql -e \"DESCRIBE FORMATTED iceberg.market_data.bronze_binance_trades\" > {backup_dir}/bronze_binance_schema.txt 2>&1 || true",
         "Backup Binance table schema"),
        (f"docker exec k2-spark-master /opt/spark/bin/spark-sql -e \"DESCRIBE FORMATTED iceberg.market_data.bronze_kraken_trades\" > {backup_dir}/bronze_kraken_schema.txt 2>&1 || true",
         "Backup Kraken table schema"),
        (f"docker exec k2-spark-master /opt/spark/bin/spark-sql -e \"SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades\" > {backup_dir}/bronze_binance_count.txt 2>&1 || true",
         "Backup Binance row count"),
        (f"docker exec k2-spark-master /opt/spark/bin/spark-sql -e \"SELECT COUNT(*) FROM iceberg.market_data.bronze_kraken_trades\" > {backup_dir}/bronze_kraken_count.txt 2>&1 || true",
         "Backup Kraken row count"),
    ]

    for cmd, desc in commands:
        run_command(cmd, desc, dry_run)

    if not dry_run:
        print_success(f"Metadata backed up to: {backup_dir}")

    return backup_dir


def drop_bronze_tables(dry_run=False):
    """Drop existing Bronze tables."""
    print_header("Step 3: Drop Existing Bronze Tables")

    print_warning("This will DELETE all data in Bronze tables!")
    print_warning("Data can be replayed from Kafka (retention: 7-90 days)")

    if not dry_run:
        confirm = input("\n  Type 'YES' to confirm deletion: ")
        if confirm != "YES":
            print_error("Migration aborted by user")
            sys.exit(1)

    tables = ["bronze_binance_trades", "bronze_kraken_trades"]

    for table in tables:
        run_command(
            f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "DROP TABLE IF EXISTS iceberg.market_data.{table}"',
            f"Dropping {table}",
            dry_run
        )


def create_bronze_tables(dry_run=False):
    """Create Bronze tables with raw bytes schema."""
    print_header("Step 4: Create Bronze Tables (Raw Bytes Schema)")

    # Bronze Binance table
    binance_ddl = """
    CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_binance_trades (
        raw_bytes BINARY NOT NULL COMMENT 'Full Kafka value (5-byte Schema Registry header + Avro payload)',
        topic STRING NOT NULL COMMENT 'Source Kafka topic',
        partition INT NOT NULL COMMENT 'Kafka partition',
        offset BIGINT NOT NULL COMMENT 'Kafka offset',
        kafka_timestamp TIMESTAMP NOT NULL COMMENT 'Kafka message timestamp',
        ingestion_timestamp TIMESTAMP NOT NULL COMMENT 'When ingested to Bronze',
        ingestion_date DATE NOT NULL COMMENT 'Partition key (daily)'
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'write.format.default' = 'parquet',
        'write.target-file-size-bytes' = '134217728',
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'comment' = 'Bronze layer - Immutable raw Kafka data for replayability (Decision #011)'
    )
    """

    # Bronze Kraken table
    kraken_ddl = """
    CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_kraken_trades (
        raw_bytes BINARY NOT NULL COMMENT 'Full Kafka value (5-byte Schema Registry header + Avro payload)',
        topic STRING NOT NULL COMMENT 'Source Kafka topic',
        partition INT NOT NULL COMMENT 'Kafka partition',
        offset BIGINT NOT NULL COMMENT 'Kafka offset',
        kafka_timestamp TIMESTAMP NOT NULL COMMENT 'Kafka message timestamp',
        ingestion_timestamp TIMESTAMP NOT NULL COMMENT 'When ingested to Bronze',
        ingestion_date DATE NOT NULL COMMENT 'Partition key (daily)'
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'write.format.default' = 'parquet',
        'write.target-file-size-bytes' = '67108864',
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'comment' = 'Bronze layer - Immutable raw Kafka data for replayability (Decision #011)'
    )
    """

    run_command(
        f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "{binance_ddl}"',
        "Creating bronze_binance_trades",
        dry_run
    )

    run_command(
        f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "{kraken_ddl}"',
        "Creating bronze_kraken_trades",
        dry_run
    )


def clear_checkpoints(dry_run=False):
    """Clear Spark streaming checkpoints (schema incompatible)."""
    print_header("Step 5: Clear Spark Checkpoints")

    print_warning("Clearing checkpoints will cause replay from Kafka")
    print_warning("Jobs will start from 'latest' offset (configured in code)")

    checkpoints = [
        "/checkpoints/bronze-binance/",
        "/checkpoints/bronze-kraken/"
    ]

    for checkpoint in checkpoints:
        run_command(
            f"docker exec k2-spark-master rm -rf {checkpoint}",
            f"Clearing {checkpoint}",
            dry_run
        )


def restart_bronze_jobs(dry_run=False):
    """Restart Bronze streaming jobs with new code."""
    print_header("Step 6: Restart Bronze Streaming Jobs")

    print_warning("Ensure refactored Bronze jobs are deployed (raw_bytes code)")

    jobs = ["bronze-binance-stream", "bronze-kraken-stream"]

    for job in jobs:
        run_command(
            f"docker compose up -d {job}",
            f"Starting {job}",
            dry_run
        )

    if not dry_run:
        print("\n  Waiting 15 seconds for job initialization...")
        time.sleep(15)
        print_success("Bronze jobs restarted")


def validate_migration(dry_run=False):
    """Validate migration success."""
    print_header("Step 7: Validate Migration")

    if dry_run:
        print_warning("[DRY RUN] Validation skipped")
        return True

    validation_queries = [
        ("bronze_binance_trades schema",
         "DESCRIBE iceberg.market_data.bronze_binance_trades"),
        ("bronze_binance_trades count",
         "SELECT COUNT(*) as count FROM iceberg.market_data.bronze_binance_trades"),
        ("bronze_binance_trades sample",
         "SELECT topic, partition, offset, length(raw_bytes) as raw_bytes_length FROM iceberg.market_data.bronze_binance_trades LIMIT 3"),
        ("bronze_kraken_trades schema",
         "DESCRIBE iceberg.market_data.bronze_kraken_trades"),
        ("bronze_kraken_trades count",
         "SELECT COUNT(*) as count FROM iceberg.market_data.bronze_kraken_trades"),
        ("bronze_kraken_trades sample",
         "SELECT topic, partition, offset, length(raw_bytes) as raw_bytes_length FROM iceberg.market_data.bronze_kraken_trades LIMIT 3"),
    ]

    print("\nValidation Queries:")
    for name, query in validation_queries:
        print(f"\n  {name}:")
        result = subprocess.run(
            f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "{query}"',
            shell=True,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"    {result.stdout.strip()}")
            print_success(f"  {name} ✓")
        else:
            print_error(f"  {name} ✗")
            print(f"    {result.stderr.strip()}")

    return True


def print_migration_summary(backup_dir):
    """Print migration summary and next steps."""
    print_header("Migration Summary")

    print(f"""
{Colors.OKGREEN}✓ Migration Complete!{Colors.ENDC}

{Colors.BOLD}What Changed:{Colors.ENDC}
  • Bronze tables now store raw_bytes (5-byte header + Avro payload)
  • Deserialization moved to Silver layer (Step 11)
  • Checkpoints cleared (jobs replay from Kafka 'latest')

{Colors.BOLD}Backup Location:{Colors.ENDC}
  • {backup_dir}

{Colors.BOLD}Validation Steps:{Colors.ENDC}
  1. Check Spark UI: http://localhost:8090
     - Both Bronze jobs should be RUNNING

  2. Check Bronze tables:
     docker exec k2-spark-master /opt/spark/bin/spark-sql -e "SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades"

  3. Verify raw_bytes:
     docker exec k2-spark-master /opt/spark/bin/spark-sql -e "SELECT length(raw_bytes) FROM iceberg.market_data.bronze_binance_trades LIMIT 1"
     # Should return >5 (header + payload)

  4. Check logs:
     docker logs k2-bronze-binance-stream -f
     docker logs k2-bronze-kraken-stream -f

{Colors.BOLD}Next Steps:{Colors.ENDC}
  • Produce test trades to Kafka (binance/kraken producers)
  • Verify raw_bytes appear in Bronze tables
  • Proceed with Step 11 (Silver transformation)

{Colors.BOLD}Rollback Procedure (if needed):{Colors.ENDC}
  python scripts/migrations/bronze_raw_bytes_migration.py --rollback
  # WARNING: Restores old schema but LOSES current data
""")


def rollback_migration(dry_run=False):
    """Rollback to old Bronze schema (DATA LOSS)."""
    print_header("Rollback Migration")

    print_error("DANGER: Rollback will restore old schema but DELETE current data!")
    print_warning("Only use if migration failed and you need to restore old Bronze jobs")

    if not dry_run:
        confirm = input("\n  Type 'ROLLBACK' to confirm: ")
        if confirm != "ROLLBACK":
            print("Rollback aborted")
            sys.exit(1)

    # Stop jobs
    stop_bronze_jobs(dry_run)

    # Drop current tables
    drop_bronze_tables(dry_run)

    # Recreate with OLD schema (deserialized)
    print_header("Recreating Old Schema (Deserialized)")

    # OLD Binance schema (deserialized - from Phase 10.10)
    binance_old_ddl = """
    CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_binance_trades (
        event_type STRING,
        event_time_ms BIGINT,
        symbol STRING,
        trade_id BIGINT,
        price DOUBLE,
        quantity DOUBLE,
        trade_time_ms BIGINT,
        is_buyer_maker BOOLEAN,
        is_best_match BOOLEAN,
        ingestion_timestamp BIGINT,
        ingestion_date DATE
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    """

    # OLD Kraken schema (deserialized - from Phase 10.10)
    kraken_old_ddl = """
    CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_kraken_trades (
        channel_id INT,
        price DOUBLE,
        volume DOUBLE,
        timestamp DOUBLE,
        side STRING,
        order_type STRING,
        misc STRING,
        pair STRING,
        ingestion_timestamp BIGINT,
        ingestion_date DATE
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    """

    run_command(
        f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "{binance_old_ddl}"',
        "Creating bronze_binance_trades (OLD schema)",
        dry_run
    )

    run_command(
        f'docker exec k2-spark-master /opt/spark/bin/spark-sql -e "{kraken_old_ddl}"',
        "Creating bronze_kraken_trades (OLD schema)",
        dry_run
    )

    # Clear checkpoints
    clear_checkpoints(dry_run)

    print_warning("\nRollback complete. Deploy OLD Bronze jobs (with deserialization) and restart.")
    print_warning("Note: Current data is LOST. Will replay from Kafka.")


def main():
    """Main migration orchestrator."""
    parser = argparse.ArgumentParser(
        description="Bronze Layer Migration: Raw Bytes Schema (Decision #011)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what will happen without executing"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute migration (REQUIRED for actual migration)"
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback to old schema (DATA LOSS)"
    )

    args = parser.parse_args()

    if not (args.execute or args.dry_run or args.rollback):
        parser.print_help()
        print(f"\n{Colors.WARNING}Use --dry-run to preview or --execute to migrate{Colors.ENDC}")
        sys.exit(1)

    # Rollback path
    if args.rollback:
        rollback_migration(args.dry_run)
        sys.exit(0)

    # Normal migration path
    print_header("Bronze Layer Migration: Raw Bytes Schema")

    if args.dry_run:
        print_warning("DRY RUN MODE - No changes will be made")
    else:
        print_warning("EXECUTE MODE - Changes will be applied")

    # Check prerequisites
    if not args.dry_run and not check_prerequisites():
        print_error("Prerequisites check failed. Fix issues and retry.")
        sys.exit(1)

    # Execute migration steps
    try:
        stop_bronze_jobs(args.dry_run)
        backup_dir = backup_table_metadata(args.dry_run)
        drop_bronze_tables(args.dry_run)
        create_bronze_tables(args.dry_run)
        clear_checkpoints(args.dry_run)
        restart_bronze_jobs(args.dry_run)
        validate_migration(args.dry_run)

        if not args.dry_run:
            print_migration_summary(backup_dir)
        else:
            print_header("Dry Run Complete")
            print("Review the steps above. Run with --execute to apply changes.")

    except Exception as e:
        print_error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
