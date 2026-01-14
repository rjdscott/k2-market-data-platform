# Claude Code Documentation Guide — Pragmatic Edition

**Purpose**: Guide Claude Code (AI assistant) in understanding and maintaining the K2 Market Data Platform documentation structure.

**Last Updated**: 2026-01-14
**Maintained By**: Engineering Team

---

## Documentation Philosophy

### Guiding Principles

1. **Progress Over Purity**: Documentation should enable forward momentum, not block implementation
2. **Lightweight Decisions**: Not every choice needs a full ADR - tier your documentation effort to match impact
3. **Separation of Concerns**: Architecture (permanent) vs Implementation (phase-specific)
4. **Layered Information**: Overview → Detailed Design → Implementation → Operations
5. **Version Everything**: All docs have last updated date and version context
6. **Cross-Link Thoughtfully**: Related docs reference each other explicitly
7. **AI-Friendly**: Clear structure, consistent naming, explicit templates

### Anti-Documentation-Overengineering Checklist

Before writing or updating documentation, ask yourself:

1. **Solving today's problem?** - Is this addressing a current, concrete need?
2. **Will it be read?** - Will someone actually reference this doc in 3 months?
3. **Proportional complexity?** - Is the documentation complexity less than 2× its value?
4. **80/20 principle?** - Can I document 80% of the behavior in 20% of the text?
5. **Engineer approval?** - Would a staff engineer approve this documentation scope?

**If you fail more than one check** → Simplify the documentation scope immediately.

### Presentation & Token Discipline

When writing/updating documentation:

**Content Limits**:
- Example code blocks: **max 20 lines** (use **bold** for key lines)
- Context snippets: **max 30-40 lines**
- Templates: **max 30 lines** (show only essential fields)
- Workflows: **max 5 steps** (combine related steps)

**Prefer**:
- Diffs over full code blocks
- References over duplication
- Inline links over repeated content
- Summaries over exhaustive listings

**Avoid**:
- Repeating content across multiple docs
- Templates longer than the median doc they describe
- Extensive examples that obscure the guidance

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

**Phase Structure** (5 phases total):

- **Phase 0**: `phase-0-technical-debt-resolution/` ✅ Complete
  - Critical technical debt cleanup
  - Operational improvements
  - Testing & quality enhancements

- **Phase 1**: `phase-1-single-node-equities/` ✅ Complete
  - Single-node implementation for equities data
  - 19 implementation steps
  - Foundation for multi-source platform

- **Phase 2**: `phase-2-prep/` ✅ Complete
  - Multi-source foundation (V2 schema)
  - Binance integration
  - Production preparation

- **Phase 3**: `phase-3-demo-enhancements/` 🟡 In Progress
  - Platform positioning & narrative
  - Demo execution enhancements
  - 9 implementation steps

- **Phase 4**: `phase-4-demo-readiness/` 🟢 **ACTIVE** (Current)
  - Final demo preparation
  - Validation & testing
  - 12 implementation steps

**Phase Documentation Pattern**:
Each phase contains:
- `README.md` - Phase overview and objectives
- `PROGRESS.md` - Step-by-step progress tracking
- `STATUS.md` - Current blockers and status
- `DECISIONS.md` - Phase-specific ADRs
- `VALIDATION_GUIDE.md` - Testing and validation procedures
- `steps/` - Individual step documentation
- `reference/` - Phase-specific reference materials

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

### `/docs/governance/` - Data Governance & Policies

**Purpose**: Data governance policies, assumptions, and RFC templates
**Stability**: High - governance policies are stable
**Update Frequency**: When governance policies change or new RFCs needed

**Key Files**:
- `data-source-assumptions.md` - Assumptions about data sources
- `rfc-template.md` - Template for Request for Comments

### `/docs/reviews/` - Expert Assessments & Code Reviews

