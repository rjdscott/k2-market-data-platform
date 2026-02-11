# ADR-012: Spark 4.1.1 and Iceberg 1.10.1 Version Upgrade

**Status**: Superseded by [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md)
**Date**: 2026-02-11
**Superseded Date**: 2026-02-11 (same day, after 2+ hours compatibility troubleshooting)
**Deciders**: Platform Engineering
**Related Phase**: Phase 5 (Cold Tier Restructure)
**Related ADRs**: [ADR-006 (Spark Batch Only)](ADR-006-spark-batch-only.md), [ADR-007 (Iceberg Cold Storage)](ADR-007-iceberg-cold-storage.md), [ADR-013 (Pragmatic Version Strategy - Supersedes this)](ADR-013-pragmatic-iceberg-version-strategy.md)

---

## ⚠️ Supersession Notice

**This ADR was superseded by [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) on the same day it was created**, after encountering persistent compatibility issues with Spark 4.1.1 + Iceberg 1.10.1 combination.

**Reason for supersession**: While all components worked individually, table creation failed with "Invalid table identifier" errors despite 2+ hours of troubleshooting and REST catalog upgrades. The team pragmatically pivoted to Apache's proven `tabulario/spark-iceberg:3.5.6_1.9.0` image to unblock Phase 5.

**Learning**: Bleeding-edge version combinations require community validation period. See ADR-013 for full analysis.

---

## Context

During Phase 5 implementation (Iceberg cold tier restructure), we discovered our initial technology versions were significantly behind current stable releases:

**Initial Versions** (Phase 5 planning - Feb 9, 2026):
- Apache Spark: 3.5.4 (released Oct 2024)
- Apache Iceberg: 1.5.2 (released Apr 2024)
- Scala: 2.12

