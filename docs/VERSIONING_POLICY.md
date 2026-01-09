# Versioning & Release Policy

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Release Engineering
**Status**: Implementation Plan
**Scope**: Semantic versioning, backward compatibility, deprecation process

---

## Overview

Versioning enables safe evolution of the platform while maintaining stability for consumers. This document defines our versioning scheme, compatibility guarantees, deprecation timeline, and migration procedures.

**Design Philosophy**: Stability for consumers, flexibility for platform evolution. Breaking changes are expensive - avoid them when possible, manage them carefully when necessary.

---

## Semantic Versioning (SemVer)

### Version Format: MAJOR.MINOR.PATCH

**Format**: `X.Y.Z` (e.g., `v2.3.1`)

- **MAJOR** (X): Breaking changes, incompatible API/schema modifications
- **MINOR** (Y): New features, backward-compatible additions
- **PATCH** (Z): Bug fixes, backward-compatible corrections

### Version Examples

| Change | Old Version | New Version | Rationale |
|--------|-------------|-------------|-----------|
| Fix schema validation bug | v1.2.3 | v1.2.4 | PATCH: Bug fix |
| Add new optional Avro field | v1.2.4 | v1.3.0 | MINOR: Backward compatible |
| Remove deprecated API endpoint | v1.3.0 | v2.0.0 | MAJOR: Breaking change |
| Add query result caching | v2.0.0 | v2.1.0 | MINOR: New feature |
| Fix Iceberg write deadlock | v2.1.0 | v2.1.1 | PATCH: Bug fix |

---

## Backward Compatibility Guarantees

### Within Major Version (v1.x.x)

**Guaranteed Stable**:
- **API Endpoints**: Existing endpoints remain functional
- **Schema Compatibility**: Schema Registry enforces BACKWARD compatibility
- **Configuration Format**: Config files remain compatible
- **Kafka Topic Structure**: Topic names and partitioning unchanged
- **Iceberg Table Schema**: Tables remain queryable (Iceberg handles evolution)

**Allowed Changes**:
- Add new optional fields to schemas (with defaults)
- Add new API endpoints
- Add new configuration options (with defaults)
- Performance improvements
- Bug fixes

**Forbidden Changes**:
- Remove API endpoints (deprecate first)
- Remove schema fields (deprecate first)
- Change Kafka partition key (breaks ordering)
- Rename Iceberg tables (breaks queries)
- Change required field types (incompatible)

### Across Major Versions (v1 → v2)

**Breaking Changes Allowed**:
- Remove deprecated endpoints/fields
- Change default behavior
- Require new mandatory configuration
- Reorganize codebase structure
- Upgrade dependencies with breaking changes

**Migration Support**:
- v1 supported for 6 months after v2 release
- Migration guide provided with v2 release
- Automated migration tooling where possible

---

## Version Compatibility Matrix

### Platform Components

| Component | Version | Compatible With |
|-----------|---------|-----------------|
| **Kafka** | v7.6.x | All platform versions |
| **Schema Registry** | v7.6.x | All platform versions |
| **Iceberg** | v1.4.x | Platform v1.0+ |
| **DuckDB** | v0.10.x | Platform v1.0+ |
| **Query API** | v1.3.0 | Clients v1.0+ |

### Client Compatibility

**Query API v1.x**:
- Clients v1.0-v1.9 → Fully compatible
- Clients v0.9 → Deprecated, upgrade recommended
- Clients v2.0 → Not compatible (future, not yet released)

**Schema Registry**:
- Schema compatibility mode: `BACKWARD`
- Producers can evolve schemas (add optional fields)
- Consumers always compatible (ignore unknown fields)

---

## Deprecation Process

### Timeline

**Standard Deprecation**: 90 days (3 months)

```
Day 0:    Announce deprecation (release notes, email, docs)
Day 30:   Add deprecation warnings (logs, API responses)
Day 60:   Increase warning severity (console warnings)
Day 90:   Remove feature in next MAJOR version
```

### Critical Components

**Extended Deprecation**: 180 days (6 months)

Applies to:
- Core API endpoints (used by 10+ teams)
- Schema changes affecting multiple producers
- Kafka topic restructuring

### Deprecation Annotations

**Code-Level**:
```python
import warnings
from functools import wraps

def deprecated(reason: str, removal_version: str):
    """Mark function as deprecated."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            warnings.warn(
                f"{func.__name__} is deprecated: {reason}. "
                f"Will be removed in {removal_version}.",
                DeprecationWarning,
                stacklevel=2
            )
            return func(*args, **kwargs)
        return wrapper
    return decorator

# Usage
@deprecated(reason="Use query_v2() instead", removal_version="v2.0.0")
def query_v1(symbol: str):
    """Old query method (deprecated)."""
    pass
```

