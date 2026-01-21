# Step 02: Consolidate Notebooks

**Phase**: 9 - Demo Materials Consolidation
**Step**: 02 of 06
**Priority**: 🔴 CRITICAL
**Status**: ✅ Complete
**Estimated Time**: 1-2 hours
**Actual Time**: 1 hour
**Dependencies**: Step 01 complete

---

## Objective

Consolidate 6 scattered notebooks into 4 active notebooks (2 primary + 2 exchange-specific) with clear naming and purpose, plus archive 2 deprecated variants.

---

## File Mappings

### Primary Notebooks (Copy & Rename)

| Source | Destination | Purpose |
|--------|-------------|---------|
| `notebooks/k2_working_demo.ipynb` | `demos/notebooks/executive-demo.ipynb` | 12-minute executive presentation |
| `notebooks/binance_e2e_demo.ipynb` | `demos/notebooks/technical-deep-dive.ipynb` | Comprehensive technical walkthrough |

### Exchange-Specific Notebooks (Copy)

| Source | Destination | Purpose |
|--------|-------------|---------|
| `notebooks/binance-demo.ipynb` | `demos/notebooks/exchange-demos/binance-crypto.ipynb` | Live crypto streaming demo |
| `notebooks/asx-demo.ipynb` | `demos/notebooks/exchange-demos/asx-equities.ipynb` | Historical equities analysis |

### Deprecated Notebooks (Archive)

| Source | Destination | Reason |
|--------|-------------|--------|
| `notebooks/k2_clean_demo.ipynb` | `notebooks/.archive/k2_clean_demo.ipynb` | Superseded by k2_working_demo |
| `notebooks/k2_clean_demo_fixed.ipynb` | `notebooks/.archive/k2_clean_demo_fixed.ipynb` | Superseded by k2_working_demo |

---

## Implementation Steps

### 1. Copy Primary Notebooks
```bash
# Copy and rename for clarity
cp notebooks/k2_working_demo.ipynb demos/notebooks/executive-demo.ipynb
cp notebooks/binance_e2e_demo.ipynb demos/notebooks/technical-deep-dive.ipynb
```

### 2. Copy Exchange-Specific Notebooks
```bash
cp notebooks/binance-demo.ipynb demos/notebooks/exchange-demos/binance-crypto.ipynb
cp notebooks/asx-demo.ipynb demos/notebooks/exchange-demos/asx-equities.ipynb
```

### 3. Create Archive Directory
```bash
mkdir -p notebooks/.archive
```

### 4. Archive Deprecated Notebooks
```bash
# Move (not copy) deprecated variants to archive
mv notebooks/k2_clean_demo.ipynb notebooks/.archive/
mv notebooks/k2_clean_demo_fixed.ipynb notebooks/.archive/
```

### 5. Create Archive README
```bash
cat > notebooks/.archive/README.md << 'EOF'
# Archived Notebooks

**Purpose**: Deprecated notebook variants superseded by newer versions

## Archived Files

### k2_clean_demo.ipynb
- **Archived**: 2026-01-17
- **Reason**: Superseded by `k2_clean_demo_fixed.ipynb`
- **Replacement**: Use `k2_working_demo.ipynb` (now `demos/notebooks/executive-demo.ipynb`)

### k2_clean_demo_fixed.ipynb
- **Archived**: 2026-01-17
- **Reason**: Superseded by `k2_working_demo.ipynb`
- **Replacement**: Use `demos/notebooks/executive-demo.ipynb`

## Current Active Notebooks

See [/demos/notebooks/](../../demos/notebooks/README.md) for current demo materials:
- `executive-demo.ipynb` - 12-minute executive presentation
- `technical-deep-dive.ipynb` - Comprehensive technical walkthrough
- `exchange-demos/binance-crypto.ipynb` - Live crypto streaming
- `exchange-demos/asx-equities.ipynb` - Historical equities analysis

**Last Archived**: 2026-01-17
EOF
```

---

## Verification Steps