**Purpose**: Comprehensive assessments from principal/staff engineers
**Stability**: Medium - reviews capture point-in-time state
**Update Frequency**: After major milestones or architecture changes

**Common Files**:
- Architecture reviews (e.g., `architecture-review.md`)
- Project assessments (e.g., `project-review.md`)
- Phase-specific reviews (e.g., `phase-2-prep-assessment.md`)
- Technical deep-dives (e.g., `connection-pool-review.md`)

**When to create**:
- Major phase completion
- Significant architectural decision
- Post-incident analysis requiring expert review

### `/docs/consolidation/` - Quality Metrics & Consolidation Reports

**Purpose**: Cross-cutting quality metrics and consolidation status
**Stability**: Low - updated frequently during consolidation efforts
**Update Frequency**: Weekly during consolidation periods

**Key Files**:
- `METRICS.md` - Documentation quality metrics
- `COMPREHENSIVE-REVIEW.md` - Full system review
- `QUALITY-CHECKLIST.md` - Quality validation checklist
- `PROGRESS.md` - Consolidation progress tracking

### `/docs/archive/` - Historical Documentation

**Purpose**: Preserve old versions and implementation logs
**Stability**: High - archives are immutable once created
**Update Frequency**: Rarely - only when archiving old versions

**Contents**:
- Historical implementation plans
- Old progress tracking
- Implementation session logs
- Deprecated documentation

**When to use**:
- Major refactoring requires preserving old approach
- Implementation logs for future reference
- Deprecated but potentially useful documentation

---

## Decision Documentation Framework (Tiered Approach)

### Overview

Not all decisions require the same level of documentation rigor. Use this three-tiered approach to match documentation effort to decision impact:

| Tier | Time Investment | Use For | Impact Level |
|------|----------------|---------|--------------|
| **Tier 1: Quick Decision** | 2-3 minutes | Phase-specific choices, library selection, minor design | < 1 day |
| **Tier 2: Simplified ADR** | 10-15 minutes | API design, data model changes, module structure | 1-5 days |
| **Tier 3: Full ADR** | 30-60 minutes | Technology stack changes, system boundaries, core principles | > 1 week, permanent |

### Tier 1: Quick Decision (4-Line Format)

**Use when**: Making phase-specific implementation choices that won't outlive the phase.

**Format** (adapted from `/CLAUDE.md`):
```markdown
Decision YYYY-MM-DD: <short title>
Reason: <one-line explanation>
Cost: <one-line trade-off>
Alternative: <one-line rejected option>
```

**Examples**:
- "Use asyncio vs threads for Kafka consumer"
- "Store config in YAML vs TOML"
- "Implement retry logic with exponential backoff"

**Where to document**: Phase DECISIONS.md (e.g., `docs/phases/phase-4-demo-readiness/DECISIONS.md`)

### Tier 2: Simplified ADR

**Use when**: Making design decisions that affect multiple components or future phases.

**Format** (condensed):
```markdown
### Decision #XXX: [Title]

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted
**Related Phase**: Phase X

#### Context
[2-3 sentences: what problem are we solving?]

#### Decision
[1-2 paragraphs: what we decided]

#### Consequences
**Positive**: [2-3 key benefits]
**Negative**: [2-3 key costs]
```

**Examples**:
- "V2 schema hybrid approach (source + universal fields)"
- "API endpoint design for multi-source queries"
- "Connection pool strategy for DuckDB"

**Where to document**: Phase DECISIONS.md for implementation, `docs/design/` if permanent

### Tier 3: Full ADR (Architectural Changes Only)

**Use when**: Making permanent architectural changes that transcend phases.

**Format**: Use the complete ADR template (see "Documentation Templates" section below)

**Examples**:
- "Replace DuckDB with Presto for distributed queries"
- "Add new persistence layer with Apache Iceberg"
- "Change platform positioning from trading to analytics"

**Where to document**:
- Phase DECISIONS.md for the implementation phase
- `docs/architecture/` for permanent reference
- May require RFC before proceeding

