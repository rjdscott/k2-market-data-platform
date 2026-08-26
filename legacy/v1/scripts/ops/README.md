# Operations Scripts

Scripts for validating and maintaining the K2 platform operational readiness.

## Available Scripts

### `validate_runbooks.sh`

Validates that runbook procedures are executable and services are accessible.

**Purpose**:
- Verify required tools are installed (Docker, Kafka CLI, curl, jq, etc.)
- Test service connectivity (Kafka, MinIO, Prometheus, Grafana, API)
- Validate runbook file structure and content
- Check alert rules configuration
- Ensure operational procedures are tested and ready

**Usage**:

```bash
# Basic validation (syntax checks only)
./scripts/ops/validate_runbooks.sh

# Full validation (includes connectivity tests-backup)
./scripts/ops/validate_runbooks.sh --full

# Show help
./scripts/ops/validate_runbooks.sh --help
```

**Exit Codes**:
- `0`: All validations passed
- `1`: One or more validations failed
- `2`: Usage error

**Validation Categories**:

1. **Tool Availability**: Docker, Docker Compose, curl, jq, Python, uv, Kafka tools
2. **Service Connectivity**: Kafka, Schema Registry, Iceberg Catalog, MinIO, Prometheus, Grafana, Query API
3. **Kafka Commands**: Topic management, consumer groups, broker health checks
4. **Docker Commands**: Container management, service status
5. **API Health Checks**: Query API, Prometheus endpoints
6. **Python Environment**: Package accessibility, imports
7. **Storage Access**: MinIO/S3 connectivity
8. **Runbook Files**: Existence, structure, required sections
9. **Alert Rules**: YAML syntax, critical alerts defined

**Examples**:

```bash
# Pre-deployment validation
./scripts/ops/validate_runbooks.sh --full

# CI/CD integration
if ./scripts/ops/validate_runbooks.sh; then
    echo "Runbooks validated successfully"
else
    echo "Runbook validation failed - check operational procedures"
    exit 1
fi

# Local development validation
./scripts/ops/validate_runbooks.sh
```

**Output Example**:

```
===================================================================
         K2 Platform - Runbook Validation Suite
===================================================================

=== Tool Availability ===
✓ Command available: Docker CLI
✓ Command available: Docker Compose
✓ Command available: curl
✓ Command available: jq (JSON processor)
...

=== Runbook File Validation ===
✓ Runbook exists: failure-recovery.md
✓ Runbook has detection section: failure-recovery.md
✓ Runbook has recovery section: failure-recovery.md
...

===================================================================
                     Validation Results
===================================================================

Passed:  23
Failed:  0
Skipped: 15

✓ All validations passed!
ℹ Run with --full flag to test service connectivity
```

## Integration with CI/CD

Add to your CI/CD pipeline:

```yaml
# .github/workflows/validate.yml
- name: Validate Runbooks
  run: ./scripts/ops/validate_runbooks.sh --full
```

## Runbook Validation Criteria

Each runbook must have:
- **Detection Section**: How to detect the issue (alerts, symptoms)
- **Recovery Section**: Step-by-step recovery procedure
- **Command Examples**: Working bash commands (validated by script)

## Maintenance

Update `validate_runbooks.sh` when:
- New runbooks are added to `docs/operations/runbooks/`
- New services are deployed (add connectivity checks)
- New critical alerts are added (add to validation)
- New operational tools are required

## Related Documentation

- [Operations Guide](../../docs/operations/README.md)
- [Runbooks](../../docs/operations/runbooks/)
- [Monitoring Guide](../../docs/operations/monitoring/)
- [Alert Rules](../../monitoring/prometheus/alerts/)

## Troubleshooting

**Issue**: `Command not found: jq`
- **Solution**: Install jq: `brew install jq` (Mac) or `apt-get install jq` (Linux)

**Issue**: `Service unreachable: Kafka Broker`
- **Solution**: Start services: `docker-compose up -d`

**Issue**: `Kafka tools not available`
- **Solution**: Normal if Kafka not running. Install Kafka CLI or use Docker exec commands.

**Issue**: `Alert rules file not found`
- **Solution**: Create alert rules file at `monitoring/prometheus/alerts/platform-alerts.yml`
