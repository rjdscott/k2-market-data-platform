# K2 v2 — Current Platform State

**Date**: 2026-02-19
**Branch**: `main`
**Status**: Production-operational — all 3 exchanges live, full medallion pipeline + daily maintenance running. Phase 7 integration hardening 60% complete.

> This is the "read-first" snapshot for new engineers. It is updated at the end of each major
> session. For the full session narrative, see the most recent HANDOFF-*.md file.

---

## Platform Health

| Component | Status | Notes |
|-----------|--------|-------|
| `feed-handler-binance` | ✅ Running | 12 symbols, live WebSocket |
| `feed-handler-kraken` | ✅ Running | 11 symbols, live WebSocket |
| `feed-handler-coinbase` | ✅ Running | 11 symbols, Advanced Trade API |
| `redpanda` | ✅ Running | 3 topics (binance: 40p, kraken: 20p, coinbase: 20p) |
| `redpanda-console` | ✅ Running | http://localhost:8080 |
| `clickhouse` | ✅ Running | 24.3 LTS, Bronze/Silver/Gold layers active |
| `spark-iceberg` | ✅ Running | JDBC driver baked in, env vars correct |
| `prefect-server` | ✅ Running | http://localhost:4200 |
| `prefect-worker` | ✅ Running | Processes offload flows |
| `prefect-db` | ✅ Running | PostgreSQL, holds watermarks |
| `minio` | ✅ Running | http://localhost:9001, Iceberg warehouse |
| `prometheus` | ✅ Running | http://localhost:9090 |
| `grafana` | ✅ Running | http://localhost:3000 (admin/admin) |

---

## Data Counts (as of 2026-02-18)

### ClickHouse (Hot Tier)

| Table | Approximate Rows | Notes |
|-------|-----------------|-------|
| `k2.bronze_trades_binance` | 1.5M+ | Growing continuously |
| `k2.bronze_trades_kraken` | 40K+ | Growing continuously |
| `k2.bronze_trades_coinbase` | 50K+ | Growing continuously |
| `k2.silver_trades` | All 3 exchanges via MVs | Unified view |
| `k2.ohlcv_1m` | — | Aggregated from silver_trades |
| `k2.ohlcv_5m` | — | Aggregated from silver_trades |
| `k2.ohlcv_15m` | — | Aggregated from silver_trades |
| `k2.ohlcv_30m` | — | Aggregated from silver_trades |
| `k2.ohlcv_1h` | — | Aggregated from silver_trades |
| `k2.ohlcv_1d` | — | Aggregated from silver_trades |

### Iceberg (Cold Tier — MinIO `cold.*`)

| Table | Rows Offloaded | Last Offload |
|-------|---------------|-------------|
| `cold.bronze_trades_binance` | 747,783 | 2026-02-18 |
| `cold.bronze_trades_kraken` | 8,728 | 2026-02-18 |
| `cold.bronze_trades_coinbase` | 20,983 | 2026-02-18 |
| `cold.silver_trades` | 538,641 | 2026-02-18 |
| `cold.gold_ohlcv_1m` | 2,251 | 2026-02-18 |
| `cold.gold_ohlcv_5m` | 534 | 2026-02-18 |
| `cold.gold_ohlcv_15m` | 195 | 2026-02-18 |
| `cold.gold_ohlcv_30m` | 104 | 2026-02-18 |
| `cold.gold_ohlcv_1h` | 69 | 2026-02-18 |
| `cold.gold_ohlcv_1d` | 61 | 2026-02-18 |

**Total cold rows**: ~1.3M (33M+ total including ClickHouse hot tier)

---

## Offload & Maintenance Schedules

### Offload (15-min)
- **Mechanism**: Prefect flow → Spark batch → Iceberg (MinIO)
- **Script**: `docker/offload/offload_generic.py`
- **Flow**: `docker/offload/flows/iceberg_offload_flow.py` (v3.1.0)
- **Cron**: `*/15 * * * *` — **DEPLOYED** (`iceberg-offload-15min`, status=READY)
- **Watermarks**: PostgreSQL `offload_watermarks` table (all 10 tables seeded)
- **Pattern**: Incremental — only rows newer than last watermark offloaded
- **Parallelism**: Bronze 3 concurrent (binance ‖ kraken ‖ coinbase) → Silver sequential → Gold 6 concurrent

### Daily Maintenance (02:00 UTC)
- **Mechanism**: Prefect flow → `docker exec k2-spark-iceberg` per table
- **Script**: `docker/offload/iceberg_maintenance.py`
- **Flow**: `docker/offload/flows/iceberg_maintenance_flow.py` (v1.0)
- **Cron**: `0 2 * * *` — **DEPLOYED** (`iceberg-maintenance-daily`, status=READY)
- **Execution order**: compact_all_tables (binpack, 128 MB) → expire_all_snapshots (7-day) → run_audit (24h window)
- **Audit log**: PostgreSQL `maintenance_audit_log` (auto-created; populated 2026-02-18)
- **Failure policy**: table failures log-and-continue; audit MISSING/ERROR raises RuntimeError → Prefect marks Failed
- **Note on gold OHLCV**: ~7% delta expected; CH background part merges reduce row count post-offload

