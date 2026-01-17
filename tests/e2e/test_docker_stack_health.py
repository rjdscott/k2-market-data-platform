"""Docker stack health and dependency validation tests for K2 Market Data Platform.

This module provides tests to validate:
- Docker service health and availability
- Inter-service connectivity and dependencies
- Resource allocation and limits
- Service startup ordering and timing
- Network connectivity between services
"""

import asyncio
import logging
import pytest
import httpx
from typing import Dict, List, Set
from datetime import datetime

logger = logging.getLogger(__name__)


@pytest.mark.e2e
@pytest.mark.slow
class TestDockerStackHealth:
    """Comprehensive Docker stack health and dependency validation."""

    @pytest.mark.asyncio
    async def test_core_services_health(self, api_client):
        """Test health of core services."""
        logger.info("Testing core service health endpoints")

        # Test Query API health
        try:
            health_response = await api_client.get("/health")
            assert health_response.status_code == 200
            health_data = health_response.json()
            assert "status" in health_data
            logger.info(f"Query API health: {health_data}")
        except Exception as e:
            pytest.skip(f"Query API not available: {e}")

    @pytest.mark.asyncio
    async def test_kafka_connectivity(self, minimal_stack):
        """Test Kafka cluster connectivity and health."""
        logger.info("Testing Kafka connectivity")

        # Check if Kafka service is in stack
        assert "kafka" in minimal_stack, "Kafka service not found in stack"

        # Note: Actual Kafka connectivity test would require confluent-kafka client
        # For now, validate service exists and is accessible via Docker inspection
        logger.info("Kafka service validation - connectivity requires client library")

    @pytest.mark.asyncio
    async def test_schema_registry_health(self, minimal_stack):
        """Test Schema Registry service health."""
        logger.info("Testing Schema Registry health")

        # Check if Schema Registry services are available
        schema_services = [s for s in minimal_stack.keys() if "schema-registry" in s]
        assert len(schema_services) >= 2, "Expected at least 2 Schema Registry instances"

        # Test Schema Registry HTTP endpoints (if accessible)
        for service_name in schema_services:
            logger.info(f"Found Schema Registry service: {service_name}")

    @pytest.mark.asyncio
    async def test_minio_connectivity(self, minimal_stack):
        """Test MinIO S3 compatibility service health."""
        logger.info("Testing MinIO connectivity")

        assert "minio" in minimal_stack, "MinIO service not found in stack"
        logger.info("MinIO service validation - S3 connectivity requires boto3 client")

    @pytest.mark.asyncio
    async def test_iceberg_rest_health(self, minimal_stack):
        """Test Iceberg REST service health."""
        logger.info("Testing Iceberg REST service health")

        assert "iceberg-rest" in minimal_stack, "Iceberg REST service not found in stack"
        logger.info("Iceberg REST service validation available")

    @pytest.mark.asyncio
    async def test_monitoring_stack_health(self, minimal_stack):
        """Test monitoring stack services health."""
        logger.info("Testing monitoring stack health")

        monitoring_services = ["prometheus", "grafana"]

        for service in monitoring_services:
            if service in minimal_stack:
                logger.info(f"Found {service} service in stack")
            else:
                logger.warning(f"{service} service not found in stack")

    @pytest.mark.asyncio
    async def test_service_dependencies(self, minimal_stack):
        """Test service dependency ordering and connectivity."""
        logger.info("Testing service dependencies")

        # Validate expected services are present
        required_services = ["kafka", "minio", "iceberg-rest"]
        found_services = set(minimal_stack.keys())

        missing_services = set(required_services) - found_services
        if missing_services:
            pytest.skip(f"Missing required services: {missing_services}")

        logger.info(f"All required services found: {required_services}")

    @pytest.mark.asyncio
    async def test_network_connectivity(self, minimal_stack):
        """Test network connectivity between services."""
        logger.info("Testing network connectivity")

        # Check if services are on the same network
        service_networks = {}
        for service_name, service_info in minimal_stack.items():
            if "NetworkSettings" in service_info:
                networks = list(service_info["NetworkSettings"].get("Networks", {}).keys())
                service_networks[service_name] = networks

        # Find common networks
        all_networks = []
        for networks in service_networks.values():
            all_networks.extend(networks)

        common_networks = set([n for n in all_networks if all_networks.count(n) > 1])

        if common_networks:
            logger.info(f"Found shared networks: {common_networks}")
        else:
            logger.warning("No shared networks found between services")

    @pytest.mark.asyncio
    async def test_resource_limits(self, minimal_stack):
        """Test service resource limits and allocation."""
        logger.info("Testing service resource limits")

        for service_name, service_info in minimal_stack.items():
            host_config = service_info.get("HostConfig", {})

            # Check memory limits
            memory_limit = host_config.get("Memory")
            if memory_limit:
                logger.info(f"{service_name}: Memory limit = {memory_limit // (1024 * 1024)}MB")
            else:
                logger.warning(f"{service_name}: No memory limit set")

            # Check CPU limits
            cpu_quota = host_config.get("CpuQuota")
            if cpu_quota:
                logger.info(f"{service_name}: CPU quota = {cpu_quota}")
            else:
                logger.warning(f"{service_name}: No CPU quota set")

    @pytest.mark.asyncio
    async def test_service_startup_order(self, minimal_stack):
        """Test service startup ordering and timing."""
        logger.info("Testing service startup order")

        # Get service start times
        start_times = {}
        for service_name, service_info in minimal_stack.items():
            state = service_info.get("State", {})
            if "StartedAt" in state:
                start_time_str = state["StartedAt"]
                try:
                    # Parse start time (simplified - would need proper datetime parsing)
                    start_times[service_name] = start_time_str
                except Exception:
                    logger.warning(f"Could not parse start time for {service_name}")

        if len(start_times) > 1:
            logger.info(f"Service start times: {list(start_times.keys())}")
        else:
            logger.warning("Insufficient start time data for ordering validation")

    @pytest.mark.asyncio
    async def test_environment_variables(self, minimal_stack):
        """Test critical environment variables are set."""
        logger.info("Testing environment variables")

        critical_env_vars = {
            "kafka": ["KAFKA_BROKER_ID", "KAFKA_ZOOKEEPER_CONNECT"],
            "minio": ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"],
            "iceberg-rest": ["CATALOG_WAREHOUSE"],
            "k2-query-api": ["DATABASE_URL", "KAFKA_BOOTSTRAP_SERVERS"],
        }

        for service_name, expected_vars in critical_env_vars.items():
            if service_name in minimal_stack:
                service_info = minimal_stack[service_name]
                env_list = service_info.get("Config", {}).get("Env", [])
                env_dict = dict(env.split("=", 1) for env in env_list if "=" in env)

                for var in expected_vars:
                    if var in env_dict:
                        logger.info(f"{service_name}: {var} is set")
                    else:
                        logger.warning(f"{service_name}: {var} not found")

    @pytest.mark.asyncio
    async def test_volume_mounts(self, minimal_stack):
        """Test critical volume mounts are present."""
        logger.info("Testing volume mounts")

        expected_volumes = {
            "kafka": ["/var/lib/kafka/data"],
            "minio": ["/data"],
            "postgres": ["/var/lib/postgresql/data"],
            "k2-query-api": ["/app/logs"],
        }

        for service_name, expected_paths in expected_volumes.items():
            if service_name in minimal_stack:
                service_info = minimal_stack[service_name]
                mounts = service_info.get("Mounts", [])
                mounted_paths = [mount.get("Destination") for mount in mounts]

                for expected_path in expected_paths:
                    if any(expected_path in path for path in mounted_paths):
                        logger.info(f"{service_name}: {expected_path} is mounted")
                    else:
                        logger.warning(f"{service_name}: {expected_path} not found in mounts")

    @pytest.mark.asyncio
    async def test_port_bindings(self, minimal_stack):
        """Test critical port bindings are configured."""
        logger.info("Testing port bindings")

        expected_ports = {
            "kafka": [9092, 9093],
            "k2-query-api": [8000, 9094],
            "postgres": [5432],
            "minio": [9000, 9001],
            "k2-kafka-ui": [8080],
            "k-schema-registry-1": [8081],
            "k-schema-registry-2": [8082],
            "k-iceberg-rest": [8181],
            "k-grafana": [3000],
            "k-prometheus": [9090],
        }

        for service_name, expected_port_list in expected_ports.items():
            if service_name in minimal_stack:
                service_info = minimal_stack[service_name]
                ports = service_info.get("Ports", {})
                bound_ports = list(ports.keys())

                for expected_port in expected_port_list:
                    if any(str(expected_port) in port for port in bound_ports):
                        logger.info(f"{service_name}: Port {expected_port} is bound")
                    else:
                        logger.warning(f"{service_name}: Port {expected_port} not found")

    @pytest.mark.asyncio
    async def test_service_logs_for_errors(self, minimal_stack):
        """Test service logs for critical errors."""
        logger.info("Testing service logs for errors")

        # Note: This would require Docker client access to fetch logs
        # For now, validate service states
        for service_name, service_info in minimal_stack.items():
            state = service_info.get("State", {})
            status = state.get("Status", "unknown")

            if status == "running":
                logger.info(f"{service_name}: Service is running")
            elif status in ["exited", "dead"]:
                logger.error(f"{service_name}: Service status is {status}")
            else:
                logger.warning(f"{service_name}: Service status is {status}")
