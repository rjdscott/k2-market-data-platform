# Claude Code Documentation Guide

**Purpose**: Guide Claude Code (AI assistant) in understanding and maintaining the K2 Market Data Platform documentation structure.

**Last Updated**: 2026-01-10
**Maintained By**: Engineering Team

---

## Documentation Philosophy

### Guiding Principles

1. **Separation of Concerns**: Architecture (permanent) vs Implementation (phase-specific)
2. **Layered Information**: Overview → Detailed Design → Implementation → Operations
3. **Version Everything**: All docs have last updated date and version context
4. **Cross-Link Thoughtfully**: Related docs reference each other explicitly
5. **AI-Friendly**: Clear structure, consistent naming, explicit templates

### Target Audiences

- **architecture/**: Principal/Staff engineers, architects
- **design/**: Senior engineers, implementation team
- **operations/**: DevOps, SREs, on-call engineers
- **testing/**: QA engineers, test automation
- **phases/**: Implementation team, project managers
- **reference/**: All engineers, new team members

---

## Directory Structure Guide

### `/docs/architecture/` - Permanent Architectural Documentation

**Purpose**: Long-lived architectural decisions that transcend individual phases
**Stability**: High - changes require RFC and review
**Update Frequency**: Quarterly or when major architecture changes

**When to update**:
- Technology stack changes (e.g., replacing DuckDB with Presto)
- Core principles evolve (e.g., adding new platform guardrail)
- System boundaries change (e.g., adding new layer)

**When NOT to update**:
- Implementation details change
- Phase-specific optimizations
- Tactical fixes or workarounds

**Key Files**:
- `platform-principles.md` - The "constitution" of the platform
- `system-design.md` - High-level component diagram
- `technology-decisions.md` - Why we chose Kafka, Iceberg, DuckDB, etc.

### `/docs/design/` - Detailed Design Documentation

**Purpose**: Component-level design decisions and trade-offs
**Stability**: Medium - may evolve between phases
**Update Frequency**: Monthly or when design changes

**When to update**:
- Query optimization strategies change
- Partitioning strategy evolves
- API contracts change
- Data consistency model changes

**Subsections**:
- `data-guarantees/` - Consistency, ordering, quality (critical for market data)
- `query-architecture.md` - How queries are routed and optimized
- `storage-layer.md` - Iceberg table design, partitioning
- `streaming-layer.md` - Kafka topics, serialization, delivery semantics

### `/docs/operations/` - Operational Runbooks

**Purpose**: Procedures for running and maintaining the platform
**Stability**: Medium - updated as incidents occur
**Update Frequency**: After each major incident or quarterly review

**When to update**:
- New failure mode discovered
- Runbook used and found incomplete
- New monitoring dashboard added
- SLOs change

**Subsections**:
- `runbooks/` - Step-by-step incident response procedures
- `monitoring/` - Dashboard design, alerting rules, SLOs
- `performance/` - Latency budgets, optimization guides

### `/docs/testing/` - Testing Strategy

**Purpose**: How we ensure correctness and quality
**Stability**: Medium - evolves with system complexity
**Update Frequency**: When test strategy changes

**When to update**:
- New test category added (e.g., chaos testing)
- Coverage targets change
- Test pyramid rebalanced
- New testing tool adopted

### `/docs/phases/` - Phase-Specific Implementation

**Purpose**: Track implementation progress for specific project phases
**Stability**: Low - actively updated during phase
**Update Frequency**: Daily to weekly during active development

**Current Phase**: `phase-1-core-demo/`
- Implementation plan with 16 steps
- Progress tracking (PROGRESS.md updated after each step)
- Phase-specific ADRs (DECISIONS.md)
- Validation guides

**Future Phases**:
- `phase-2-production-prep/` - Productionization (distributed deployment, auth, etc.)
- `phase-3-scale/` - Scaling to production load

**When to update**:
- Step completed or started
- Blocker encountered
- Architectural decision made (log in DECISIONS.md)
- Validation test results change

### `/docs/reference/` - Quick Reference Materials

**Purpose**: Quick lookup for common information
**Stability**: High - reference materials are stable
**Update Frequency**: When schemas, APIs, or terminology changes

**Key Files**:
- `glossary.md` - Terminology (e.g., "what is a sequence number?")
- `data-dictionary.md` - Schema definitions, field types
- `api-reference.md` - REST API endpoints, request/response examples
- `configuration.md` - All configurable parameters

---

## Documentation Update Workflows

### Workflow 1: Implementing a Step (Phase-Specific)

**Scenario**: User asks Claude to implement Step 5 (Configuration Management)

**Claude Should**:
1. Read `docs/phases/phase-1-core-demo/steps/step-05-configuration.md`
2. Implement the code per the step
3. Update the step file:
   - Change Status from ⬜ to ✅
   - Add actual time taken
   - Fill in Notes & Decisions section
4. Update `docs/phases/phase-1-core-demo/PROGRESS.md`:
   - Update "Detailed Progress" section for Step 5
   - Update completion percentage
5. If architectural decision made → Add to `docs/phases/phase-1-core-demo/DECISIONS.md`
6. If tests added → Reference in `docs/testing/strategy.md` if pattern is novel
7. Commit with message: `feat: complete step 5 - configuration management`

**Files Updated**:
- `docs/phases/phase-1-core-demo/steps/step-05-configuration.md`
- `docs/phases/phase-1-core-demo/PROGRESS.md`
- `docs/phases/phase-1-core-demo/DECISIONS.md` (if applicable)

### Workflow 2: Architectural Change

**Scenario**: User asks Claude to replace DuckDB with Presto

**Claude Should**:
1. Check `docs/architecture/technology-decisions.md` - understand current rationale
2. Ask user: "This is an architectural change. Should I create an RFC first?"
3. Update `docs/architecture/technology-decisions.md` - change query engine section
4. Update `docs/design/query-architecture.md` - detailed query routing changes
5. Create ADR in `docs/phases/phase-X-*/DECISIONS.md` for the phase implementing it
6. Update `docs/reference/configuration.md` - new config parameters
7. Update relevant runbooks in `docs/operations/runbooks/`

**Files Updated**:
- `docs/architecture/technology-decisions.md`
- `docs/design/query-architecture.md`
- `docs/phases/phase-X-*/DECISIONS.md`
- `docs/reference/configuration.md`
- `docs/operations/runbooks/*.md`

### Workflow 3: Adding Operational Runbook

**Scenario**: Production incident occurred, need runbook

**Claude Should**:
1. Create new file in `docs/operations/runbooks/` (e.g., `kafka-consumer-lag-runbook.md`)
2. Use runbook template (see Templates section below)
3. Add link to `docs/operations/README.md`
4. Reference from `docs/operations/monitoring/alerting.md` if alert exists

### Workflow 4: Updating Test Strategy

**Scenario**: Adding chaos engineering tests

**Claude Should**:
1. Update `docs/testing/strategy.md` - add chaos testing section
2. Create `docs/testing/chaos-testing.md` if substantial
3. Update `docs/phases/phase-X-*/steps/` - add chaos testing step
4. Add validation criteria to `docs/phases/phase-X-*/reference/verification-checklist.md`

### Workflow 5: Schema Evolution

**Scenario**: Adding new field to Trade schema

**Claude Should**:
1. Update `docs/reference/data-dictionary.md` - add new field
2. Update `docs/design/data-guarantees/consistency-model.md` if consistency implications
3. Add ADR to `docs/phases/phase-X-*/DECISIONS.md` explaining why field added
4. Update schema version in `docs/reference/versioning-policy.md`

---

## Documentation Templates

### ADR Template (for DECISIONS.md)

```markdown
### Decision #XXX: [Title]

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Deprecated | Superseded by #YYY
**Deciders**: [Names]
**Related Phase**: Phase X
**Related Steps**: Step Y, Step Z

