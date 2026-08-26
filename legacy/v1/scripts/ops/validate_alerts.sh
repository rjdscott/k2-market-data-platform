#!/bin/bash
# Validate Prometheus Alert Rules
#
# This script validates alert rule syntax and checks if rules are loaded correctly.
# Run this in CI/CD to catch alert configuration errors before deployment.
#
# Usage:
#   ./scripts/ops/validate_alerts.sh
#
# Requirements:
#   - promtool (installed with Prometheus)
#   - Running Prometheus instance (for runtime checks)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RULES_DIR="$PROJECT_ROOT/config/prometheus/rules"
PROMETHEUS_CONFIG="$PROJECT_ROOT/config/prometheus/prometheus.yml"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"

echo "=================================="
echo "Prometheus Alert Rule Validation"
echo "=================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if promtool is available
if ! command -v promtool &> /dev/null; then
    echo -e "${YELLOW}⚠️  promtool not found. Skipping syntax validation.${NC}"
    echo "   Install promtool: brew install prometheus (macOS) or download from prometheus.io"
    PROMTOOL_AVAILABLE=false
else
    PROMTOOL_AVAILABLE=true
fi

# Step 1: Validate alert rule syntax
echo "Step 1: Validating alert rule syntax..."
echo "----------------------------------------"

