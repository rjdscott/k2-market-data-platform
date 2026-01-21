# K2 Platform - Demo Materials

**Purpose**: Consolidated demo materials for executive presentations, technical deep-dives, and operational demonstrations.

**Last Updated**: 2026-01-17
**Status**: Active

---

## Quick Navigation

### For Executives (10-12 minutes)

**Goal**: See platform value proposition and key capabilities

1. **Start here**: [Executive Demo Notebook](./notebooks/executive-demo.ipynb)
   - 12-minute interactive demonstration
   - Key metrics and business value
   - Live data ingestion and querying
2. **Reference materials**: [Quick Reference](./reference/quick-reference.md)
   - One-page Q&A for common questions
   - Key metrics to memorize

**Quick start**:
```bash
# Start services
docker-compose up -d

# Validate environment
python demos/scripts/validation/pre_demo_check.py

# Open executive demo
jupyter notebook demos/notebooks/executive-demo.ipynb
```

### For Engineers (Deep Dive)

**Goal**: Understand technical architecture and implementation details

1. **Start here**: [Technical Deep-Dive Notebook](./notebooks/technical-deep-dive.ipynb)
   - Comprehensive technical walkthrough
   - Architecture patterns and trade-offs
   - Performance characteristics
2. **Technical guide**: [Technical Guide](./docs/technical-guide.md)
   - Detailed architecture explanation
   - Component interactions
   - Advanced usage patterns
3. **Architecture decisions**: [Architecture Decisions](./reference/architecture-decisions.md)
   - Why we chose X over Y
   - Trade-offs and rationale

**Quick start**:
```bash
# Start services
docker-compose up -d

# Validate environment
python demos/scripts/validation/pre_demo_check.py

# Run technical demo
jupyter notebook demos/notebooks/technical-deep-dive.ipynb

# Or run CLI demo
python demos/scripts/execution/demo.py
```

### For Demo Operators

**Goal**: Successfully deliver demos without issues

1. **Pre-demo checklist**: [Demo Checklist](./reference/demo-checklist.md)
   - 30-minute validation routine
   - Critical checks before demo
2. **Quick start**: [Quick Start Guide](./docs/quick-start.md)
   - 5-minute setup from cold start
   - Common troubleshooting
3. **Contingency plan**: [Contingency Plan](./reference/contingency-plan.md)
   - What to do when things go wrong
   - Fallback strategies
4. **Useful commands**: [Command Reference](./reference/useful-commands.md)
   - CLI cheat sheet
   - Common operations

**Pre-demo routine**:
```bash
# 30 minutes before demo
python demos/scripts/validation/pre_demo_check.py --full

# Review metrics
cat demos/reference/key-metrics.md

# Review checklist
cat demos/reference/demo-checklist.md
```

---

## Quick Commands

### Start Demo Environment

```bash
# Start all services (Kafka, Schema Registry, DuckDB)
docker-compose up -d

# Wait for services to be healthy
sleep 10

# Validate environment
python demos/scripts/validation/pre_demo_check.py
```

### Run CLI Demo

```bash
# Interactive CLI demo (5 steps, 10-15 minutes)
python demos/scripts/execution/demo.py

# Quick demo (skips waits, 2-3 minutes)
python demos/scripts/execution/demo.py --quick

# Clean variant
python demos/scripts/execution/demo_clean.py
```

### Run Jupyter Notebooks

```bash
# Executive demo (12 minutes)
jupyter notebook demos/notebooks/executive-demo.ipynb

# Technical deep-dive (30-40 minutes)
jupyter notebook demos/notebooks/technical-deep-dive.ipynb

# Exchange-specific demos
jupyter notebook demos/notebooks/exchange-demos/binance-crypto.ipynb
jupyter notebook demos/notebooks/exchange-demos/kraken-crypto.ipynb
```

### Reset Demo Environment

```bash
# Full reset (services + data)
python demos/scripts/utilities/reset_demo.py

# Toggle demo mode
python demos/scripts/utilities/demo_mode.py

# Clean demo data only
python demos/scripts/utilities/clean_demo_data.py
```

### Resilience Demo

```bash
# Demonstrate graceful degradation
python demos/scripts/resilience/demo_degradation.py --quick
```

---

## Directory Structure

```
demos/
├── README.md                    # This file - master navigation
│
├── notebooks/                   # Interactive Jupyter demos
│   ├── README.md                # Notebook selection guide
│   ├── executive-demo.ipynb     # 12-minute executive presentation
│   ├── technical-deep-dive.ipynb # Comprehensive technical walkthrough
│   └── exchange-demos/
│       ├── binance-crypto.ipynb # Live crypto streaming demo
│       └── kraken-crypto.ipynb   # Kraken crypto analysis
│
├── scripts/                     # Demo execution and utilities
│   ├── README.md                # Script usage guide
│   ├── execution/
│   │   ├── demo.py              # Interactive CLI demo (primary)
│   │   └── demo_clean.py        # Clean variant
│   ├── utilities/
│   │   ├── demo_mode.py         # Toggle demo mode
│   │   ├── reset_demo.py        # Full environment reset
│   │   ├── clean_demo_data.py   # Data cleanup
│   │   └── init_e2e_demo.py     # E2E demo initialization
│   ├── validation/
│   │   └── pre_demo_check.py    # Pre-demo validation (run 30 min before)
│   └── resilience/
│       └── demo_degradation.py  # Graceful degradation demo
│
├── docs/                        # Living documentation
│   ├── README.md                # Documentation index
│   ├── quick-start.md           # 5-minute getting started
│   ├── technical-guide.md       # Deep technical walkthrough
│   └── performance-validation.md # Evidence-based metrics
│
└── reference/                   # Quick reference materials (print-ready)
    ├── README.md                # Reference index
    ├── demo-checklist.md        # Pre-demo validation checklist
    ├── quick-reference.md       # One-page Q&A
    ├── architecture-decisions.md # "Why X vs Y" answers
    ├── key-metrics.md           # Numbers to memorize
    ├── contingency-plan.md      # Failure recovery
    └── useful-commands.md       # CLI cheat sheet
```