#### Context
[Problem statement - what are we solving?]

#### Decision
[What we decided]

#### Consequences
**Positive**:
- Benefit 1
- Benefit 2

**Negative**:
- Cost 1
- Cost 2

**Neutral**:
- Trade-off 1

#### Alternatives Considered
1. **Option A**: [Why rejected]
2. **Option B**: [Why rejected]

#### Implementation Notes
[How to implement]

#### Verification
- [ ] Verification step 1
- [ ] Verification step 2
```

### Runbook Template

```markdown
# Runbook: [Incident Type]

**Severity**: Critical | High | Medium | Low
**Last Updated**: YYYY-MM-DD
**Maintained By**: [Team]

## Symptoms
[What the user/operator sees]

## Diagnosis
[How to confirm this is the issue]
- Command 1
- Expected output 1

## Resolution

### Step 1: [Action]
\`\`\`bash
command
\`\`\`
Expected result:

### Step 2: [Action]
...

## Prevention
[How to avoid this in the future]

## Related Monitoring
- Dashboard: [link]
- Alert: [name]
- Metrics: [metric names]

## Post-Incident
- [ ] Update this runbook if steps changed
- [ ] Create Jira ticket for root cause fix
- [ ] Update alerting if needed
```

### Architecture Document Template

```markdown
# [Component Name] Architecture

**Status**: Draft | Active | Deprecated
**Last Updated**: YYYY-MM-DD
**Maintained By**: [Team]
**Related ADRs**: #001, #002

## Overview
[1-2 paragraph summary of this component]

## Design Goals
1. Goal 1 (e.g., sub-second query latency)
2. Goal 2 (e.g., horizontal scalability)
3. Goal 3 (e.g., fault tolerance)

## Architecture Diagram
[Mermaid or ASCII diagram]

## Component Details

### Component A
**Purpose**: [What it does]
**Technology**: [What it's built with]
**Interfaces**: [How it's accessed]

### Component B
...

## Data Flow
[Describe how data flows through the system]

## Trade-offs
| Decision | Rationale | Limitation |
|----------|-----------|------------|
| Choice A | Reason    | Downside   |

## Scaling Strategy
[How this component scales]

## Failure Modes
[What can go wrong and impact]

## Monitoring
[Key metrics to track]

## Future Enhancements
[Planned improvements]

## References
- [Link to related design docs]
- [Link to ADRs]
```

