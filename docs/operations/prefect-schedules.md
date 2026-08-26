# Prefect Schedules

**Status:** deployed — both schedules active on the `iceberg-offload` work pool.

Two Prefect 3.x deployments drive the cold tier, both executing against the shared
`k2-spark-iceberg` container:

| Deployment | Cron (UTC) | Purpose |
|------------|------------|---------|
| `iceberg-offload-main/iceberg-offload-15min` | `*/15 * * * *` | ClickHouse → Iceberg incremental offload (all 10 cold tables) |
| `iceberg-maintenance-main/iceberg-maintenance-daily` | `0 2 * * *` | Compact + expire snapshots + audit for all 10 cold tables |

Both use the `iceberg-offload` work pool and the `k2-prefect-worker` process worker.
Confirm they are live:

```bash
docker exec k2-prefect-server prefect deployment ls
```

---

## Architecture

```
Prefect Server (http://localhost:4200)
    │
    ├── Work Pool: iceberg-offload
    │       │
    │       └── prefect-worker container
    │               ├── [*/15 UTC] iceberg_offload_flow.py:iceberg_offload_main
    │               │       └── docker exec k2-spark-iceberg python3 offload_generic.py
    │               │           (10 tables: Bronze parallel → Silver → Gold parallel)
    │               │
    │               └── [02:00 UTC] iceberg_maintenance_flow.py:iceberg_maintenance_main
    │                       └── docker exec k2-spark-iceberg python3 iceberg_maintenance.py
    │                           (10 tables: compact → expire → audit, all sequential)
    │
    └── Prefect DB (PostgreSQL)
            ├── offload_watermarks   — last offloaded timestamp per table
            └── maintenance_audit_log — ClickHouse vs Iceberg count comparison
```

---

## Offload Pipeline (`iceberg-offload-15min`)

### What it does

Incrementally offloads trade data from ClickHouse (hot — 7-day TTL on bronze, 30-day on
silver, 1-year on gold) to Iceberg (cold, permanent).

**Execution order**:
1. **Bronze** — 3 concurrent Spark jobs (binance ‖ kraken ‖ coinbase)
2. **Silver** — 1 sequential Spark job (`silver_trades`)
3. **Gold** — 6 concurrent Spark jobs (ohlcv_1m, 5m, 15m, 30m, 1h, 1d)

**Watermark pattern**: Each table has a row in `offload_watermarks`. The flow reads the last watermark, queries ClickHouse for rows newer than that watermark, appends to Iceberg, then updates the watermark atomically.

### Key files

| File | Purpose |
|------|---------|
| `docker/offload/offload_generic.py` | Spark offload script (invoked via docker exec) |
| `docker/offload/flows/iceberg_offload_flow.py` | Prefect flow (v3.1.0) |
| `docker/offload/flows/deploy_production.py` | Deployment helper script |

### Deploy

```bash
# Run inside the prefect-worker container (or from host with PREFECT_API_URL set)
docker exec k2-prefect-worker python3 /opt/prefect/flows/deploy_production.py
```

Or use `prefect.yaml` directly:

```bash
cd docker/offload/flows
prefect deploy --all  # Deploys both flows from prefect.yaml
```

### Monitor

```bash
# Prefect UI
open http://localhost:4200

# Check watermarks progressing
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, last_offload_timestamp, last_successful_run, status FROM offload_watermarks ORDER BY table_name"

# Spark logs for a specific offload
docker logs k2-spark-iceberg | grep -A 5 "Offload complete"
```

---

## Maintenance Pipeline (`iceberg-maintenance-daily`)

### What it does

Runs once per day at 02:00 UTC. Three sequential stages:

| Stage | Action | Timeout |
|-------|--------|---------|
| `compact_all_tables` | Rewrites small Parquet files (binpack, 128 MB target) for all 10 tables | 10 min/table |
| `expire_all_snapshots` | Removes Iceberg snapshots older than 7 days, retaining the last 3 | 5 min/table |
| `run_audit` | Compares ClickHouse vs Iceberg row counts for the previous 24h UTC window | 10 min total |

**Failure policy**: Individual table failures log-and-continue (overall_status = `partial`). If the audit finds `MISSING > 0` or `ERROR > 0`, the flow raises `RuntimeError` → Prefect marks the run as Failed → alert triggers.

### Key files

| File | Purpose |
|------|---------|
| `docker/offload/iceberg_maintenance.py` | Spark maintenance script (compact, expire, audit subcommands) |
| `docker/offload/flows/iceberg_maintenance_flow.py` | Prefect flow (v1.0) |
| `docker/offload/flows/deploy_maintenance.py` | Deployment helper script |

### Deploy

```bash
docker exec k2-prefect-worker python3 /opt/prefect/flows/deploy_maintenance.py
```

### Monitor