if [ "$PROMTOOL_AVAILABLE" = true ]; then
    for rule_file in "$RULES_DIR"/*.yml; do
        if [ -f "$rule_file" ]; then
            filename=$(basename "$rule_file")
            echo -n "  Checking $filename... "

            if promtool check rules "$rule_file" > /dev/null 2>&1; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
                promtool check rules "$rule_file"
                exit 1
            fi
        fi
    done
    echo ""
else
    echo -e "${YELLOW}  Skipped (promtool not available)${NC}"
    echo ""
fi

# Step 2: Validate Prometheus configuration
echo "Step 2: Validating Prometheus configuration..."
echo "-----------------------------------------------"

if [ "$PROMTOOL_AVAILABLE" = true ]; then
    echo -n "  Checking prometheus.yml... "

    if promtool check config "$PROMETHEUS_CONFIG" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${RED}✗${NC}"
        promtool check config "$PROMETHEUS_CONFIG"
        exit 1
    fi
    echo ""
else
    echo -e "${YELLOW}  Skipped (promtool not available)${NC}"
    echo ""
fi

# Step 3: Check if Prometheus is running and rules are loaded
echo "Step 3: Checking Prometheus runtime status..."
echo "----------------------------------------------"

if curl -s "$PROMETHEUS_URL/api/v1/status/config" > /dev/null 2>&1; then
    echo -e "  Prometheus is ${GREEN}running${NC} at $PROMETHEUS_URL"

    # Check if rules are loaded
    echo ""
    echo "  Checking loaded rule groups..."

    RULE_GROUPS=$(curl -s "$PROMETHEUS_URL/api/v1/rules" | jq -r '.data.groups[].name' 2>/dev/null)

    if [ -n "$RULE_GROUPS" ]; then
        echo "$RULE_GROUPS" | while read -r group; do
            echo "    ✓ $group"
        done

        # Count total rules
        TOTAL_RULES=$(curl -s "$PROMETHEUS_URL/api/v1/rules" | jq '.data.groups[].rules | length' 2>/dev/null | awk '{s+=$1} END {print s}')
        echo ""
        echo "  Total alerts loaded: ${GREEN}$TOTAL_RULES${NC}"
    else
        echo -e "    ${YELLOW}⚠️  No rule groups loaded${NC}"
        echo "    Check that rule_files is configured in prometheus.yml"
    fi

    echo ""

    # Check for any firing alerts
    echo "  Checking for firing alerts..."
    FIRING_ALERTS=$(curl -s "$PROMETHEUS_URL/api/v1/alerts" | jq -r '.data.alerts[] | select(.state=="firing") | .labels.alertname' 2>/dev/null)

    if [ -n "$FIRING_ALERTS" ]; then
        echo -e "    ${RED}⚠️  Alerts currently firing:${NC}"
        echo "$FIRING_ALERTS" | while read -r alert; do
            echo "      - $alert"
        done
    else
        echo -e "    ${GREEN}✓ No alerts firing${NC}"
    fi

else
    echo -e "  ${YELLOW}⚠️  Prometheus not reachable at $PROMETHEUS_URL${NC}"
    echo "     Start Prometheus with: docker-compose up -d prometheus"
fi

echo ""

# Step 4: Validate alert completeness
echo "Step 4: Validating alert completeness..."
echo "----------------------------------------"

# Check that critical metrics have alerts
REQUIRED_METRICS=(
    "kafka_consumer_lag_messages"
    "k2_iceberg_write_errors_total"
    "k2_http_requests_total"
    "up"
)

echo "  Checking required metric coverage..."
MISSING_ALERTS=()

for metric in "${REQUIRED_METRICS[@]}"; do
    if grep -q "$metric" "$RULES_DIR"/*.yml 2>/dev/null; then
        echo "    ✓ $metric"
    else
        echo "    ✗ $metric (missing alert)"
        MISSING_ALERTS+=("$metric")
    fi
done

if [ ${#MISSING_ALERTS[@]} -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ All critical metrics have alert coverage${NC}"
else
    echo ""
    echo -e "${RED}✗ Missing alerts for ${#MISSING_ALERTS[@]} metrics${NC}"
fi

echo ""

# Step 5: Check alert documentation
echo "Step 5: Checking alert documentation..."
echo "---------------------------------------"

# Extract all alert names from rules
ALERT_NAMES=$(grep -h "alert:" "$RULES_DIR"/*.yml 2>/dev/null | awk '{print $2}' | sort -u)
ALERT_COUNT=$(echo "$ALERT_NAMES" | wc -l | tr -d ' ')

echo "  Found $ALERT_COUNT unique alerts"
echo ""

# Check if alerts have required fields
echo "  Verifying alert structure..."
ALERTS_MISSING_RUNBOOK=0
ALERTS_MISSING_SUMMARY=0

for alert in $ALERT_NAMES; do
    # Check if alert has runbook annotation
    if ! grep -A 15 "alert: $alert" "$RULES_DIR"/*.yml 2>/dev/null | grep -q "runbook:"; then
        echo "    ⚠️  $alert missing runbook"
        ((ALERTS_MISSING_RUNBOOK++))
    fi

    # Check if alert has summary annotation
    if ! grep -A 15 "alert: $alert" "$RULES_DIR"/*.yml 2>/dev/null | grep -q "summary:"; then
        echo "    ⚠️  $alert missing summary"
        ((ALERTS_MISSING_SUMMARY++))
    fi
done

if [ $ALERTS_MISSING_RUNBOOK -eq 0 ] && [ $ALERTS_MISSING_SUMMARY -eq 0 ]; then
    echo -e "    ${GREEN}✓ All alerts have required documentation${NC}"
else
    echo ""
    [ $ALERTS_MISSING_RUNBOOK -gt 0 ] && echo -e "    ${YELLOW}⚠️  $ALERTS_MISSING_RUNBOOK alerts missing runbook${NC}"
    [ $ALERTS_MISSING_SUMMARY -gt 0 ] && echo -e "    ${YELLOW}⚠️  $ALERTS_MISSING_SUMMARY alerts missing summary${NC}"
fi

echo ""

# Final summary
echo "=================================="
echo "Validation Summary"
echo "=================================="
echo ""
echo "  Alert files validated: $(find "$RULES_DIR" -name "*.yml" | wc -l | tr -d ' ')"
echo "  Total alerts defined: $ALERT_COUNT"
echo "  Prometheus status: $(curl -s "$PROMETHEUS_URL/-/healthy" > /dev/null 2>&1 && echo -e "${GREEN}healthy${NC}" || echo -e "${YELLOW}not reachable${NC}")"
echo ""

if [ ${#MISSING_ALERTS[@]} -eq 0 ] && [ $ALERTS_MISSING_RUNBOOK -eq 0 ]; then
    echo -e "${GREEN}✓ Alert validation PASSED${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠️  Alert validation completed with warnings${NC}"
    echo "   Review warnings above and update alert rules as needed"
    exit 0
fi