---

## Common Scenarios and Responses

### Scenario: "Update the architecture documentation"

**Claude Should Ask**:
1. "Which specific architectural component changed?"
2. "Is this a permanent architectural change or phase-specific?"
3. "Should I create an ADR for this change?"

**Then**:
- Update `docs/architecture/` if permanent
- Update `docs/design/` if component-level
- Add ADR to appropriate phase's DECISIONS.md
- Update cross-references

### Scenario: "Document this new feature"

**Claude Should**:
1. Add implementation to current phase's step (if part of plan)
2. Update PROGRESS.md
3. Add to reference docs if it's user-facing (API, config)
4. Update testing docs if new test pattern
5. Create runbook if operational implications

### Scenario: "The implementation plan says to do X but you're doing Y"

**Claude Should**:
1. Acknowledge the deviation
2. Explain rationale
3. Ask: "Should I update the implementation plan step to reflect this approach?"
4. If yes: Update step file + add ADR explaining why approach changed

### Scenario: "Fix this bug"

**Claude Should**:
1. Fix the bug
2. Add regression test
3. Update runbook if it's an operational issue
4. NO need to update architecture/design docs unless bug revealed design flaw

---

## Documentation Maintenance Schedule

### Daily (During Active Development)
- `docs/phases/phase-X-*/PROGRESS.md` - Update after each step
- `docs/phases/phase-X-*/STATUS.md` - Update current blockers

### Weekly
- `docs/phases/phase-X-*/DECISIONS.md` - Add any ADRs for decisions made
- Review and update step files as implementation progresses

### Monthly
- Review `docs/design/` - Ensure design docs match implementation
- Update `docs/testing/strategy.md` - Reflect current test coverage
- Update `docs/operations/monitoring/` - Add new dashboards/alerts

### Quarterly
- Review `docs/architecture/` - Ensure principles still valid
- Update `docs/reference/` - Refresh glossary, data dictionary
- Review `docs/operations/runbooks/` - Test and update procedures

### Per Incident
- Create or update runbook immediately after incident
- Add to post-mortem checklist

