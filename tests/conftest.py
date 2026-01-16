"""
Global pytest configuration and fixtures for K2 Market Data Platform testing.

This module provides the foundational testing infrastructure including:
- External service fixtures (Kafka, MinIO, DuckDB)
- Test data generation and management
- Resource cleanup and monitoring
- Performance and chaos testing utilities
"""

import gc
import logging
import os
import uuid
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import docker
import pytest
from docker.errors import DockerException
from docker.models.containers import Container
from pytest_mock import MockerFixture

# Configure logging for test execution
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Test configuration
TEST_CONFIG = {
    "kafka": {
        "image": "confluentinc/cp-kafka:7.4.0",
        "schema_registry_image": "confluentinc/cp-schema-registry:7.4.0",
        "ports": {"kafka": 9092, "schema_registry": 8081},
        "topics": ["test-trades", "test-quotes", "test-reference", "test-dead-letter"],
    },
    "minio": {
        "image": "minio/minio:latest",
        "port": 9000,
        "access_key": "minioadmin",
        "secret_key": "minioadmin",
        "bucket": "test-lakehouse",
    },
    "duckdb": {"memory_limit": "2GB", "threads": 4},
    "timeouts": {"container_start": 60, "health_check": 30, "cleanup": 10},
}


class ResourceTracker:
    """Track resource usage during test execution."""

    def __init__(self):
        self.containers_created: list[str] = []
        self.temp_files_created: list[str] = []
        self.connections_opened: list[str] = []
        self.memory_usage: list[float] = []

    def add_container(self, container_id: str):
        """Track created container."""
        self.containers_created.append(container_id)

    def add_temp_file(self, file_path: str):
        """Track temporary file."""
        self.temp_files_created.append(file_path)

    def add_connection(self, connection_id: str):
        """Track opened connection."""
        self.connections_opened.append(connection_id)

    def verify_cleanup(self) -> dict[str, bool | float]:
        """Verify all resources are cleaned up."""
        import psutil

        # Check for remaining containers
        try:
            client = docker.from_env()
            remaining_containers = [
                c for c in client.containers.list(all=True) if c.id in self.containers_created
            ]
            containers_clean = len(remaining_containers) == 0
        except DockerException:
            containers_clean = True  # Assume clean if Docker unavailable

        # Check for remaining temp files
        temp_files_clean = all(not Path(path).exists() for path in self.temp_files_created)

        # Check memory usage
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        memory_acceptable = memory_mb < 1000  # 1GB limit

        return {
            "containers": containers_clean,
            "temp_files": temp_files_clean,
            "memory": memory_acceptable,
            "memory_mb": memory_mb,
        }