**API-Level**:
```yaml
# OpenAPI spec
/api/v1/query:
  get:
    deprecated: true
    x-deprecation:
      removal_version: "2.0.0"
      alternative: "/api/v2/query"
      reason: "v1 API lacks pagination support"
```

**Schema-Level**:
```avro
{
  "type": "record",
  "name": "MarketTick",
  "fields": [
    {
      "name": "old_field",
      "type": ["null", "string"],
      "default": null,
      "doc": "DEPRECATED: Use new_field instead. Will be removed in schema v3.0."
    },
    {
      "name": "new_field",
      "type": "string"
    }
  ]
}
```

---

## Release Process

### Release Cadence

**Regular Releases**:
- **PATCH**: Every 2 weeks (bug fixes)
- **MINOR**: Every 6 weeks (new features)
- **MAJOR**: Every 12 months (breaking changes)

**Hotfix Releases**:
- Critical bugs (P0/P1) → Release within 24 hours
- Security vulnerabilities → Release within 12 hours

### Release Checklist

```markdown
## Pre-Release (T-1 week)

- [ ] Update CHANGELOG.md with all changes
- [ ] Update version in pyproject.toml
- [ ] Run full test suite (unit + integration + performance)
- [ ] Update documentation (README, API docs)
- [ ] Create migration guide (if MAJOR version)
- [ ] Notify stakeholders (platform-announce@)

## Release Day (T-0)

- [ ] Create Git tag: `git tag -a v1.3.0 -m "Release v1.3.0"`
- [ ] Push tag: `git push origin v1.3.0`
- [ ] Build and publish Docker images
- [ ] Deploy to staging environment
- [ ] Run smoke tests in staging
- [ ] Deploy to production (canary rollout)
- [ ] Monitor metrics for 24 hours

## Post-Release (T+1 day)

- [ ] Publish release notes (blog post, docs site)
- [ ] Email announcement to all users
- [ ] Update roadmap with completed items
- [ ] Create GitHub release with artifacts
```

### Canary Deployment

**Phased Rollout**:
```
1. Deploy to 5% of production traffic (1 hour)
2. Monitor error rate, latency, CPU/memory
3. If metrics stable → Deploy to 25% (2 hours)
4. If metrics stable → Deploy to 50% (4 hours)
5. If metrics stable → Deploy to 100%

Rollback criteria:
- Error rate increase > 2%
- Latency p99 increase > 50%
- Any P0/P1 incident
```

---

## Version Numbering Examples

### Scenario 1: Bug Fix Release

**Current**: v1.2.3
**Change**: Fix Iceberg write deadlock

```bash
# Update version
sed -i 's/version = "1.2.3"/version = "1.2.4"/' pyproject.toml

# Commit and tag
git commit -m "fix: resolve Iceberg write deadlock"
git tag -a v1.2.4 -m "Release v1.2.4: Fix Iceberg deadlock"
git push origin v1.2.4
```

**New Version**: v1.2.4 (PATCH bump)

### Scenario 2: New Feature Release

**Current**: v1.2.4
**Change**: Add query result caching

```bash
# Update version
sed -i 's/version = "1.2.4"/version = "1.3.0"/' pyproject.toml

# Commit and tag
git commit -m "feat: add query result caching with Redis"
git tag -a v1.3.0 -m "Release v1.3.0: Query result caching"
git push origin v1.3.0
```

**New Version**: v1.3.0 (MINOR bump, PATCH reset to 0)

### Scenario 3: Breaking Change Release

**Current**: v1.9.8
**Change**: Remove deprecated query_v1 API

```bash
# Update version (MAJOR bump)
sed -i 's/version = "1.9.8"/version = "2.0.0"/' pyproject.toml

# Create migration guide
cat > docs/migration-v2.md <<EOF
# Migration Guide: v1 → v2

## Breaking Changes

1. **query_v1() removed**
   - Old: `query_v1(symbol='BHP')`
   - New: `query_v2(symbol='BHP', pagination=True)`

2. **Default pagination enabled**
   - All queries now paginated by default (limit=1000)
   - Use `limit=None` for unpaginated (not recommended)
EOF

# Commit and tag
git commit -m "feat!: remove deprecated query_v1 API (BREAKING)"
git tag -a v2.0.0 -m "Release v2.0.0: Remove query_v1 (BREAKING)"
git push origin v2.0.0
```