---

## Notebooks

| Notebook | Audience | Duration | Purpose |
|----------|----------|----------|---------|
| [executive-demo.ipynb](./notebooks/executive-demo.ipynb) | CTO, VP Engineering | 12 min | Business value, key metrics, live demo |
| [technical-deep-dive.ipynb](./notebooks/technical-deep-dive.ipynb) | Engineers, Architects | 30-40 min | Architecture, performance, implementation |
| [binance-crypto.ipynb](./notebooks/exchange-demos/binance-crypto.ipynb) | Technical, Crypto specialists | 15-20 min | Live crypto streaming, WebSocket ingestion |
| [kraken-crypto.ipynb](./notebooks/exchange-demos/kraken-crypto.ipynb) | Technical, Crypto specialists | 15-20 min | Kraken crypto analysis, WebSocket streaming |

**When to use which notebook**: See [Notebook Selection Guide](./notebooks/README.md)

---

## Scripts

| Script | Category | Purpose | Usage |
|--------|----------|---------|-------|
| demo.py | Execution | Interactive CLI demo | `python demos/scripts/execution/demo.py` |
| demo_clean.py | Execution | Clean variant | `python demos/scripts/execution/demo_clean.py` |
| demo_mode.py | Utilities | Toggle demo mode | `python demos/scripts/utilities/demo_mode.py` |
| reset_demo.py | Utilities | Full environment reset | `python demos/scripts/utilities/reset_demo.py` |
| clean_demo_data.py | Utilities | Data cleanup | `python demos/scripts/utilities/clean_demo_data.py` |
| init_e2e_demo.py | Utilities | E2E initialization | `python demos/scripts/utilities/init_e2e_demo.py` |
| pre_demo_check.py | Validation | Pre-demo checks | `python demos/scripts/validation/pre_demo_check.py` |
| demo_degradation.py | Resilience | Degradation demo | `python demos/scripts/resilience/demo_degradation.py --quick` |

**Detailed usage**: See [Script Usage Guide](./scripts/README.md)

---

## Documentation

### Living Documentation (demos/docs/)

Documents that evolve with the platform:
- [Quick Start Guide](./docs/quick-start.md) - 5-minute setup from cold start
- [Technical Guide](./docs/technical-guide.md) - Detailed architecture walkthrough
- [Performance Validation](./docs/performance-validation.md) - Evidence-based metrics

### Reference Materials (demos/reference/)

Print-ready reference materials for demos:
- [Demo Checklist](./reference/demo-checklist.md) - Pre-demo validation (print and check off)
- [Quick Reference](./reference/quick-reference.md) - One-page Q&A for common questions
- [Architecture Decisions](./reference/architecture-decisions.md) - "Why X vs Y" rationale
- [Key Metrics](./reference/key-metrics.md) - Numbers to memorize for Q&A
- [Contingency Plan](./reference/contingency-plan.md) - Failure recovery strategies
- [Useful Commands](./reference/useful-commands.md) - CLI cheat sheet

---

## Historical Context

This demo consolidation builds on validated materials from previous phases:

- **Phase 8**: [E2E Demo Validation](../docs/phases/phase-8-e2e-demo/README.md) - 97.8/100 score
  - Validated 12-minute executive demo sequence
  - Performance benchmarking and validation
  - Resilience testing

- **Phase 4**: [Demo Readiness](../docs/phases/phase-4-demo-readiness/README.md) - 135/100 score
  - Quick reference materials created
  - Architecture decision documentation
  - Contingency planning

- **Phase 3**: Demo Enhancements
  - Platform positioning and narrative
  - Demo execution improvements

**For historical implementation details**: See [Phase 9 Documentation](../docs/phases/phase-9-demo-consolidation/README.md)

---

## Common Workflows

### First-Time Setup

```bash
# 1. Clone repository and install dependencies
uv sync --all-extras

# 2. Start services
docker-compose up -d

# 3. Validate environment
python demos/scripts/validation/pre_demo_check.py

# 4. Initialize demo data (optional)
python demos/scripts/utilities/init_e2e_demo.py

# 5. Run quick demo
python demos/scripts/execution/demo.py --quick
```

### Pre-Demo Preparation (30 minutes before)

```bash
# 1. Full validation
python demos/scripts/validation/pre_demo_check.py --full

# 2. Review key metrics
cat demos/reference/key-metrics.md

# 3. Review checklist
cat demos/reference/demo-checklist.md

# 4. Test notebook execution
jupyter nbconvert --execute demos/notebooks/executive-demo.ipynb
```

### Troubleshooting

If something goes wrong, see:
1. [Contingency Plan](./reference/contingency-plan.md) - Immediate fallback strategies
2. [Useful Commands](./reference/useful-commands.md) - Common operations
3. [Technical Guide](./docs/technical-guide.md) - Detailed troubleshooting

---

## Maintenance

**Last Updated**: 2026-01-17
**Maintained By**: Engineering Team
**Questions?**: See [Quick Reference](./reference/quick-reference.md) or create an issue

**Update Frequency**:
- Living docs (demos/docs/): Update as platform evolves
- Reference materials (demos/reference/): Review quarterly
- Notebooks: Update when APIs change
- Scripts: Update when functionality changes

**Contributing**: When updating demo materials, ensure:
1. Test all notebooks execute successfully
2. Validate all script functionality
3. Update relevant documentation
4. Run pre-demo validation checks