```bash
# Last 10 audit results
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "
  SELECT run_timestamp, iceberg_table, ch_row_count, iceberg_row_count, delta_pct, status
  FROM maintenance_audit_log
  ORDER BY run_timestamp DESC
  LIMIT 10"

# Check for any MISSING_DATA status
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "
  SELECT * FROM maintenance_audit_log
  WHERE status = 'missing_data'
  ORDER BY run_timestamp DESC
  LIMIT 5"
```

### Run manually (one-off)

```bash
# Full maintenance cycle
docker exec k2-spark-iceberg python3 /home/iceberg/offload/iceberg_maintenance.py \
  audit --audit-window-hours 24

# Compact a specific table
docker exec k2-spark-iceberg python3 /home/iceberg/offload/iceberg_maintenance.py \
  compact --table cold.bronze_trades_binance --target-file-size-mb 128

# Dry-run compact (no changes written)
docker exec k2-spark-iceberg python3 /home/iceberg/offload/iceberg_maintenance.py \
  compact --table cold.silver_trades --dry-run
```

---

## Deployment Reference (`prefect.yaml`)

```yaml
# docker/offload/flows/prefect.yaml
deployments:
  - name: iceberg-offload-15min
    entrypoint: iceberg_offload_flow.py:iceberg_offload_main
    schedule:
      cron: "*/15 * * * *"
      timezone: "UTC"
    work_pool:
      name: iceberg-offload

  - name: iceberg-maintenance-daily
    entrypoint: iceberg_maintenance_flow.py:iceberg_maintenance_main
    schedule:
      cron: "0 2 * * *"
      timezone: "UTC"
    work_pool:
      name: iceberg-offload
    parameters:
      target_file_size_mb: 128
      snapshot_max_age_hours: 168   # 7 days
      snapshot_retain_last: 3
      audit_window_hours: 24
```

---

## Troubleshooting

### Offload lag growing (watermark stale)

```bash
# Check last successful offload per table
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "
  SELECT table_name, last_offload_timestamp, NOW() - last_successful_run AS lag, status
  FROM offload_watermarks ORDER BY lag DESC"
```

Expected: lag < 20 min. If > 30 min, check Spark logs:

```bash
docker logs k2-spark-iceberg --tail 50
```

Common causes: Spark OOM (increase executor memory), ClickHouse down, MinIO unreachable.

See also: [iceberg-offload-failure.md](./runbooks/iceberg-offload-failure.md), [iceberg-offload-lag.md](./runbooks/iceberg-offload-lag.md)

### Maintenance audit failed (MISSING_DATA)

```bash
# Check audit log for details
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "
  SELECT iceberg_table, ch_row_count, iceberg_row_count, delta_pct, notes
  FROM maintenance_audit_log
  WHERE status = 'missing_data'
  ORDER BY run_timestamp DESC"
```

If `delta_pct < -5%` (iceberg has significantly fewer rows than ClickHouse), the offload pipeline has a gap. Check watermark for the affected table and re-run offload for the missing window.

### Worker not picking up jobs

```bash
# Verify worker is running
docker ps | grep prefect-worker
docker logs k2-prefect-worker --tail 20

# Check work pool exists in Prefect UI
open http://localhost:4200/work-pools
```

The `iceberg-offload` pool is created automatically when the worker starts with `--pool iceberg-offload`. If missing, restart the worker container.

---

## Quick Reference

| Task | Command |
|------|---------|
| List deployments | `docker exec k2-prefect-server prefect deployment ls` |
| (Re)deploy schedules | `docker exec k2-prefect-worker python3 /opt/prefect/flows/deploy_production.py` then `… deploy_maintenance.py` |
| Trigger offload now | `docker exec k2-prefect-server prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'` |
| Trigger maintenance now | `docker exec k2-prefect-server prefect deployment run 'iceberg-maintenance-main/iceberg-maintenance-daily'` |
| Pause a schedule | `docker exec k2-prefect-server prefect deployment set-schedule iceberg-offload-main/iceberg-offload-15min --paused` |
| Check watermarks | `docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "SELECT * FROM offload_watermarks ORDER BY table_name"` |
| Check audit log | `docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c "SELECT * FROM maintenance_audit_log ORDER BY run_timestamp DESC LIMIT 20"` |
| Prefect UI | http://localhost:4200 |

---

## Related

- [ADR-017 — Iceberg maintenance pipeline](../decisions/ADR-017-iceberg-maintenance-pipeline.md) — design rationale
- [ADR-014 — Spark-based Iceberg offload](../decisions/ADR-014-spark-based-iceberg-offload.md) — why Spark batch, and the watermark design
- [runbooks/iceberg-offload-failure.md](./runbooks/iceberg-offload-failure.md)
- [runbooks/iceberg-offload-watermark-recovery.md](./runbooks/iceberg-offload-watermark-recovery.md)
- [runbooks/iceberg-scheduler-recovery.md](./runbooks/iceberg-scheduler-recovery.md)
