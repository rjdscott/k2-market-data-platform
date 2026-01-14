# K2 Market Data Platform - Documentation

**Last Updated**: 2026-01-10
**Status**: Active Development

Welcome to the K2 platform documentation. This README serves as your navigation hub for all platform documentation.

---

## Quick Navigation

### For New Engineers
1. Start with [System Architecture & Design](./architecture/system-design.md) - **Visual overview with diagrams**
2. Read [Platform Principles](./architecture/platform-principles.md) - Core philosophy
3. Review [Architecture Overview](./architecture/README.md) - Detailed documentation
4. Check current [Implementation Status](phases/phase-1-single-node-equities/STATUS.md)

### For AI Assistants (Claude Code)
- **Start Here**: [CLAUDE.md](./CLAUDE.md) - Comprehensive AI assistant guidance
- **Current Phase**: [Phase 1 Progress](phases/phase-1-single-node-equities/PROGRESS.md)
- **Decision Log**: [Architectural Decisions](phases/phase-1-single-node-equities/DECISIONS.md)

### For Operators
- [Operations Runbooks](./operations/runbooks/)
- [Monitoring Dashboards](./operations/monitoring/)
- [Common Issues](./operations/README.md#troubleshooting)

---

## Documentation Structure

### 📐 [Architecture](./architecture/) - Permanent Architectural Decisions
Long-lived architectural choices that transcend individual phases.

**Key Documents**:
- [System Architecture & Design](./architecture/system-design.md) - **START HERE** - Visual diagrams, data flow, scaling
- [Platform Principles](./architecture/platform-principles.md) - Core operational philosophy
- [Kafka Topic Strategy](./architecture/kafka-topic-strategy.md) - Exchange-level topic architecture
- [Technology Decisions](./architecture/README.md#technology-stack) - Why Kafka, Iceberg, DuckDB
- [Alternative Architectures](./architecture/alternatives.md) - Architectures we considered

**When to read**: Understanding the "why" behind technology choices

---

### 🔧 [Design](./design/) - Detailed Component Design
Component-level design decisions, interfaces, and trade-offs.

**Key Documents**:
- [Query Architecture](./design/query-architecture.md) - Query routing and optimization
- [Storage Layer](./design/README.md#storage-layer) - Iceberg table design
- [Streaming Layer](./design/README.md#streaming-layer) - Kafka topics and serialization
- [Data Guarantees](./design/data-guarantees/) - Consistency, ordering, quality

**When to read**: Implementing or modifying specific components

---

### 🚨 [Operations](./operations/) - Runbooks and Procedures
Operational procedures, incident response, monitoring, and performance tuning.

**Key Documents**:
- [Runbooks](./operations/runbooks/) - Step-by-step incident response
- [Monitoring & Alerting](./operations/monitoring/) - Dashboards, alerts, SLOs
- [Performance Tuning](./operations/performance/) - Latency budgets, optimization

**When to read**: Running the platform, responding to incidents, performance issues

---

### 🧪 [Testing](./testing/) - Testing Strategy
How we ensure correctness, quality, and performance.

**Key Documents**:
- [Testing Strategy](./testing/strategy.md) - Overall approach and test pyramid
- [Unit Testing](./testing/README.md#unit-testing) - Unit test patterns
- [Integration Testing](./testing/README.md#integration-testing) - Component integration tests
- [E2E Testing](./testing/README.md#end-to-end-testing) - Full workflow validation

**When to read**: Writing tests, understanding test coverage requirements

---

### 📦 [Phases](./phases/) - Phase-Specific Implementation
Implementation tracking for specific project phases.

**Current Phase**: [Phase 1 - Core Demo](phases/phase-1-single-node-equities/)
- [Implementation Plan](phases/phase-1-single-node-equities/IMPLEMENTATION_PLAN.md) - 16-step plan
- [Progress Tracking](phases/phase-1-single-node-equities/PROGRESS.md) - Current status
- [Decisions Log](phases/phase-1-single-node-equities/DECISIONS.md) - Phase 1 ADRs
- [Validation Guide](phases/phase-1-single-node-equities/VALIDATION_GUIDE.md) - Testing procedures

**Future Phases**:
- [Phase 2 - Production Prep](phases/phase-3-demo-enhancements/) - Productionization
- [Phase 3 - Scale](phases/phase-4-consolidate-docs/) - Distributed scaling

**When to read**: Tracking implementation progress, understanding what's built

---

### 📚 [Reference](./reference/) - Quick Lookup
Quick reference materials for common information.

**Key Documents**:
- [Glossary](./reference/README.md#glossary) - Platform terminology
- [Data Dictionary](./reference/README.md#data-dictionary) - Schema and field reference
- [API Reference](./reference/README.md#api-reference) - REST API endpoints
- [Configuration](./reference/README.md#configuration) - All config parameters

**When to read**: Looking up terminology, API endpoints, configuration options

---

### 🏛️ [Governance](./governance/) - Data Governance
Data governance policies, assumptions, and RFC templates.

**Key Documents**:
- [Data Source Assumptions](./governance/data-source-assumptions.md) - Upstream data assumptions
- [RFC Template](./governance/rfc-template.md) - Request for Comments template

**When to read**: Understanding data lineage, proposing process changes

---

### 📝 [Reviews](./reviews/) - Project Reviews
Architecture and project review documents.

**Documents**:
- [Project Review](./reviews/project-review.md)
- [Architecture Review](./reviews/architecture-review.md)

---

## Documentation Maintenance

### Update Frequency
- **Architecture**: Quarterly or when major changes occur
- **Design**: Monthly or when component design changes
- **Operations**: After incidents or quarterly review
- **Testing**: When test strategy evolves
- **Phases**: Daily/weekly during active development
- **Reference**: When APIs or schemas change

### Ownership
- **Architecture & Design**: Engineering Team
- **Operations**: DevOps/SRE Team
- **Testing**: QA/Engineering Team
- **Phases**: Implementation Team
- **Reference**: Engineering Team
- **Governance**: Data Engineering Team

---

## Common Tasks

### Starting New Work
1. Check [Current Phase Status](phases/phase-1-single-node-equities/STATUS.md)
2. Review relevant [Implementation Step](phases/phase-1-single-node-equities/steps/)
3. Read related [Design Docs](./design/)
4. Understand [Testing Requirements](./testing/strategy.md)

### Responding to Incidents
1. Check [Operations Runbooks](./operations/runbooks/)
2. Review [Monitoring Dashboards](./operations/monitoring/)
3. Follow incident response procedures
4. Update runbook after resolution

### Making Architectural Changes
1. Review [Platform Principles](./architecture/platform-principles.md)
2. Check existing [Technology Decisions](./architecture/README.md#technology-stack)
3. Create ADR in [DECISIONS.md](phases/phase-1-single-node-equities/DECISIONS.md)
4. Update architecture and design docs
5. Update reference materials if needed

---

## Finding Information

### By Task Type
- **Implementing a feature**: phases/ → design/ → testing/
- **Fixing a bug**: operations/runbooks/ → testing/
- **Understanding design**: architecture/ → design/
- **Looking up API**: reference/
- **Responding to incident**: operations/

### By Keyword
Use `grep` or GitHub search:
```bash
# Find all mentions of "DuckDB"
grep -r "DuckDB" docs/

# Find all ADRs
grep -r "Decision #" docs/phases/

# Find all runbooks
find docs/operations/runbooks -name "*.md"
```

---

## Contributing to Documentation

### Before Making Changes
1. Read [CLAUDE.md](./CLAUDE.md) - AI assistant guidance
2. Check which directory your change belongs in
3. Follow file naming conventions (kebab-case)
4. Update "Last Updated" date at top of file

### When Adding New Documentation
1. Create file in appropriate directory
2. Follow template (see [CLAUDE.md Templates](./CLAUDE.md#documentation-templates))
3. Add cross-references to related docs
4. Update this README if adding new major section
5. Run link checker: `find docs -name "*.md" -exec markdown-link-check {} \;`

### Quality Checklist
- [ ] File in correct directory
- [ ] Last Updated date current
- [ ] Cross-references added
- [ ] Markdown properly formatted
- [ ] Code blocks have syntax highlighting
- [ ] No broken links

---

## Support

### Questions or Issues?
- Create GitHub issue with label `documentation`
- Tag `@engineering-team` for architecture questions
- Tag `@devops-team` for operations questions

### Improving This Documentation
- Submit PR with proposed changes
- Include rationale in PR description
- Update "Last Updated" date
- Ensure all links work

---

**Maintained By**: Engineering Team
**Review Frequency**: Monthly
**Last Review**: 2026-01-10