### 1. Verify All Notebooks Copied
```bash
ls -lh demos/notebooks/*.ipynb
ls -lh demos/notebooks/exchange-demos/*.ipynb
```

**Expected**: 4 notebook files (2 in notebooks/, 2 in exchange-demos/)

### 2. Verify Archive Created
```bash
ls -lh notebooks/.archive/
```

**Expected**: 2 archived notebooks + README.md

### 3. Test Notebook Execution
```bash
# Test executive demo
jupyter nbconvert --execute --to notebook \
  demos/notebooks/executive-demo.ipynb \
  --output /tmp/test-executive.ipynb

# Test technical deep-dive
jupyter nbconvert --execute --to notebook \
  demos/notebooks/technical-deep-dive.ipynb \
  --output /tmp/test-technical.ipynb

# Test exchange-specific notebooks
jupyter nbconvert --execute --to notebook \
  demos/notebooks/exchange-demos/binance-crypto.ipynb \
  --output /tmp/test-binance.ipynb

jupyter nbconvert --execute --to notebook \
  demos/notebooks/exchange-demos/asx-equities.ipynb \
  --output /tmp/test-asx.ipynb
```

**Expected**: All 4 notebooks execute without errors

### 4. Verify File Sizes
```bash
# Check notebooks were copied completely
du -h demos/notebooks/*.ipynb
du -h demos/notebooks/exchange-demos/*.ipynb
```

**Expected**:
- executive-demo.ipynb: ~18 KB
- technical-deep-dive.ipynb: ~98 KB
- binance-crypto.ipynb: ~35 KB
- asx-equities.ipynb: ~127 KB

---

## Success Criteria

- [ ] 4 active notebooks copied to demos/notebooks/
- [ ] All notebooks renamed for clarity (executive-demo, technical-deep-dive)
- [ ] 2 notebooks archived to notebooks/.archive/
- [ ] Archive README.md created with explanations
- [ ] All 4 notebooks execute without errors
- [ ] File sizes match originals (complete copies)
- [ ] Original notebooks remain in notebooks/ directory (not deleted yet)

**Completion Score**: 100/100 if all criteria met

---

## Common Issues

### Issue 1: Notebook Execution Fails
**Symptom**: `jupyter nbconvert` errors
**Cause**: Missing kernel or dependencies
**Fix**:
```bash
# Ensure correct environment
uv sync --all-extras

# Check kernel
jupyter kernelspec list

# Run notebook manually to debug
jupyter notebook demos/notebooks/executive-demo.ipynb
```

### Issue 2: File Size Mismatch
**Symptom**: Copied notebook smaller than original
**Cause**: Incomplete copy
**Fix**:
```bash
# Remove incomplete copy
rm demos/notebooks/executive-demo.ipynb

# Re-copy with verbose output
cp -v notebooks/k2_working_demo.ipynb demos/notebooks/executive-demo.ipynb

# Verify
diff notebooks/k2_working_demo.ipynb demos/notebooks/executive-demo.ipynb
```

---

## Time Tracking

**Estimated**: 1-2 hours
**Actual**: 1 hour
**Notes**:
- Successfully consolidated all 4 active notebooks (file integrity verified via diff)
- Created archive structure with 2 deprecated notebooks and explanatory README
- Identified pre-existing execution issues in 3 notebooks (not introduced by consolidation):
  - technical-deep-dive.ipynb: TypeError with BinanceWebSocketClient.__init__() 'producer' argument
  - binance-crypto.ipynb: execution error (requires investigation)
  - asx-equities.ipynb: execution error (requires investigation)
- executive-demo.ipynb executes successfully
- All file copies verified identical to source (consolidation successful)
- Recommend addressing notebook execution issues in future maintenance phase

---

## Next Step

After completing this step successfully, proceed to:
→ [Step 03: Organize Scripts + Extract Docs](./step-03-organize-scripts-docs.md)

---

**Last Updated**: 2026-01-17
**Step Owner**: Implementation Team
