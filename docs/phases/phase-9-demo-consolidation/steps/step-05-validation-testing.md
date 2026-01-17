# Step 05: Validation & Testing

**Phase**: 9 - Demo Materials Consolidation
**Step**: 05 of 06
**Priority**: 🔴 CRITICAL
**Status**: ⬜ Not Started
**Estimated Time**: 2-3 hours
**Dependencies**: Step 04 complete

---

## Objective

Perform comprehensive validation including cross-reference updates between demos/ and phase docs, Makefile target updates, and end-to-end testing of all notebooks, scripts, and documentation links.

---

## Part A: Update Cross-References (1 hour)

### Task: Bidirectional Linking

Create bidirectional cross-references so:
- Phase 8 and Phase 4 READMEs link to `/demos/` for current materials
- Demo docs link back to phase docs for historical context

### 1. Update Phase 8 README

**File**: `docs/phases/phase-8-e2e-demo/README.md`

Add section after "Phase Overview":

```markdown
## Current Demo Materials

**Active Location**: [`/demos/`](../../../demos/README.md)

The demo materials validated in this phase are now maintained in:
- **Notebooks**: [`demos/notebooks/`](../../../demos/notebooks/README.md)
- **Scripts**: [`demos/scripts/`](../../../demos/scripts/README.md)
- **Documentation**: [`demos/docs/`](../../../demos/docs/) and [`demos/reference/`](../../../demos/reference/)

For historical context and validation methodology, see:
- [EXECUTIVE_RUNBOOK.md](./EXECUTIVE_RUNBOOK.md) - Now copied to [`demos/docs/executive-runbook.md`](../../../demos/docs/executive-runbook.md)
- [PERFORMANCE_VALIDATION.md](./PERFORMANCE_VALIDATION.md) - Now copied to [`demos/docs/performance-validation.md`](../../../demos/docs/performance-validation.md)
```

### 2. Update Phase 4 README

**File**: `docs/phases/phase-4-demo-readiness/README.md`

Add section after "Phase Overview":

```markdown
## Current Demo Materials

**Active Location**: [`/demos/`](../../../demos/README.md)

The demo materials prepared in this phase are now maintained in:
- **Quick Reference**: [`demos/reference/quick-reference.md`](../../../demos/reference/quick-reference.md)
- **Architecture Decisions**: [`demos/reference/architecture-decisions.md`](../../../demos/reference/architecture-decisions.md)
- **Contingency Plan**: [`demos/reference/contingency-plan.md`](../../../demos/reference/contingency-plan.md)
- **Troubleshooting**: [`demos/docs/troubleshooting.md`](../../../demos/docs/troubleshooting.md)

For historical context, see the [reference/](./reference/) directory in this phase.
```

### 3. Ensure Demo Docs Link Back

Verify the following files have phase references:

**`demos/docs/executive-runbook.md`**:
```markdown
> **Historical Context**: This runbook was validated in [Phase 8: E2E Demo Validation](../../docs/phases/phase-8-e2e-demo/README.md) (97.8/100 score).
```

**`demos/docs/performance-validation.md`**:
```markdown
> **Historical Context**: These metrics were validated in [Phase 8](../../docs/phases/phase-8-e2e-demo/README.md).
```

**`demos/reference/quick-reference.md`**:
```markdown
> **Historical Context**: This reference was created in [Phase 4: Demo Readiness](../../docs/phases/phase-4-demo-readiness/README.md).
```

### 4. Validation Commands

```bash
# Check all demo markdown files have valid links
find demos/ -name "*.md" -exec markdown-link-check {} \;

# Check Phase 8 and Phase 4 links to demos/
markdown-link-check docs/phases/phase-8-e2e-demo/README.md
markdown-link-check docs/phases/phase-4-demo-readiness/README.md

# Verify all internal cross-references
grep -r "demos/" docs/phases/phase-{4,8}*/README.md
grep -r "phases/phase" demos/**/*.md
```

---

## Part B: Update Makefile (30 minutes)

### Task: Update Demo Targets

Ensure all demo-related Makefile targets use new `demos/scripts/` paths.

### Current Demo Targets

**File**: `Makefile`

Update these targets:

