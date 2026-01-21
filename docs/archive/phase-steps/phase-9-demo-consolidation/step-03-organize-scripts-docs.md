# Step 03: Organize Scripts + Extract Documentation

**Phase**: 9 - Demo Materials Consolidation
**Step**: 03 of 06
**Priority**: 🟡 HIGH
**Status**: ✅ Complete
**Estimated Time**: 2-3 hours
**Actual Time**: 1 hour
**Dependencies**: Step 02 complete

---

## Objective

Organize 8 demo scripts by purpose (execution, utilities, validation, resilience) and extract living documentation from Phase 4 and Phase 8 materials.

---

## Part A: Organize Scripts (1 hour)

### Script Mappings by Purpose

#### Execution Scripts
| Source | Destination | Purpose |
|--------|-------------|---------|
| `scripts/demo.py` | `demos/scripts/execution/demo.py` | Interactive CLI demo (5 steps) |
| `scripts/demo_clean.py` | `demos/scripts/execution/demo_clean.py` | Clean variant |

#### Utility Scripts
| Source | Destination | Purpose |
|--------|-------------|---------|
| `scripts/demo_mode.py` | `demos/scripts/utilities/demo_mode.py` | Demo mode toggle |
| `scripts/reset_demo.py` | `demos/scripts/utilities/reset_demo.py` | Environment reset |
| `scripts/clean_demo_data.py` | `demos/scripts/utilities/clean_demo_data.py` | Data cleanup |
| `scripts/init_e2e_demo.py` | `demos/scripts/utilities/init_e2e_demo.py` | E2E initialization |

#### Validation Scripts
| Source | Destination | Purpose |
|--------|-------------|---------|
| `scripts/pre_demo_check.py` | `demos/scripts/validation/pre_demo_check.py` | Pre-demo validation |

#### Resilience Scripts
| Source | Destination | Purpose |
|--------|-------------|---------|
| `scripts/demo_degradation.py` | `demos/scripts/resilience/demo_degradation.py` | Degradation demo |

### Implementation Commands

```bash
# Execution scripts
cp scripts/demo.py demos/scripts/execution/
cp scripts/demo_clean.py demos/scripts/execution/

# Utility scripts
cp scripts/demo_mode.py demos/scripts/utilities/
cp scripts/reset_demo.py demos/scripts/utilities/
cp scripts/clean_demo_data.py demos/scripts/utilities/
cp scripts/init_e2e_demo.py demos/scripts/utilities/

# Validation scripts
cp scripts/pre_demo_check.py demos/scripts/validation/

# Resilience scripts
cp scripts/demo_degradation.py demos/scripts/resilience/
```

### Verification
```bash
# Test each script category
python demos/scripts/execution/demo.py --quick
python demos/scripts/utilities/demo_mode.py --help
python demos/scripts/validation/pre_demo_check.py --help
python demos/scripts/resilience/demo_degradation.py --quick
```

---

## Part B: Extract Documentation (1-2 hours)

### Documentation Mappings

#### From Phase 8 (E2E Demo Validation - 97.8/100)
| Source | Destination | Purpose |
|--------|-------------|---------|
| `docs/phases/phase-8-e2e-demo/EXECUTIVE_RUNBOOK.md` | `demos/docs/executive-runbook.md` | 12-minute demo sequence |
| `docs/phases/phase-8-e2e-demo/PERFORMANCE_VALIDATION.md` | `demos/docs/performance-validation.md` | Evidence-based metrics |

#### From Phase 4 (Demo Readiness - 135/100)
| Source | Destination | Purpose |
|--------|-------------|---------|
| `docs/phases/phase-4-demo-readiness/reference/demo-quick-reference.md` | `demos/reference/quick-reference.md` | One-page Q&A |
| `docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md` | `demos/reference/architecture-decisions.md` | "Why X vs Y" answers |
| `docs/phases/phase-4-demo-readiness/reference/contingency-plan.md` | `demos/reference/contingency-plan.md` | Failure recovery |
| `docs/phases/phase-4-demo-readiness/reference/troubleshooting-guide.md` | `demos/docs/troubleshooting.md` | Common issues |

