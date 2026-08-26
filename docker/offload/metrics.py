#!/usr/bin/env python3
"""
K2 Market Data Platform - Prometheus exporter for the Iceberg offload pipeline.

Metrics are derived from the PostgreSQL `offload_watermarks` table, not from
in-process counters. Prefect runs every flow in a short-lived subprocess, so
anything counted inside a flow run dies with that run and is never scraped.
The watermark row is the durable record of what the pipeline actually did.

Runs as a long-lived sidecar next to the Prefect worker:

    python /opt/prefect/offload/metrics.py --serve

Config via the standard PREFECT_DB_* env vars (same as watermark_pg.py).
"""

import argparse
import logging
import os
import time

import psycopg2
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Watermark table names map 1:1 onto medallion layers by prefix.
_LAYER_PREFIXES = (("bronze_", "bronze"), ("silver_", "silver"), ("ohlcv_", "gold"))

WATERMARK_QUERY = """
    SELECT table_name,
           last_offload_timestamp,
           last_offload_row_count,
           status,
           last_run_duration_seconds,
           updated_at
    FROM offload_watermarks
"""

# ============================================================================
# Metric Definitions
# ============================================================================

offload_lag_minutes = Gauge(
    "offload_lag_minutes", "Age of the offload watermark (minutes)", ["table"]
)

watermark_timestamp_seconds = Gauge(
    "watermark_timestamp_seconds",
    "Unix timestamp of the current offload watermark",
    ["table"],
)

offload_last_duration_seconds = Gauge(
    "offload_last_duration_seconds", "Duration of the last offload run", ["table"]
)

offload_last_rows_per_second = Gauge(
    "offload_last_rows_per_second", "Throughput of the last offload run", ["table"]
)

offload_tables_configured = Gauge(
    "offload_tables_configured", "Number of tables configured for offload"
)

offload_rows_total = Counter(
    "offload_rows_total", "Rows offloaded to Iceberg", ["table", "layer"]
)

offload_cycles_total = Counter(
    "offload_cycles_total", "Finished offload runs by outcome", ["status"]
)

offload_errors_total = Counter(
    "offload_errors_total", "Failed offload runs", ["table", "error_type"]
)

offload_duration_seconds = Histogram(
    "offload_duration_seconds",
    "Duration of offload runs in seconds",
    ["table", "layer"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)


# ============================================================================
# Refresh loop
# ============================================================================

# table -> updated_at of the last run already folded into the counters, so a
# run is counted exactly once no matter how often we poll.
_counted = {}


def _layer(table_name: str) -> str:
    for prefix, layer in _LAYER_PREFIXES:
        if table_name.startswith(prefix):
            return layer
    return "unknown"


def _init_series(table: str, layer: str) -> None:
    """Create the label children so panels have series before the first run."""
    offload_rows_total.labels(table=table, layer=layer).inc(0)
    offload_errors_total.labels(table=table, error_type="failed").inc(0)
    offload_duration_seconds.labels(table=table, layer=layer)
    for status in ("success", "failed"):
        offload_cycles_total.labels(status=status).inc(0)


def refresh(conn) -> int:
    """Re-derive all metrics from the watermark table. Returns row count."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(WATERMARK_QUERY)
        rows = cur.fetchall()

    now = time.time()
    offload_tables_configured.set(len(rows))

    for row in rows:
        table = row["table_name"]
        layer = _layer(table)
        watermark = row["last_offload_timestamp"].timestamp()
        duration = row["last_run_duration_seconds"] or 0
        row_count = row["last_offload_row_count"] or 0

        if table not in _counted:
            _init_series(table, layer)

        watermark_timestamp_seconds.labels(table=table).set(watermark)
        offload_lag_minutes.labels(table=table).set((now - watermark) / 60)
        offload_last_duration_seconds.labels(table=table).set(duration)
        offload_last_rows_per_second.labels(table=table).set(
            row_count / duration if duration else 0
        )

        # Count each finished run once, on the transition of updated_at.
        # 'running' rows are skipped: they resolve to success/failed shortly.
        previous = _counted.get(table)
        _counted[table] = row["updated_at"]
        if previous is None or row["updated_at"] == previous or row["status"] == "running":
            continue

        offload_cycles_total.labels(status=row["status"]).inc()
        offload_duration_seconds.labels(table=table, layer=layer).observe(duration)
        if row["status"] == "success":
            offload_rows_total.labels(table=table, layer=layer).inc(row_count)
        else:
            offload_errors_total.labels(table=table, error_type=row["status"]).inc()

    return len(rows)


def _connect():
    return psycopg2.connect(
        host=os.environ.get("PREFECT_DB_HOST", "prefect-db"),
        port=int(os.environ.get("PREFECT_DB_PORT", "5432")),
        database=os.environ.get("PREFECT_DB_NAME", "prefect"),
        user=os.environ.get("PREFECT_DB_USER", "prefect"),
        password=os.environ["PREFECT_DB_PASSWORD"],
        connect_timeout=10,
    )


def serve(port: int = 8000, interval: int = 15) -> None:
    """Expose /metrics on `port`, refreshing from PostgreSQL every `interval`s."""
    start_http_server(port)
    logger.info("offload metrics exporter listening on :%d/metrics", port)

    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = _connect()
            count = refresh(conn)
            logger.debug("refreshed metrics for %d tables", count)
        except Exception as exc:  # noqa: BLE001 - exporter must never exit
            # ponytail: reconnect on any error; the watermark table is the only
            # dependency, so there is nothing finer-grained worth handling.
            logger.warning("metrics refresh failed: %s", exc)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
        time.sleep(interval)


def _self_check() -> None:
    """Offline check of the counting logic (no DB required)."""
    from datetime import datetime, timedelta, timezone

    _counted.clear()
    now = datetime.now(timezone.utc)

    def row(status, updated, rows=100, duration=5):
        return {
            "table_name": "bronze_trades_binance",
            "last_offload_timestamp": now - timedelta(minutes=3),
            "last_offload_row_count": rows,
            "status": status,
            "last_run_duration_seconds": duration,
            "updated_at": updated,
        }

    class FakeConn:
        def __init__(self, rows):
            self.rows = rows

        def cursor(self, **_):
            outer = self

            class Cur:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *a):
                    return False

                def execute(self_, _sql):
                    pass

                def fetchall(self_):
                    return outer.rows

            return Cur()

    def rows_value():
        return offload_rows_total.labels(table="bronze_trades_binance", layer="bronze")._value.get()

    assert _layer("ohlcv_1m") == "gold"
    assert _layer("silver_trades") == "silver"

    refresh(FakeConn([row("success", now)]))
    base = rows_value()
    # Same updated_at -> not double counted.
    refresh(FakeConn([row("success", now)]))
    assert rows_value() == base, "re-poll must not double count"
    # New run -> counted once.
    refresh(FakeConn([row("success", now + timedelta(minutes=15))]))
    assert rows_value() == base + 100, "new run must be counted"
    # 'running' is not counted.
    refresh(FakeConn([row("running", now + timedelta(minutes=16))]))
    assert rows_value() == base + 100, "running must not be counted"
    assert offload_lag_minutes.labels(table="bronze_trades_binance")._value.get() > 2
    print("self-check ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="run the HTTP exporter")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=int, default=15, help="refresh seconds")
    parser.add_argument("--self-check", action="store_true", help="run offline logic check")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.self_check:
        _self_check()
    elif args.serve:
        serve(port=args.port, interval=args.interval)
    else:
        parser.error("nothing to do: pass --serve or --self-check")