### Decision Tier Selection Guide

**Start with Tier 1** by default. Upgrade to higher tier if:

- **Upgrade to Tier 2** if:
  - Decision affects API contracts
  - Multiple teams need to understand the choice
  - Design trade-offs are non-obvious

- **Upgrade to Tier 3** if:
  - Technology stack changes
  - System boundaries change
  - Core platform principles evolve
  - Architectural review required

**When unsure**: Ask user "Should I use a quick decision format or create a full ADR?"

---

## Documentation Update Workflows

### Workflow 1: Implementing a Step (Phase-Specific)

**Scenario**: User asks Claude to implement Step 5 (Configuration Management)

**Claude Should**:
1. Read the step file for the current phase (e.g., `docs/phases/phase-4-demo-readiness/steps/step-05-xxx.md`)
2. Implement the code per the step
3. Update the step file:
   - Change Status from ⬜ to ✅
   - Add actual time taken
   - Add brief notes (2-3 sentences)
4. Update the phase's `PROGRESS.md`:
   - Update step status to ✅
   - Update completion percentage
5. **If significant decision made** → Add Tier 1 (4-line) decision to phase DECISIONS.md
6. **If tests added** → Only update `docs/testing/strategy.md` if novel pattern introduced
7. Commit with message: `feat(phase-X): complete step Y - [title]`

**Important**: Commit when code works and basic docs are updated. Documentation polish can iterate separately. No need to update architecture/ or design/ docs for phase-specific implementation.

**Files Updated**:
- `docs/phases/phase-1-single-node-equities/steps/step-05-configuration.md`
- `docs/phases/phase-1-single-node-equities/PROGRESS.md`
- `docs/phases/phase-1-single-node-equities/DECISIONS.md` (if applicable)

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

### Tier 3: Full ADR Template (Architectural Changes Only)

Use this comprehensive template only for Tier 3 decisions (architectural changes). For Tier 1/2, use the simpler formats described in the "Decision Documentation Framework" section above.

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
2. "Is this a permanent architectural change (Tier 3) or phase-specific (Tier 1/2)?"

**Then**:
- **If Tier 3 (permanent)**: Update `docs/architecture/` + create full ADR
- **If Tier 2 (design)**: Update `docs/design/` + simplified ADR
- **If Tier 1 (phase-specific)**: Update phase docs + 4-line decision
- Update cross-references

### Scenario: "Document this new feature"

**Claude Should**:
1. Implement the feature first (code + tests)
2. Add brief notes to current phase's step file
3. Update PROGRESS.md (mark step complete)
4. **Decision documentation**: Default to **Tier 1** (4-line format) unless:
   - API contract changes → Tier 2 (simplified ADR)
   - Architecture changes → Tier 3 (full ADR)
5. Add to reference docs only if user-facing (API, config)
6. Create runbook only if operational implications

### Scenario: "The implementation plan says to do X but you're doing Y"

**Claude Should**:
1. Acknowledge the deviation
2. Explain rationale (1-2 sentences)
3. Ask: "Should I update the implementation plan step to reflect this approach?"
4. If yes: Update step file + add **Tier 1 decision** (4-line format) explaining why approach changed
5. If significant architectural deviation: Upgrade to Tier 2/3 as appropriate

### Scenario: "Fix this bug"

