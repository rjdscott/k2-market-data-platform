"""Global pytest configuration and resource management fixtures.

This module provides automatic resource cleanup and monitoring to prevent
test suite from draining system resources.

Features:
- Automatic garbage collection after each test
- Session-level memory leak detection
- Docker container health checks
- Prevents resource exhaustion

Usage:
    These fixtures run automatically - no explicit imports needed.
"""

import gc
import logging

import psutil
import pytest

logger = logging.getLogger(__name__)


# ==============================================================================
# Resource Cleanup Fixtures
# ==============================================================================


@pytest.fixture(autouse=True, scope="function")
def cleanup_after_test():
    """Force garbage collection after each test to prevent memory accumulation.

    This fixture runs automatically after every test function to ensure
    that Python's garbage collector runs, preventing memory buildup across
    the test session.

    Runs: After every test
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True, scope="session")
def check_system_resources():
    """Monitor system resources and fail if critical threshold exceeded.

    Tracks memory usage from start to end of test session. If memory growth
    exceeds 500MB, the test session is failed with a clear error message.

    Threshold: 500MB memory growth
    Runs: Once per session (start and end)
    """
    process = psutil.Process()
    initial_mem_mb = process.memory_info().rss / 1024 / 1024

    logger.info(f"Test session starting - Initial memory: {initial_mem_mb:.1f}MB")

    yield

    final_mem_mb = process.memory_info().rss / 1024 / 1024
    mem_growth_mb = final_mem_mb - initial_mem_mb

    logger.info(
        f"Test session ending - Final memory: {final_mem_mb:.1f}MB "
        f"(growth: {mem_growth_mb:+.1f}MB)",
    )

    if mem_growth_mb > 500:  # >500MB growth indicates leak
        pytest.fail(
            f"MEMORY LEAK DETECTED: Test session leaked {mem_growth_mb:.0f}MB of memory.\n"
            f"Initial: {initial_mem_mb:.0f}MB, Final: {final_mem_mb:.0f}MB\n"
            f"This indicates a resource leak in the test suite.",
        )


# ==============================================================================
# Docker Container Health Checks
# ==============================================================================


@pytest.fixture(autouse=True)
def docker_container_health_check(request):
    """Ensure critical Docker containers are running after each test.

    If a test manipulates Docker containers (chaos/operational tests), this
    fixture ensures that critical containers are restarted if they were stopped.

    Only runs for tests that use docker-related fixtures.

    Containers monitored:
    - k2-kafka
    - k2-minio
    - k2-postgres
    """
    yield

    # Only run health check if docker_client fixture was used
    if "docker_client" not in request.fixturenames:
        return

    try:
        import docker

        docker_client = docker.from_env()
        critical_containers = ["k2-kafka", "k2-minio", "k2-postgres"]

        for container_name in critical_containers:
            try:
                container = docker_client.containers.get(container_name)
                if container.status != "running":
                    logger.warning(
                        f"Container {container_name} is {container.status}, attempting restart",
                    )
                    container.start()
                    logger.info(f"Container {container_name} restarted successfully")
            except docker.errors.NotFound:
                # Container doesn't exist - this is OK for unit tests
                pass
            except Exception as e:
                logger.error(f"Failed to check/restart container {container_name}: {e}")
    except ImportError:
        # docker library not available - this is OK for unit tests
        pass
    except Exception as e:
        logger.error(f"Docker health check failed: {e}")


# ==============================================================================
# Logging Configuration
# ==============================================================================


@pytest.fixture(scope="session", autouse=True)
def configure_test_logging():
    """Configure logging for test runs.

    Sets up structured logging with appropriate levels for test visibility.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Reduce noise from external libraries during tests
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("docker").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3fs").setLevel(logging.WARNING)
