# Documentation Maintenance Schedule

**Last Updated**: 2026-01-14
**Purpose**: Ensure K2 platform documentation remains current, accurate, and valuable
**Status**: Active

---

## Quick Reference

**Most Common Tasks**:
- ✏️ **Update phase progress** → `docs/phases/phase-X-*/PROGRESS.md` (weekly during active phase)
- 📝 **Add ADR** → `docs/phases/phase-X-*/DECISIONS.md` (as decisions made)
- 🔧 **Update API docs** → `docs/reference/api-reference.md` (when API changes)
- 📊 **Update runbook** → `docs/operations/runbooks/*.md` (after incidents)
- ✅ **Validate links** → `bash scripts/validate-docs.sh` (weekly or in CI/CD)

**Need Help?**
- Read [CLAUDE.md](./CLAUDE.md) for AI assistant guidance
- Check [QUALITY-CHECKLIST.md](./consolidation/QUALITY-CHECKLIST.md) for quality standards
- See [NAVIGATION.md](./NAVIGATION.md) for finding documentation

---

## Maintenance Philosophy

### Guiding Principles

1. **Documentation is Code**: Maintain docs with same rigor as code (version control, reviews, CI/CD)
2. **Update as You Work**: Change docs in the same commit as code changes
3. **Ownership is Clear**: Every doc category has a clear owner
4. **Automation First**: Automate validation wherever possible
5. **Quality Over Quantity**: Better to have 100 accurate pages than 200 stale pages

### Documentation Lifecycle

```
Create → Maintain → Archive → Delete
   ↑                    ↓
   └────── Update ←─────┘
```

- **Create**: New docs follow templates from CLAUDE.md
- **Maintain**: Regular updates per schedule below
- **Update**: Continuous updates as system evolves
- **Archive**: Move outdated docs to archive/ with context
- **Delete**: Remove only after 2+ years in archive

---

## Ownership Matrix

### By Category

