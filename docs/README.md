# K2 Market Data Platform - Documentation

**Last Updated**: 2026-02-09
**Version**: v0.1.0 (Preview Release)
**Status**: ⚠️ **Preview** - Phase 13 at 70% (Known security issues documented)

Welcome to the K2 platform documentation. This README serves as your navigation hub for all platform documentation.

> **⚠️ Important**: v0.1.0 is a preview release with [known security vulnerabilities](../KNOWN-ISSUES.md). Use for development/testing only. Production deployment requires v0.2 (target: 2026-02-20).

---

## Quick Start Paths

**New to K2?** → [**NAVIGATION.md**](./NAVIGATION.md) - Role-based documentation paths

### Find What You Need in <2 Minutes

- **🆕 New Engineer** (30 min) → [Onboarding Path](./NAVIGATION.md#-new-engineer-30-minute-onboarding-path)
- **🚨 On-Call Engineer** (15 min) → [Emergency Runbooks](./NAVIGATION.md#-operatoron-call-engineer-15-minute-emergency-path)
- **📡 API Consumer** (20 min) → [Integration Guide](./NAVIGATION.md#-api-consumer-20-minute-integration-path)
- **👨‍💻 Contributor** (45 min) → [Deep Dive Path](./NAVIGATION.md#-contributordeveloper-45-minute-deep-dive-path)

### AI Assistants (Claude Code)
- **Start Here**: [CLAUDE.md](./CLAUDE.md) - Comprehensive AI assistant guidance
- **Current Version**: v0.1.0 - [Release Notes](../RELEASE-NOTES-v0.1.md) | [Changelog](../CHANGELOG.md) | [Known Issues](../KNOWN-ISSUES.md)
- **Current Phase**: [Phase 13 OHLCV Analytics](phases/v1/phase-13-ohlcv-analytics/) 🟡 Preview (70%)
- **All Phases**: [Phase Guide](phases/PHASE-GUIDE.md) - Complete overview of 14 phases (0-13)

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
Implementation tracking for specific project phases across 14 development phases.

**📘 Complete Phase Guide**: [phases/PHASE-GUIDE.md](phases/PHASE-GUIDE.md) - Comprehensive overview of all 14 phases

**Foundation (Phases 0-3)**: ✅ Complete
- [Phase 0 - Technical Debt Resolution](phases/v1/phase-0-technical-debt-resolution/)
- [Phase 1 - Single-Node Implementation](phases/v1/phase-1-single-node-equities/)
- [Phase 2 - Multi-Source Foundation](phases/v1/phase-2-prep/)
- [Phase 3 - Demo Enhancements](phases/v1/phase-3-demo-enhancements/)

**Operations (Phases 4-7)**: ✅ Complete
- [Phase 4 - Demo Readiness](phases/v1/phase-4-demo-readiness/)
- [Phase 5 - Binance Production Resilience](phases/v1/phase-5-binance-production-resilience/)
- [Phase 6 - CI/CD Infrastructure](phases/v1/phase-6-cicd/)
- [Phase 7 - End-to-End Testing](phases/v1/phase-7-e2e/)

**Streaming (Phases 8-12)**: ✅ Complete
- [Phase 8 - E2E Demo Validation](phases/v1/phase-8-e2e-demo/)
- [Phase 9 - Demo Consolidation](phases/v1/phase-9-demo-consolidation/)
- [Phase 10 - Streaming Crypto Platform](phases/v1/phase-10-streaming-crypto/)
- [Phase 11 - Production Readiness](phases/v1/phase-11-production-readiness/)
- [Phase 12 - Flink Bronze Implementation](phases/v1/phase-12-flink-bronze-implementation/)

**Analytics (Phase 13)**: 🟡 Preview (70% - Security fixes needed for v0.2)
- [Phase 13 - OHLCV Analytics](phases/v1/phase-13-ohlcv-analytics/) - ⚠️ Known security issues

**When to read**: Tracking implementation progress, understanding what's built, planning next phases

---

### 📚 [Reference](./reference/) - Quick Lookup
Quick reference materials for common information.

**Key Documents**:
- [API Reference](./reference/api-reference.md) - REST API endpoints (includes new OHLCV endpoints)
- [Security Features](./reference/security-features.md) - Security architecture and best practices ⭐ NEW
- [Data Dictionary](./reference/data-dictionary-v2.md) - Schema and field reference
- [Glossary](./reference/README.md#glossary) - Platform terminology
- [Configuration](./reference/README.md#configuration) - All config parameters

**When to read**: Looking up terminology, API endpoints, security controls, configuration options

---

### 🏛️ [Governance](./governance/) - Data Governance
Data governance policies, assumptions, and RFC templates.

**Key Documents**:
- [Data Source Assumptions](./governance/data-source-assumptions.md) - Upstream data assumptions
- [RFC Template](./governance/rfc-template.md) - Request for Comments template

**When to read**: Understanding data lineage, proposing process changes

---

### 📝 [Reviews](./reviews/) - Expert Assessments & Code Reviews
Architecture and project review documents from staff engineers.

**Recent Reviews**:
- [OHLCV API Staff Review](./reviews/ohlcv-api-staff-review.md) - Comprehensive security & architecture review ⭐ NEW
- [OHLCV API Security Fixes Summary](./reviews/ohlcv-api-security-fixes-summary.md) - Implementation summary (A- grade) ⭐ NEW
- [Project Review](./reviews/project-review.md) - Overall platform assessment
- [Architecture Review](./reviews/architecture-review.md) - System design evaluation

**When to read**: Understanding security posture, production readiness, architectural decisions

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
1. Check [Current Phase Status](phases/v1/phase-1-single-node-equities/STATUS.md)
2. Review relevant [Implementation Step](phases/v1/phase-1-single-node-equities/steps/)
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
3. Create ADR in [DECISIONS.md](phases/v1/phase-1-single-node-equities/DECISIONS.md)
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
