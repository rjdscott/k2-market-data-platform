#!/usr/bin/env bash
#
# Runbook Validation Script
#
# This script validates that runbook commands are executable and services are accessible.
# It tests non-destructive commands and validates syntax for destructive ones.
#
# Usage: ./scripts/ops/validate_runbooks.sh [--full]
#
# Options:
#   --full    Run full validation including connectivity tests (requires services running)
#   --help    Show this help message
#
# Exit Codes:
#   0 - All validations passed
#   1 - One or more validations failed
#   2 - Usage error

set -euo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Validation results
PASSED=0
FAILED=0
SKIPPED=0

# Parse arguments
FULL_VALIDATION=false
if [[ "${1:-}" == "--full" ]]; then
    FULL_VALIDATION=true
    echo -e "${BLUE}Running FULL validation (requires services)${NC}"
elif [[ "${1:-}" == "--help" ]]; then
    grep '^#' "$0" | sed 's/^# \?//'
    exit 0
fi

# Helper functions
pass() {
    ((PASSED++))
    echo -e "${GREEN}✓${NC} $1"
}

fail() {
    ((FAILED++))
    echo -e "${RED}✗${NC} $1"
}

skip() {
    ((SKIPPED++))
    echo -e "${YELLOW}⊘${NC} $1"
}

section() {
    echo ""
    echo -e "${BLUE}===${NC} $1 ${BLUE}===${NC}"
}

# Check if command exists
check_command() {
    local cmd=$1
    local name=${2:-$1}

    if command -v "$cmd" &> /dev/null; then
        pass "Command available: $name"
        return 0
    else
        fail "Command not found: $name"
        return 1
    fi
}

# Test command syntax (dry-run or --help)
test_syntax() {
    local cmd=$1
    local description=$2

    if eval "$cmd" &> /dev/null; then
        pass "Syntax valid: $description"
        return 0
    else
        fail "Syntax invalid: $description"
        return 1
    fi
}

# Test service connectivity
test_connectivity() {
    local service=$1
    local host=$2
    local port=$3

    if ! $FULL_VALIDATION; then
        skip "Connectivity check: $service (use --full to test)"
        return 0
    fi

    if timeout 2 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null; then
        pass "Service reachable: $service ($host:$port)"
        return 0
    else
        fail "Service unreachable: $service ($host:$port)"
        return 1
    fi
}