| Category | Primary Owner | Secondary Owner | Update Frequency | Last Review |
|----------|---------------|-----------------|------------------|-------------|
| **architecture/** | Principal/Staff Engineers | Tech Lead | Quarterly | 2026-01-14 |
| **design/** | Senior Engineers | Tech Lead | Monthly | 2026-01-14 |
| **operations/** | DevOps/SRE Team | On-Call Engineers | After incidents, quarterly | 2026-01-14 |
| **testing/** | QA Engineers | Engineering Team | When strategy changes | 2026-01-14 |
| **phases/** | Phase Lead Engineer | Implementation Team | Daily/weekly (active phase) | 2026-01-14 |
| **reference/** | Technical Lead | Engineering Team | When APIs/schemas change | 2026-01-14 |
| **reviews/** | Project Lead | Engineering Manager | After major milestones | 2026-01-14 |
| **governance/** | Data Engineering Team | Principal Engineers | Quarterly | 2026-01-14 |
| **consolidation/** | Documentation Team | - | During consolidation project | 2026-01-14 |

### By Key Document

| Document | Owner | Update Trigger | Review Frequency |
|----------|-------|----------------|------------------|
| README.md (root) | Tech Lead | Major releases | Monthly |
| docs/README.md | Documentation Team | Structure changes | Monthly |
| docs/CLAUDE.md | Documentation Team | Process changes | Quarterly |
| docs/NAVIGATION.md | Documentation Team | Structure changes | Quarterly |
| docs/MAINTENANCE.md | Documentation Team | Process changes | Quarterly |
| platform-principles.md | Principal Engineer | Philosophy changes | Quarterly |
| system-design.md | Staff Engineer | Architecture changes | Quarterly |
| api-reference.md | Technical Lead | API changes | Per release |
| glossary.md | Technical Lead | New terms | Monthly |
| PROGRESS.md (phases) | Phase Lead | After each step | Weekly (active) |
| DECISIONS.md (phases) | Phase Lead | Per decision | As needed |

---

## Maintenance Schedules

### Continuous (Every Commit)

**Automated Checks**:
```bash
# Run in pre-commit hook or CI/CD
bash scripts/validate-docs.sh
```

**Manual Checks** (before committing):
- [ ] Updated "Last Updated" date in modified files
- [ ] All links in modified files work
- [ ] No placeholder text added (TODO, FIXME, TBD)
- [ ] Code examples tested and working
- [ ] Cross-references added where relevant

**Triggers**:
- API endpoint added/changed → Update `api-reference.md`
- Schema field added/changed → Update `data-dictionary-v2.md`
- Config parameter added/changed → Update `configuration.md`
- New operational scenario → Create/update runbook
- Architecture decision made → Add ADR to `DECISIONS.md`

---

### Daily (During Active Development)

**Phase Documentation**:
- Update `PROGRESS.md` after completing steps
- Update `STATUS.md` if blockers or risks change
- Add ADRs to `DECISIONS.md` for decisions made
- Update step files with actual time and notes

**Checklist**:
- [ ] Phase PROGRESS.md reflects today's work
- [ ] All completed steps marked ✅
- [ ] Blockers documented in STATUS.md
- [ ] ADRs added for any architectural decisions

---

### Weekly (During Active Development)

**Phase Review**:
- Review PROGRESS.md for accuracy
- Update overall completion percentage
- Review DECISIONS.md for completeness
- Check if design docs need updates

**Operations Review**:
- Review incident logs for runbook updates
- Check monitoring dashboards still documented
- Verify alert definitions match actual alerts

**Checklist**:
- [ ] Phase completion % accurate
- [ ] All ADRs have proper format
- [ ] Design docs reflect current implementation
- [ ] New runbooks created for any incidents

**Time Commitment**: 30 minutes/week

---

### Monthly (First Monday of Month)

**Design & Architecture Review**:
- Review design docs for currency
- Check if architecture docs need updates
- Verify technology stack documentation current
- Update reference materials (API, config, glossary)

**Quality Check**:
- Run full validation: `bash scripts/validate-docs.sh`
- Check for stale dates (>3 months old)
- Review cross-references for accuracy
- Spot-check code examples still work

**Checklist**:
- [ ] All design docs reviewed
- [ ] Architecture docs current
- [ ] Reference materials up-to-date
- [ ] All automated checks pass
- [ ] No stale dates in active docs
- [ ] Cross-references validated

**Time Commitment**: 2 hours/month

**Owner**: Technical Lead + Documentation Team

---

### Quarterly (January, April, July, October)

**Comprehensive Review**:
- Review all architecture docs
- Update all reference materials
- Validate all operational runbooks
- Review and update glossary
- Check governance docs current
- Update cost model
- Review test strategy
- Archive outdated phase docs

**Quality Validation**:
- Run full `QUALITY-CHECKLIST.md` validation
- Test all runbook procedures
- Validate all API examples
- Check all configuration examples
- Review documentation health metrics

**Checklist**:
- [ ] All items in QUALITY-CHECKLIST.md validated
- [ ] Architecture docs reviewed by Principal Engineer
- [ ] All runbooks tested (at least read-through)
- [ ] Reference materials validated against actual system
- [ ] Glossary covers all current terms
- [ ] Cost model reflects current infrastructure
- [ ] Test coverage targets still appropriate
- [ ] Outdated phase docs moved to archive/
- [ ] Documentation health metrics updated

**Time Commitment**: 4-6 hours/quarter

**Owner**: Documentation Team + Category Owners

**Quarterly Review Dates (2026)**:
- ✅ January 14, 2026 (Phase 7 completion)
- ⬜ April 14, 2026 (Q2 review)
- ⬜ July 14, 2026 (Q3 review)
- ⬜ October 14, 2026 (Q4 review)

---

### Annual (January)

**Full Documentation Audit**:
- Comprehensive review of all categories
- Major updates to outdated content
- Archive completed phases (if >1 year old)
- Review documentation structure
- Update templates and guidelines
- Refresh all screenshots and diagrams
- Validate all external links

**Strategic Planning**:
- Review documentation strategy
- Update CLAUDE.md guidance
- Improve NAVIGATION.md based on user feedback
- Update MAINTENANCE.md schedule
- Set documentation goals for next year

**Checklist**:
- [ ] All categories comprehensively reviewed
- [ ] Completed phases archived appropriately
- [ ] Documentation structure still optimal
- [ ] Templates and guidelines current
- [ ] All screenshots/diagrams refreshed
- [ ] External links validated
- [ ] User feedback incorporated
- [ ] Next year's goals set

**Time Commitment**: 8-12 hours/year

**Owner**: Documentation Team + All Category Owners

**Next Annual Review**: January 2027

---

### Event-Driven Maintenance

#### After Incidents (Immediate)

**Runbook Update**:
- Update existing runbook if steps changed
- Create new runbook if new failure mode
- Add incident timeline to runbook
- Update monitoring/alerting docs

**Checklist**:
- [ ] Runbook created or updated
- [ ] Symptoms documented
- [ ] Diagnosis steps clear
- [ ] Resolution steps tested
- [ ] Prevention section added
- [ ] Monitoring links included
- [ ] "Last Tested" date current

**Time Commitment**: 1-2 hours per incident

**Owner**: On-Call Engineer who resolved incident

---

#### After Major Releases (Within 1 Week)

**API & Reference Updates**:
- Update API reference for any endpoint changes
- Update configuration reference for new parameters
- Update data dictionary for schema changes
- Update glossary for new terms
- Update architecture docs for major changes

**Checklist**:
- [ ] API reference matches `/docs` endpoint
- [ ] All new config parameters documented
- [ ] Schema changes reflected in data dictionary
- [ ] New terms added to glossary
- [ ] Architecture docs reflect any changes
- [ ] Migration guides created if breaking changes
- [ ] Changelog updated

**Time Commitment**: 2-4 hours per major release

**Owner**: Technical Lead

---

#### After Architectural Changes (Immediate)

**Architecture & Design Updates**:
- Create ADR in current phase DECISIONS.md
- Update architecture docs
- Update design docs
- Update implementation plans
- Update runbooks if operational impact

**Checklist**:
- [ ] ADR created with full context
- [ ] Architecture docs updated
- [ ] Design docs updated
- [ ] Implementation plans adjusted
- [ ] Runbooks updated if needed
- [ ] Cross-references added
- [ ] Team notified of changes

**Time Commitment**: 2-4 hours per architectural change

**Owner**: Architect/Principal Engineer who made decision

---

#### After Test Strategy Changes (Within 1 Week)

**Testing Documentation Updates**:
- Update testing strategy doc
- Update coverage targets
- Update test pyramid if changed
- Document new test types
- Update CI/CD docs if changed

**Checklist**:
- [ ] Strategy doc reflects new approach
- [ ] Coverage targets updated
- [ ] Test pyramid adjusted
- [ ] New test types documented with examples
- [ ] CI/CD changes documented

**Time Commitment**: 1-2 hours per strategy change

**Owner**: QA Lead

---

## Maintenance Procedures

### Procedure 1: Updating Phase Documentation

**When**: Daily/weekly during active phase development

**Steps**:
1. Complete implementation step
2. Update step file:
   - Change status: ⬜ → 🟡 → ✅
   - Fill in actual time taken
   - Add notes and decisions
3. Update `PROGRESS.md`:
   - Update step status
   - Recalculate completion percentage
   - Add any blockers
4. If architectural decision made:
   - Add ADR to `DECISIONS.md`
   - Follow ADR template from CLAUDE.md
5. If design changed:
   - Update relevant design doc in `docs/design/`
6. Commit with message: `docs: update phase X progress - completed step Y`

**Example Commit**:
```bash
git add docs/phases/phase-3-demo-enhancements/
git commit -m "docs: update phase 3 progress - completed step 2 (streaming demos)"
```

---

### Procedure 2: Creating/Updating Runbooks

**When**: After incidents or when new operational scenarios discovered

**Steps**:
1. Identify the scenario (symptom, impact, severity)
2. Create or update runbook file in `docs/operations/runbooks/`
3. Use runbook template from CLAUDE.md:
   - Symptoms
   - Diagnosis
   - Resolution (step-by-step)
   - Prevention
   - Related Monitoring
4. Test the runbook (at minimum, read-through)
5. Add "Last Tested" date
6. Link from `docs/operations/README.md`
7. Link from relevant alert definition in `docs/operations/monitoring/alerting.md`
8. Commit with message: `docs: add/update runbook for [scenario]`

**Example**:
```bash
# Create new runbook
vim docs/operations/runbooks/iceberg-table-lock-timeout.md

# Update operations index
vim docs/operations/README.md

# Link from alerting doc
vim docs/operations/monitoring/alerting.md

# Commit
git add docs/operations/
git commit -m "docs: add runbook for Iceberg table lock timeouts"
```

---

### Procedure 3: Updating API Documentation

**When**: API endpoint added, changed, or removed

**Steps**:
1. Make code changes to API
2. Update `docs/reference/api-reference.md`:
   - Add new endpoint or modify existing
   - Update request/response examples
   - Update error codes if changed
3. Test examples (curl, Python, JavaScript)
4. Update OpenAPI spec if applicable
5. Verify `/docs` endpoint matches documentation
6. Update CHANGELOG.md
7. Commit API changes and docs together

**Example**:
```bash
# Make API changes
vim src/k2/api/routes/trades.py

# Update documentation
vim docs/reference/api-reference.md

# Test examples
curl http://localhost:8000/api/v1/trades?symbol=BHP

# Commit together
git add src/k2/api/ docs/reference/api-reference.md CHANGELOG.md
git commit -m "feat(api): add trade filtering by exchange

- Add exchange parameter to /v1/trades endpoint
- Update API documentation with examples
- Add tests for new filter"
```

---

### Procedure 4: Adding Architectural Decision Record (ADR)

**When**: Significant architectural or design decision made

**Steps**:
1. Identify which phase this belongs to (current active phase usually)
2. Open `docs/phases/phase-X-*/DECISIONS.md`
3. Add new ADR using template from CLAUDE.md:
   - Title with decision number
   - Date, status, deciders
   - Context (problem statement)
   - Decision (what was decided)
   - Consequences (positive, negative, neutral)
   - Alternatives considered
4. Update cross-phase ADR index: `docs/architecture/DECISIONS.md`
5. Update relevant architecture/design docs
6. Commit with message: `docs: add ADR #XXX - [title]`