class KafkaTestCluster:
    """Dockerized Kafka cluster for testing."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.client = docker.from_env()
        self.kafka_container: Container | None = None
        self.schema_registry_container: Container | None = None
        self.network_name = f"k2-test-kafka-{uuid.uuid4().hex[:8]}"

    def start(self) -> None:
        """Start Kafka cluster with schema registry."""
        try:
            # Create network
            self.network = self.client.networks.create(self.network_name, driver="bridge")

            # Start Zookeeper (required for Kafka)
            zookeeper_container = self.client.containers.run(
                "confluentinc/cp-zookeeper:7.4.0",
                environment={"ZOOKEEPER_CLIENT_PORT": "2181", "ZOOKEEPER_TICK_TIME": "2000"},
                network=self.network_name,
                name=f"k2-test-zookeeper-{uuid.uuid4().hex[:8]}",
                detach=True,
            )

            # Start Kafka broker
            self.kafka_container = self.client.containers.run(
                self.config["image"],
                environment={
                    "KAFKA_BROKER_ID": "1",
                    "KAFKA_ZOOKEEPER_CONNECT": "zookeeper:2181",
                    "KAFKA_ADVERTISED_LISTENERS": "PLAINTEXT://localhost:9092,PLAINTEXT_HOST://kafka:29092",
                    "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT",
                    "KAFKA_INTER_BROKER_LISTENER_NAME": "PLAINTEXT",
                    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                    "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "true",
                },
                ports={"9092/tcp": self.config["ports"]["kafka"]},
                network=self.network_name,
                name=f"k2-test-kafka-{uuid.uuid4().hex[:8]}",
                detach=True,
            )

            # Start Schema Registry
            self.schema_registry_container = self.client.containers.run(
                self.config["schema_registry_image"],
                environment={
                    "SCHEMA_REGISTRY_HOST_NAME": "schema-registry",
                    "SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS": "kafka:29092",
                    "SCHEMA_REGISTRY_LISTENERS": "http://0.0.0.0:8081",
                },
                ports={"8081/tcp": self.config["ports"]["schema_registry"]},
                network=self.network_name,
                name=f"k2-test-schema-registry-{uuid.uuid4().hex[:8]}",
                detach=True,
            )

            logger.info("Kafka cluster started successfully")

        except DockerException as e:
            logger.error(f"Failed to start Kafka cluster: {e}")
            self.cleanup()
            raise

    def wait_for_health(self, timeout: int = 30) -> None:
        """Wait for Kafka cluster to be healthy."""
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check Kafka container health
                if self.kafka_container:
                    kafka_stats = self.kafka_container.stats(stream=False)
                    if kafka_stats:
                        logger.info("Kafka broker is running")

                # Check Schema Registry
                import requests

                response = requests.get(
                    f"http://localhost:{self.config['ports']['schema_registry']}/subjects",
                    timeout=5,
                )
                if response.status_code == 200:
                    logger.info("Schema Registry is healthy")
                    break

            except Exception as e:
                logger.debug(f"Health check failed: {e}")

            time.sleep(2)
        else:
            raise TimeoutError("Kafka cluster failed to become healthy")

    def create_topics(self) -> None:
        """Create test topics."""
        try:
            from confluent_kafka.admin import AdminClient
            from confluent_kafka.cimpl import NewTopic
        except ImportError:
            logger.warning("confluent_kafka not available, skipping topic creation")
            return

        admin_client = AdminClient(
            {"bootstrap.servers": f"localhost:{self.config['ports']['kafka']}"}
        )

        topics = [
            NewTopic(topic, num_partitions=3, replication_factor=1)
            for topic in self.config["topics"]
        ]

        futures = admin_client.create_topics(topics)

        for topic, future in futures.items():
            try:
                future.result()
                logger.info(f"Created topic: {topic}")
            except Exception as e:
                logger.warning(f"Failed to create topic {topic}: {e}")

    @property
    def brokers(self) -> str:
        """Get Kafka broker connection string."""
        return f"localhost:{self.config['ports']['kafka']}"

    @property
    def schema_registry_url(self) -> str:
        """Get Schema Registry URL."""
        return f"http://localhost:{self.config['ports']['schema_registry']}"

    def cleanup(self) -> None:
        """Clean up Kafka cluster resources."""
        try:
            # Stop and remove containers
            if self.kafka_container:
                self.kafka_container.stop(timeout=10)
                self.kafka_container.remove()

            if self.schema_registry_container:
                self.schema_registry_container.stop(timeout=10)
                self.schema_registry_container.remove()

            # Remove network
            try:
                network = self.client.networks.get(self.network_name)
                network.remove()
            except Exception:
                pass

            logger.info("Kafka cluster cleaned up")

        except DockerException as e:
            logger.warning(f"Cleanup warning: {e}")


class MinioTestBackend:
    """Dockerized MinIO for S3-compatible storage testing."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.client = docker.from_env()
        self.container: Container | None = None
        self.container_name = f"k2-test-minio-{uuid.uuid4().hex[:8]}"

    def start(self) -> None:
        """Start MinIO container."""
        try:
            self.container = self.client.containers.run(
                self.config["image"],
                command=["server", "/data", "--console-address", ":9001"],
                environment={
                    "MINIO_ROOT_USER": self.config["access_key"],
                    "MINIO_ROOT_PASSWORD": self.config["secret_key"],
                },
                ports={
                    f"{self.config['port']}/tcp": self.config["port"],
                    "9001/tcp": 9001,  # Console
                },
                name=self.container_name,
                detach=True,
            )

            logger.info("MinIO backend started")

        except DockerException as e:
            logger.error(f"Failed to start MinIO: {e}")
            self.cleanup()
            raise

    def wait_for_health(self, timeout: int = 30) -> None:
        """Wait for MinIO to be healthy."""
        import time

        import requests

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"http://localhost:{self.config['port']}/minio/health/live", timeout=5
                )
                if response.status_code == 200:
                    logger.info("MinIO is healthy")
                    break
            except Exception:
                pass

            time.sleep(2)
        else:
            raise TimeoutError("MinIO failed to become healthy")

    def create_bucket(self) -> None:
        """Create test bucket."""
        try:
            from minio import Minio

            client = Minio(
                f"localhost:{self.config['port']}",
                access_key=self.config["access_key"],
                secret_key=self.config["secret_key"],
                secure=False,
            )

            if not client.bucket_exists(self.config["bucket"]):
                client.make_bucket(self.config["bucket"])
                logger.info(f"Created bucket: {self.config['bucket']}")
        except ImportError:
            logger.warning("minio library not available, skipping bucket creation")

    @property
    def endpoint_url(self) -> str:
        """Get MinIO endpoint URL."""
        return f"http://localhost:{self.config['port']}"

    def cleanup(self) -> None:
        """Clean up MinIO resources."""
        try:
            if self.container:
                self.container.stop(timeout=10)
                self.container.remove()

            logger.info("MinIO backend cleaned up")

        except DockerException as e:
            logger.warning(f"MinIO cleanup warning: {e}")


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def resource_tracker() -> Generator[ResourceTracker]:
    """Track resource usage across test session."""
    tracker = ResourceTracker()
    yield tracker

    # Verify cleanup at end of session
    cleanup_status = tracker.verify_cleanup()
    logger.info(f"Session cleanup status: {cleanup_status}")

    # Fail session if resources not cleaned up
    if not all(cleanup_status.values()):
        failed_resources = [resource for resource, clean in cleanup_status.items() if not clean]
        pytest.fail(f"Resource cleanup failed: {failed_resources}")


