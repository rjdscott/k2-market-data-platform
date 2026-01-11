"""Integration tests for Docker infrastructure.

This module validates that all required Docker services are healthy and accessible.
Tests should be run with docker-compose services running.
"""

import pytest
import requests
import structlog
from confluent_kafka.admin import AdminClient
from sqlalchemy import create_engine, text

logger = structlog.get_logger()


@pytest.mark.integration
class TestInfrastructure:
    """Validate all Docker services are healthy and accessible."""

    def test_kafka_broker_available(self):
        """Kafka broker should respond to admin requests."""
        admin = AdminClient({"bootstrap.servers": "localhost:9092"})

        # List topics should work even if empty
        metadata = admin.list_topics(timeout=5)

        assert metadata is not None, "Kafka metadata should not be None"
        logger.info("Kafka broker accessible", topics_count=len(metadata.topics))

    def test_schema_registry_available(self):
        """Schema Registry should list subjects (even if empty)."""
        response = requests.get("http://localhost:8081/subjects", timeout=5)

        assert response.status_code == 200, "Schema Registry should return 200"
        subjects = response.json()

        logger.info("Schema Registry accessible", subjects_count=len(subjects))

    def test_minio_available(self):
        """MinIO should respond to health checks."""
        response = requests.get("http://localhost:9000/minio/health/live", timeout=5)

        assert response.status_code == 200, "MinIO should return 200 for health check"
        logger.info("MinIO accessible")

    def test_postgres_available(self):
        """PostgreSQL should accept connections and execute queries."""
        engine = create_engine("postgresql://iceberg:iceberg@localhost:5432/iceberg_catalog")

        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 as test"))
            value = result.scalar()

            assert value == 1, "PostgreSQL should execute simple queries"

        logger.info("PostgreSQL accessible")

    def test_iceberg_rest_available(self):
        """Iceberg REST catalog should return configuration."""
        response = requests.get("http://localhost:8181/v1/config", timeout=5)

        assert response.status_code == 200, "Iceberg REST should return 200"
        config = response.json()

        assert (
            "defaults" in config or "overrides" in config
        ), "Iceberg config should contain defaults or overrides"

        logger.info("Iceberg REST catalog accessible")

    def test_prometheus_available(self):
        """Prometheus should respond to health checks."""
        response = requests.get("http://localhost:9090/-/healthy", timeout=5)

        assert response.status_code == 200, "Prometheus should return 200"
        logger.info("Prometheus accessible")

    def test_grafana_available(self):
        """Grafana should respond to health API."""
        response = requests.get("http://localhost:3000/api/health", timeout=5)

        assert response.status_code == 200, "Grafana should return 200"
        health = response.json()

        assert health.get("database") == "ok", "Grafana database should be ok"
        logger.info("Grafana accessible")

    def test_kafka_ui_available(self):
        """Kafka UI should be accessible."""
        response = requests.get("http://localhost:8080", timeout=5)

        assert response.status_code == 200, "Kafka UI should return 200"
        logger.info("Kafka UI accessible")