---

## Cross-Referencing Guidelines

### Always Cross-Reference When:
1. Architecture doc mentions a component → Link to design doc
2. Design doc has operational implications → Link to runbook
3. Implementation step requires testing → Link to testing strategy
4. ADR supersedes previous decision → Link to old ADR
5. Runbook references monitoring → Link to dashboard doc

### Cross-Reference Format
```markdown
See [Query Architecture](../design/query-architecture.md) for detailed routing logic.

Related:
- [Platform Principles](../architecture/platform-principles.md#idempotency)
- [Decision #005](../phases/phase-1-core-demo/DECISIONS.md#decision-005)
- [Runbook: Query Timeout](../operations/runbooks/query-timeout-runbook.md)
```

---

## File Naming Conventions

### Directory Names
- Use kebab-case: `data-guarantees/`, `runbooks/`, `phase-1-core-demo/`
- Be specific: `monitoring/` not `mon/`
- Plural for collections: `runbooks/`, `reviews/`

### File Names
- Use kebab-case: `query-architecture.md`, `failure-recovery.md`
- Be descriptive: `kafka-consumer-lag-runbook.md` not `runbook1.md`
- Use prefixes for organization: `step-01-infrastructure.md`, `adr-001-duckdb.md`

### Special Files
- `README.md` - Overview/index for each major directory
- `PROGRESS.md` - Phase-specific progress tracking
- `DECISIONS.md` - Phase-specific ADR log
- `STATUS.md` - Current phase status snapshot
- `VALIDATION_GUIDE.md` - Phase validation procedures

---

## Claude Code Specific Guidelines

### When Reading Documentation
1. **Start with README.md** in the relevant directory
2. **Check PROGRESS.md** for current phase status
3. **Review DECISIONS.md** for recent architectural decisions
4. **Read the specific step file** if implementing a step

### When Updating Documentation
1. **Always update Last Updated date** at the top of modified files
2. **Use consistent emoji indicators**: ⬜ Not Started, 🟡 In Progress, ✅ Complete, 🔴 Blocked
3. **Maintain markdown formatting**: Use tables, code blocks, headers consistently
4. **Add cross-references**: Link to related docs
5. **Update multiple locations**: If architecture changes, update architecture/, design/, and phase DECISIONS.md

### When Unsure
1. **Ask the user**: "Should I update the architecture docs or just the implementation docs?"
2. **Default to ADR**: When in doubt, create an ADR documenting the decision
3. **Link don't duplicate**: Reference existing docs rather than duplicating content
4. **Err on the side of over-documentation**: Better to document and delete than forget

### Quality Checks Before Committing
- [ ] All cross-references work (no broken links)
- [ ] Last Updated date is current
- [ ] Status indicators are accurate
- [ ] Code blocks have syntax highlighting (```python, ```bash)
- [ ] Tables are properly formatted
- [ ] Markdown renders correctly (check with preview)

---

## Anti-Patterns to Avoid

### ❌ Don't Do This:
1. **Update implementation step file without updating PROGRESS.md**
2. **Make architectural change without ADR**
3. **Create duplicate documentation** (e.g., having same info in architecture/ and design/)
4. **Leave TODO comments in documentation** (either do it or create a ticket)
5. **Update design docs without checking if architecture docs need update**
6. **Skip cross-references** (every doc should link to related docs)
7. **Use absolute paths in links** (use relative paths: `../architecture/platform-principles.md`)