---

## Phase Completion Matrix

| Phase | Name | Status |
|-------|------|--------|
| Phase 1 | Infrastructure Baseline | ✅ Complete |
| Phase 2 | Redpanda Migration | ✅ Complete |
| Phase 3 | ClickHouse Foundation | ✅ Complete |
| Phase 4 | Streaming Pipeline (Kotlin handlers) | ✅ Complete |
| Phase 5 | Cold Tier / Iceberg Offload | ✅ Complete — offload + maintenance deployed, audit validated |
| Phase 6 | Kotlin Feed Handlers (v2 refactor) | ✅ Complete |
| Phase 7 | Integration Hardening | 🟡 In Progress — Steps 1 ✅, 2 🟡, 3 ✅, 4 🟡 |
| Phase 8 | API Migration | ⬜ Not started |

---

## Pending Work

| Item | Priority | Notes |
|------|---------|-------|
| Tag `v2-phase-5-complete` | Low | Git tag pending (branch merged to main via PR #47) |
| Step 2: 24h resource burn-in | High | Sampling loop running (PID 107291, /tmp/k2-burn-in.csv). Collect results 2026-02-20 morning. |
| Step 4: Alert testing + Alertmanager | Medium | Alert rules live in Prometheus. FeedHandlerDown fire test pending. Alertmanager optional. |
| Step 5: Runbooks + docs finalisation | High | 5 runbooks to write, ARCHITECTURE-V2.md update, v2-phase-7-complete git tag |

---

## Known Issues

| Issue | Severity | Workaround |
|-------|---------|-----------|
| Iceberg silver excludes `trade_conditions`, `vendor_data`, `validation_errors` | Low | JDBC can't handle `Array(String)` / `Map(String,String)` types; columns omitted from cold schema |
| Docker bind mount inode staleness | Medium | After editing `instruments.yaml`, run `docker compose up -d --force-recreate --no-deps <service>` — NOT `docker restart` |
| ClickHouse DateTime64 TTL must use `toDateTime()` cast | Low | `TTL toDateTime(timestamp) + INTERVAL 30 DAY` — required for silver_trades |
| prefect-worker startup RAM spike | Low | Uses ~488MiB briefly at start (init), settles to ~100MiB. Limit is 512MiB — increase to 768MiB if OOM observed. |
| Coinbase normalized trades not flowing | Medium | Schema registration race condition on startup — coinbase `producer-2` stuck in permanent retry loop. Raw topic fine. Fix: `docker compose -f docker-compose.v2.yml up -d --force-recreate --no-deps feed-handler-coinbase`. Binance + Kraken unaffected. |

---

## Architecture Quick Reference

```
Feed Handlers (Kotlin) → Redpanda → ClickHouse Kafka Engine
                                              │
                            Bronze tables (per exchange)
                                              │ Materialised Views
                            silver_trades (unified)
                                              │ Materialised Views
                            ohlcv_{1m,5m,15m,30m,1h,1d}
                                              │
                                         Spark batch (every 15m)
                                              │
                            cold.* (Iceberg on MinIO)
```

**Instrument registry**: `config/instruments.yaml` — single source of truth for all 3 exchanges.
Edit this file, then `--force-recreate` any affected feed-handler container.

---

## Starting the Stack

```bash
# Start all services
docker compose -f docker-compose.v2.yml up -d

# Check health
docker compose -f docker-compose.v2.yml ps

# View ClickHouse data
docker exec -it k2-clickhouse clickhouse-client --query "SELECT count() FROM k2.silver_trades"

# View Iceberg via Spark
docker exec k2-spark-iceberg spark-sql --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
  --conf spark.sql.catalog.cold=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.cold.type=rest \
  --conf spark.sql.catalog.cold.uri=http://iceberg-rest:8181 \
  -e "SELECT count(*) FROM cold.bronze_trades_binance"
```

---

## Related Documents

- [ARCHITECTURE-V2.md](../../decisions/platform-v2/ARCHITECTURE-V2.md) — Full v2 architectural overview
- [docs/decisions/platform-v2/](../../decisions/platform-v2/) — All 20 ADRs + decisions
- [HANDOFF-2026-02-19.md](./HANDOFF-2026-02-19.md) — Tomorrow's start brief (Phase 7 execution)
- [HANDOFF-2026-02-18.md](./HANDOFF-2026-02-18.md) — Full 2026-02-18 session narrative
- [docs/operations/adding-new-exchanges.md](../../operations/adding-new-exchanges.md) — How to add exchange #4
- [docs/operations/prefect-schedule-config.md](../../operations/prefect-schedule-config.md) — Prefect schedule setup
