#!/usr/bin/env bash
#
# Deployment Validation Script
#
# Validates that the Binance streaming deployment is healthy:
# 1. Container is running and healthy
# 2. Metrics endpoint is accessible
# 3. Message rate is acceptable (>10 msg/sec)
# 4. Memory usage is within limits (<400MB)
# 5. No critical errors in logs
#
# Usage:
#   ./validate_deployment.sh [container_name] [metrics_port]
#
# Exit codes:
#   0 - All checks passed
#   1 - One or more checks failed

set -euo pipefail

# Configuration
readonly CONTAINER_NAME="${1:-k2-binance-stream}"
readonly METRICS_PORT="${2:-9091}"
readonly MIN_MESSAGE_RATE=10  # messages per second
readonly MAX_MEMORY_MB=400    # Maximum RSS memory in MB

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m'

# Track validation results
VALIDATION_PASSED=true

# Logging
log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
    VALIDATION_PASSED=false
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
    VALIDATION_PASSED=false
}

log_check() {
    echo -e "${GREEN}[✓]${NC} $*"
}

# Check 1: Container is running
check_container_running() {
    log_info "Check 1/5: Container status..."

    if ! docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
        log_error "Container ${CONTAINER_NAME} not found"
        return 1
    fi

    local status
    status=$(docker inspect --format='{{.State.Status}}' "${CONTAINER_NAME}")

    if [ "${status}" != "running" ]; then
        log_error "Container status: ${status} (expected: running)"
        return 1
    fi

    log_check "Container is running"
    return 0
}

# Check 2: Health check status
check_container_health() {
    log_info "Check 2/5: Container health..."

    local health_status
    health_status=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_NAME}" 2>/dev/null || echo "none")

    if [ "${health_status}" = "healthy" ]; then
        log_check "Container is healthy"
        return 0
    elif [ "${health_status}" = "none" ]; then
        log_warn "No healthcheck defined"
        return 0
    else
        log_error "Container health: ${health_status}"
        return 1
    fi
}

# Check 3: Metrics endpoint accessible
check_metrics_endpoint() {
    log_info "Check 3/5: Metrics endpoint..."

    if ! curl -sf "http://localhost:${METRICS_PORT}/metrics" >/dev/null; then
        log_error "Metrics endpoint not accessible at http://localhost:${METRICS_PORT}/metrics"
        return 1
    fi

    log_check "Metrics endpoint accessible"
    return 0
}

# Check 4: Message rate
check_message_rate() {
    log_info "Check 4/5: Message rate..."

    # Get total messages
    local total_messages
    total_messages=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                     grep "binance_messages_received_total" | \
                     grep -v "#" | \
                     awk '{print $2}')

    if [ -z "${total_messages}" ] || [ "${total_messages}" = "0" ]; then
        log_warn "No messages received yet (container may be starting)"
        return 0
    fi

    # Wait 10 seconds and check again
    sleep 10

    local total_messages_after
    total_messages_after=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                           grep "binance_messages_received_total" | \
                           grep -v "#" | \
                           awk '{print $2}')

    local message_diff=$((total_messages_after - total_messages))
    local message_rate=$((message_diff / 10))

    if [ $message_rate -lt $MIN_MESSAGE_RATE ]; then
        log_warn "Message rate: ${message_rate} msg/sec (expected: >${MIN_MESSAGE_RATE} msg/sec)"
    else
        log_check "Message rate: ${message_rate} msg/sec"
    fi

    return 0
}

# Check 5: Memory usage
check_memory_usage() {
    log_info "Check 5/5: Memory usage..."

    local memory_bytes
    memory_bytes=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                   grep "process_memory_rss_bytes" | \
                   grep -v "#" | \
                   awk '{print $2}')

    if [ -z "${memory_bytes}" ]; then
        log_warn "Could not retrieve memory metrics"
        return 0
    fi

    local memory_mb=$((memory_bytes / 1024 / 1024))

    if [ $memory_mb -gt $MAX_MEMORY_MB ]; then
        log_error "Memory usage: ${memory_mb}MB (limit: ${MAX_MEMORY_MB}MB)"
        return 1
    fi

    log_check "Memory usage: ${memory_mb}MB"
    return 0
}

# Check 6: Recent errors in logs
check_logs() {
    log_info "Check 6/6: Recent errors in logs..."

    local error_count
    error_count=$(docker logs "${CONTAINER_NAME}" --since 5m 2>&1 | \
                  grep -i "error\|exception\|critical" | \
                  grep -v "DEBUG" | \
                  wc -l || echo "0")

    if [ "$error_count" -gt 0 ]; then
        log_warn "Found ${error_count} error(s) in last 5 minutes"
        log_info "Recent errors:"
        docker logs "${CONTAINER_NAME}" --since 5m 2>&1 | \
            grep -i "error\|exception\|critical" | \
            grep -v "DEBUG" | \
            tail -5
    else
        log_check "No errors in recent logs"
    fi

    return 0
}

# Display summary metrics
display_summary() {
    log_info ""
    log_info "=== Deployment Metrics Summary ==="

    # Container uptime
    local started_at
    started_at=$(docker inspect --format='{{.State.StartedAt}}' "${CONTAINER_NAME}" | cut -d'.' -f1)
    log_info "Started: ${started_at}"

    # Messages
    local total_messages
    total_messages=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                     grep "binance_messages_received_total" | \
                     grep -v "#" | \
                     awk '{print $2}' || echo "0")
    log_info "Total messages: ${total_messages}"

    # Connection rotations
    local rotations
    rotations=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                grep "binance_connection_rotations_total" | \
                grep -v "#" | \
                awk '{print $2}' || echo "0")
    log_info "Connection rotations: ${rotations}"

    # Memory leak score
    local leak_score
    leak_score=$(curl -s "http://localhost:${METRICS_PORT}/metrics" | \
                 grep "memory_leak_detection_score" | \
                 grep -v "#" | \
                 awk '{print $2}' || echo "N/A")
    log_info "Memory leak score: ${leak_score}"

    log_info ""
}

# Main validation
main() {
    log_info "Validating deployment: ${CONTAINER_NAME}"
    log_info "Metrics port: ${METRICS_PORT}"
    log_info ""

    check_container_running
    check_container_health
    check_metrics_endpoint
    check_message_rate
    check_memory_usage
    check_logs

    log_info ""

    if [ "${VALIDATION_PASSED}" = true ]; then
        log_info "=== ✓ All validation checks passed ==="
        display_summary
        exit 0
    else
        log_error "=== ✗ Validation failed ==="
        log_error "See errors above for details"
        exit 1
    fi
}

main "$@"