### ✅ Do This Instead:
1. **Update step file AND PROGRESS.md together**
2. **Create ADR first, then implement architectural change**
3. **Link to canonical source** (reference it, don't duplicate it)
4. **Complete the work or create Jira ticket**
5. **Check both architecture/ and design/ for consistency**
6. **Add "See also:" section with related docs**
7. **Use relative paths for portability**

---

## Examples

### Example 1: Completing Implementation Step

**User Request**: "Implement Step 6 - Kafka Producer"

**Claude Workflow**:
1. Read: `docs/phases/phase-1-core-demo/steps/step-06-kafka-producer.md`
2. Implement code in `src/k2/ingestion/producer.py`
3. Write tests in `tests/unit/test_producer.py`
4. Update `docs/phases/phase-1-core-demo/steps/step-06-kafka-producer.md`:
   - Status: ⬜ → ✅
   - Actual Time: 5 hours
   - Notes: "Implemented with idempotent config, added retry logic"
5. Update `docs/phases/phase-1-core-demo/PROGRESS.md`:
   - Step 06 status: ✅ Complete
   - Overall progress: 6/16 (37.5%)
6. No ADR needed (implementation matches plan)
7. Commit: `feat: complete step 6 - kafka producer with idempotency`

### Example 2: Architectural Change with ADR

**User Request**: "Replace DuckDB with Presto for query engine"

**Claude Workflow**:
1. Ask: "This is a significant architectural change. Should I create an RFC first or proceed with ADR?"
2. User: "Just ADR is fine"
3. Create ADR in `docs/phases/phase-2-production-prep/DECISIONS.md`:
   - Decision #008: Replace DuckDB with Presto
   - Context: Need distributed query capability
   - Decision: Migrate to Presto cluster
   - Consequences: Better scalability, higher operational complexity
4. Update `docs/architecture/technology-decisions.md`:
   - Change "Query Engine: DuckDB" → "Query Engine: Presto"
   - Add migration notes
5. Update `docs/design/query-architecture.md`:
   - Rewrite query execution section
   - Update connection pooling strategy
6. Update `docs/reference/configuration.md`:
   - Add Presto connection config parameters
7. Update `docs/operations/runbooks/query-timeout-runbook.md`:
   - Change DuckDB debugging to Presto debugging
8. Create `docs/operations/runbooks/presto-cluster-health.md`:
   - New runbook for Presto-specific issues

### Example 3: Adding a Runbook After Incident

**User Request**: "Document how to recover from Kafka consumer lag"

**Claude Workflow**:
1. Create `docs/operations/runbooks/kafka-consumer-lag-recovery.md`:
   - Use runbook template
   - Add symptoms, diagnosis, resolution steps
2. Update `docs/operations/README.md`:
   - Add link to new runbook in "Streaming Issues" section
3. Update `docs/operations/monitoring/alerting.md`:
   - Link runbook from "KafkaConsumerLag" alert definition
4. Update `docs/design/streaming-layer.md`:
   - Add note about consumer lag monitoring in "Operational Considerations"
5. Commit: `docs: add kafka consumer lag recovery runbook`

---

## Quick Reference

### Documentation Update Decision Tree

```
User asks to make a change
    │
    ├── Is it architectural (technology, boundaries, principles)?
    │   ├── Yes → Update docs/architecture/ + ADR + design/
    │   └── No → Continue
    │
    ├── Is it a design detail (component behavior, interfaces)?
    │   ├── Yes → Update docs/design/ + ADR if significant
    │   └── No → Continue
    │
    ├── Is it operational (runbook, monitoring, alerts)?
    │   ├── Yes → Update docs/operations/
    │   └── No → Continue
    │
    ├── Is it testing-related (new test pattern, strategy)?
    │   ├── Yes → Update docs/testing/
    │   └── No → Continue
    │
    ├── Is it implementation step completion?
    │   ├── Yes → Update phase step file + PROGRESS.md
    │   └── No → Continue
    │
    ├── Is it reference material (API, config, glossary)?
    │   ├── Yes → Update docs/reference/
    │   └── No → Continue
    │
    └── Unsure? Ask the user!
```

### Common Commands

```bash
# Check phase progress
make plan-check

# View current status
make plan-status

# List all steps with status
make plan-steps

# Open implementation plan
make plan-open

# Verify documentation structure
find docs -name "README.md" | sort

# Check for broken markdown links (install markdown-link-check)
find docs -name "*.md" -exec markdown-link-check {} \;

# Count documentation by category
wc -l docs/architecture/*.md | tail -1
wc -l docs/design/*.md | tail -1
wc -l docs/operations/**/*.md | tail -1
```

---

**Last Updated**: 2026-01-10
**Maintained By**: Engineering Team
**Questions?**: Create an issue or update this doc with your learnings