**Example**:
```bash
# Add ADR to phase DECISIONS.md
vim docs/phases/phase-3-demo-enhancements/DECISIONS.md

# Update cross-phase index
vim docs/architecture/DECISIONS.md

# Update architecture doc if relevant
vim docs/architecture/system-design.md

# Commit
git add docs/
git commit -m "docs: add ADR #012 - use Plotly for interactive visualizations

Decision to use Plotly instead of Matplotlib for demo notebooks
to enable interactive charts and better user experience."
```

---

### Procedure 5: Archiving Outdated Documentation

**When**: Documentation no longer relevant (>1 year old, superseded, or abandoned work)

**Steps**:
1. Create archive directory if needed: `docs/archive/YYYY-MM-DD-[topic]/`
2. Move outdated docs to archive
3. Create `README.md` in archive directory explaining:
   - What was archived
   - Why it was archived
   - Date of archiving
   - Links to current replacements (if any)
4. Update cross-references (point to new docs)
5. Update main README if major section archived
6. Commit with message: `docs: archive outdated [topic] documentation`

**Example**:
```bash
# Create archive directory
mkdir -p docs/archive/2026-01-14-phase-0-detailed-logs/

# Move outdated docs
mv docs/reviews/day1-morning.md docs/archive/2026-01-14-phase-0-detailed-logs/
mv docs/reviews/day1-afternoon.md docs/archive/2026-01-14-phase-0-detailed-logs/

# Create archive README
cat > docs/archive/2026-01-14-phase-0-detailed-logs/README.md << 'EOF'
# Archived: Phase 0 Detailed Progress Logs

**Archived Date**: 2026-01-14
**Reason**: Detailed day-by-day logs consolidated into completion reports

## Contents
- day1-morning.md - Phase 0 fixes (morning session)
- day1-afternoon.md - Phase 0 fixes (afternoon session)

## Current Documentation
See consolidated completion report:
- [Phase 0 Completion Report](../phases/phase-0-technical-debt-resolution/COMPLETION-REPORT.md)
EOF

# Commit
git add docs/archive/ docs/reviews/
git commit -m "docs: archive Phase 0 detailed logs - consolidated into completion report"
```

