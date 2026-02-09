# V1 Platform Archival Plan

**Date**: 2026-02-09
**Purpose**: Archive v1 platform components while preserving historical context
**Strategy**: Preserve learnings, archive artifacts, clean active workspace

---

## Archival Philosophy

### What to Archive
✅ Implementation artifacts no longer in use
✅ Temporary progress tracking documents
✅ Obsolete operational runbooks (v1-specific)
✅ Old implementation logs and session notes
✅ Deprecated configuration files
✅ Superseded documentation

### What to Keep Active
✅ Architecture decisions (ADRs) - moved to `docs/decisions/platform-v1/`
✅ Phase documentation - moved to `docs/phases/v1/`
✅ Lessons learned and completion reports
✅ Historical reference for "why we made v2"

### What to Delete
❌ Duplicate files
❌ Temporary scratch files
❌ Build artifacts
❌ Generated files that can be recreated

---

## Archive Structure

```
docs/archive/
├── v1-platform/                    # Main v1 archive (NEW)
│   ├── README.md                   # V1 overview and context
│   ├── infrastructure/             # V1 docker-compose, configs
│   ├── code-artifacts/             # V1 Python code references
│   ├── operations/                 # V1-specific runbooks
│   ├── sessions/                   # Implementation session logs
│   └── lessons-learned/            # Post-mortems, analyses
├── 2026-01-13-implementation-logs/ # Existing logs (keep)
├── reviews-historical/             # Historical reviews (keep)
└── [other archive files]           # Existing archives (keep)
```

---

## Archival Categories

### Category 1: V1 Infrastructure (Archive)

**Source**: Root directory
**Destination**: `docs/archive/v1-platform/infrastructure/`

Files to archive:
- `docker-compose.yml` (v1 baseline) → Already snapshotted as `docker/v1-baseline.yml`
- Any v1-specific .env files
- Old Kafka, Spark, Prefect configurations

**Action**:
```bash
# V1 docker-compose already preserved in docker/v1-baseline.yml
# No additional action needed
```

### Category 2: V1 Code Artifacts (Reference Only)

**Source**: `src/` directory
**Destination**: Keep in git history, document in archive README

**Action**:
- Create reference doc listing v1 code locations in git history
- Tag v1.0.0 provides access to all v1 code
- No need to duplicate in archive

### Category 3: V1 Operations (Archive)

**Source**: `docs/operations/runbooks/` (v1-specific)
**Destination**: `docs/archive/v1-platform/operations/`

Files that are v1-specific:
- Spark streaming troubleshooting
- Prefect workflow management
- DuckDB query optimization
- Kafka consumer tuning (for Spark)

**Action**: Review each runbook, archive if v1-specific

### Category 4: Implementation Sessions (Already Archived)

**Status**: ✅ Already in `docs/archive/2026-01-13-implementation-logs/`

**Action**: None needed

### Category 5: Progress Tracking (Archive Temporary)

**Source**: `docs/archive/`
**Destination**: `docs/archive/v1-platform/progress-tracking/`

Files that are temporary progress tracking:
- `PROGRESS_OLD.md`
- `TODO.md`
- `consolidation-PROGRESS.md`
- `consolidation-METRICS.md`

**Action**: Move to v1-platform subdirectory

### Category 6: Keep in Place (Active Reference)

**Location**: `docs/phases/v1/`
**Status**: ✅ Already moved in today's commit

All phase documentation is now under `docs/phases/v1/`:
- phase-0-technical-debt-resolution/
- phase-1-single-node-equities/
- phase-2-prep/
- phase-3-demo-enhancements/
- phase-4-demo-readiness/
- phase-5-binance-production-resilience/
- phase-6-cicd/
- phase-7-e2e/
- phase-8-e2e-demo/
- phase-9-demo-consolidation/
- phase-10-streaming-crypto/
- phase-11-production-readiness/
- phase-12-flink-bronze-implementation/
- phase-13-ohlcv-analytics/

---

## Execution Plan

### Phase 1: Create Archive Structure (Now)
```bash
mkdir -p docs/archive/v1-platform/{infrastructure,operations,sessions,lessons-learned,progress-tracking}
```

### Phase 2: Move Temporary Docs (Now)
```bash
cd docs/archive
mv PROGRESS_OLD.md v1-platform/progress-tracking/
mv TODO.md v1-platform/progress-tracking/
mv consolidation-PROGRESS.md v1-platform/progress-tracking/
mv consolidation-METRICS.md v1-platform/progress-tracking/
```