**Claude Should**:
1. Fix the bug (code first)
2. Add regression test
3. Document fix briefly in commit message
4. **No formal decision documentation needed** for straightforward bug fixes
5. If bug reveals design flaw: Add Tier 1 decision noting the issue + fix approach
6. Update runbook only if operational/incident-related
7. NO need to update architecture/design docs unless bug revealed architectural flaw

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
- [Decision #005](../phases/phase-1-single-node-equities/DECISIONS.md#decision-005)
- [Runbook: Query Timeout](../operations/runbooks/query-timeout-runbook.md)
```

---

## File Naming Conventions

### Directory Names
- Use kebab-case: `data-guarantees/`, `runbooks/`, `phase-1-single-node-equities/`
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
2. **Default to simpler tier**: Start with Tier 1 (quick decision), upgrade if needed later
3. **Quick decision first**: Document with 4-line format now, formalize into full ADR if it becomes important
4. **Link don't duplicate**: Reference existing docs rather than duplicating content
5. **Progress over perfection**: Better to have working code with brief docs than blocked on documentation

### Quality Checks Before Committing

**Critical** (must have):
- [ ] Status indicators are accurate (⬜/🟡/✅/🔴)
- [ ] Last Updated date is current
- [ ] No broken cross-references in main workflow paths

**Nice-to-have** (fix if easy, defer if blocking):
- [ ] Code blocks have syntax highlighting (```python, ```bash)
- [ ] Tables are properly formatted
- [ ] Markdown renders correctly (check with preview)

**Note**: If quality checks are blocking progress, commit with working code and iterate on documentation polish later. Documentation debt is better than implementation debt.

---

## Anti-Patterns to Avoid

### ❌ Don't Do This:
1. **Update implementation step file without updating PROGRESS.md**
2. **Create full ADR for phase-specific decisions** (use Tier 1: 4-line format instead)
3. **Update documentation before code works** (violates "make it work first" principle)
4. **Create duplicate documentation** (e.g., having same info in architecture/ and design/)
5. **Write 50+ line templates when 20 lines sufficient** (violates token discipline)
6. **Block progress on documentation polish** (commit working code, iterate docs later)
7. **Skip cross-references** (every doc should link to related docs)
8. **Use absolute paths in links** (use relative paths: `../architecture/platform-principles.md`)

### ✅ Do This Instead:
1. **Update step file AND PROGRESS.md together** in same commit
2. **Use tiered approach**: Tier 1 (4-line) for phase decisions, Tier 3 (full ADR) for architectural only
3. **Document after code works**: Brief notes immediately, comprehensive docs when stable
4. **Link to canonical source** (reference it, don't duplicate it)
5. **Keep examples under 20-30 lines** (use diffs for longer changes)
6. **Commit when code works + basic docs updated** (polish can iterate)
7. **Add "See also:" section with related docs** (cross-reference actively)
8. **Use relative paths for portability** (works across different environments)

---

## Examples

### Example 1: Completing Implementation Step

**User Request**: "Implement Step 6 - Kafka Producer"

**Claude Workflow**:
1. Read: `docs/phases/phase-1-single-node-equities/steps/step-06-kafka-producer.md`
2. Implement code in `src/k2/ingestion/producer.py`
3. Write tests in `tests/unit/test_producer.py`
4. Update `docs/phases/phase-1-single-node-equities/steps/step-06-kafka-producer.md`:
   - Status: ⬜ → ✅
   - Actual Time: 5 hours
   - Notes: "Implemented with idempotent config, added retry logic"
5. Update `docs/phases/phase-1-single-node-equities/PROGRESS.md`:
   - Step 06 status: ✅ Complete
   - Overall progress: 6/16 (37.5%)
6. No ADR needed (implementation matches plan)
7. Commit: `feat: complete step 6 - kafka producer with idempotency`

### Example 2: Architectural Change with ADR (Tier 3)

**User Request**: "Replace DuckDB with Presto for query engine"

**Note**: This is a **Tier 3 decision** (architectural change) - requires full ADR and may need RFC.

**Claude Workflow**:
1. Recognize this is Tier 3 (technology stack change)
2. Create full ADR in `docs/phases/phase-2-prep/DECISIONS.md`:
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

**Last Updated**: 2026-01-14
**Maintained By**: Engineering Team
**Questions?**: Create an issue or update this doc with your learnings

---

*This guide inherits pragmatic principles from `/CLAUDE.md` while maintaining documentation-specific structure and rigor.*
