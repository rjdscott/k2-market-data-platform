# Python Environment Consolidation Review

**Reviewer**: Staff/Principal Data Engineer
**Date**: 2026-01-11
**Status**: Implemented (pending `uv.lock` generation)
**Scope**: Python environment management, dependency reproducibility, HFT/MFT best practices

---

## Executive Summary

The K2 Market Data Platform has accumulated environment management debt that introduces risk for an HFT/MFT production system. This review identifies issues and provides a migration path to `uv` for consolidated, reproducible Python environment management.

**Risk Level**: Medium (development friction, potential production discrepancies)
**Effort**: Low (2-3 hours implementation)
**Impact**: High (faster onboarding, reproducible builds, reduced "works on my machine" issues)

### Implementation Status

| Item | Status |
|------|--------|
| `.python-version` created | Done |
| `pyproject.toml` updated with `[tool.uv]` | Done |
| `Makefile` updated to use uv | Done |
| `.gitignore` updated | Done |
| `requirements.txt` deleted | Done |
| `requirements-dev.txt` deleted | Done |
| `venv/` directory deleted | Done |
| `README.md` updated | Done |
| `uv.lock` generated | **Pending** (requires uv installation) |

---

## Current State Assessment

### Environment Files Audit

| File | Lines | Purpose | Issue |
|------|-------|---------|-------|
| `pyproject.toml` | 342 | Primary config (PEP 621) | Authoritative, well-structured |
| `requirements.txt` | 168 | Flat pip dependencies | **Duplicates pyproject.toml** |
| `requirements-dev.txt` | 14 | Pinned dev subset | **Duplicates optional-dependencies[dev]** |

### Virtual Environment Directories

| Directory | Size | Status |
|-----------|------|--------|
| `.venv/` | 1.0 GB | Active (used by Makefile) |
| `venv/` | 437 MB | **Legacy, unused** |

### Missing Components

| Component | Status | Risk |
|-----------|--------|------|
| Lock file | **Missing** | High - no reproducible builds |
| `.python-version` | **Missing** | Medium - version drift |
| Pre-commit hooks | Missing | Low (per user preference) |
| CI/CD pipeline | Missing | Deferred (per user preference) |

---

## Problems Identified

### 1. Duplicate Dependency Specifications (High Priority)

**Issue**: `requirements.txt` and `requirements-dev.txt` duplicate `pyproject.toml`.

**Evidence**:
```
# requirements.txt line 1-10 vs pyproject.toml [project.dependencies]
confluent-kafka[avro,schema-registry]>=2.13.0  # Identical
kafka-python>=2.0.2                             # Identical
avro>=1.11.3                                    # Identical
...
```

**Risk**:
- Version specifications can drift between files
- Developers may update one file but not others
- CI vs local environment discrepancies

**Recommendation**: Delete `requirements.txt` and `requirements-dev.txt`. Use `pyproject.toml` as single source of truth.

### 2. No Dependency Lock File (Critical for HFT)

**Issue**: No `requirements.lock`, `poetry.lock`, or `uv.lock` exists.

**Risk for HFT/MFT**:
- Production may install different transitive dependency versions than development
- Security patches could introduce breaking changes unexpectedly
- Audit trail for dependency versions is missing

**Example Scenario**:
```
# Developer A installs today:
pyarrow==22.0.0 -> pulls numpy==2.4.1

# Developer B installs next week:
pyarrow==22.0.0 -> pulls numpy==2.4.2 (new release)

# Subtle numerical differences in trading calculations
```

**Recommendation**: Generate and commit `uv.lock` for exact reproducibility.

### 3. Duplicate Virtual Environments (Medium Priority)

**Issue**: Both `.venv/` (1.0GB) and `venv/` (437MB) exist.

**Risk**:
- Confusion about which environment is active
- Wasted disk space (437MB)
- Potential for running wrong environment

**Evidence from Makefile**:
```makefile
VENV := .venv  # Only .venv is used
```

**Recommendation**: Delete `venv/` directory.

### 4. Hardcoded Python Version (Medium Priority)

**Issue**: Makefile hardcodes `python3.13` without version management.

```makefile
PYTHON := python3.13  # What if 3.13 isn't installed?
```

**Risk**:
- New developers may have different Python versions
- No enforcement of minimum version requirement
- CI/CD may use different version

**Recommendation**: Create `.python-version` file for tooling (pyenv, mise, uv).

### 5. Slow Install Times (Development Friction)

**Issue**: `pip install -e ".[all]"` takes 60-120 seconds.

**Impact**:
- Developer context-switching during installs
- CI pipeline time accumulation
- Friction for dependency updates

