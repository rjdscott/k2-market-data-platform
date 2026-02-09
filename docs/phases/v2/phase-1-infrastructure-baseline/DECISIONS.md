# Phase 1: Infrastructure Baseline - Decisions Log

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Decision 2026-02-09: Greenfield v2 Build vs Incremental Migration

**Status:** Accepted
**Impact:** High (changes entire migration approach)

### Context
Original plan called for incremental migration: version v1 docker-compose, then gradually replace components (Kafka→Redpanda, add ClickHouse, etc.). However, this approach carries technical debt forward and makes it harder to apply modern best practices.

### Decision
Build v2 as a **greenfield docker-compose stack** from scratch, running in parallel with v1. Instead of migrating the old system, we build the new system clean using 2026 best practices.

### Rationale
1. **Clean Architecture**: Start with modern design patterns, no legacy constraints
2. **Latest Versions**: Use current stable releases (Redpanda 25.x, ClickHouse 26.x, Kotlin 2.3.x, Spring Boot 4.x)
3. **Best Practices**: Proper health checks, resource limits, security, observability from day 1
4. **Parallel Validation**: Run v2 alongside v1, validate independently before cutover
5. **Easier Rollback**: v1 remains untouched and running; rollback is just switching symlink
6. **Technical Debt**: Zero legacy baggage carried forward

### Consequences

**Positive:**
- Clean, modern codebase with 2026 best practices
- Independent testing without affecting v1
- Can use latest stable component versions
- Better documentation (building from scratch forces explicit decisions)
- Easier to optimize from the start (no migration compromises)

**Negative:**
- More upfront design work (but better long-term)
- Need to rebuild monitoring/observability (but with modern tooling)
- Parallel infrastructure during migration (temporary resource overhead)

**Neutral:**
- Phase timeline unchanged (1 week for Phase 1)
- v1 remains as rollback target indefinitely

### Implementation
- Keep `docker/v1-baseline.yml` as safety net
- Create `docker-compose.v2.yml` from scratch
- Build incrementally: core infrastructure → streaming → processing → API
- Validate each component before adding next

---

## Decision 2026-02-09: Component Version Selection

**Status:** Accepted
**Impact:** Medium (foundational technology choices)

### Component Versions (Feb 2026)

| Component | Version | Rationale |
|-----------|---------|-----------|
| **Redpanda** | v25.3.4 | Latest stable (Feb 2026), mature Kafka-compatible streaming platform |
| **Redpanda Console** | v3.5.1 | Latest console UI (Jan 2026) |
| **ClickHouse** | v26.1 | Latest feature release with newest enhancements, stable release cycle |
| **Kotlin** | 2.3.10 | Current stable (Feb 2026), modern language features |
| **Spring Boot** | 4.0.2 | Latest major release (Jan 2026), virtual threads, native compilation |
| **Apache Spark** | 4.1.1 | Latest stable (Jan 2026), batch-only for cold tier maintenance |
| **Apache Iceberg** | 1.9.2 | Latest stable (May 2025), no 2.0 exists yet |
| **JDK** | 21 LTS | Spring Boot 4.x baseline, virtual threads support |

### Version Selection Criteria
1. **Stability**: Production-ready, not bleeding edge
2. **Support**: Active maintenance, security patches
3. **Compatibility**: Components integrate well
4. **Performance**: Proven at scale
5. **Modernity**: 2026 best practices (virtual threads, async I/O, etc.)

### Trade-offs

**ClickHouse 26.1 vs 25.8 LTS:**
- Chose 26.1 (latest features) over 25.8 LTS
- Reason: We're building greenfield, want latest performance optimizations
- Risk: Slightly less battle-tested than LTS
- Mitigation: Easy to downgrade to 25.8 LTS if issues arise

**Spring Boot 4.0.2 vs 3.5.x:**
- Chose 4.0.2 (latest major)
- Reason: Virtual threads (Project Loom), native image improvements, modern Spring ecosystem
- Risk: Newer major version, potential ecosystem lag
- Mitigation: Spring Boot 4.x is stable release, strong community

**JDK 21 LTS:**
- Virtual threads (critical for high-concurrency Kotlin services)
- Spring Boot 4.x requires JDK 17+, recommends 21+
- LTS support through 2029

### Research Sources
- Redpanda: [Docker Hub](https://hub.docker.com/r/redpandadata/redpanda), [GitHub Releases](https://github.com/redpanda-data/redpanda/releases)
- ClickHouse: [GitHub Releases](https://github.com/ClickHouse/ClickHouse/releases), [Changelog 2026](https://clickhouse.com/docs/whats-new/changelog)
- Kotlin: [Release Process](https://kotlinlang.org/docs/releases.html), [GitHub Releases](https://github.com/jetbrains/kotlin/releases)
- Spring Boot: [Release Notes](https://github.com/spring-projects/spring-boot/releases), [Maven Central](https://mvnrepository.com/artifact/org.springframework.boot/spring-boot)
- Apache Spark: [Downloads](https://spark.apache.org/downloads.html), [4.1.1 Release](https://spark.apache.org/releases/spark-release-4.1.1.html)
- Apache Iceberg: [Releases](https://iceberg.apache.org/releases/)

---

**Last Updated:** 2026-02-09
**Decisions:** 2
**Next Review:** After Phase 1 completion