### Implementation Commands

```bash
# From Phase 8
cp docs/phases/phase-8-e2e-demo/EXECUTIVE_RUNBOOK.md demos/docs/executive-runbook.md
cp docs/phases/phase-8-e2e-demo/PERFORMANCE_VALIDATION.md demos/docs/performance-validation.md

# From Phase 4
cp docs/phases/phase-4-demo-readiness/reference/demo-quick-reference.md demos/reference/quick-reference.md
cp docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md demos/reference/architecture-decisions.md
cp docs/phases/phase-4-demo-readiness/reference/contingency-plan.md demos/reference/contingency-plan.md
cp docs/phases/phase-4-demo-readiness/reference/troubleshooting-guide.md demos/docs/troubleshooting.md
```

### Verification
```bash
# Check all docs copied
ls -lh demos/docs/*.md
ls -lh demos/reference/*.md

# Verify markdown syntax
markdown-link-check demos/docs/*.md
markdown-link-check demos/reference/*.md
```

---

## Success Criteria

**Part A - Scripts**:
- [ ] All 8 scripts copied to appropriate subdirectories
- [ ] Scripts organized by purpose (2 execution, 4 utilities, 1 validation, 1 resilience)
- [ ] All scripts executable with `--quick` or `--help` flags
- [ ] No import errors or missing dependencies

**Part B - Documentation**:
- [ ] 2 docs extracted from Phase 8
- [ ] 4 docs extracted from Phase 4
- [ ] All docs in correct locations (demos/docs/ or demos/reference/)
- [ ] Markdown syntax valid (no broken links yet - will fix in Step 05)

**Completion Score**: 100/100 if all criteria met

---

## Common Issues

### Issue 1: Script Import Errors
**Symptom**: `ModuleNotFoundError` when running scripts
**Cause**: Scripts reference paths relative to `/scripts/` directory
**Fix**:
```bash
# Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Test script
python demos/scripts/execution/demo.py --quick
```

### Issue 2: Files Not Found
**Symptom**: `cp: cannot stat` errors
**Cause**: Source file path incorrect
**Fix**:
```bash
# Verify source file exists
ls -lh docs/phases/phase-8-e2e-demo/EXECUTIVE_RUNBOOK.md

# Check current directory
pwd

# Ensure running from project root
cd /path/to/k2-market-data-platform
```

---

## Time Tracking

**Estimated**: 2-3 hours
- Part A (Scripts): 1 hour
- Part B (Docs): 1-2 hours

**Actual**: 1 hour
- Part A (Scripts): 30 minutes
- Part B (Docs): 30 minutes

**Notes**:
- Successfully organized all 8 scripts into purpose-based subdirectories (execution, utilities, validation, resilience)
- All scripts tested functional with --help or --quick flags
- **Plan Variance**: Phase 8 documentation files (EXECUTIVE_RUNBOOK.md, PERFORMANCE_VALIDATION.md) not found in docs/phases/phase-8-e2e-demo/ - these files don't exist in the repository
- Extracted 4 documentation files from Phase 4 reference directory:
  - demo-quick-reference.md → quick-reference.md (11K)
  - architecture-decisions-summary.md → architecture-decisions.md (14K)
  - contingency-plan.md → contingency-plan.md (12K)
  - performance-results.md → performance-validation.md (4.7K, used as substitute)
- Troubleshooting guide also not found in Phase 4 - will need to create in Step 04
- Completed ahead of estimated time due to efficient batch copying and simpler than expected file organization

---

## Next Step

After completing this step successfully, proceed to:
→ [Step 04: Create New Documentation](./step-04-create-documentation.md)

---

**Last Updated**: 2026-01-17
**Step Owner**: Implementation Team