**New Version**: v2.0.0 (MAJOR bump, MINOR and PATCH reset to 0)

---

## Version Support Policy

### Support Lifecycle

| Phase | Duration | Support Level |
|-------|----------|---------------|
| **Current** | Until next MAJOR | Full support (features + bugs) |
| **Maintenance** | 6 months after next MAJOR | Security + critical bugs only |
| **End of Life (EOL)** | After maintenance period | No support |

### Example Timeline

```
2026-01-01: v1.0.0 released (Current)
2026-07-01: v2.0.0 released
            ├─ v1.0.0 enters Maintenance (security fixes only)
            └─ v2.0.0 becomes Current

2027-01-01: v1.0.0 reaches EOL (no support)
            └─ All users must upgrade to v2.x

2027-07-01: v3.0.0 released
            ├─ v2.0.0 enters Maintenance
            └─ v3.0.0 becomes Current
```

---

## Upgrade Procedures

### Minor Version Upgrade (v1.2 → v1.3)

**Risk**: Low (backward compatible)

**Steps**:
```bash
# 1. Review release notes
curl https://k2platform.com/releases/v1.3.0/CHANGELOG.md

# 2. Update dependencies
pip install --upgrade k2-platform==1.3.0

# 3. Restart services (rolling restart, no downtime)
kubectl rollout restart deployment/query-api
kubectl rollout restart deployment/ingestion-consumer

# 4. Verify health
kubectl rollout status deployment/query-api
curl https://api.k2platform.com/health

# Expected: All checks pass, version shows v1.3.0
```

### Major Version Upgrade (v1.x → v2.0)

**Risk**: High (breaking changes)

**Steps**:
```bash
# 1. Review migration guide
curl https://k2platform.com/releases/v2.0.0/MIGRATION.md

# 2. Update client code (breaking changes)
# - Replace query_v1() with query_v2()
# - Add pagination handling
# - Update configuration format

# 3. Test in staging environment
kubectl --context staging apply -f k8s/v2.0.0/

# 4. Run migration tests
pytest tests/migration/v1_to_v2.py

# 5. Deploy to production (canary rollout)
kubectl --context production apply -f k8s/v2.0.0/
kubectl rollout status deployment/query-api

# 6. Monitor for 24 hours
# - Check error rates
# - Verify client compatibility
# - Monitor rollback triggers

# 7. If stable, complete rollout
kubectl rollout resume deployment/query-api
```

---

## Version Tags and Branching

### Git Branching Strategy

**Main Branches**:
- `main`: Current stable release
- `develop`: Next release candidate

**Release Branches**:
- `release/v1.3.0`: Release candidate for v1.3.0
- `release/v2.0.0`: Release candidate for v2.0.0

**Hotfix Branches**:
- `hotfix/v1.2.4`: Critical bugfix for v1.2.3

### Tagging Convention

```bash
# Release tags
v1.2.3          # Stable release
v1.3.0-rc.1     # Release candidate 1
v1.3.0-beta.2   # Beta release 2
v2.0.0-alpha.1  # Alpha release 1

# Metadata tags (optional)
v1.2.3+build.123  # Build metadata
```

---

## Monitoring Version Adoption

### Metrics

```
# Active versions by client
platform_version_active{version="1.2.3", client="query-api"}
platform_version_active{version="1.3.0", client="query-api"}

# Deprecated feature usage
deprecated_feature_calls_total{feature="query_v1", version="1.9.8"}

# Version upgrade lag
platform_version_age_days{current_version, latest_version}
```

### Dashboards

**Version Adoption Dashboard**:
- Active versions (pie chart)
- Upgrade lag distribution (histogram)
- Deprecated feature usage (time series)
- EOL warnings (count)

**Alerts**:
```yaml
# Clients on EOL version
- alert: ClientsOnEOLVersion
  expr: platform_version_age_days > 365
  severity: warning
  summary: "Clients still using EOL version (> 1 year old)"

# High deprecated feature usage
- alert: DeprecatedFeatureUsage
  expr: rate(deprecated_feature_calls_total[5m]) > 100
  severity: warning
  summary: "High usage of deprecated feature (removal imminent)"
```

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Backward compatibility principle
- [Testing Strategy](./TESTING_STRATEGY.md) - Upgrade testing procedures
- [RFC Template](./RFC_TEMPLATE.md) - Process for proposing breaking changes