**Benchmark** (typical):
```
pip install -e ".[all]"  → 90 seconds
uv sync --all-extras     → 3 seconds (cached: <1 second)
```

---

## Recommended Solution: Migrate to `uv`

### Why `uv`?

| Feature | pip | uv |
|---------|-----|-----|
| Install speed | ~90s | ~3s (30x faster) |
| Lock file | No | Yes (`uv.lock`) |
| Python management | No | Yes (auto-installs) |
| PEP 621 support | Yes | Yes |
| Rust-based | No | Yes (memory safe) |
| Active development | Maintenance | Rapid iteration |

### Target State Architecture

```
k2-market-data-platform/
├── .python-version          # NEW: "3.13"
├── pyproject.toml           # EXISTING: Single source of truth
├── uv.lock                  # NEW: Committed for reproducibility
├── .venv/                   # EXISTING: Managed by uv
├── Makefile                 # MODIFIED: Uses uv commands
└── (no requirements*.txt)   # DELETED
```

---

## Implementation Plan

### Phase 1: Preparation

#### Step 1.1: Install uv
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify
uv --version  # Should be >= 0.5.x
```

#### Step 1.2: Create `.python-version`
```bash
echo "3.13" > .python-version
```

### Phase 2: Configuration

#### Step 2.1: Update `pyproject.toml`

Add at end of file:
```toml
# ==============================================================================
# UV Configuration
# ==============================================================================

[tool.uv]
python-preference = "managed"
dev-dependencies = ["k2-platform[all]"]
```

#### Step 2.2: Generate lock file
```bash
uv lock
```

This creates `uv.lock` with pinned versions of all direct and transitive dependencies.

### Phase 3: Makefile Migration

#### Step 3.1: Update Variables

**Before**:
```makefile
PYTHON := python3.13
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
BLACK := $(VENV)/bin/black
ISORT := $(VENV)/bin/isort
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
```

**After**:
```makefile
VENV := .venv
UV := uv

