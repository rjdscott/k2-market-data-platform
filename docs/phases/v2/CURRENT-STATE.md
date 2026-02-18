# K2 v2 — Current Platform State

**Date**: 2026-02-18
**Branch**: `phase-5-prefect-iceberg-offload`
**Status**: Production-operational — all 3 exchanges live, full medallion pipeline running

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

## Offload Schedule

- **Mechanism**: Prefect flow → Spark batch → Iceberg (MinIO)
- **Script**: `docker/offload/offload_generic.py`
- **Flow**: `docker/offload/flows/iceberg_offload_flow.py` (v3.1.0)
- **Schedule**: 15-minute intervals (deployment pending — see Pending Work)
- **Watermarks**: PostgreSQL `offload_watermarks` table (all 10 tables in `success` state)
- **Pattern**: Incremental — only rows newer than last watermark are offloaded

---

## Phase Completion Matrix

| Phase | Name | Status |
|-------|------|--------|
| Phase 1 | Infrastructure Baseline | ✅ Complete |
| Phase 2 | Redpanda Migration | ✅ Complete |
| Phase 3 | ClickHouse Foundation | ✅ Complete |
| Phase 4 | Streaming Pipeline (Kotlin handlers) | ✅ Complete |
| Phase 5 | Cold Tier / Iceberg Offload | 🟡 80% — offload working, schedule pending |
| Phase 6 | Kotlin Feed Handlers (v2 refactor) | ✅ Complete |
| Phase 7 | Integration Hardening | ✅ Complete |
| Phase 8 | API Migration | ⬜ Not started |

---

## Pending Work (Phase 5)

| Item | Priority | Notes |
|------|---------|-------|
| Deploy Prefect 15-min schedule | High | `prefect deployment build/apply` or via UI |
| Spark daily maintenance job | Medium | Compaction (02:00), expiry (02:20), audit (02:30) |
| Warm-cold consistency validation | Medium | Row count parity check ClickHouse vs Iceberg |
| Tag `v2-phase-5-complete` + PR | Low | After consistency validated |

---

## Known Issues

| Issue | Severity | Workaround |
|-------|---------|-----------|
| Iceberg silver excludes `trade_conditions`, `vendor_data`, `validation_errors` | Low | JDBC can't handle `Array(String)` / `Map(String,String)` types; columns omitted from cold schema |
| Docker bind mount inode staleness | Medium | After editing `instruments.yaml`, run `docker compose up -d --force-recreate --no-deps <service>` — NOT `docker restart` |
| ClickHouse DateTime64 TTL must use `toDateTime()` cast | Low | `TTL toDateTime(timestamp) + INTERVAL 30 DAY` — required for silver_trades |

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
- [HANDOFF-2026-02-18.md](./HANDOFF-2026-02-18.md) — Latest session narrative
- [docs/operations/adding-new-exchanges.md](../../operations/adding-new-exchanges.md) — How to add exchange #4
- [docs/operations/prefect-schedule-config.md](../../operations/prefect-schedule-config.md) — Prefect schedule setup