@pytest.fixture(scope="session")
def kafka_cluster(resource_tracker: ResourceTracker) -> Generator[KafkaTestCluster]:
    """Dockerized Kafka cluster for integration testing."""
    cluster = KafkaTestCluster(TEST_CONFIG["kafka"])

    try:
        cluster.start()
        cluster.wait_for_health(timeout=TEST_CONFIG["timeouts"]["container_start"])
        cluster.create_topics()

        if cluster.kafka_container and cluster.kafka_container.id:
            resource_tracker.add_container(cluster.kafka_container.id)
        if cluster.schema_registry_container and cluster.schema_registry_container.id:
            resource_tracker.add_container(cluster.schema_registry_container.id)

        yield cluster

    finally:
        cluster.cleanup()


@pytest.fixture(scope="session")
def minio_backend(resource_tracker: ResourceTracker) -> Generator[MinioTestBackend]:
    """Dockerized MinIO backend for storage testing."""
    backend = MinioTestBackend(TEST_CONFIG["minio"])

    try:
        backend.start()
        backend.wait_for_health(timeout=TEST_CONFIG["timeouts"]["container_start"])
        backend.create_bucket()

        if backend.container and backend.container.id:
            resource_tracker.add_container(backend.container.id)

        yield backend

    finally:
        backend.cleanup()


