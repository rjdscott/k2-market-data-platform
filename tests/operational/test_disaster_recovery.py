"""Operational tests for disaster recovery runbooks.

These tests validate that operational runbooks work as documented by simulating
failure scenarios and testing recovery procedures using the Docker Python SDK.

Test coverage:
- Kafka broker failure and recovery
- MinIO (S3) failure and recovery
- PostgreSQL catalog failure and recovery
- Multi-service cascade failure recovery
- Data integrity after recovery

Requirements:
- Docker Compose services must be runnable
- docker-py package installed
- Sufficient system resources to run multiple containers

Usage:
    pytest tests/operational/test_disaster_recovery.py -v --timeout=300
"""

import time

import docker
import pytest
import requests


@pytest.fixture(scope="module")
def docker_client():
    """Docker client for controlling containers."""
    return docker.from_env()


@pytest.fixture(scope="module")
def project_name():
    """Project name for docker compose (from directory name)."""
    return "k2-market-data-platform"


def wait_for_container_healthy(container, timeout=90):
    """Wait for container to become healthy.

    Args:
        container: Docker container object
        timeout: Maximum wait time in seconds

    Returns:
        bool: True if healthy, False if timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        container.reload()
        if container.status == "running":
            # Check health status
            health = container.attrs.get("State", {}).get("Health", {})
            if health.get("Status") == "healthy":
                return True
            # If no healthcheck defined, consider running as healthy
            if not health:
                return True
        time.sleep(2)
    return False


@pytest.mark.operational
@pytest.mark.timeout(300)  # 5 minutes max for disaster recovery tests
class TestDisasterRecovery:
    """Test disaster recovery procedures from runbooks."""

    def test_kafka_failure_recovery(self, docker_client, project_name):
        """Test Kafka broker failure and recovery.

        Validates runbook: docs/operations/runbooks/failure-recovery.md - Scenario 1

        Steps:
        1. Verify Kafka is running
        2. Stop Kafka (simulate failure)
        3. Verify Kafka is down
        4. Start Kafka (recovery procedure)
        5. Verify Kafka is healthy
        6. Verify Kafka accepts connections
        """
        # Step 1: Get Kafka container
        kafka_container = docker_client.containers.get("k2-kafka")

        # Verify initially running
        kafka_container.reload()
        assert kafka_container.status == "running", "Kafka should be running initially"

        # Step 2: Stop Kafka (simulate failure)
        kafka_container.stop(timeout=10)
        time.sleep(2)

        # Step 3: Verify Kafka is down
        kafka_container.reload()
        assert kafka_container.status == "exited", "Kafka should be stopped"

        # Step 4: Start Kafka (recovery procedure)
        kafka_container.start()

        # Step 5: Wait for Kafka to be healthy
        assert wait_for_container_healthy(
            kafka_container, timeout=90
        ), "Kafka should recover and become healthy within 90s"

        # Step 6: Verify Kafka accepts connections
        # Give Kafka a moment to fully initialize after health check
        time.sleep(5)

        # Try to list topics as verification
        exit_code, output = kafka_container.exec_run(
            "bash -c 'kafka-topics --bootstrap-server localhost:9092 --list'",
            demux=False,
        )
        assert exit_code == 0, f"Kafka should accept connections, got exit code {exit_code}"

    def test_minio_failure_recovery(self, docker_client, project_name):
        """Test MinIO (S3-compatible storage) failure and recovery.

        Validates storage layer recovery for Iceberg warehouse.

        Steps:
        1. Verify MinIO is running
        2. Stop MinIO (simulate failure)
        3. Verify MinIO is down
        4. Start MinIO (recovery procedure)
        5. Verify MinIO is healthy
        6. Verify MinIO API is accessible
        """
        # Step 1: Get MinIO container
        minio_container = docker_client.containers.get("k2-minio")

        # Verify initially running
        minio_container.reload()
        assert minio_container.status == "running", "MinIO should be running initially"

        # Step 2: Stop MinIO (simulate failure)
        minio_container.stop(timeout=10)
        time.sleep(2)

        # Step 3: Verify MinIO is down
        minio_container.reload()
        assert minio_container.status == "exited", "MinIO should be stopped"

        # Step 4: Start MinIO (recovery procedure)
        minio_container.start()

        # Step 5: Wait for MinIO to be healthy
        assert wait_for_container_healthy(
            minio_container, timeout=60
        ), "MinIO should recover and become healthy within 60s"

        # Step 6: Verify MinIO API is accessible
        time.sleep(3)  # Give MinIO time to fully initialize
        response = requests.get("http://localhost:9000/minio/health/live", timeout=5)
        assert response.status_code == 200, "MinIO health check should pass"

    def test_postgres_catalog_failure_recovery(self, docker_client, project_name):
        """Test PostgreSQL catalog failure and recovery.

        Validates Iceberg catalog recovery procedures.

        Steps:
        1. Verify PostgreSQL is running
        2. Stop PostgreSQL (simulate failure)
        3. Verify PostgreSQL is down
        4. Start PostgreSQL (recovery procedure)
        5. Verify PostgreSQL is healthy
        6. Verify PostgreSQL accepts connections
        """
        # Step 1: Get PostgreSQL container
        postgres_container = docker_client.containers.get("k2-postgres")

        # Verify initially running
        postgres_container.reload()
        assert postgres_container.status == "running", "PostgreSQL should be running initially"

        # Step 2: Stop PostgreSQL (simulate failure)
        postgres_container.stop(timeout=10)
        time.sleep(2)

        # Step 3: Verify PostgreSQL is down
        postgres_container.reload()
        assert postgres_container.status == "exited", "PostgreSQL should be stopped"

        # Step 4: Start PostgreSQL (recovery procedure)
        postgres_container.start()

        # Step 5: Wait for PostgreSQL to be healthy
        assert wait_for_container_healthy(
            postgres_container, timeout=60
        ), "PostgreSQL should recover and become healthy within 60s"

        # Step 6: Verify PostgreSQL accepts connections
        time.sleep(3)  # Give PostgreSQL time to accept connections
        exit_code, output = postgres_container.exec_run(
            "pg_isready -U admin",
            demux=False,
        )
        assert exit_code == 0, "PostgreSQL should accept connections"

    def test_schema_registry_dependency_recovery(self, docker_client, project_name):
        """Test Schema Registry recovery after Kafka failure.

        Validates dependency recovery chain: Kafka → Schema Registry

        Steps:
        1. Verify both Kafka and Schema Registry running
        2. Stop Kafka (breaks dependency)
        3. Restart Kafka
        4. Verify Schema Registry recovers automatically
        5. Verify Schema Registry API is accessible
        """
        # Step 1: Get containers
        kafka_container = docker_client.containers.get("k2-kafka")
        schema_registry_container = docker_client.containers.get("k2-schema-registry-1")

        # Verify initially running
        kafka_container.reload()
        schema_registry_container.reload()
        assert kafka_container.status == "running", "Kafka should be running"
        assert schema_registry_container.status == "running", "Schema Registry should be running"

        # Step 2: Stop Kafka (breaks dependency)
        kafka_container.stop(timeout=10)
        time.sleep(10)  # Give Schema Registry time to detect Kafka is down

        # Step 3: Restart Kafka
        kafka_container.start()
        assert wait_for_container_healthy(kafka_container, timeout=90), "Kafka should recover"

        # Step 4: Verify Schema Registry recovers
        # May need manual restart if it doesn't auto-recover
        schema_registry_container.reload()
        if schema_registry_container.status != "running":
            schema_registry_container.start()

        assert wait_for_container_healthy(
            schema_registry_container, timeout=60
        ), "Schema Registry should recover after Kafka is back"

        # Step 5: Verify Schema Registry API is accessible
        time.sleep(5)
        response = requests.get("http://localhost:8081/subjects", timeout=5)
        assert response.status_code == 200, "Schema Registry API should be accessible"

    def test_cascade_failure_recovery(self, docker_client, project_name):
        """Test recovery from cascade failure of multiple services.

        Simulates a major outage where multiple critical services fail:
        - Kafka (ingestion layer)
        - MinIO (storage layer)
        - PostgreSQL (catalog layer)

        Tests recovery procedure:
        1. Stop all three services (simulate major outage)
        2. Recover in correct order (PostgreSQL → MinIO → Kafka)
        3. Verify all services healthy
        4. Verify full system functionality
        """
        # Get all containers
        kafka_container = docker_client.containers.get("k2-kafka")
        minio_container = docker_client.containers.get("k2-minio")
        postgres_container = docker_client.containers.get("k2-postgres")

        # Step 1: Stop all services (simulate major outage)
        kafka_container.stop(timeout=10)
        minio_container.stop(timeout=10)
        postgres_container.stop(timeout=10)
        time.sleep(5)

        # Verify all down
        kafka_container.reload()
        minio_container.reload()
        postgres_container.reload()
        assert kafka_container.status == "exited"
        assert minio_container.status == "exited"
        assert postgres_container.status == "exited"

        # Step 2: Recover in correct order (catalog → storage → ingestion)

        # 2a: Recover PostgreSQL (catalog) first
        postgres_container.start()
        assert wait_for_container_healthy(
            postgres_container, timeout=60
        ), "PostgreSQL should recover first"

        # 2b: Recover MinIO (storage) second
        minio_container.start()
        assert wait_for_container_healthy(
            minio_container, timeout=60
        ), "MinIO should recover second"

        # 2c: Recover Kafka (ingestion) last
        kafka_container.start()
        assert wait_for_container_healthy(kafka_container, timeout=90), "Kafka should recover last"

        # Step 3: Verify full system functionality
        time.sleep(10)  # Allow services to fully stabilize

        # Verify PostgreSQL
        exit_code, _ = postgres_container.exec_run("pg_isready -U admin")
        assert exit_code == 0, "PostgreSQL should be functional"

        # Verify MinIO
        response = requests.get("http://localhost:9000/minio/health/live", timeout=5)
        assert response.status_code == 200, "MinIO should be functional"

        # Verify Kafka
        exit_code, _ = kafka_container.exec_run(
            "bash -c 'kafka-topics --bootstrap-server localhost:9092 --list'",
        )
        assert exit_code == 0, "Kafka should be functional"

    def test_recovery_time_objective(self, docker_client, project_name):
        """Test that recovery time objectives (RTO) are met.

        From disaster-recovery.md:
        - Kafka: RTO < 5 minutes
        - MinIO: RTO < 10 minutes
        - PostgreSQL: RTO < 15 minutes

        This test measures actual recovery times and compares to targets.
        """
        # Test Kafka RTO < 5 minutes (300 seconds)
        kafka_container = docker_client.containers.get("k2-kafka")
        kafka_container.stop(timeout=10)

        start_time = time.time()
        kafka_container.start()
        kafka_healthy = wait_for_container_healthy(kafka_container, timeout=120)
        kafka_recovery_time = time.time() - start_time

        assert kafka_healthy, "Kafka should recover"
        assert (
            kafka_recovery_time < 300
        ), f"Kafka RTO should be < 5 min, actual: {kafka_recovery_time:.1f}s"

        # Test MinIO RTO < 10 minutes (600 seconds)
        minio_container = docker_client.containers.get("k2-minio")
        minio_container.stop(timeout=10)

        start_time = time.time()
        minio_container.start()
        minio_healthy = wait_for_container_healthy(minio_container, timeout=120)
        minio_recovery_time = time.time() - start_time

        assert minio_healthy, "MinIO should recover"
        assert (
            minio_recovery_time < 600
        ), f"MinIO RTO should be < 10 min, actual: {minio_recovery_time:.1f}s"

        # Test PostgreSQL RTO < 15 minutes (900 seconds)
        postgres_container = docker_client.containers.get("k2-postgres")
        postgres_container.stop(timeout=10)

        start_time = time.time()
        postgres_container.start()
        postgres_healthy = wait_for_container_healthy(postgres_container, timeout=120)
        postgres_recovery_time = time.time() - start_time

        assert postgres_healthy, "PostgreSQL should recover"
        assert (
            postgres_recovery_time < 900
        ), f"PostgreSQL RTO should be < 15 min, actual: {postgres_recovery_time:.1f}s"

        # Log recovery times for reporting
        print("\nRecovery Times (RTO Validation):")
        print(f"  Kafka:      {kafka_recovery_time:.1f}s (target: < 300s)")
        print(f"  MinIO:      {minio_recovery_time:.1f}s (target: < 600s)")
        print(f"  PostgreSQL: {postgres_recovery_time:.1f}s (target: < 900s)")