---

### Procedure 6: Running Quality Validation

**When**: Weekly (quick check) or quarterly (comprehensive)

**Steps**:
1. Run automated validation:
   ```bash
   bash scripts/validate-docs.sh
   ```
2. Check results:
   - Zero broken links
   - Zero old phase references
   - Zero empty directories
3. If errors found, fix immediately
4. For quarterly review, run full QUALITY-CHECKLIST.md
5. Document any issues found
6. Track issues to resolution

**Example Weekly Check**:
```bash
# Run validation
bash scripts/validate-docs.sh

# If issues found, fix and re-run
vim docs/path/to/broken/link.md
bash scripts/validate-docs.sh

# Commit fixes
git add docs/
git commit -m "docs: fix broken cross-references found in validation"
```

---

## Automation

### Pre-Commit Hook (Recommended)

**Setup**:
```bash
# Create pre-commit hook
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash

# Run documentation validation
echo "Running documentation validation..."
bash scripts/validate-docs.sh --quiet

if [ $? -ne 0 ]; then
    echo "❌ Documentation validation failed. Please fix issues before committing."
    exit 1
fi

echo "✅ Documentation validation passed."
EOF

chmod +x .git/hooks/pre-commit
```

---

### CI/CD Integration (Optional)

**GitHub Actions Example**:
```yaml
# .github/workflows/docs-validation.yml
name: Documentation Validation

on: [push, pull_request]

jobs:
  validate-docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Run documentation validation
        run: bash scripts/validate-docs.sh

      - name: Check for placeholders
        run: |
          if grep -r "TODO\|FIXME\|TBD" docs/ --exclude-dir=archive | grep -v "TODOWrite"; then
            echo "❌ Found placeholder text in documentation"
            exit 1
          fi

      - name: Validate markdown links
        uses: gaurav-nelson/github-action-markdown-link-check@v1
        with:
          use-quiet-mode: 'yes'
          config-file: '.markdown-link-check.json'
```