```makefile
# Demo Execution
.PHONY: demo
demo:  ## Run interactive CLI demo (5 steps)
	python demos/scripts/execution/demo.py

.PHONY: demo-quick
demo-quick:  ## Run quick demo (skips waits)
	python demos/scripts/execution/demo.py --quick

.PHONY: demo-clean
demo-clean:  ## Run clean demo variant
	python demos/scripts/execution/demo_clean.py

# Demo Utilities
.PHONY: demo-mode
demo-mode:  ## Toggle demo mode
	python demos/scripts/utilities/demo_mode.py

.PHONY: demo-reset
demo-reset:  ## Reset demo environment
	python demos/scripts/utilities/reset_demo.py

.PHONY: demo-clean-data
demo-clean-data:  ## Clean demo data
	python demos/scripts/utilities/clean_demo_data.py

.PHONY: demo-init-e2e
demo-init-e2e:  ## Initialize E2E demo
	python demos/scripts/utilities/init_e2e_demo.py

# Demo Validation
.PHONY: pre-demo-check
pre-demo-check:  ## Run pre-demo validation checks
	python demos/scripts/validation/pre_demo_check.py

# Demo Resilience
.PHONY: demo-degradation
demo-degradation:  ## Run degradation demo
	python demos/scripts/resilience/demo_degradation.py --quick

# Demo Documentation
.PHONY: demo-docs
demo-docs:  ## Open demo documentation
	@echo "Opening demo documentation..."
	@if command -v open > /dev/null; then \
		open demos/README.md; \
	elif command -v xdg-open > /dev/null; then \
		xdg-open demos/README.md; \
	else \
		echo "Please open demos/README.md manually"; \
	fi
```

### Verification Commands

```bash
# Test all demo targets (dry-run)
make demo-quick
make pre-demo-check
make demo-mode --help
make demo-reset --dry-run

# Verify Makefile syntax
make -n demo
make -n demo-degradation
```

---

## Part C: End-to-End Validation (1-2 hours)

### 1. Environment Validation

```bash
# Start services
docker-compose up -d

# Wait for services to be healthy
sleep 10

# Run pre-demo check
python demos/scripts/validation/pre_demo_check.py

# Expected: All checks pass (Docker, Kafka, DuckDB, API)
```

### 2. Notebook Validation

```bash
# Test executive demo
jupyter nbconvert --execute --to notebook \
  demos/notebooks/executive-demo.ipynb \
  --output /tmp/test-executive-demo.ipynb

# Test technical deep-dive
jupyter nbconvert --execute --to notebook \
  demos/notebooks/technical-deep-dive.ipynb \
  --output /tmp/test-technical-deep-dive.ipynb

# Test exchange-specific notebooks
jupyter nbconvert --execute --to notebook \
  demos/notebooks/exchange-demos/binance-crypto.ipynb \
  --output /tmp/test-binance-crypto.ipynb

jupyter nbconvert --execute --to notebook \
  demos/notebooks/exchange-demos/asx-equities.ipynb \
  --output /tmp/test-asx-equities.ipynb

# Expected: All 4 notebooks execute without errors
```

### 3. Script Validation

```bash
# Test execution scripts
python demos/scripts/execution/demo.py --quick
python demos/scripts/execution/demo_clean.py --quick

# Test utility scripts
python demos/scripts/utilities/demo_mode.py --help
python demos/scripts/utilities/reset_demo.py --help
python demos/scripts/utilities/clean_demo_data.py --help
python demos/scripts/utilities/init_e2e_demo.py --help

# Test validation script
python demos/scripts/validation/pre_demo_check.py

# Test resilience script
python demos/scripts/resilience/demo_degradation.py --quick

# Expected: All scripts run without import errors
```

### 4. Link Validation

```bash
# Check all demo markdown files
find demos/ -name "*.md" -exec markdown-link-check {} \;

# Check Phase 8 and Phase 4 cross-references
markdown-link-check docs/phases/phase-8-e2e-demo/README.md
markdown-link-check docs/phases/phase-4-demo-readiness/README.md

# Expected: Zero broken links
```

### 5. Makefile Target Validation

```bash
# Test all demo targets
make demo-quick
make pre-demo-check
make demo-reset --dry-run
make demo-degradation

# Expected: All targets work, no path errors
```

### 6. Documentation Validation