@pytest.fixture(scope="function")
def sample_market_data() -> dict[str, Any]:
    """Generate sample market data for testing."""
    import random
    from decimal import Decimal

    symbols = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
    exchanges = ["NASDAQ", "NYSE", "ARCA"]

    # Generate trades
    trades = []
    base_time = datetime.now() - timedelta(hours=1)

    for i in range(100):
        timestamp = base_time + timedelta(seconds=i * 36)  # 1 trade per minute

        trade = {
            "symbol": random.choice(symbols),
            "exchange": random.choice(exchanges),
            "timestamp": timestamp.isoformat(),
            "price": str(Decimal(random.uniform(10, 1000)).quantize(Decimal("0.01"))),
            "quantity": random.randint(100, 10000),
            "trade_id": f"TRD-{uuid.uuid4().hex[:12]}",
            "conditions": ["regular"],
        }
        trades.append(trade)

    # Generate quotes
    quotes = []
    for i in range(200):
        timestamp = base_time + timedelta(seconds=i * 18)  # 1 quote per 30 seconds

        base_price = random.uniform(10, 1000)
        spread = base_price * random.uniform(0.0001, 0.01)

        quote = {
            "symbol": random.choice(symbols),
            "exchange": random.choice(exchanges),
            "timestamp": timestamp.isoformat(),
            "bid_price": str(Decimal(base_price - spread / 2).quantize(Decimal("0.01"))),
            "bid_size": random.randint(100, 5000),
            "ask_price": str(Decimal(base_price + spread / 2).quantize(Decimal("0.01"))),
            "ask_size": random.randint(100, 5000),
            "quote_condition": "real-time",
        }
        quotes.append(quote)

    return {
        "trades": trades,
        "quotes": quotes,
        "symbols": symbols,
        "time_range": {
            "start": base_time.isoformat(),
            "end": (base_time + timedelta(hours=1)).isoformat(),
        },
    }


@pytest.fixture(scope="function")
def kafka_producer_config(kafka_cluster: KafkaTestCluster) -> dict[str, str | int | bool]:
    """Kafka producer configuration for testing."""
    return {
        "bootstrap.servers": kafka_cluster.brokers,
        "schema.registry.url": kafka_cluster.schema_registry_url,
        "value.serializer": "io.confluent.kafka.serializers.KafkaAvroSerializer",
        "key.serializer": "org.apache.kafka.common.serialization.StringSerializer",
        "acks": "all",
        "retries": 3,
        "retry.backoff.ms": 100,
        "max.in.flight.requests.per.connection": 5,
        "linger.ms": 10,
        "batch.size": 16384,
    }


@pytest.fixture(scope="function")
def kafka_consumer_config(kafka_cluster: KafkaTestCluster) -> dict[str, str | int | bool]:
    """Kafka consumer configuration for testing."""
    group_id = f"test-group-{uuid.uuid4().hex[:8]}"

    return {
        "bootstrap.servers": kafka_cluster.brokers,
        "schema.registry.url": kafka_cluster.schema_registry_url,
        "value.deserializer": "io.confluent.kafka.serializers.KafkaAvroDeserializer",
        "key.deserializer": "org.apache.kafka.common.serialization.StringDeserializer",
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "max.poll.records": 100,
        "session.timeout.ms": 30000,
        "heartbeat.interval.ms": 10000,
    }


@pytest.fixture(scope="function")
def iceberg_config(minio_backend: MinioTestBackend) -> dict[str, str]:
    """Iceberg configuration for testing."""
    return {
        "warehouse": f"s3://{TEST_CONFIG['minio']['bucket']}/warehouse",
        "catalog.backend": "jdbc",
        "catalog.uri": "jdbc:sqlite::memory:",
        "s3.endpoint": minio_backend.endpoint_url,
        "s3.access-key-id": TEST_CONFIG["minio"]["access_key"],
        "s3.secret-access-key": TEST_CONFIG["minio"]["secret_key"],
        "s3.path-style-access": "true",
    }