# Tool execution via uv run (handles venv automatically)
PYTEST := $(UV) run pytest
BLACK := $(UV) run black
ISORT := $(UV) run isort
RUFF := $(UV) run ruff
MYPY := $(UV) run mypy
```

#### Step 3.2: Update `install` target

**Before**:
```makefile
install:
	@$(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip setuptools wheel
	@$(PIP) install -e .
```

**After**:
```makefile
install:
	@$(UV) sync
```

#### Step 3.3: Update `dev-install` target

**Before**:
```makefile
dev-install:
	@$(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip setuptools wheel
	@$(PIP) install -e ".[all]"
```

**After**:
```makefile
dev-install:
	@$(UV) sync --all-extras
```

#### Step 3.4: Update test targets

```makefile
test:
	@$(UV) run pytest tests/ -v

test-unit:
	@$(UV) run pytest tests/unit/ -v -m unit

coverage:
	@$(UV) run pytest tests/ --cov=src/k2 --cov-report=html
```

#### Step 3.5: Update quality targets

```makefile
lint:
	@$(UV) run ruff check src/ tests/

lint-fix:
	@$(UV) run ruff check --fix src/ tests/

format:
	@$(UV) run black src/ tests/
	@$(UV) run isort src/ tests/

type-check:
	@$(UV) run mypy src/
```

#### Step 3.6: Update API targets

```makefile
api:
	@$(UV) run uvicorn k2.api.main:app --reload --host 0.0.0.0 --port 8000

api-prod:
	@$(UV) run gunicorn k2.api.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

#### Step 3.7: Update check-env target

```makefile
check-env:
	@which uv > /dev/null || (echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" && exit 1)
	@which docker > /dev/null || echo "Docker not found"
```

#### Step 3.8: Add new uv helper targets

```makefile
# ==============================================================================
# UV Package Management
# ==============================================================================

uv-lock: ## Update lock file after pyproject.toml changes
	@$(UV) lock

uv-upgrade: ## Upgrade all dependencies to latest compatible versions
	@$(UV) lock --upgrade
	@$(UV) sync

uv-add: ## Add dependency (usage: make uv-add PKG=package-name)
	@$(UV) add $(PKG)

uv-add-dev: ## Add dev dependency (usage: make uv-add-dev PKG=package-name)
	@$(UV) add --dev $(PKG)

clean-venv: ## Remove virtual environment for fresh reinstall
	@rm -rf $(VENV)
```

### Phase 4: Cleanup

#### Step 4.1: Remove old directories and files
```bash
rm -rf venv/
rm -rf .venv/
rm requirements.txt
rm requirements-dev.txt
```

#### Step 4.2: Create fresh environment
```bash
uv sync --all-extras
```

### Phase 5: Documentation Updates

#### Step 5.1: Update README.md Quick Start

**Before**:
```markdown
# Create virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -e ".[dev]"
```

**After**:
```markdown
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies (creates .venv automatically)
uv sync --all-extras

# Optional: Activate venv
source .venv/bin/activate
```

#### Step 5.2: Add Development Workflow section

```markdown
### Package Management with uv

# Install dependencies
uv sync                    # Core only
uv sync --all-extras       # All (dev, api, monitoring, quality)

# Add new packages
uv add package-name        # Production dependency
uv add --dev package-name  # Development dependency

# Update lock file
uv lock

# Upgrade all packages
uv lock --upgrade && uv sync
```

### Phase 6: Verification

```bash
# Verify uv installation
uv --version

# Verify environment
ls -la .venv/

# Run unit tests
uv run pytest tests/unit/ -v --tb=short

# Verify CLI
uv run k2 --help
uv run k2-query --help
uv run k2-ingest --help

# Verify API starts
uv run uvicorn k2.api.main:app --host 0.0.0.0 --port 8000 &
curl http://localhost:8000/health
kill %1

# Verify make targets
make test-unit
make lint
make format
```

---

## Commit Strategy

### Single Commit (Recommended)
```bash
git add .python-version uv.lock pyproject.toml Makefile .gitignore README.md
git rm requirements.txt requirements-dev.txt
git commit -m "chore: migrate to uv for Python environment management

- Remove requirements.txt and requirements-dev.txt (duplicates pyproject.toml)
- Add .python-version (3.13) for uv Python version management
- Add uv.lock for reproducible builds (critical for HFT)
- Add [tool.uv] section to pyproject.toml
- Update Makefile to use 'uv run' and 'uv sync'
- Remove legacy venv/ directory (keep .venv)
- Update README.md Quick Start with uv instructions

Benefits:
- 10-100x faster installs (cached binary packages)
- Reproducible builds via lock file
- Single tool for Python version + venv + deps
- Simpler Makefile (no pip upgrade, no venv creation)"
```

---

## Risk Assessment

### Low Risk
- uv is production-ready (used by major projects)
- Makefile changes are straightforward
- Rollback is simple (reinstall from pyproject.toml with pip)

### Mitigation
- Keep `.venv` backup before deletion (optional)
- Test all Makefile targets after migration
- Verify CI/CD compatibility if added later

---

## Post-Migration Checklist

- [ ] `uv --version` shows installed
- [ ] `.python-version` contains `3.13`
- [ ] `uv.lock` exists and is committed
- [ ] No `requirements.txt` or `requirements-dev.txt`
- [ ] No `venv/` directory (only `.venv/`)
- [ ] `make install` works
- [ ] `make dev-install` works
- [ ] `make test-unit` passes
- [ ] `make lint` works
- [ ] `make format` works
- [ ] `uv run k2 --help` shows CLI
- [ ] `uv run k2-api --help` works
- [ ] README.md updated with uv instructions

---

## Appendix A: Full Makefile Diff

See implementation steps above for specific changes. Key targets affected:
- `install`, `dev-install`, `clean`
- `test`, `test-unit`, `test-integration`, `coverage`
- `lint`, `lint-fix`, `format`, `type-check`
- `api`, `api-prod`
- `check-env`
- New: `uv-lock`, `uv-upgrade`, `uv-add`, `uv-add-dev`, `clean-venv`

---

## Appendix B: pyproject.toml Addition

```toml
# ==============================================================================
# UV Configuration
# ==============================================================================

[tool.uv]
# Use managed Python from .python-version
python-preference = "managed"

# Default dev dependencies for 'uv sync' without --all-extras
dev-dependencies = ["k2-platform[all]"]
```

---

## Appendix C: uv Quick Reference

```bash
# Environment setup
uv sync                     # Install from lock file
uv sync --all-extras        # Include optional dependencies
uv sync --frozen            # Fail if lock file outdated

# Lock file management
uv lock                     # Update lock file
uv lock --upgrade           # Upgrade all packages
uv lock --upgrade-package X # Upgrade specific package

# Dependency management
uv add package              # Add production dep
uv add --dev package        # Add dev dep
uv remove package           # Remove dep

# Running tools
uv run pytest               # Run in venv
uv run python script.py     # Run script in venv
uv run -- command           # Run arbitrary command

# Python management
uv python install 3.13      # Install Python
uv python list              # List available versions
```

---

## Summary

This migration consolidates Python environment management from 3 files + 2 venvs to a single source of truth with reproducible builds. The change is low-risk, low-effort, and provides significant benefits for an HFT/MFT environment where reproducibility and speed are critical.

**Estimated Implementation Time**: 2-3 hours
**Recommended Priority**: High (address before next production deployment)