```bash
# Verify all critical files exist
test -f demos/README.md || echo "MISSING: demos/README.md"
test -f demos/docs/quick-start.md || echo "MISSING: demos/docs/quick-start.md"
test -f demos/docs/technical-guide.md || echo "MISSING: demos/docs/technical-guide.md"
test -f demos/reference/demo-checklist.md || echo "MISSING: demos/reference/demo-checklist.md"
test -f demos/reference/key-metrics.md || echo "MISSING: demos/reference/key-metrics.md"
test -f demos/reference/useful-commands.md || echo "MISSING: demos/reference/useful-commands.md"

# Check word counts (target ranges)
wc -l demos/README.md                           # Target: 200-300 lines
wc -l demos/docs/quick-start.md                 # Target: 100-200 lines
wc -l demos/docs/technical-guide.md             # Target: 100-200 lines
wc -l demos/reference/demo-checklist.md         # Target: 50-100 lines
wc -l demos/reference/key-metrics.md            # Target: 50-100 lines
wc -l demos/reference/useful-commands.md        # Target: 50-100 lines

# Expected: All files exist, word counts in range
```

---

## Optional: Backwards Compatibility (15 minutes)

### Task: Create Symlinks (If Needed)

If there are external dependencies on old paths, create symlinks:

```bash
# Link old notebook paths to new locations
ln -s demos/notebooks/executive-demo.ipynb notebooks/k2_working_demo.ipynb
ln -s demos/notebooks/technical-deep-dive.ipynb notebooks/binance_e2e_demo.ipynb

# Link old script paths (if needed)
ln -s demos/scripts/execution/demo.py scripts/demo.py
ln -s demos/scripts/utilities/reset_demo.py scripts/reset_demo.py
```

**Verification**:
```bash
# Test symlinks work
ls -lh notebooks/k2_working_demo.ipynb
python scripts/demo.py --help
```

**Note**: Only create symlinks if absolutely necessary for backwards compatibility. Prefer updating references to use new paths.

---

## Success Criteria

**Part A - Cross-References**:
- [ ] Phase 8 README links to `/demos/` for current materials
- [ ] Phase 4 README links to `/demos/` for current materials
- [ ] Demo docs link back to phase docs for historical context
- [ ] All markdown links valid (zero broken links)

**Part B - Makefile**:
- [ ] All demo targets updated to use `demos/scripts/` paths
- [ ] All targets tested and working
- [ ] Help text accurate and descriptive

**Part C - End-to-End Validation**:
- [ ] All 4 notebooks execute without errors
- [ ] All 8 scripts run without import errors
- [ ] Zero broken markdown links in demos/ and phase docs
- [ ] All Makefile targets work
- [ ] All critical documentation files exist with appropriate word counts

**Completion Score**: 100/100 if all criteria met

---

## Common Issues

### Issue 1: Broken Links

**Symptom**: `markdown-link-check` reports broken links
**Cause**: Relative paths incorrect after file moves
**Fix**:
```bash
# Check broken link
markdown-link-check demos/docs/executive-runbook.md

# Fix relative path (example)
# Wrong: ../../phases/phase-8-e2e-demo/README.md
# Right: ../../../docs/phases/phase-8-e2e-demo/README.md

# Re-validate
markdown-link-check demos/docs/executive-runbook.md
```

### Issue 2: Makefile Target Fails

**Symptom**: `make demo` fails with "No such file or directory"
**Cause**: Script path not updated in Makefile
**Fix**:
```bash
# Check current target
make -n demo

# Update Makefile target
# Wrong: python scripts/demo.py
# Right: python demos/scripts/execution/demo.py

# Re-test
make demo-quick
```

### Issue 3: Notebook Execution Fails

**Symptom**: `jupyter nbconvert` fails with kernel error
**Cause**: Missing dependencies or wrong kernel
**Fix**:
```bash
# Sync environment
uv sync --all-extras

# Check kernel
jupyter kernelspec list

# Run notebook manually to debug
jupyter notebook demos/notebooks/executive-demo.ipynb
```

### Issue 4: Script Import Errors

**Symptom**: `ModuleNotFoundError` when running scripts
**Cause**: Scripts reference paths relative to project root
**Fix**:
```bash
# Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Test script
python demos/scripts/execution/demo.py --quick

# Permanent fix: Update scripts to use absolute imports
# Or add to .envrc / shell profile
```

---

## Time Tracking

**Estimated**: 2-3 hours
- Part A (Cross-references): 1 hour
- Part B (Makefile): 30 minutes
- Part C (E2E validation): 1-2 hours

**Actual**: -
**Notes**: -

---

## Next Step

After completing this step successfully, proceed to:
→ [Step 06: Finalize & Commit](./step-06-finalize-commit.md)

---

**Last Updated**: 2026-01-17
**Step Owner**: Implementation Team