**Latest Stable Versions** (as of Feb 11, 2026):
- Apache Spark: 4.1.1 (released Jan 9, 2026) [[1]](https://spark.apache.org/releases/spark-release-4.1.0.html)
- Apache Iceberg: 1.10.1 (released Jan 9, 2026) [[2]](https://iceberg.apache.org/releases/)
- Scala: 2.13 (standard for Spark 4.x)

**Gap Analysis**:
- Spark: ~15 months behind (major version gap: 3.5 → 4.1)
- Iceberg: ~10 months behind (5 minor versions: 1.5 → 1.10)
- Missing 5 Iceberg releases: 1.6.0, 1.7.0, 1.8.0, 1.9.x, 1.10.x
- Missing Spark 4.0.0, 4.0.1, 4.0.2, 4.1.0, 4.1.1 releases

**Critical Compatibility Discovery**:
Research confirmed that **Iceberg 1.5-1.9 do NOT support Spark 4.x**. Spark 4.0 compatibility was introduced in **Iceberg 1.10.0** (Sep 2025) [[3]](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html). This means our initial version combination (Spark 3.5.4 + Iceberg 1.5.2) would block any future Spark 4.x adoption.

**Problem Statement**:
Building a greenfield cold storage tier with 10-15 month old versions creates technical debt from day zero. Given Phase 5 is net-new infrastructure (no migration required), we have a zero-cost opportunity to adopt latest stable versions and avoid compatibility issues.

---

## Decision

**Adopt latest stable versions for Phase 5 Iceberg cold tier**:

| Component | Version | Scala | Release Date | Maven Artifact |
|-----------|---------|-------|--------------|----------------|
| **Apache Spark** | 4.1.1 | 2.13 | Jan 9, 2026 | `apache/spark:4.1.1-scala2.13-java17-python3-ubuntu` |
| **Apache Iceberg** | 1.10.1 | 2.13 | Jan 9, 2026 | `org.apache.iceberg:iceberg-spark-runtime-4.0_2.13:1.10.1` |
| **Hadoop AWS** | 3.4.1 | - | Latest | `org.apache.hadoop:hadoop-aws:3.4.1` |
| **AWS SDK Bundle** | 2.29.51 | - | Latest | `software.amazon.awssdk:bundle:2.29.51` |

**Key Changes**:
1. Spark 3.5.4 → 4.1.1 (major version upgrade)
2. Iceberg 1.5.2 → 1.10.1 (5 minor versions, includes Spark 4.x support)
3. Scala 2.12 → 2.13 (Spark 4.x standard)
4. Iceberg runtime artifact: `iceberg-spark-runtime-3.5_2.12` → `iceberg-spark-runtime-4.0_2.13`

**Rationale**:
- **Zero migration cost**: Phase 5 is greenfield (no existing Iceberg tables to migrate)
- **Future-proof**: Spark 4.x is the current stable line; staying on 3.5.x means immediate obsolescence
- **Bug fixes**: 5 Iceberg minor releases contain critical bug fixes and performance improvements
- **Spark 4.0 compatibility**: Iceberg 1.10.0+ required for Spark 4.x [[3]](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html)
- **Security**: Latest versions include recent security patches
- **V3 spec maturity**: Iceberg 1.10.x matures the V3 table format spec [[4]](https://www.snowflake.com/en/engineering-blog/apache-iceberg-1-10-new-features-fixes/)

---

## Consequences

### Positive

1. **Future-proof architecture**: Spark 4.1.1 is the current stable line (released Jan 2026)
2. **Performance improvements**: Spark 4.x includes significant optimizations for Iceberg [[3]](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html)
3. **Bug fixes**: 5 Iceberg releases (1.6-1.10) worth of stability improvements
4. **REST catalog maturity**: Iceberg 1.10.x improves REST catalog implementation
5. **V3 format support**: Access to latest Iceberg table format features
6. **Zero migration cost**: Greenfield deployment means no backward compatibility concerns
7. **Longer support window**: Latest versions will have longer upstream support lifecycle
8. **Better documentation**: Recent versions have more current examples and community knowledge

### Negative

1. **Spark 4.x breaking changes**: Major version may include breaking changes from 3.5.x
   - **Mitigation**: Phase 5 is greenfield - no existing Spark SQL scripts to migrate

2. **Less mature ecosystem**: Spark 4.x (6 months old) has smaller knowledge base than 3.5.x (2 years old)
   - **Mitigation**: Official Iceberg 1.10+ explicitly supports Spark 4.0, indicating maturity [[3]](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html)

3. **Scala 2.13 requirement**: Must use Scala 2.13 (not 2.12) for Spark 4.x
   - **Mitigation**: v2 platform is Kotlin-based; Spark is batch-only with minimal Scala surface area

4. **Docker image size**: Spark 4.x image may be larger than 3.5.x
   - **Mitigation**: Spark runs ephemeral (profile-based) - startup time and disk space not critical

5. **Potential unknown bugs**: Recent versions may have undiscovered edge cases
   - **Mitigation**: Both released Jan 9, 2026 (~1 month of production use); Phase 5 is cold tier (non-critical path)

### Neutral

1. **Maven coordinates change**: `iceberg-spark-runtime-3.5_2.12` → `iceberg-spark-runtime-4.0_2.13`
2. **Configuration compatibility**: Most Spark configurations carry forward 3.x → 4.x
3. **REST catalog wire protocol**: Iceberg REST protocol is version-agnostic

---

## Implementation Notes

### Docker Image Update

```yaml
# Before (Phase 5 initial planning)
spark:
  image: apache/spark:3.5.4-scala2.12-java17-python3-ubuntu

# After (ADR-012)
spark:
  image: apache/spark:4.1.1-scala2.13-java17-python3-ubuntu
```

### Maven Package Update

```bash
# Before (Phase 5 initial planning)
PACKAGES="org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,
          org.apache.hadoop:hadoop-aws:3.3.4,
          org.apache.iceberg:iceberg-aws:1.5.2,
          software.amazon.awssdk:bundle:2.20.18"

# After (ADR-012)
PACKAGES="org.apache.iceberg:iceberg-spark-runtime-4.0_2.13:1.10.1,
          org.apache.hadoop:hadoop-aws:3.4.1,
          org.apache.iceberg:iceberg-aws:1.10.1,
          software.amazon.awssdk:bundle:2.29.51"
```

### Breaking Changes to Monitor

From Spark 3.5 → 4.x:
- Default behavior changes in SQL parsing (check Spark 4.0 migration guide)
- Deprecated API removal (unlikely to affect our DDL-only usage)
- Configuration key renames (audit during testing)

**Action**: Review [Spark 4.0 Migration Guide](https://spark.apache.org/docs/latest/sql-migration-guide.html) before production deployment.

### Testing Strategy

**Phase 5 Step 1 Validation** (immediate):
1. Create 9 Iceberg tables via Spark 4.1.1 + Iceberg 1.10.1
2. Verify partitioning, compression, and catalog metadata
3. Insert sample data and confirm Parquet/Zstd writes to MinIO
4. Query tables via Spark SQL to validate read path

**Phase 5 Step 4 Validation** (daily maintenance):
1. Test Spark 4.1.1 compaction jobs on hourly Parquet files
2. Verify snapshot expiry (7-day retention)
3. Confirm row count audit queries execute correctly

**Compatibility with ClickHouse** (Phase 5 Step 5):
1. Validate ClickHouse Iceberg table engine can read Spark 4.1-written tables
2. Test federated queries across ClickHouse (warm) + Iceberg (cold)

---

## Alternatives Considered

### Alternative 1: Stay on Spark 3.5.4 + Iceberg 1.5.2

**Rationale**: Minimize risk by using well-tested versions

**Rejected because**:
- Creates 10-15 months of technical debt on day zero
- Iceberg 1.5.2 (Apr 2024) lacks 5 minor releases of bug fixes
- Blocks future Spark 4.x adoption (would require Iceberg data migration)
- Spark 3.5.x will reach EOL sooner than 4.x
- No benefit: Phase 5 is greenfield - no migration cost to absorb

### Alternative 2: Spark 3.5.4 + Iceberg 1.10.1 (hybrid)

**Rationale**: Get Iceberg improvements without Spark major version jump

**Rejected because**:
- Iceberg 1.10.1 is designed for Spark 4.x (primary testing target [[3]](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html))
- Still blocks Spark 4.x adoption (would require re-testing compatibility)
- No risk reduction: Spark 4.1.1 + Iceberg 1.10.1 is the validated pairing
- Defers inevitable Spark 4.x upgrade

### Alternative 3: Spark 4.1.1 + Iceberg 1.9.1

**Rationale**: Use latest "stable" Iceberg from before Spark 4.0 support

**Rejected because**:
- **Incompatible**: Iceberg 1.9.x does NOT support Spark 4.x [[5]](https://github.com/apache/iceberg/issues/13358)
- Spark 4.0 runtime artifacts only available in Iceberg 1.10.0+ [[6]](https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/)
- Would require immediate Iceberg upgrade to 1.10.x

### Alternative 4: Wait for Spark 4.2.0 or Iceberg 1.11.0

**Rationale**: Use "more mature" future versions

**Rejected because**:
- Spark 4.2.0 is preview-only (not production-ready as of Feb 2026)
- Iceberg 1.11.0 has no release date
- Blocks Phase 5 progress for uncertain future releases
- Current versions (Spark 4.1.1, Iceberg 1.10.1) are stable (released Jan 9, 2026)

---

## Verification Checklist

- [ ] Spark 4.1.1 Docker image pulls successfully
- [ ] Iceberg 1.10.1 Maven artifacts resolve correctly
- [ ] Spark SQL connects to Iceberg REST catalog (http://iceberg-rest:8181)
- [ ] All 9 Iceberg tables created successfully (Bronze: 2, Silver: 1, Gold: 6)
- [ ] Partitioning verified (days/months transforms working)
- [ ] Zstd compression level 3 applied to Parquet files
- [ ] Sample data written to MinIO (s3a://warehouse/cold/*)
- [ ] Tables queryable via Spark SQL (`SHOW TABLES IN cold;`)
- [ ] Snapshot metadata visible in PostgreSQL catalog
- [ ] ClickHouse Iceberg table engine compatibility verified (Phase 5 Step 5)

---

## References

1. [Apache Spark 4.1.1 Release](https://spark.apache.org/releases/spark-release-4.1.0.html)
2. [Apache Iceberg Releases](https://iceberg.apache.org/releases/)
3. [Apache Iceberg 1.10: Spark 4.0 Compatibility](https://opensource.googleblog.com/2025/09/apache-iceberg-110-maturing-the-v3-spec-the-rest-api-and-google-contributions.html)
4. [Snowflake: Apache Iceberg 1.10 Features](https://www.snowflake.com/en/engineering-blog/apache-iceberg-1-10-new-features-fixes/)
5. [GitHub Issue: Iceberg Spark 4.0 Guidance](https://github.com/apache/iceberg/issues/13358)
6. [Maven Central: iceberg-spark-runtime-4.0_2.13](https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/)
7. [Iceberg Multi-Engine Support](https://iceberg.apache.org/multi-engine-support/)
8. [Spark 4.0 Migration Guide](https://spark.apache.org/docs/latest/sql-migration-guide.html)

---

## Decision Log

| Date | Event | Outcome |
|------|-------|---------|
| 2026-02-09 | Phase 5 planning started | Used Spark 3.5.4 + Iceberg 1.5.2 (outdated) |
| 2026-02-11 | Version audit during Step 1 implementation | Discovered 10-15 month version gap |
| 2026-02-11 | Compatibility research | Found Iceberg 1.10.0+ required for Spark 4.x |
| 2026-02-11 | **ADR-012 approved** | **Upgrade to Spark 4.1.1 + Iceberg 1.10.1** |

---

**Last Updated**: 2026-02-11
**Status**: Accepted
**Next Review**: After Phase 5 Step 1 completion (validate DDL execution)