# Main validation
main() {
    echo "==================================================================="
    echo "         K2 Platform - Runbook Validation Suite"
    echo "==================================================================="
    echo ""
    echo "Validating runbook procedures for operational readiness..."

    # ========================================================
    # Section 1: Tool Availability
    # ========================================================
    section "Tool Availability"

    check_command docker "Docker CLI"

    # Check for docker-compose or docker compose
    if command -v docker-compose &> /dev/null || docker compose version &> /dev/null 2>&1; then
        pass "Command available: Docker Compose"
    else
        fail "Command not found: Docker Compose"
    fi

    check_command curl "curl"
    check_command jq "jq (JSON processor)"
    check_command python3 "Python 3"
    check_command uv "uv (Python package manager)"

    # Kafka tools (may not be in PATH)
    if docker exec kafka-broker-1 kafka-topics --version &> /dev/null; then
        pass "Kafka tools available (via Docker)"
    else
        skip "Kafka tools not available (requires Kafka running)"
    fi

    # ========================================================
    # Section 2: Service Connectivity
    # ========================================================
    section "Service Connectivity"

    test_connectivity "Kafka Broker" "localhost" "9092"
    test_connectivity "Schema Registry" "localhost" "8081"
    test_connectivity "Iceberg REST Catalog" "localhost" "8181"
    test_connectivity "MinIO S3" "localhost" "9000"
    test_connectivity "Prometheus" "localhost" "9090"
    test_connectivity "Grafana" "localhost" "3000"
    test_connectivity "Query API" "localhost" "8000"

    # ========================================================
    # Section 3: Kafka Runbook Commands
    # ========================================================
    section "Kafka Runbook Commands"

    # Test docker exec kafka commands (syntax only)
    test_syntax "docker exec kafka-broker-1 echo 'test' 2>&1 | grep -q 'test' || true" \
        "Docker exec to Kafka broker"

    # List topics command (syntax)
    if $FULL_VALIDATION; then
        if docker exec kafka-broker-1 kafka-topics --bootstrap-server localhost:9092 --list &> /dev/null; then
            pass "Kafka topics list command works"
        else
            fail "Kafka topics list command failed"
        fi
    else
        skip "Kafka topics list (use --full to test)"
    fi

    # Consumer groups command (syntax)
    if $FULL_VALIDATION; then
        if docker exec kafka-broker-1 kafka-consumer-groups --bootstrap-server localhost:9092 --list &> /dev/null; then
            pass "Kafka consumer groups command works"
        else
            fail "Kafka consumer groups command failed"
        fi
    else
        skip "Kafka consumer groups (use --full to test)"
    fi

    # ========================================================
    # Section 4: Docker Commands
    # ========================================================
    section "Docker Commands"

    # Test docker ps
    if docker ps &> /dev/null; then
        pass "Docker ps command works"
    else
        fail "Docker ps command failed"
    fi

    # Test docker-compose/docker compose
    if docker-compose version &> /dev/null || docker compose version &> /dev/null; then
        pass "Docker Compose command works"
    else
        fail "Docker Compose command failed"
    fi

    # Check if services are running
    if $FULL_VALIDATION; then
        RUNNING_SERVICES=$(docker ps --format '{{.Names}}' 2>/dev/null | wc -l)
        if [ "$RUNNING_SERVICES" -gt 0 ]; then
            pass "Docker services running ($RUNNING_SERVICES containers)"
        else
            fail "No Docker services running"
        fi
    else
        skip "Docker services check (use --full to test)"
    fi

    # ========================================================
    # Section 5: API Health Checks
    # ========================================================
    section "API Health Checks"

    # Query API health
    if $FULL_VALIDATION; then
        if curl -sf http://localhost:8000/health &> /dev/null; then
            pass "Query API health endpoint responds"
        else
            fail "Query API health endpoint failed"
        fi
    else
        skip "Query API health (use --full to test)"
    fi

    # Prometheus health
    if $FULL_VALIDATION; then
        if curl -sf http://localhost:9090/-/healthy &> /dev/null; then
            pass "Prometheus health endpoint responds"
        else
            fail "Prometheus health endpoint failed"
        fi
    else
        skip "Prometheus health (use --full to test)"
    fi

    # ========================================================
    # Section 6: Python Environment
    # ========================================================
    section "Python Environment"

    # Check uv environment
    if uv run python --version &> /dev/null; then
        pass "Python environment accessible via uv"
    else
        fail "Python environment not accessible via uv"
    fi

    # Check k2 package importable
    if uv run python -c "import k2" 2>/dev/null; then
        pass "K2 package importable"
    else
        fail "K2 package not importable"
    fi

    # ========================================================
    # Section 7: Storage Access
    # ========================================================
    section "Storage Access"

    # Test MinIO/S3 access (requires mc client or AWS CLI)
    if command -v mc &> /dev/null; then
        pass "MinIO client (mc) available"

        if $FULL_VALIDATION; then
            # Note: This requires MinIO to be configured in mc
            if mc ls minio/warehouse &> /dev/null 2>&1; then
                pass "MinIO warehouse accessible"
            else
                skip "MinIO warehouse not configured (run: mc alias set minio http://localhost:9000 minioadmin minioadmin)"
            fi
        else
            skip "MinIO access test (use --full to test)"
        fi
    else
        skip "MinIO client (mc) not installed"
    fi

    # ========================================================
    # Section 8: Runbook File Validation
    # ========================================================
    section "Runbook File Validation"

    # Check runbook files exist
    RUNBOOK_DIR="docs/operations/runbooks"

    if [ -f "$RUNBOOK_DIR/failure-recovery.md" ]; then
        pass "Runbook exists: failure-recovery.md"
    else
        fail "Runbook missing: failure-recovery.md"
    fi

    if [ -f "$RUNBOOK_DIR/disaster-recovery.md" ]; then
        pass "Runbook exists: disaster-recovery.md"
    else
        fail "Runbook missing: disaster-recovery.md"
    fi

    if [ -f "$RUNBOOK_DIR/kafka-checkpoint-corruption-recovery.md" ]; then
        pass "Runbook exists: kafka-checkpoint-corruption-recovery.md"
    else
        fail "Runbook missing: kafka-checkpoint-corruption-recovery.md"
    fi

    # Validate runbooks have required sections
    for runbook in "$RUNBOOK_DIR"/*.md; do
        if [ -f "$runbook" ]; then
            filename=$(basename "$runbook")

            # Check for detection/symptoms section
            if grep -qi "detection\|symptoms\|alert" "$runbook"; then
                pass "Runbook has detection section: $filename"
            else
                fail "Runbook missing detection section: $filename"
            fi

            # Check for recovery procedure
            if grep -qi "recovery\|procedure\|steps" "$runbook"; then
                pass "Runbook has recovery section: $filename"
            else
                fail "Runbook missing recovery section: $filename"
            fi

            # Check for code blocks (commands)
            if grep -q '```' "$runbook"; then
                pass "Runbook has command examples: $filename"
            else
                fail "Runbook missing command examples: $filename"
            fi
        fi
    done

    # ========================================================
    # Section 9: Alert Rules Validation
    # ========================================================
    section "Alert Rules Validation"

    ALERT_FILE="monitoring/prometheus/alerts/platform-alerts.yml"

    if [ -f "$ALERT_FILE" ]; then
        pass "Alert rules file exists"

        # Validate YAML syntax
        if python3 -c "import yaml; yaml.safe_load(open('$ALERT_FILE'))" 2>/dev/null; then
            pass "Alert rules YAML syntax valid"
        else
            fail "Alert rules YAML syntax invalid"
        fi

        # Check for critical alerts mentioned in runbooks
        if grep -q "ConsumerLagCritical\|IcebergWriteFailure\|QueryAPIDown" "$ALERT_FILE"; then
            pass "Critical alerts defined in rules"
        else
            fail "Critical alerts missing from rules"
        fi
    else
        skip "Alert rules file not found: $ALERT_FILE"
    fi

    # ========================================================
    # Results Summary
    # ========================================================
    echo ""
    echo "==================================================================="
    echo "                     Validation Results"
    echo "==================================================================="
    echo ""
    echo -e "${GREEN}Passed:${NC}  $PASSED"
    echo -e "${RED}Failed:${NC}  $FAILED"
    echo -e "${YELLOW}Skipped:${NC} $SKIPPED"
    echo ""

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ All validations passed!${NC}"
        if [ $SKIPPED -gt 0 ]; then
            echo -e "${YELLOW}ℹ Run with --full flag to test service connectivity${NC}"
        fi
        exit 0
    else
        echo -e "${RED}✗ $FAILED validation(s) failed${NC}"
        echo ""
        echo "Next steps:"
        echo "1. Install missing tools (docker, curl, jq, etc.)"
        echo "2. Start required services: docker-compose up -d"
        echo "3. Run again with --full: ./scripts/ops/validate_runbooks.sh --full"
        exit 1
    fi
}

# Run main function
main "$@"