@pytest.fixture(scope="function")
def duckdb_connection() -> Generator[Any]:
    """In-memory DuckDB connection for testing."""
    import duckdb

    conn = duckdb.connect(":memory:")

    # Configure for testing
    conn.execute(f"SET memory_limit='{TEST_CONFIG['duckdb']['memory_limit']}'")
    conn.execute(f"SET threads={TEST_CONFIG['duckdb']['threads']}")

    yield conn

    conn.close()


@pytest.fixture(scope="function", autouse=True)
def cleanup_temp_files():
    """Automatically clean up temporary files after each test."""
    temp_files = []

    yield

    # Clean up any temp files created during test
    for temp_file in temp_files:
        try:
            if Path(temp_file).exists():
                os.unlink(temp_file)
        except Exception as e:
            logger.warning(f"Failed to clean up temp file {temp_file}: {e}")


@pytest.fixture(scope="function")
def mock_external_apis(mocker: MockerFixture) -> dict[str, Any]:
    """Mock external API calls for unit testing."""
    # Mock Binance API
    mock_binance = mocker.patch("k2.ingestion.binance_client.requests.get")
    mock_binance.return_value.status_code = 200
    mock_binance.return_value.json.return_value = [
        {
            "symbol": "BTCUSDT",
            "price": "50000.00",
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
    ]

    # Mock other external services as needed
    return {"binance": mock_binance}


# =============================================================================
# Test Utilities
# =============================================================================


def wait_for_condition(condition_func, timeout: int = 30, interval: float = 1.0) -> bool:
    """Wait for a condition to become true."""
    import time

    start_time = time.time()

    while time.time() - start_time < timeout:
        if condition_func():
            return True
        time.sleep(interval)

    return False


def assert_data_quality(data: list[dict], data_type: str) -> None:
    """Assert data quality for market data."""
    if not data:
        pytest.fail(f"No {data_type} data provided")

    # Check required fields
    if data_type == "trades":
        required_fields = ["symbol", "timestamp", "price", "quantity", "trade_id"]
    elif data_type == "quotes":
        required_fields = ["symbol", "timestamp", "bid_price", "ask_price"]
    else:
        pytest.fail(f"Unknown data type: {data_type}")

    for record in data:
        for field in required_fields:
            if field not in record:
                pytest.fail(f"Missing required field '{field}' in {data_type}")


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "integration: marks tests as requiring external services")
    config.addinivalue_line("markers", "performance: marks tests as performance benchmarks")
    config.addinivalue_line("markers", "chaos: marks tests as chaos engineering tests")
    config.addinivalue_line("markers", "soak: marks tests as long-running soak tests")


def pytest_collection_modifyitems(config, items):
    """Modify test collection for parallel execution."""
    # Ensure integration tests run sequentially to avoid resource conflicts
    integration_items = [item for item in items if "integration" in item.keywords]
    unit_items = [item for item in items if "integration" not in item.keywords]

    # Reorder items: unit tests first, then integration
    items[:] = unit_items + integration_items


@pytest.fixture(scope="function", autouse=True)
def test_logging(request):
    """Enhanced logging for test execution."""
    logger.info(f"Starting test: {request.node.name}")

    yield

    logger.info(f"Finished test: {request.node.name}")


# =============================================================================
# Environment Setup
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def verify_test_environment():
    """Verify test environment is properly configured."""
    # Check Docker availability (but don't fail tests)
    try:
        client = docker.from_env()
        client.ping()
        logger.info("Docker is available for testing")
    except DockerException as e:
        logger.warning(f"Docker not available: {e}")
        # Don't skip - just warn for unit tests that don't need Docker

    # Check required environment variables
    required_vars = []
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}")


# Memory management for resource-intensive tests
@pytest.fixture(scope="function", autouse=True)
def memory_management():
    """Manage memory usage during tests."""
    # Force garbage collection before test
    gc.collect()

    yield

    # Force garbage collection after test
    gc.collect()
