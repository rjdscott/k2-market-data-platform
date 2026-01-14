"""
Chaos Testing Fixtures and Utilities.

This module provides pytest fixtures for fault injection and chaos testing
of the K2 Market Data Platform.

Chaos Testing Categories:
- Service failures (Kafka, Schema Registry, MinIO, API)
- Network faults (partitions, latency injection, packet loss)
- Resource exhaustion (CPU, memory, disk, connections)
- Data corruption (malformed messages, schema mismatches)
- Time-based faults (clock skew, timeouts)

Usage:
    pytest tests/chaos/ -v -m chaos
"""

import contextlib
import logging
import os
import time
from typing import Generator, Optional

import docker
import pytest
import requests
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient

logger = logging.getLogger(__name__)

# ==============================================================================
# Docker Service Fixtures
# ==============================================================================


@pytest.fixture(scope="session")
def docker_client() -> docker.DockerClient:
    """Get Docker client for service manipulation."""
    return docker.from_env()


@pytest.fixture(scope="session")
def kafka_container(docker_client):
    """Get Kafka container for chaos injection."""
    try:
        container = docker_client.containers.get("k2-kafka")
        return container
    except docker.errors.NotFound:
        pytest.skip("Kafka container not running")


@pytest.fixture(scope="session")
def schema_registry_container(docker_client):
    """Get Schema Registry container for chaos injection."""
    try:
        container = docker_client.containers.get("k2-schema-registry-1")
        return container
    except docker.errors.NotFound:
        pytest.skip("Schema Registry container not running")


@pytest.fixture(scope="session")
def minio_container(docker_client):
    """Get MinIO container for chaos injection."""
    try:
        container = docker_client.containers.get("k2-minio")
        return container
    except docker.errors.NotFound:
        pytest.skip("MinIO container not running")


# ==============================================================================
# Chaos Injection Utilities
# ==============================================================================


@contextlib.contextmanager
def service_failure(
    container,
    duration_seconds: int = 5,
    failure_mode: str = "stop",
) -> Generator[None, None, None]:
    """
    Simulate service failure by stopping/pausing container.

    Args:
        container: Docker container to manipulate
        duration_seconds: How long to keep service down
        failure_mode: "stop" (graceful shutdown) or "pause" (freeze process)

    Usage:
        with service_failure(kafka_container, duration_seconds=10):
            # Kafka is down - test resilience
            producer.produce(...)
            # Producer should buffer or fail gracefully
    """
    original_status = container.status
    logger.info(
        f"Injecting service failure",
        container=container.name,
        mode=failure_mode,
        duration=duration_seconds,
    )

    try:
        # Inject failure
        if failure_mode == "stop":
            container.stop()
        elif failure_mode == "pause":
            container.pause()
        else:
            raise ValueError(f"Unknown failure mode: {failure_mode}")

        logger.info(f"Service {container.name} is down")
        yield

    finally:
        # Restore service
        time.sleep(duration_seconds)

        if failure_mode == "stop":
            container.start()
            # Wait for service to be healthy
            _wait_for_container_health(container, timeout_seconds=30)
        elif failure_mode == "pause":
            container.unpause()

        logger.info(f"Service {container.name} restored", status=container.status)


@contextlib.contextmanager
def network_partition(
    container,
    duration_seconds: int = 5,
) -> Generator[None, None, None]:
    """
    Simulate network partition by disconnecting container from networks.

    Args:
        container: Docker container to partition
        duration_seconds: How long to keep partition active

    Usage:
        with network_partition(kafka_container, duration_seconds=10):
            # Kafka is unreachable - test timeout handling
            consumer.poll(timeout=5.0)
    """
    networks = container.attrs["NetworkSettings"]["Networks"]
    network_names = list(networks.keys())

    logger.info(
        f"Injecting network partition",
        container=container.name,
        networks=network_names,
        duration=duration_seconds,
    )

    try:
        # Disconnect from all networks
        docker_client = docker.from_env()
        for network_name in network_names:
            network = docker_client.networks.get(network_name)
            network.disconnect(container)
            logger.info(f"Disconnected {container.name} from {network_name}")

        yield

    finally:
        time.sleep(duration_seconds)

        # Reconnect to networks
        for network_name in network_names:
            network = docker_client.networks.get(network_name)
            network.connect(container)
            logger.info(f"Reconnected {container.name} to {network_name}")


@contextlib.contextmanager
def resource_limit(
    container,
    cpu_quota: Optional[int] = None,
    mem_limit: Optional[str] = None,
) -> Generator[None, None, None]:
    """
    Inject resource constraints on container.

    Args:
        container: Docker container to constrain
        cpu_quota: CPU quota in microseconds per period (e.g., 50000 = 50% of 1 core)
        mem_limit: Memory limit (e.g., "512m", "1g")

    Usage:
        with resource_limit(kafka_container, cpu_quota=50000, mem_limit="512m"):
            # Kafka has only 50% CPU and 512MB RAM
            producer.produce(...)  # Should handle resource constraints
    """
    logger.info(
        f"Injecting resource limits",
        container=container.name,
        cpu_quota=cpu_quota,
        mem_limit=mem_limit,
    )

    # Store original limits
    original_config = container.attrs["HostConfig"]
    original_cpu_quota = original_config.get("CpuQuota")
    original_mem_limit = original_config.get("Memory")

    try:
        # Apply new limits
        update_kwargs = {}
        if cpu_quota is not None:
            update_kwargs["cpu_quota"] = cpu_quota
            update_kwargs["cpu_period"] = 100000  # Standard period
        if mem_limit is not None:
            update_kwargs["mem_limit"] = mem_limit

        container.update(**update_kwargs)
        logger.info(f"Resource limits applied to {container.name}")

        yield

    finally:
        # Restore original limits
        restore_kwargs = {}
        if cpu_quota is not None:
            restore_kwargs["cpu_quota"] = original_cpu_quota or -1  # -1 = unlimited
        if mem_limit is not None:
            restore_kwargs["mem_limit"] = original_mem_limit or 0  # 0 = unlimited

        container.update(**restore_kwargs)
        logger.info(f"Resource limits restored for {container.name}")