---

### Automated Metrics Collection

**Cron Job for Weekly Metrics**:
```bash
# Add to crontab: 0 9 * * 1 (Monday 9am)
#!/bin/bash

cd /path/to/k2-market-data-platform

# Count files by category
echo "Documentation Metrics - $(date)" >> docs/consolidation/metrics-history.log
find docs/architecture -name "*.md" | wc -l >> docs/consolidation/metrics-history.log
find docs/design -name "*.md" | wc -l >> docs/consolidation/metrics-history.log
find docs/operations -name "*.md" | wc -l >> docs/consolidation/metrics-history.log

# Run validation
bash scripts/validate-docs.sh >> docs/consolidation/validation-history.log
```

---

## Troubleshooting

### Issue: Validation Script Fails with "Broken Links"

**Diagnosis**:
```bash
bash scripts/validate-docs.sh | grep "❌"
```

**Resolution**:
1. Check if file was renamed/moved
2. Update all references to old path
3. Use grep to find all references:
   ```bash
   grep -r "old-filename.md" docs/
   ```
4. Fix all cross-references
5. Re-run validation

---

### Issue: "Last Updated" Dates Getting Stale

**Diagnosis**:
```bash
# Find docs not updated in 6 months
find docs -name "*.md" -mtime +180 ! -path "*/archive/*"
```

**Resolution**:
1. Review each stale document
2. Update content if needed
3. Update "Last Updated" date
4. If no updates needed, document review in git:
   ```bash
   git commit --allow-empty -m "docs: reviewed [file] - no changes needed"
   ```

---

### Issue: Duplicate Content Found

**Diagnosis**:
```bash
# Search for duplicate key phrases
grep -r "specific phrase" docs/ --include="*.md" | wc -l
```

**Resolution**:
1. Identify canonical source
2. Remove duplicates
3. Add cross-references to canonical source
4. Update QUALITY-CHECKLIST.md if new canonical source

---

## Contacts

### Documentation Team
- **Lead**: Technical Lead
- **Email**: tech-lead@k2platform.example
- **Slack**: #k2-documentation

### Category Owners
- **Architecture**: principal-engineer@k2platform.example
- **Operations**: devops-team@k2platform.example
- **Testing**: qa-lead@k2platform.example

### Questions?
- Create GitHub issue with label `documentation`
- Post in Slack #k2-documentation
- Review this maintenance guide
- Check CLAUDE.md for AI assistant help

---

## Success Metrics

### Health Indicators

**Excellent (A grade)**:
- [ ] Zero broken links
- [ ] Zero placeholders in active docs
- [ ] 95%+ docs updated within 6 months
- [ ] All runbooks tested within 6 months
- [ ] Full quarterly reviews completed

**Good (B grade)**:
- [ ] <5 broken links
- [ ] <5 placeholders in active docs
- [ ] 85%+ docs updated within 6 months
- [ ] 80%+ runbooks tested within 6 months
- [ ] Quarterly reviews mostly complete

**Needs Improvement (C grade)**:
- <10 broken links
- <10 placeholders
- 70%+ docs updated within 6 months
- Action plan needed

**Current Status**: ✅ **Excellent (A- grade, 9.2/10)**

---

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-01-14 | Initial creation (Phase 7) | Documentation Team |
| 2026-01-14 | Added ownership matrix and schedules | Documentation Team |

---

**Maintained By**: Documentation Team
**Review Frequency**: Quarterly
**Last Review**: 2026-01-14
**Next Review**: 2026-04-14
