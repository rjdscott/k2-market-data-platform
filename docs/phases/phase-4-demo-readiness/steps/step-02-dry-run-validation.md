# Step 02: Dry Run Validation & Error Resolution

**Status**: ⬜ Not Started
**Priority**: 🔴 CRITICAL
**Estimated Time**: 45-60 minutes
**Dependencies**: Step 01 (Infrastructure Startup)
**Last Updated**: 2026-01-14

---

## Goal

Execute the complete demo notebook (`notebooks/binance-demo.ipynb`) end-to-end to identify and fix all errors before the actual presentation.

**Why Critical**: Presenting without a dry run is extremely risky. Unknown errors, empty query results, or timing issues will undermine credibility during the demo.

---

## Deliverables

1. ✅ Complete execution of notebook without errors
2. ✅ All queries return data (not empty results)
3. ✅ All visualizations render correctly
4. ✅ Timing validation (demo completes in <12 minutes)
5. ✅ Error log with resolutions documented
6. ✅ Pre-demo validation script created

---

## Implementation

### 1. Pre-Flight Check

Before starting the dry run:

```bash
# Verify Step 01 complete
docker compose ps | grep -c "Up"  # Should be 7+

# Check data exists
curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length'
# Should be >0, ideally >1000 rows
```

### 2. Start Jupyter Notebook

```bash
# Start Jupyter server
cd /Users/rjdscott/Documents/code/k2-market-data-platform
jupyter notebook notebooks/binance-demo.ipynb

# Opens in browser at http://localhost:8888
```

### 3. Execute All Cells

**Process**:
1. Click "Kernel" → "Restart & Run All"
2. Watch each cell execute in sequence
3. Note any errors, warnings, or empty results
4. Time the complete execution (use stopwatch)

**What to Watch For**:
- Import errors (missing packages)
- Connection failures (services not ready)
- Empty query results (insufficient data)
- Timeout errors (queries too slow)
- Visualization rendering issues
- Rich console formatting problems

### 4. Document Issues

Create an error log:

```markdown
# Dry Run Error Log - 2026-01-14

## Issue 1: Empty Query Results
**Cell**: Section 5 - Query Historical Data
**Error**: Query returned 0 rows
**Cause**: Not enough data accumulated (only 200 rows in Iceberg)
**Resolution**: Let services run 10 more minutes, re-run
**Status**: ✅ Resolved

## Issue 2: Import Error
**Cell**: Section 1 - Imports
**Error**: ModuleNotFoundError: No module named 'rich'
**Cause**: Missing dependency
**Resolution**: pip install rich
**Status**: ✅ Resolved

## Issue 3: Slow Query
**Cell**: Section 6 - Hybrid Query
**Error**: Query took 8 seconds (timeout warning)
**Cause**: Large data scan, no partition pruning
**Resolution**: Added date filter to query
**Status**: ✅ Resolved
```

### 5. Fix Issues Immediately

For each issue found:

**Import Errors**:
```bash
# Install missing packages
pip install <package_name>

# Or use uv
uv add <package_name>
```

**Connection Failures**:
```bash
# Check services
docker compose ps

# Restart specific service if needed
docker compose restart <service_name>
```

**Empty Query Results**:
```bash
# Wait for more data accumulation
# Or load demo data
python scripts/demo_mode.py --load  # (Step 07 deliverable)
```

**Timeout Errors**:
- Add filters to queries (date range, symbol)
- Optimize query structure
- Check Iceberg partition pruning

### 6. Re-Run From Top

After fixing issues:

1. Restart kernel: "Kernel" → "Restart & Clear Output"
2. Run all cells again: "Kernel" → "Run All"
3. Verify all cells execute without errors
4. Time the execution

**Target**: Complete execution in <12 minutes

### 7. Validate All Outputs

Check each section:

**Section 1: Architecture Context**
- [ ] Platform positioning table renders
- [ ] Latency tier explanation displays

**Section 2: Live Ingestion**
- [ ] Binance stream logs show recent trades
- [ ] Kafka topic message count >0

**Section 3: Storage**
- [ ] Iceberg tables list shows trades_v2
- [ ] Sample query returns rows
- [ ] Time-travel query works

