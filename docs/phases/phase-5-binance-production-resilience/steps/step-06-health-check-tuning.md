# Step 06: Health Check Timeout Tuning

**Priority**: P1.2 (High)
**Estimated**: 1 hour
**Status**: ⬜ Not Started
**Depends On**: Steps 01-05

---

## Objective

Reduce health check timeout from 60s → 30s for faster detection of stale connections.

---

## Implementation

**Files**:
- `src/k2/common/config.py` line 198: Change default from 60 → 30
- `docker-compose.yml`: Update `K2_BINANCE_HEALTH_CHECK_TIMEOUT` env var
- Update documentation (README.md, runbook)

---

## Validation

- [ ] Default is 30s
- [ ] Reconnect triggered after 30s of no messages

---

**Time**: 1 hour
**Status**: ⬜ Not Started