### Phase 3: Archive V1-Specific Operations (Review Required)
```bash
# Review each runbook in docs/operations/runbooks/
# Move v1-specific ones to docs/archive/v1-platform/operations/
# Update remaining runbooks to v2 or mark as deprecated
```

### Phase 4: Consolidate Implementation Artifacts (Now)
```bash
cd docs/archive
mv *IMPLEMENTATION* v1-platform/sessions/
mv *SUMMARY*.md v1-platform/lessons-learned/
mv *ANALYSIS*.md v1-platform/lessons-learned/
```

### Phase 5: Create V1 README (Now)
```bash
# Create comprehensive README in docs/archive/v1-platform/
# Document what v1 was, why we moved to v2, where to find things
```

### Phase 6: Update Root Documentation (Next)
```bash
# Update docs/README.md to clarify v1 vs v2
# Update docs/architecture/README.md to point to v1 archive
# Update docs/NAVIGATION.md for clarity
```

---

## Decision: Keep vs Archive

### Keep Active (Under docs/phases/v1/)

**Rationale**: Historical reference for decisions, patterns, and lessons
- ✅ Phase documentation (complete historical record)
- ✅ Architecture decisions (ADRs under docs/decisions/platform-v1/)
- ✅ Completion reports (valuable context)
- ✅ Validation guides (methodology reference)

### Archive (Under docs/archive/v1-platform/)

**Rationale**: Reduce clutter, preserve artifacts, maintain history
- 📦 Temporary progress tracking (PROGRESS_OLD.md, TODO.md)
- 📦 Implementation session logs (if not already archived)
- 📦 Consolidation metrics (one-time analysis)
- 📦 Crash analyses (point-in-time issues)
- 📦 Old requirements docs (superseded)

### Delete (If Found)

**Rationale**: Can be recreated or no longer relevant
- 🗑️ Build artifacts (.pyc, __pycache__, .pytest_cache)
- 🗑️ Temporary files (.tmp, .bak, .swp)
- 🗑️ Duplicate files (exact copies)
- 🗑️ Empty directories

---

## V1 Reference Guide (To Be Created)

Location: `docs/archive/v1-platform/README.md`

Contents:
1. **What Was V1**: Technology stack, architecture, components
2. **Why V2**: Resource efficiency, operational simplicity, performance
3. **Where to Find V1 Code**: Git tag v1.0.0, branches
4. **V1 Documentation Map**: Where each piece of v1 docs lives
5. **Migration Guide**: For teams transitioning from v1 to v2
6. **Lessons Learned**: What worked, what didn't, key insights
7. **Historical Metrics**: Performance, cost, operational overhead
8. **Decision Context**: Links to ADRs explaining v1 choices

---

## Validation Checklist

After archival:
- [ ] `docs/phases/v1/` contains all historical phase docs
- [ ] `docs/decisions/platform-v1/` contains all v1 ADRs
- [ ] `docs/archive/v1-platform/` has organized artifacts
- [ ] `docs/archive/v1-platform/README.md` explains structure
- [ ] Root READMEs updated to clarify v1 vs v2
- [ ] No broken links in documentation
- [ ] Git history preserved (v1.0.0 tag accessible)
- [ ] Duplicate files removed
- [ ] Active workspace is clean and focused on v2

---

## Next Steps

1. ✅ Create archive directory structure
2. ✅ Move temporary progress docs
3. ✅ Consolidate implementation artifacts
4. ✅ Create comprehensive v1-platform/README.md
5. 🟡 Review and archive v1-specific runbooks (requires manual review)
6. 🟡 Update root documentation references
7. 🟡 Validate no broken links
8. 🟡 Commit archival changes

---

## Benefits

### Immediate
- Cleaner workspace focused on v2 development
- Clear separation of v1 (historical) vs v2 (active)
- Easier navigation for new team members
- Reduced cognitive load

### Long-term
- Historical context preserved for future reference
- Decision rationale documented and accessible
- Lessons learned captured for next platform iteration
- Audit trail for compliance and knowledge transfer

---

**Status**: Draft - Ready for execution
**Owner**: Engineering Team
**Timeline**: Immediate (during cleanup phase)