**Section 4: Monitoring**
- [ ] Prometheus metrics query succeeds
- [ ] Grafana dashboard screenshot/embed shows data

**Section 5: Query**
- [ ] Historical query returns trades
- [ ] Aggregation query (OHLCV) works
- [ ] Performance table shows latency <500ms

**Section 6: Hybrid Query**
- [ ] Kafka + Iceberg merge returns data
- [ ] Explanation of uncommitted + committed data

**Section 7: Scaling**
- [ ] Cost model table renders
- [ ] Deployment comparison shows 3 tiers

### 8. Create Pre-Demo Validation Script

Create `scripts/pre_demo_check.py`:

```python
#!/usr/bin/env python3
"""
Pre-Demo Validation Script

Validates all prerequisites before demo execution.
Run this 30 minutes before demo to catch issues early.
"""

import sys
import subprocess
import requests
import json
from rich.console import Console
from rich.table import Table

console = Console()

def check_services():
    """Verify all Docker services running."""
    console.print("\n[bold]1. Checking Docker Services[/bold]")
    
    result = subprocess.run(
        ['docker', 'compose', 'ps'],
        capture_output=True,
        text=True
    )
    
    up_count = result.stdout.count("Up")
    
    if up_count >= 7:
        console.print(f"  ✓ {up_count} services running", style="green")
        return True
    else:
        console.print(f"  ✗ Only {up_count} services running (expected 7+)", style="red")
        return False

def check_binance_stream():
    """Verify Binance stream ingesting."""
    console.print("\n[bold]2. Checking Binance Stream[/bold]")
    
    result = subprocess.run(
        ['docker', 'logs', 'k2-binance-stream', '--tail', '50'],
        capture_output=True,
        text=True
    )
    
    trade_count = result.stdout.count("Trade")
    
    if trade_count > 0:
        console.print(f"  ✓ Stream active ({trade_count} recent trades)", style="green")
        return True
    else:
        console.print("  ✗ No recent trades in logs", style="red")
        return False

def check_iceberg_data():
    """Verify data in Iceberg."""
    console.print("\n[bold]3. Checking Iceberg Data[/bold]")
    
    try:
        response = requests.get(
            "http://localhost:8000/v1/symbols",
            headers={"X-API-Key": "k2-dev-api-key-2026"},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            count = len(data.get('data', []))
            
            if count > 0:
                console.print(f"  ✓ {count} symbols in Iceberg", style="green")
                return True
            else:
                console.print("  ✗ No data in Iceberg", style="red")
                return False
        else:
            console.print(f"  ✗ API returned {response.status_code}", style="red")
            return False
            
    except Exception as e:
        console.print(f"  ✗ Error: {e}", style="red")
        return False

def check_prometheus():
    """Verify Prometheus healthy."""
    console.print("\n[bold]4. Checking Prometheus[/bold]")
    
    try:
        response = requests.get("http://localhost:9090/-/healthy", timeout=5)
        
        if response.status_code == 200:
            console.print("  ✓ Prometheus healthy", style="green")
            return True
        else:
            console.print(f"  ✗ Prometheus returned {response.status_code}", style="red")
            return False
            
    except Exception as e:
        console.print(f"  ✗ Error: {e}", style="red")
        return False

def check_grafana():
    """Verify Grafana responding."""
    console.print("\n[bold]5. Checking Grafana[/bold]")
    
    try:
        response = requests.get("http://localhost:3000/api/health", timeout=5)
        
        if response.status_code == 200:
            console.print("  ✓ Grafana responding", style="green")
            return True
        else:
            console.print(f"  ✗ Grafana returned {response.status_code}", style="red")
            return False
            
    except Exception as e:
        console.print(f"  ✗ Error: {e}", style="red")
        return False

def check_api_health():
    """Verify API server healthy."""
    console.print("\n[bold]6. Checking API Health[/bold]")
    
    try:
        response = requests.get(
            "http://localhost:8000/health",
            headers={"X-API-Key": "k2-dev-api-key-2026"},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            status = data.get('status')
            
            if status == 'healthy':
                console.print("  ✓ API healthy", style="green")
                return True
            else:
                console.print(f"  ✗ API status: {status}", style="red")
                return False
        else:
            console.print(f"  ✗ API returned {response.status_code}", style="red")
            return False
            
    except Exception as e:
        console.print(f"  ✗ Error: {e}", style="red")
        return False

def main():
    """Run all validation checks."""
    console.print("\n[bold cyan]═══ Pre-Demo Validation ═══[/bold cyan]\n")
    
    checks = [
        check_services(),
        check_binance_stream(),
        check_iceberg_data(),
        check_prometheus(),
        check_grafana(),
        check_api_health(),
    ]
    
    passed = sum(checks)
    total = len(checks)
    
    console.print(f"\n[bold]Results: {passed}/{total} checks passed[/bold]")
    
    if passed == total:
        console.print("\n[bold green]✓ All checks passed - READY FOR DEMO[/bold green]\n")
        return 0
    else:
        console.print("\n[bold red]✗ Some checks failed - FIX BEFORE DEMO[/bold red]\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
```

