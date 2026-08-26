#!/usr/bin/env python3
"""Reset K2 Market Data Platform demo environment.

Provides fine-grained control over resetting infrastructure components
with support for preserving specific data via flags.

Usage:
    python scripts/reset_demo.py                    # Full reset with confirmation
    python scripts/reset_demo.py --force            # Skip confirmation
    python scripts/reset_demo.py --dry-run          # Preview operations
    python scripts/reset_demo.py --keep-metrics     # Preserve Prometheus/Grafana
    python scripts/reset_demo.py --keep-kafka       # Preserve Kafka messages
    python scripts/reset_demo.py --keep-iceberg     # Preserve Iceberg data
    python scripts/reset_demo.py --no-reload        # Skip sample data reload
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import boto3
import structlog
import typer
from botocore.exceptions import ClientError
from confluent_kafka import TopicPartition
from confluent_kafka.admin import AdminClient, KafkaException
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add src to path for k2 imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.kafka import get_topic_builder
from k2.storage.catalog import IcebergCatalogManager

logger = structlog.get_logger()
console = Console()
app = typer.Typer(help="Reset K2 demo environment")

# Project root for docker-compose commands
PROJECT_ROOT = Path(__file__).parent.parent


@dataclass
class ResetConfig:
    """Configuration for reset operations."""

    keep_metrics: bool = False
    keep_kafka: bool = False
    keep_iceberg: bool = False
    reload_data: bool = True
    dry_run: bool = False
    force: bool = False

    # Infrastructure endpoints
    kafka_bootstrap: str = "localhost:9092"
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "admin"
    minio_secret_key: str = "password"
    iceberg_catalog_uri: str = "http://localhost:8181"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "iceberg"
    postgres_password: str = "iceberg"
    postgres_db: str = "iceberg_catalog"


def get_kafka_topics() -> list[str]:
    """Get list of K2 topics from configuration."""
    try:
        builder = get_topic_builder()
        return builder.list_all_topics()
    except Exception as e:
        logger.warning("Could not load topic config, using defaults", error=str(e))
        return [
            "market.equities.trades.asx",
            "market.equities.quotes.asx",
            "market.equities.reference_data.asx",
            "market.crypto.trades.binance",
            "market.crypto.quotes.binance",
            "market.crypto.reference_data.binance",
        ]


def purge_kafka_topic(admin: AdminClient, topic: str, dry_run: bool = False) -> dict:
    """Purge all messages from a Kafka topic without recreation.

    Uses delete_records to clear messages while preserving topic configuration.
    """
    result = {"topic": topic, "partitions_purged": 0, "status": "pending"}

    if dry_run:
        result["status"] = "would_purge"
        return result

    try:
        # Get topic metadata
        metadata = admin.list_topics(topic=topic, timeout=10)
        if topic not in metadata.topics:
            result["status"] = "not_found"
            return result

        topic_metadata = metadata.topics[topic]
        partitions = topic_metadata.partitions

        if not partitions:
            result["status"] = "no_partitions"
            return result

        # For each partition, delete all records up to the high watermark
        # We need to use the Consumer API to get watermarks
        from confluent_kafka import Consumer

        consumer = Consumer(
            {
                "bootstrap.servers": "localhost:9092",
                "group.id": "k2-reset-temp",
                "auto.offset.reset": "earliest",
            }
        )

        delete_records = {}
        for partition_id in partitions.keys():
            tp = TopicPartition(topic, partition_id)
            try:
                low, high = consumer.get_watermark_offsets(tp, timeout=5.0)
                if high > 0:
                    delete_records[tp] = high
            except Exception as e:
                logger.debug(f"Could not get watermark for {topic}:{partition_id}", error=str(e))

        consumer.close()

        if delete_records:
            # Delete records up to high watermark
            futures = admin.delete_records(delete_records)
            for tp, future in futures.items():
                try:
                    future.result(timeout=10)
                    result["partitions_purged"] += 1
                except KafkaException as e:
                    logger.warning(
                        f"Failed to purge partition {tp.partition}",
                        topic=topic,
                        error=str(e),
                    )

        result["status"] = "purged"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error("Failed to purge topic", topic=topic, error=str(e))

    return result


def reset_kafka(cfg: ResetConfig) -> list[dict]:
    """Reset all Kafka topics by purging messages."""
    logger.info("Resetting Kafka topics", dry_run=cfg.dry_run)

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would purge Kafka topics")

    admin = AdminClient({"bootstrap.servers": cfg.kafka_bootstrap})
    topics = get_kafka_topics()

    results = []
    for topic in topics:
        result = purge_kafka_topic(admin, topic, cfg.dry_run)
        results.append(result)

        if cfg.dry_run:
            console.print(f"  [dim]Would purge:[/dim] {topic}")
        else:
            if result["status"] == "purged":
                status = "[green]purged[/green]"
            elif result["status"] == "not_found":
                status = "[yellow]not found[/yellow]"
            else:
                status = f"[red]{result['status']}[/red]"
            console.print(f"  {status}: {topic} ({result['partitions_purged']} partitions)")

    return results


def reset_iceberg_tables(cfg: ResetConfig) -> list[dict]:
    """Drop and recreate Iceberg tables."""
    logger.info("Resetting Iceberg tables", dry_run=cfg.dry_run)

    tables = [
        ("market_data", "trades"),
        ("market_data", "quotes"),
    ]

    results = []

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would drop Iceberg tables")
        for namespace, table_name in tables:
            console.print(f"  [dim]Would drop:[/dim] {namespace}.{table_name}")
            results.append({"table": f"{namespace}.{table_name}", "status": "would_drop"})
        return results

    try:
        manager = IcebergCatalogManager(
            catalog_uri=cfg.iceberg_catalog_uri,
            s3_endpoint=cfg.minio_endpoint,
            s3_access_key=cfg.minio_access_key,
            s3_secret_key=cfg.minio_secret_key,
        )

        for namespace, table_name in tables:
            table_id = f"{namespace}.{table_name}"
            try:
                if manager.table_exists(namespace, table_name):
                    manager.drop_table(namespace, table_name, purge=True)
                    console.print(f"  [green]Dropped:[/green] {table_id}")
                    results.append({"table": table_id, "status": "dropped"})
                else:
                    console.print(f"  [dim]Not found:[/dim] {table_id}")
                    results.append({"table": table_id, "status": "not_found"})
            except Exception as e:
                console.print(f"  [red]Error:[/red] {table_id} - {e}")
                results.append({"table": table_id, "status": "error", "error": str(e)})

    except Exception as e:
        logger.error("Failed to connect to Iceberg catalog", error=str(e))
        console.print(f"  [red]Connection error:[/red] {e}")
        results.append({"table": "all", "status": "connection_error", "error": str(e)})

    return results


def clear_minio_warehouse(cfg: ResetConfig) -> dict:
    """Clear all objects in MinIO warehouse/ bucket."""
    logger.info("Clearing MinIO warehouse bucket", dry_run=cfg.dry_run)

    result = {"objects_deleted": 0, "status": "pending"}

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would clear MinIO warehouse/")
        result["status"] = "would_clear"
        return result

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=cfg.minio_endpoint,
            aws_access_key_id=cfg.minio_access_key,
            aws_secret_access_key=cfg.minio_secret_key,
        )

        # List all objects in warehouse bucket
        paginator = s3.get_paginator("list_objects_v2")
        objects_to_delete = []

        for page in paginator.paginate(Bucket="warehouse"):
            if "Contents" in page:
                for obj in page["Contents"]:
                    objects_to_delete.append({"Key": obj["Key"]})

        if objects_to_delete:
            # Delete in batches of 1000 (S3 limit)
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                s3.delete_objects(Bucket="warehouse", Delete={"Objects": batch})
                result["objects_deleted"] += len(batch)

            console.print(
                f"  [green]Cleared:[/green] {result['objects_deleted']} objects from warehouse/"
            )
            result["status"] = "cleared"
        else:
            console.print("  [dim]Empty:[/dim] warehouse/ already empty")
            result["status"] = "already_empty"

    except ClientError as e:
        console.print(f"  [red]Error:[/red] {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


def reset_postgres_tables(cfg: ResetConfig) -> dict:
    """Reset PostgreSQL audit and lineage tables."""
    logger.info("Resetting PostgreSQL tables", dry_run=cfg.dry_run)

    import psycopg2

    result = {"tables_truncated": [], "status": "pending"}

    tables_to_truncate = [
        "audit_log",
        "data_lineage",
    ]

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would truncate PostgreSQL tables")
        for table in tables_to_truncate:
            console.print(f"  [dim]Would truncate:[/dim] {table}")
        result["status"] = "would_truncate"
        result["tables_truncated"] = tables_to_truncate
        return result

    try:
        conn = psycopg2.connect(
            host=cfg.postgres_host,
            port=cfg.postgres_port,
            user=cfg.postgres_user,
            password=cfg.postgres_password,
            database=cfg.postgres_db,
        )
        cursor = conn.cursor()

        for table in tables_to_truncate:
            try:
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
                result["tables_truncated"].append(table)
                console.print(f"  [green]Truncated:[/green] {table}")
            except psycopg2.Error as e:
                # Table may not exist in minimal setups
                console.print(f"  [yellow]Warning:[/yellow] {table} - {e.pgerror or e}")
                conn.rollback()

        conn.commit()
        cursor.close()
        conn.close()

        result["status"] = "truncated"

    except Exception as e:
        console.print(f"  [yellow]Warning:[/yellow] PostgreSQL not available - {e}")
        result["status"] = "skipped"

    return result


def reset_prometheus(cfg: ResetConfig) -> dict:
    """Reset Prometheus time-series data."""
    logger.info("Resetting Prometheus data", dry_run=cfg.dry_run)

    result = {"status": "pending"}

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would reset Prometheus")
        console.print("  [dim]Would stop:[/dim] prometheus container")
        console.print("  [dim]Would clear:[/dim] prometheus-data volume")
        console.print("  [dim]Would start:[/dim] prometheus container")
        result["status"] = "would_reset"
        return result

    try:
        # Stop Prometheus
        console.print("  Stopping Prometheus...")
        subprocess.run(
            ["docker-compose", "stop", "prometheus"],
            check=True,
            capture_output=True,
            cwd=PROJECT_ROOT,
        )

        # Clear volume contents using docker
        console.print("  Clearing Prometheus data...")
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                "k2-market-data-platform_prometheus-data:/data",
                "alpine",
                "sh",
                "-c",
                "rm -rf /data/*",
            ],
            check=True,
            capture_output=True,
        )

        # Restart Prometheus
        console.print("  Starting Prometheus...")
        subprocess.run(
            ["docker-compose", "start", "prometheus"],
            check=True,
            capture_output=True,
            cwd=PROJECT_ROOT,
        )

        console.print("  [green]Prometheus reset complete[/green]")
        result["status"] = "reset"

    except subprocess.CalledProcessError as e:
        console.print(f"  [red]Error:[/red] {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


def reset_grafana(cfg: ResetConfig) -> dict:
    """Reset Grafana session data while preserving dashboards."""
    logger.info("Resetting Grafana session data", dry_run=cfg.dry_run)

    result = {"status": "pending"}

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would reset Grafana sessions")
        console.print("  [dim]Would restart:[/dim] grafana container")
        console.print("  [dim]Would preserve:[/dim] provisioned dashboards")
        result["status"] = "would_reset"
        return result

    try:
        console.print("  Restarting Grafana to clear sessions...")
        subprocess.run(
            ["docker-compose", "restart", "grafana"],
            check=True,
            capture_output=True,
            cwd=PROJECT_ROOT,
        )

        console.print("  [green]Grafana session reset complete[/green]")
        console.print("  [dim]Dashboards preserved (provisioned from config/)[/dim]")
        result["status"] = "reset"

    except subprocess.CalledProcessError as e:
        console.print(f"  [red]Error:[/red] {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


def reload_sample_data(cfg: ResetConfig) -> dict:
    """Reload sample data using init_infra.py."""
    logger.info("Reloading sample data", dry_run=cfg.dry_run)

    result = {"status": "pending"}

    if cfg.dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Would reload sample data")
        console.print("  [dim]Would run:[/dim] python scripts/init_infra.py")
        result["status"] = "would_reload"
        return result

    try:
        console.print("  Running init_infra.py...")

        # Run init_infra.py as subprocess for clean execution
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "init_infra.py")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        if proc.returncode == 0:
            console.print("  [green]Sample data reloaded[/green]")
            result["status"] = "reloaded"
        else:
            console.print("  [red]Error:[/red] init_infra.py failed")
            if proc.stderr:
                console.print(f"  [dim]{proc.stderr[:500]}[/dim]")
            result["status"] = "error"
            result["error"] = proc.stderr[:200] if proc.stderr else "Unknown error"

    except Exception as e:
        console.print(f"  [red]Error:[/red] {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


def confirm_reset(cfg: ResetConfig) -> bool:
    """Display reset plan and prompt for confirmation."""
    console.print()
    console.print(Panel("[bold red]Demo Reset Plan[/bold red]", border_style="red"))
    console.print()

    table = Table(box=box.ROUNDED)
    table.add_column("Component", style="cyan")
    table.add_column("Action", style="white")
    table.add_column("Status")

    # Kafka
    if cfg.keep_kafka:
        table.add_row("Kafka", "Preserve messages", "[yellow]SKIP[/yellow]")
    else:
        table.add_row("Kafka", "Purge all topic messages", "[red]RESET[/red]")

    # Iceberg/MinIO
    if cfg.keep_iceberg:
        table.add_row("Iceberg", "Preserve tables", "[yellow]SKIP[/yellow]")
        table.add_row("MinIO", "Preserve warehouse/", "[yellow]SKIP[/yellow]")
    else:
        table.add_row("Iceberg", "Drop and recreate tables", "[red]RESET[/red]")
        table.add_row("MinIO", "Clear warehouse/ bucket", "[red]RESET[/red]")

    # PostgreSQL
    if cfg.keep_iceberg:
        table.add_row("PostgreSQL", "Preserve audit/lineage", "[yellow]SKIP[/yellow]")
    else:
        table.add_row("PostgreSQL", "Truncate audit/lineage tables", "[red]RESET[/red]")

    # Observability
    if cfg.keep_metrics:
        table.add_row("Prometheus", "Preserve metrics", "[yellow]SKIP[/yellow]")
        table.add_row("Grafana", "Preserve sessions", "[yellow]SKIP[/yellow]")
    else:
        table.add_row("Prometheus", "Clear time-series data", "[red]RESET[/red]")
        table.add_row("Grafana", "Clear sessions (keep dashboards)", "[red]RESET[/red]")

    # Sample data
    if cfg.reload_data and not (cfg.keep_kafka and cfg.keep_iceberg):
        table.add_row("Sample Data", "Reload ASX sample data", "[green]RELOAD[/green]")
    else:
        table.add_row("Sample Data", "Skip reload", "[dim]SKIP[/dim]")

    console.print(table)
    console.print()

    if cfg.force:
        console.print("[yellow]--force flag set, skipping confirmation[/yellow]")
        return True

    return typer.confirm("Proceed with reset?", default=False)


@app.command()
def main(
    keep_metrics: bool = typer.Option(
        False,
        "--keep-metrics",
        "-m",
        help="Preserve Prometheus and Grafana data",
    ),
    keep_kafka: bool = typer.Option(
        False,
        "--keep-kafka",
        "-k",
        help="Preserve Kafka messages",
    ),
    keep_iceberg: bool = typer.Option(
        False,
        "--keep-iceberg",
        "-i",
        help="Preserve Iceberg tables and MinIO data",
    ),
    no_reload: bool = typer.Option(
        False,
        "--no-reload",
        help="Skip reloading sample data after reset",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview operations without executing",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Reset K2 demo environment.

    Provides fine-grained control over resetting infrastructure components.
    By default, resets everything and reloads sample data.
    """
    console.print()
    console.print(
        Panel(
            "[bold cyan]K2 Market Data Platform[/bold cyan]\n[dim]Demo Reset Utility[/dim]",
            border_style="cyan",
        )
    )

    # Build configuration
    cfg = ResetConfig(
        keep_metrics=keep_metrics,
        keep_kafka=keep_kafka,
        keep_iceberg=keep_iceberg,
        reload_data=not no_reload,
        dry_run=dry_run,
        force=force,
    )

    # Confirm or dry-run
    if dry_run:
        console.print("\n[bold yellow]DRY RUN MODE[/bold yellow] - No changes will be made\n")
    elif not confirm_reset(cfg):
        console.print("\n[yellow]Reset cancelled[/yellow]")
        raise typer.Exit(0)

    console.print()

    # Execute resets
    results = {}

    # 1. Kafka
    if not cfg.keep_kafka:
        console.print("\n[bold]1. Resetting Kafka...[/bold]")
        results["kafka"] = reset_kafka(cfg)
    else:
        console.print("\n[bold]1. Kafka[/bold] [yellow]SKIPPED[/yellow]")

    # 2. Iceberg tables
    if not cfg.keep_iceberg:
        console.print("\n[bold]2. Resetting Iceberg tables...[/bold]")
        results["iceberg"] = reset_iceberg_tables(cfg)
    else:
        console.print("\n[bold]2. Iceberg[/bold] [yellow]SKIPPED[/yellow]")

    # 3. MinIO warehouse
    if not cfg.keep_iceberg:
        console.print("\n[bold]3. Clearing MinIO warehouse...[/bold]")
        results["minio"] = clear_minio_warehouse(cfg)
    else:
        console.print("\n[bold]3. MinIO[/bold] [yellow]SKIPPED[/yellow]")

    # 4. PostgreSQL
    if not cfg.keep_iceberg:
        console.print("\n[bold]4. Resetting PostgreSQL...[/bold]")
        results["postgres"] = reset_postgres_tables(cfg)
    else:
        console.print("\n[bold]4. PostgreSQL[/bold] [yellow]SKIPPED[/yellow]")

    # 5. Prometheus
    if not cfg.keep_metrics:
        console.print("\n[bold]5. Resetting Prometheus...[/bold]")
        results["prometheus"] = reset_prometheus(cfg)
    else:
        console.print("\n[bold]5. Prometheus[/bold] [yellow]SKIPPED[/yellow]")

    # 6. Grafana
    if not cfg.keep_metrics:
        console.print("\n[bold]6. Resetting Grafana...[/bold]")
        results["grafana"] = reset_grafana(cfg)
    else:
        console.print("\n[bold]6. Grafana[/bold] [yellow]SKIPPED[/yellow]")

    # 7. Reload sample data
    if cfg.reload_data and not (cfg.keep_kafka and cfg.keep_iceberg):
        console.print("\n[bold]7. Reloading sample data...[/bold]")
        results["reload"] = reload_sample_data(cfg)
    else:
        console.print("\n[bold]7. Sample data[/bold] [yellow]SKIPPED[/yellow]")

    # Summary
    console.print()
    if dry_run:
        console.print(
            Panel(
                "[bold yellow]DRY RUN COMPLETE[/bold yellow]\nNo changes were made.",
                border_style="yellow",
            )
        )
    else:
        console.print(Panel("[bold green]RESET COMPLETE[/bold green]", border_style="green"))

    console.print()


if __name__ == "__main__":
    app()