@contextlib.contextmanager
def inject_latency(
    container,
    latency_ms: int = 100,
    jitter_ms: int = 20,
) -> Generator[None, None, None]:
    """
    Inject network latency using tc (traffic control).

    Args:
        container: Docker container to add latency to
        latency_ms: Base latency in milliseconds
        jitter_ms: Jitter/variance in milliseconds

    Note: Requires container to have 'tc' command available.

    Usage:
        with inject_latency(kafka_container, latency_ms=100, jitter_ms=20):
            # Kafka responses now have 100ms ± 20ms latency
            consumer.poll(timeout=5.0)
    """
    logger.info(
        f"Injecting network latency",
        container=container.name,
        latency_ms=latency_ms,
        jitter_ms=jitter_ms,
    )

    try:
        # Add latency using tc (traffic control)
        # Note: This requires NET_ADMIN capability on container
        exec_result = container.exec_run(
            f"tc qdisc add dev eth0 root netem delay {latency_ms}ms {jitter_ms}ms",
            privileged=True,
        )

        if exec_result.exit_code == 0:
            logger.info(f"Latency injected on {container.name}")
        else:
            logger.warning(
                f"Failed to inject latency (tc not available?)",
                error=exec_result.output.decode(),
            )

        yield

    finally:
        # Remove latency
        container.exec_run("tc qdisc del dev eth0 root", privileged=True)
        logger.info(f"Latency removed from {container.name}")


# ==============================================================================
# Chaos Test Fixtures
# ==============================================================================


@pytest.fixture
def chaos_kafka_failure(kafka_container):
    """Fixture to simulate Kafka broker failure."""

    def _inject(duration_seconds: int = 5, mode: str = "stop"):
        return service_failure(kafka_container, duration_seconds, mode)

    return _inject


@pytest.fixture
def chaos_schema_registry_failure(schema_registry_container):
    """Fixture to simulate Schema Registry failure."""

    def _inject(duration_seconds: int = 5, mode: str = "stop"):
        return service_failure(schema_registry_container, duration_seconds, mode)

    return _inject


@pytest.fixture
def chaos_minio_failure(minio_container):
    """Fixture to simulate MinIO/S3 storage failure."""

    def _inject(duration_seconds: int = 5, mode: str = "stop"):
        return service_failure(minio_container, duration_seconds, mode)

    return _inject


@pytest.fixture
def chaos_network_partition(kafka_container):
    """Fixture to simulate network partition."""

    def _inject(duration_seconds: int = 5):
        return network_partition(kafka_container, duration_seconds)

    return _inject


@pytest.fixture
def chaos_kafka_resource_limit(kafka_container):
    """Fixture to inject Kafka resource constraints."""

    def _inject(cpu_quota: Optional[int] = None, mem_limit: Optional[str] = None):
        return resource_limit(kafka_container, cpu_quota, mem_limit)

    return _inject


@pytest.fixture
def chaos_kafka_latency(kafka_container):
    """Fixture to inject Kafka network latency."""

    def _inject(latency_ms: int = 100, jitter_ms: int = 20):
        return inject_latency(kafka_container, latency_ms, jitter_ms)

    return _inject


# ==============================================================================
# Helper Functions
# ==============================================================================


def _wait_for_container_health(container, timeout_seconds: int = 30) -> bool:
    """Wait for container to become healthy."""
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        container.reload()
        status = container.status
        if status == "running":
            # Additional health check - depends on service
            if _check_service_health(container):
                return True
        time.sleep(1)

    logger.warning(
        f"Container did not become healthy",
        container=container.name,
        timeout=timeout_seconds,
    )
    return False


def _check_service_health(container) -> bool:
    """Check if service inside container is healthy."""
    # This is a basic check - can be extended per service
    container_name = container.name

    if "kafka" in container_name:
        # Check if Kafka is responding
        try:
            admin = AdminClient({"bootstrap.servers": "localhost:9092"})
            metadata = admin.list_topics(timeout=5.0)
            return len(metadata.topics) >= 0
        except Exception:
            return False

    elif "schema-registry" in container_name:
        # Check if Schema Registry is responding
        try:
            response = requests.get("http://localhost:8081/subjects", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    elif "minio" in container_name:
        # Check if MinIO is responding
        try:
            response = requests.get("http://localhost:9000/minio/health/live", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    # Default: assume healthy if running
    return container.status == "running"


# ==============================================================================
# Chaos Test Markers
# ==============================================================================

# Register custom pytest markers
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "chaos: mark test as chaos engineering test (destructive, requires services)",
    )
    config.addinivalue_line(
        "markers",
        "chaos_kafka: mark test as Kafka chaos test",
    )
    config.addinivalue_line(
        "markers",
        "chaos_storage: mark test as storage chaos test",
    )
    config.addinivalue_line(
        "markers",
        "chaos_network: mark test as network chaos test",
    )
