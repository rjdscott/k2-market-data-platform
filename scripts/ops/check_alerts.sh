#!/bin/bash
# Check Prometheus Alert Status
#
# This script checks the current status of all alerts and displays
# any firing or pending alerts in a human-readable format.
#
# Usage:
#   ./scripts/ops/check_alerts.sh
#   ./scripts/ops/check_alerts.sh --watch    # Refresh every 30s
#   ./scripts/ops/check_alerts.sh --firing   # Show only firing alerts

PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
WATCH_MODE=false
FIRING_ONLY=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --watch)
            WATCH_MODE=true
            shift
            ;;
        --firing)
            FIRING_ONLY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--watch] [--firing]"
            exit 1
            ;;
    esac
done

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

show_alerts() {
    clear
    echo "=================================="
    echo "K2 Alert Status"
    echo "=================================="
    echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Prometheus: $PROMETHEUS_URL"
    echo ""

    # Check if Prometheus is reachable
    if ! curl -s "$PROMETHEUS_URL/-/healthy" > /dev/null 2>&1; then
        echo -e "${RED}✗ Prometheus not reachable${NC}"
        echo ""
        echo "Start Prometheus with: docker-compose up -d prometheus"
        return 1
    fi

    # Get all alerts
    ALERTS_JSON=$(curl -s "$PROMETHEUS_URL/api/v1/alerts")

    # Count alerts by state
    TOTAL_ALERTS=$(echo "$ALERTS_JSON" | jq '.data.alerts | length' 2>/dev/null)
    FIRING_COUNT=$(echo "$ALERTS_JSON" | jq '[.data.alerts[] | select(.state=="firing")] | length' 2>/dev/null)
    PENDING_COUNT=$(echo "$ALERTS_JSON" | jq '[.data.alerts[] | select(.state=="pending")] | length' 2>/dev/null)
    INACTIVE_COUNT=$((TOTAL_ALERTS - FIRING_COUNT - PENDING_COUNT))

    # Summary
    echo "Alert Summary:"
    echo "  Total: $TOTAL_ALERTS"
    echo -e "  Firing: ${RED}$FIRING_COUNT${NC}"
    echo -e "  Pending: ${YELLOW}$PENDING_COUNT${NC}"
    echo -e "  Inactive: ${GREEN}$INACTIVE_COUNT${NC}"
    echo ""

    # Show firing alerts
    if [ "$FIRING_COUNT" -gt 0 ]; then
        echo -e "${RED}🔥 FIRING ALERTS${NC}"
        echo "===================="
        echo ""

        echo "$ALERTS_JSON" | jq -r '.data.alerts[] | select(.state=="firing") |
            "Alert: \(.labels.alertname)\n" +
            "Severity: \(.labels.severity)\n" +
            "Component: \(.labels.component // "unknown")\n" +
            "Summary: \(.annotations.summary)\n" +
            "Active since: \(.activeAt)\n" +
            "Value: \(.value // "N/A")\n" +
            "---"' 2>/dev/null

        echo ""
    fi

    # Show pending alerts (unless --firing flag is set)
    if [ "$FIRING_ONLY" = false ] && [ "$PENDING_COUNT" -gt 0 ]; then
        echo -e "${YELLOW}⏳ PENDING ALERTS${NC}"
        echo "===================="
        echo ""

        echo "$ALERTS_JSON" | jq -r '.data.alerts[] | select(.state=="pending") |
            "Alert: \(.labels.alertname)\n" +
            "Severity: \(.labels.severity)\n" +
            "Component: \(.labels.component // "unknown")\n" +
            "Summary: \(.annotations.summary)\n" +
            "Active since: \(.activeAt)\n" +
            "---"' 2>/dev/null

        echo ""
    fi

    # If no alerts firing and not in watch mode, show recent resolved alerts
    if [ "$FIRING_COUNT" -eq 0 ] && [ "$PENDING_COUNT" -eq 0 ] && [ "$WATCH_MODE" = false ]; then
        echo -e "${GREEN}✓ All alerts are inactive${NC}"
        echo ""

        # Show rule groups and alert count
        echo "Loaded Alert Groups:"
        echo "--------------------"
        curl -s "$PROMETHEUS_URL/api/v1/rules" | jq -r '.data.groups[] |
            "  \(.name): \(.rules | length) rules"' 2>/dev/null
    fi

    # Show navigation hint in watch mode
    if [ "$WATCH_MODE" = true ]; then
        echo ""
        echo -e "${BLUE}Press Ctrl+C to exit watch mode${NC}"
    fi
}

# Main execution
if [ "$WATCH_MODE" = true ]; then
    while true; do
        show_alerts
        sleep 30
    done
else
    show_alerts
fi
