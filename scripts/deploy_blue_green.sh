#!/usr/bin/env bash
#
# Blue-Green Deployment Script for Binance Streaming Service
#
# This script implements zero-downtime deployment using blue-green strategy:
# 1. Build new image (green)
# 2. Deploy green alongside blue (both produce to Kafka)
# 3. Monitor dual-operation for 10 minutes
# 4. Cutover to green (stop blue)
# 5. Validate green for 15 minutes
#
# Total Time: ~33 minutes
# Downtime: 0 seconds

set -euo pipefail

# Configuration
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly SERVICE_NAME="binance-stream"
readonly GREEN_VERSION="${1:-v2.0-stable}"
readonly MONITOR_DUAL_MINUTES=10
readonly VALIDATE_GREEN_MINUTES=15

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Check if service is healthy
check_service_health() {
    local container_name=$1
    local max_attempts=30
    local attempt=1

    log_info "Checking health of ${container_name}..."

    while [ $attempt -le $max_attempts ]; do
        if docker inspect "${container_name}" >/dev/null 2>&1; then
            local health_status
            health_status=$(docker inspect --format='{{.State.Health.Status}}' "${container_name}" 2>/dev/null || echo "none")

            if [ "${health_status}" = "healthy" ]; then
                log_info "${container_name} is healthy"
                return 0
            elif [ "${health_status}" = "none" ]; then
                # No healthcheck defined, check if running
                local state
                state=$(docker inspect --format='{{.State.Status}}' "${container_name}")
                if [ "${state}" = "running" ]; then
                    log_info "${container_name} is running (no healthcheck)"
                    return 0
                fi
            fi
        fi

        log_warn "Attempt ${attempt}/${max_attempts}: ${container_name} not healthy yet..."
        sleep 2
        ((attempt++))
    done

    log_error "${container_name} failed to become healthy"
    return 1
}

# Get message count from metrics
get_message_count() {
    local port=$1
    local count
    count=$(curl -s "http://localhost:${port}/metrics" 2>/dev/null | \
            grep "binance_messages_received_total" | \
            grep -v "#" | \
            awk '{print $2}' || echo "0")
    echo "${count}"
}

# Monitor metrics during deployment
monitor_metrics() {
    local blue_port=$1
    local green_port=$2
    local duration_minutes=$3
    local duration_seconds=$((duration_minutes * 60))
    local check_interval=30
    local elapsed=0

    log_info "Monitoring metrics for ${duration_minutes} minutes..."

    while [ $elapsed -lt $duration_seconds ]; do
        local blue_count green_count
        blue_count=$(get_message_count "${blue_port}")
        green_count=$(get_message_count "${green_port}")

        log_info "Blue messages: ${blue_count} | Green messages: ${green_count}"

        if [ "${green_count}" = "0" ] && [ $elapsed -gt 60 ]; then
            log_error "Green instance not receiving messages after 60 seconds"
            return 1
        fi

        sleep $check_interval
        elapsed=$((elapsed + check_interval))
    done

    log_info "Monitoring complete"
    return 0
}

# Main deployment procedure
main() {
    log_info "Starting blue-green deployment for ${SERVICE_NAME}"
    log_info "Target version: ${GREEN_VERSION}"

    cd "${PROJECT_ROOT}"

    # Step 1: Build new image (green)
    log_info "Step 1/5: Building new image (${GREEN_VERSION})..."
    if ! docker build -t "k2-platform:${GREEN_VERSION}" .; then
        log_error "Failed to build new image"
        exit 1
    fi
    log_info "Image built successfully"

    # Step 2: Deploy green container
    log_info "Step 2/5: Deploying green container..."

    # Get current blue container metrics port
    readonly BLUE_PORT=9091
    readonly GREEN_PORT=9092

    # Update docker-compose to use new image and different port for green
    export K2_IMAGE_TAG="${GREEN_VERSION}"
    export K2_METRICS_PORT="${GREEN_PORT}"

    # Scale to 2 instances
    if ! docker compose up -d --scale "${SERVICE_NAME}=2" --no-recreate; then
        log_error "Failed to deploy green container"
        exit 1
    fi

    # Wait for green to be healthy
    sleep 10
    local green_container
    green_container=$(docker ps --filter "name=${SERVICE_NAME}" --format "{{.Names}}" | grep -v "k2-${SERVICE_NAME}$" | head -1)

    if [ -z "${green_container}" ]; then
        log_error "Could not find green container"
        docker compose ps
        exit 1
    fi

    log_info "Green container: ${green_container}"

    if ! check_service_health "${green_container}"; then
        log_error "Green container failed health check"
        docker logs "${green_container}" --tail 50
        exit 1
    fi

    # Step 3: Monitor dual-operation
    log_info "Step 3/5: Monitoring dual-operation (${MONITOR_DUAL_MINUTES} minutes)..."
    log_info "Both blue and green are producing to Kafka (idempotent)"

    if ! monitor_metrics "${BLUE_PORT}" "${GREEN_PORT}" "${MONITOR_DUAL_MINUTES}"; then
        log_error "Monitoring failed - rolling back"
        docker compose stop "${green_container}"
        exit 1
    fi

    # Step 4: Cutover to green
    log_info "Step 4/5: Cutting over to green..."
    log_info "Stopping blue container: k2-${SERVICE_NAME}"

    if ! docker compose stop "k2-${SERVICE_NAME}"; then
        log_warn "Failed to gracefully stop blue container, forcing..."
        docker stop "k2-${SERVICE_NAME}" 2>/dev/null || true
    fi

    log_info "Blue container stopped"

    # Step 5: Validate green
    log_info "Step 5/5: Validating green (${VALIDATE_GREEN_MINUTES} minutes)..."

    # Check green is still healthy
    if ! check_service_health "${green_container}"; then
        log_error "Green container unhealthy after cutover - CRITICAL"
        log_error "Manual intervention required"
        exit 1
    fi

    # Monitor green metrics
    local start_count end_count
    start_count=$(get_message_count "${GREEN_PORT}")
    sleep $((VALIDATE_GREEN_MINUTES * 60))
    end_count=$(get_message_count "${GREEN_PORT}")

    local message_diff=$((end_count - start_count))
    local expected_min_messages=$((VALIDATE_GREEN_MINUTES * 60 * 10)) # 10 msg/sec minimum

    if [ $message_diff -lt $expected_min_messages ]; then
        log_warn "Green message rate lower than expected: ${message_diff} messages in ${VALIDATE_GREEN_MINUTES} min"
        log_warn "Expected at least ${expected_min_messages} messages"
    else
        log_info "Green message rate healthy: ${message_diff} messages in ${VALIDATE_GREEN_MINUTES} min"
    fi

    # Deployment complete
    log_info "Blue-green deployment complete!"
    log_info "New version ${GREEN_VERSION} is now live"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Monitor for 24 hours (Step 09 validation)"
    log_info "  2. Check Prometheus/Grafana dashboards"
    log_info "  3. Verify memory stability (<50MB growth over 24h)"
    log_info ""
    log_info "Rollback procedure (if needed):"
    log_info "  docker compose up -d k2-${SERVICE_NAME}"
    log_info "  docker compose stop ${green_container}"
}

# Run main
main "$@"
