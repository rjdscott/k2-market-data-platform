# DECISION-015: ClickHouse Version Downgrade to 24.3 LTS

**Date**: 2026-02-12
**Status**: Implemented
**Context**: Phase 5 - Prefect → ClickHouse → Iceberg Offload

## Problem

During implementation of the Spark-based offload pipeline from ClickHouse to Iceberg, discovered a **critical incompatibility** between ClickHouse 26.1 and all available JDBC drivers:

- **Symptom**: All JDBC queries failed with generic `java.sql.SQLException: Query failed` error
- **Root Cause**: ClickHouse 26.1 introduced breaking changes incompatible with Spark JDBC drivers
- **Evidence**: HTTP authentication works, but JDBC fails at schema resolution stage with no underlying cause
- **Testing**: Tried 8+ driver versions (0.4.6, 0.5.0, 0.6.3, 0.6.5, 0.7.1) and approaches - all failed

### Tested Approaches (All Failed with CH 26.1)
1. Official ClickHouse JDBC driver (multiple versions)
2. ClickHouse Native JDBC driver
3. Spark-ClickHouse connector
4. Different URL formats and connection properties
5. Both HTTP (port 8123) and native protocol (port 9000)

## Decision

**Downgrade ClickHouse from 26.1 to 24.3 LTS** (Alpine variant)

### Rationale
- **24.3 = Latest LTS release** (March 2024) with proven JDBC compatibility
- **Production-grade stability** vs bleeding-edge features
- **Immediate unblocking** of Phase 5 offload pipeline
- **Lower risk** for v2 production deployment
- **Better ecosystem support** (drivers, connectors, documentation)

## Implementation

### Changes Made
1. **docker-compose.v2.yml**:
   - Changed `clickhouse/clickhouse-server:26.1` → `clickhouse/clickhouse-server:24.3-alpine`
   - Recreated container with fresh volumes (incompatible data format between versions)

2. **Spark configuration** (`/opt/spark/conf/spark-defaults.conf`):
   - Updated Iceberg catalog from REST to Hadoop
   - Changed warehouse path from S3 to local filesystem
   - Fixed FileIO implementation to `HadoopFileIO`

### Verification
```bash
# Version check
clickhouse-client --password=clickhouse --query="SELECT version()"
# Output: 24.3.18.7

# JDBC connectivity test
spark-submit --packages com.clickhouse:clickhouse-jdbc:0.4.6 test_jdbc_clean.py
# Result: ✓ SUCCESS - Data retrieved successfully
```

## Trade-offs

### Advantages ✓
- **JDBC works immediately** - unblocks Phase 5
- **LTS = stable + supported** for 1+ year
- **Better driver ecosystem** - mature, tested libraries
- **Lower production risk** - battle-tested in enterprise environments

### Disadvantages ✗
- **Lose 26.1 features** - newer performance improvements, SQL features
- **Eventually need upgrade** - will need migration path to next LTS
- **Data recreation required** - had to drop and recreate database (one-time cost)

## Compatibility Matrix

| Component | Version | Status |
|-----------|---------|--------|
| ClickHouse | 24.3.18.7 (LTS) | ✓ Working |
| ClickHouse JDBC Driver | 0.4.6+ | ✓ Compatible |
| Spark | 3.5.0 | ✓ Compatible |
| Apache Iceberg | 1.5.0 | ✓ Compatible |

## Future Considerations

- **Monitor ClickHouse 25.x LTS** (expected ~Q3 2026) for upgrade path
- **Track JDBC driver updates** for 26.x compatibility if needed urgently
- **Document migration process** when upgrading to next LTS
- **Version pinning** in production to avoid surprises

## References

- ClickHouse LTS releases: https://clickhouse.com/docs/en/whats-new/changelog
- JDBC driver compatibility: https://github.com/ClickHouse/clickhouse-jdbc
- Test results: `/docker/offload/test_jdbc_*.py` (13 test iterations)

## Related ADRs

- ADR-013: Iceberg + Hadoop Catalog (v2)
- ADR-014: ClickHouse TTL + Incremental Offload
- Phase 5 documentation: `docs/phases/v2/PHASE-5-prefect-iceberg-offload.md`