Make executable:
```bash
chmod +x scripts/pre_demo_check.py
```

---

## Validation

Run the pre-demo check script:

```bash
python scripts/pre_demo_check.py

# Expected output:
# ═══ Pre-Demo Validation ═══
# 
# 1. Checking Docker Services
#   ✓ 7 services running
# 
# 2. Checking Binance Stream
#   ✓ Stream active (23 recent trades)
# 
# 3. Checking Iceberg Data
#   ✓ 1247 symbols in Iceberg
# 
# 4. Checking Prometheus
#   ✓ Prometheus healthy
# 
# 5. Checking Grafana
#   ✓ Grafana responding
# 
# 6. Checking API Health
#   ✓ API healthy
# 
# Results: 6/6 checks passed
# ✓ All checks passed - READY FOR DEMO
```

Execute notebook via command line (alternative validation):

```bash
jupyter nbconvert --execute --to notebook \
  notebooks/binance-demo.ipynb \
  --output /tmp/test-output.ipynb

# Should complete without errors in <12 minutes
```

---

## Success Criteria

**20/20 points** — Demo Flow

- [ ] All notebook cells execute without errors
- [ ] All queries return >0 rows (no empty results)
- [ ] All tables and visualizations render correctly
- [ ] Complete execution time <12 minutes
- [ ] No warnings in output
- [ ] Grafana dashboards show data
- [ ] Prometheus metrics visible
- [ ] Pre-demo validation script passes all checks
- [ ] Dry run performed within 24 hours of demo

---

## Common Issues & Solutions

### Empty Query Results

**Cause**: Insufficient data accumulation

**Solution**:
```bash
# Check row count
curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length'

# If <1000, let stream run longer
# Monitor accumulation:
watch -n 60 'curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq ".data | length"'
```

### Slow Queries

**Cause**: No partition pruning, scanning too much data

**Solution**: Add filters to queries
```sql
-- Before (slow)
SELECT * FROM trades_v2 WHERE symbol = 'BTCUSDT'

-- After (fast)
SELECT * FROM trades_v2 
WHERE symbol = 'BTCUSDT' 
  AND exchange_date >= '2026-01-14'
```

### Kernel Crashes

**Cause**: Memory exhaustion, large result sets

**Solution**:
- Add `LIMIT` clauses to queries
- Reduce result set size
- Restart kernel before demo

---

## Next Steps

Once dry run passes:

1. **Mark Step 02 complete** in PROGRESS.md
2. **Document timing**: Record execution time for each section
3. **Proceed to Step 03**: Performance Benchmarking (can run in parallel)
4. **Proceed to Step 04**: Quick Reference (can run in parallel)

---

## Demo Talking Points

When explaining the dry run process:

> "Before this demo, I ran a complete dry run to validate everything works.
> 
> **What I validated**:
> - All notebook cells execute without errors
> - All queries return actual data (not empty results)
> - Complete execution time is under 12 minutes
> - Grafana dashboards show real-time metrics
> - Prometheus is scraping all 83 metrics
> 
> **Why this matters**:
> - Demonstrates operational discipline
> - Catches issues before the presentation
> - Shows I've thought about the demo experience
> 
> This is the same discipline we'd apply to production deployments -
> validate everything in a staging environment first."

---

**Last Updated**: 2026-01-14
