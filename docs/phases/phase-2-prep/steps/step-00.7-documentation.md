# Step 00.7: Documentation

**Status**: ⬜ Not Started  
**Estimated Time**: 3 hours  
**Actual Time**: -  
**Part**: Schema Evolution (Step 0)

---

## Goal

Document v2 schemas, migration strategy, and field mappings.

---

## Tasks

### 1. Update README.md

Add "Schema Evolution" section:

```markdown
## Schema Evolution

K2 uses v2 industry-standard hybrid schemas:
- **Core fields**: Standard across all exchanges
- **vendor_data**: Exchange-specific extensions

### Key Changes (v1 → v2)
- `volume` → `quantity` (clearer semantics)
- Added: `message_id` (UUID for deduplication)
- Added: `side` (BUY/SELL/SELL_SHORT/UNKNOWN)
- Added: `currency` (AUD/USD/USDT)
- Added: `asset_class` (equities/crypto/futures)
- ASX-specific fields moved to `vendor_data` map
```

### 2. Create docs/architecture/schema-design-v2.md

```markdown
# v2 Schema Design

## Overview
Industry-standard hybrid schemas with vendor extensions.

## Design Decisions
- Hybrid approach: core + vendor_data
- Follows FIX Protocol naming conventions
- timestamp-micros for precision
- Decimal (18,8) for price/quantity

## Field-by-Field Documentation
[Document each field...]

## Vendor Extension Patterns
[Document how to use vendor_data...]

## Migration Guide
[Document v1 → v2 migration...]
```

### 3. Update DECISIONS.md

Add Decision #015: Schema Evolution to v2

### 4. Update Demo Script

Mention v2 schemas in demo narrative.

---

## Validation

```bash
# Check README updated
grep -A 5 "Schema Evolution" README.md

# Check schema-design-v2.md exists
ls -la docs/architecture/schema-design-v2.md

# Check Decision #015
grep "Decision #015" docs/phases/phase-1-*/DECISIONS.md
```

---

## Commit Message

```
docs: document v2 schema design and migration

- Add "Schema Evolution" section to README
- Create docs/architecture/schema-design-v2.md
- Document field-by-field mappings
- Add Decision #015 to DECISIONS.md
- Update demo script to mention v2

Related: Phase 2 Prep, Step 00.7
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
