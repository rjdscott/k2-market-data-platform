# Data Governance Documentation

**Last Updated**: 2026-01-10
**Stability**: High - governance policies are stable
**Target Audience**: Data Engineers, Compliance, Architects

This directory contains data governance policies, data source assumptions, and process improvement templates.

---

## Overview

Data governance ensures:
- **Data Quality**: Validation, completeness, accuracy
- **Data Lineage**: Track data from source to consumption
- **Compliance**: Regulatory requirements (future phases)
- **Process Improvement**: RFCs for proposing changes

**Note**: Phase 1 has minimal governance (focus on portfolio demo). Production governance comes in Phase 2+.

---

## Key Documents

### [Data Source Assumptions](./data-source-assumptions.md)
**Assumptions about upstream data sources**

Documents what we assume about incoming market data:
- Data format (CSV structure)
- Timestamp semantics (UTC, millisecond precision)
- Sequence number guarantees
- Exchange data quality
- Update frequency

**When to read**: Understanding data source contracts, debugging data issues

### [RFC Template](./rfc-template.md)
**Request for Comments Template**

Use this template to propose:
- Process improvements
- Governance policy changes
- Major architectural changes (that require team discussion)
- New data sources or integrations

**When to use**: Before making significant process or policy changes

---

## Data Governance Principles

### 1. Schema-First
**Principle**: Schemas are contracts, registered before use

**Implementation**:
- All Avro schemas in `src/k2/schemas/`
- Schema Registry enforces schema evolution rules
- Breaking changes require new major version

**Benefit**: Prevents data quality issues, enables evolution

### 2. Data Lineage (Future - Phase 2+)
**Principle**: Track data from source to consumption

**Planned Implementation**:
- OpenLineage integration
- Track transformations (CSV → Kafka → Iceberg)
- Record data provenance metadata

**Benefit**: Debugging, compliance, audit trails

### 3. Data Quality Validation
**Principle**: Validate at system boundaries

**Current Implementation**:
- Avro schema validation on Kafka produce
- Pydantic model validation in API
- Sequence number gap detection

**Future Enhancements**:
- Outlier detection (price spikes)
- Completeness checks (missing fields)
- Freshness monitoring (data staleness)

### 4. Access Control (Future - Phase 2+)
**Principle**: Least privilege access

**Planned Implementation**:
- Apache Ranger for table-level RBAC
- Column masking for sensitive data
- Row-level security for multi-tenant

**Benefit**: Data security, compliance

---

## Data Classification

### Public Data
**Classification**: Public
**Examples**: Stock symbols, company names, exchanges
**Access**: Unrestricted
**Retention**: Indefinite

### Market Data
**Classification**: Sensitive
**Examples**: Trades, quotes, prices
**Access**: Authenticated users (Phase 2+)
**Retention**: 7 years (regulatory requirement)

### Reference Data
**Classification**: Internal
**Examples**: Company IDs, sector mappings
**Access**: Internal systems only
**Retention**: Until superseded

---

## Data Retention Policy

### Phase 1 (Portfolio Demo)
- **Trades**: Retain all (small dataset)
- **Quotes**: Retain all (small dataset)
- **Reference Data**: Latest version only

### Phase 2+ (Production)
- **Trades**: 7 years (regulatory)
- **Quotes**: 1 year (operational)
- **Reference Data**: All versions (audit trail)
- **Iceberg Snapshots**: 30 days (time-travel)

---

## Data Quality Metrics

### Completeness
**Definition**: % of expected records received
**Target**: 99.9%
**Measurement**: Compare expected vs actual record counts
**Alert**: < 95% triggers warning

### Accuracy
**Definition**: % of records passing validation
**Target**: 99.99%
**Measurement**: Schema validation + range checks
**Alert**: < 99.9% triggers investigation

### Freshness
**Definition**: Time from exchange timestamp to query availability
**Target**: < 5 minutes
**Measurement**: Exchange timestamp vs ingestion timestamp
**Alert**: > 10 minutes triggers warning

### Consistency
**Definition**: No sequence gaps detected
**Target**: 0 gaps
**Measurement**: Sequence number gap detection
**Alert**: Any gap triggers investigation

---

## Compliance (Future - Phase 2+)

### Regulatory Requirements
- **MiFID II** (Europe): Trade reporting within 24 hours
- **SEC Rule 17a-25** (US): CAT reporting
- **GDPR** (Europe): Data privacy, right to deletion

### Audit Trail
- **User Actions**: Track API requests (who, what, when)
- **Data Modifications**: Track schema changes, data updates
- **Access Logs**: Track query execution

### Data Privacy
- **Anonymization**: Hash user identifiers (Phase 2+)
- **Encryption**: At-rest (S3) and in-transit (TLS)
- **Right to Deletion**: Support data removal requests

---

## Request for Comments (RFC) Process

### When to Create an RFC
- **Architectural Changes**: Technology stack changes
- **Process Changes**: New deployment procedures, governance policies
- **Major Features**: New data sources, significant integrations
- **Breaking Changes**: API changes, schema evolution

### RFC Template Usage

1. **Copy template**: [RFC Template](./rfc-template.md)
2. **Fill out sections**: Problem, Proposal, Alternatives, Impact
3. **Submit for review**: Create PR with RFC in `docs/governance/rfcs/`
4. **Discuss**: Team reviews and comments
5. **Decision**: Accept, Reject, or Request Changes
6. **Implement**: If accepted, create implementation plan
7. **Archive**: Link to ADR in relevant phase DECISIONS.md

### RFC Review Process
- **Author**: Proposes RFC
- **Reviewers**: Tech Lead + affected teams
- **Timeline**: 1 week for review
- **Decision**: Consensus or Tech Lead tie-breaker

---

## Data Source Assumptions

See [data-source-assumptions.md](./data-source-assumptions.md) for complete details.

### Key Assumptions
1. **CSV Format**: Consistent column order, headers present
2. **Timestamps**: UTC, millisecond precision
3. **Sequence Numbers**: Per-symbol monotonic (may have gaps)
4. **Exchange Codes**: Consistent (ASX, NYSE, NASDAQ, etc.)
5. **Price Decimals**: Up to 4 decimal places
6. **Volume**: Non-negative integers
7. **Symbol Format**: Alphanumeric, uppercase

### When Assumptions Violated
- **Log Warning**: Record assumption violation
- **Alert**: If critical (e.g., missing timestamps)
- **Reject Record**: If validation fails
- **Update Assumption Doc**: If source contract changes

---

## Future Governance Enhancements (Phase 2+)

### Data Catalog
- **Tool**: Apache Atlas or Amundsen
- **Purpose**: Discover datasets, understand lineage
- **Integration**: Catalog all Iceberg tables, Kafka topics

### Data Quality Platform
- **Tool**: Great Expectations or Deequ
- **Purpose**: Automated data quality checks
- **Integration**: Run checks on Iceberg write

### Data Lineage Tracking
- **Tool**: OpenLineage + Marquez
- **Purpose**: Track transformations end-to-end
- **Integration**: Instrument producer, consumer, query engine

### Access Control & Masking
- **Tool**: Apache Ranger
- **Purpose**: RBAC, column masking, row-level security
- **Integration**: Integrate with Iceberg and Presto/Trino

---

## Related Documentation

- **Architecture**: [../architecture/](../architecture/)
- **Design**: [../design/data-guarantees/](../design/data-guarantees/)
- **Operations**: [../operations/](../operations/)
- **Testing**: [../testing/](../testing/)

---

**Maintained By**: Data Engineering Team
**Review Frequency**: Quarterly
**Last Review**: 2026-01-10
