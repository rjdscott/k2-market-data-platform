# Step 5: Runbooks & Documentation Finalisation

**Status:** ⬜ Not Started
**Phase:** 7 — Integration Hardening
**Last Updated:** 2026-02-18

---

## Objective

Write operational runbooks for each Phase 7 failure mode so an on-call engineer can diagnose
and recover any failure within **15 minutes** using the runbooks alone. Finalise all architecture
docs with actual measured values from Steps 1-4. Create git tags to mark completion.

---

## Runbooks to Create

All runbooks go in `docs/operations/runbooks/`:

| File | Scenario | MTTR Target |
|------|----------|-------------|
| `feed-handler-crash-runbook.md` | Feed handler stopped / crashed | <5 min |
| `clickhouse-unavailable-runbook.md` | ClickHouse container down or unresponsive | <5 min |
| `redpanda-restart-runbook.md` | Redpanda restart / consumer lag spike | <5 min |
| `silver-mv-stall-runbook.md` | Silver or Gold MV stops processing | <10 min |
| `minio-unavailable-runbook.md` | MinIO down; Iceberg offloads failing | <5 min |

### Runbook Template

Each runbook follows the structure in `docs/CLAUDE.md`:
- **Symptoms** — what the on-call sees (alert, dashboard, user report)
- **Diagnosis** — commands to confirm root cause
- **Resolution** — ordered steps with copy-paste commands
- **Prevention** — what to change to avoid recurrence
- **Related monitoring** — dashboard link, alert name, metrics

---

## Documents to Update

### ARCHITECTURE-V2.md
- Update resource table with **actual measured** values from Step 2 (burn-in)
- Add latency numbers from Step 1 (p50/p99 per segment)
- Mark Phase 7 complete in the phase overview table

### Phase 7 PROGRESS.md
- Mark all 5 steps complete with dates and brief results summary
- Fill in latency benchmark results table
- Fill in resource validation results table

### v2/README.md
- Update status to "Phases 1–7 complete (87.5%); Phase 8 deferred"
- Update budget checkpoints table with Phase 7 actuals

### CURRENT-STATE.md
- Update Phase 7 to ✅ Complete
- Update Pending Work to Phase 8 (deferred) or "all done"

---

## Git Tags

```bash
# After all Phase 5 docs verified clean
git tag -a v2-phase-5-complete -m "Phase 5: Cold Tier / Iceberg Offload complete — 10/10 tables, 99.9%+ consistency"

# After Phase 7 acceptance criteria all pass
git tag -a v2-phase-7-complete -m "Phase 7: Integration Hardening complete — latency, resources, failure modes, alerting all verified"

git push origin v2-phase-5-complete v2-phase-7-complete
```

---

## Acceptance Criteria

- [ ] 5 runbooks created; each covers symptoms/diagnosis/resolution/prevention
- [ ] Any engineer unfamiliar with the system can recover each failure mode in <15 min
- [ ] `ARCHITECTURE-V2.md` updated with actual resource + latency measurements
- [ ] Phase 7 PROGRESS.md shows 5/5 complete with results
- [ ] v2/README.md shows Phases 1-7 complete
- [ ] CURRENT-STATE.md shows Phase 7 ✅ Complete
- [ ] Git tags `v2-phase-5-complete` and `v2-phase-7-complete` created and pushed

---

## Related

- [Phase 7 README](../README.md)
- [Phase 7 PROGRESS.md](../PROGRESS.md)
- [Step 1: Latency Benchmark](step-01-latency-benchmark.md) — results feed into ARCHITECTURE-V2.md
- [Step 2: Resource Validation](step-02-resource-validation.md) — results feed into ARCHITECTURE-V2.md
- [Step 3: Failure Mode Testing](step-03-failure-mode-testing.md) — scenarios become runbooks
- [Step 4: Monitoring & Alerting](step-04-monitoring-alerting.md) — alerts referenced in runbooks
- [Operations runbooks](../../../../operations/runbooks/)
