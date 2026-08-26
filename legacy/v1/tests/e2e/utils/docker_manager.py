"""Docker Compose management utilities for E2E testing.

This module provides comprehensive Docker Compose management with proper
lifecycle handling, resource monitoring, and cleanup for end-to-end tests.

Key Features:
- Start/stop minimal and full Docker stacks
- Health check monitoring with timeout handling
- Resource usage monitoring and logging
- Proper cleanup and resource management
- Service dependency validation
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import docker

logger = logging.getLogger(__name__)


class E2EDockerManager:
    """Manage Docker Compose for E2E tests with proper lifecycle management."""

    def __init__(self, compose_file: str = "docker-compose.v1.yml"):
        """Initialize Docker manager with compose file path."""
        self.compose_file = Path(compose_file)
        self.docker_client = docker.from_env()
        self.running_services: dict[str, str] = {}
        self.start_time: datetime | None = None
        self.resource_monitor: dict[str, dict[str, float]] = {}

        logger.info(f"Initialized E2E Docker manager with compose file: {self.compose_file}")

    async def start_minimal_stack(self) -> dict[str, str]:
        """Start minimal stack: Kafka, Schema Registry, MinIO, PostgreSQL, Iceberg REST."""

        logger.info("Starting minimal Docker stack for E2E tests")

        # Define minimal services
        minimal_services = [
            "kafka",
            "schema-registry-1",
            "minio",
            "postgres",
            "iceberg-rest",
            "minio-init",
        ]

        try:
            # Start Docker Compose
            cmd = ["docker-compose", "-f", str(self.compose_file), "up", "-d"] + minimal_services

            result = await self._execute_command(cmd, timeout=120)

            if result["returncode"] != 0:
                raise RuntimeError(f"Failed to start minimal stack: {result['stderr']}")

            # Wait for services to be healthy
            healthy_services = {}
            for service in minimal_services:
                if await self.wait_for_health(service, timeout=60):
                    healthy_services[service] = "healthy"
                    logger.info(f"Service {service} is healthy")
                else:
                    logger.warning(f"Service {service} failed to become healthy")

            self.running_services.update(healthy_services)
            self.start_time = datetime.utcnow()

            return healthy_services

        except Exception as e:
            logger.error(f"Failed to start minimal stack: {e}")
            raise

    async def start_full_stack(self) -> dict[str, str]:
        """Start full stack including k2-query-api and consumer-crypto."""

        logger.info("Starting full Docker stack for E2E tests")

        # First start minimal stack
        minimal_services = await self.start_minimal_stack()

        # Then start additional services
        additional_services = [
            "k2-query-api",
            "binance-stream",
            "consumer-crypto",
            "kafka-ui",
            "prometheus",
            "grafana",
        ]

        try:
            for service in additional_services:
                cmd = ["docker-compose", "-f", str(self.compose_file), "up", "-d", service]

                result = await self._execute_command(cmd, timeout=60)

                if result["returncode"] != 0:
                    logger.warning(f"Failed to start {service}: {result['stderr']}")
                    continue

                # Wait for service to be healthy
                if await self.wait_for_health(service, timeout=60):
                    minimal_services[service] = "healthy"
                    logger.info(f"Service {service} is healthy")
                else:
                    logger.warning(f"Service {service} failed to become healthy")

            self.running_services = minimal_services
            return minimal_services

        except Exception as e:
            logger.error(f"Failed to start full stack: {e}")
            raise

    async def stop_stack(self) -> None:
        """Stop and clean up all services with proper error handling."""

        logger.info("Stopping Docker stack and cleaning up resources")

        try:
            cmd = ["docker-compose", "-f", str(self.compose_file), "down", "-v", "--remove-orphans"]

            result = await self._execute_command(cmd, timeout=60)

            if result["returncode"] != 0:
                logger.warning(f"Non-zero exit code stopping stack: {result['stderr']}")

            # Clear running services tracking
            self.running_services.clear()
            self.start_time = None
            self.resource_monitor.clear()

            logger.info("Docker stack stopped successfully")

        except Exception as e:
            logger.error(f"Error stopping Docker stack: {e}")
            raise

    async def wait_for_health(self, service_name: str, timeout: int = 60) -> bool:
        """Wait for service health check with timeout."""

        logger.info(f"Waiting for {service_name} health check (timeout: {timeout}s)")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check if container exists and is running
                container_name = f"k2-{service_name}"
                container = self.docker_client.containers.get(container_name)

                if container and container.status == "running":
                    # Check health status if health check is defined
                    if hasattr(container, "attrs") and "Health" in container.attrs.get("State", {}):
                        health_status = container.attrs["State"]["Health"]["Status"]
                        if health_status == "healthy":
                            logger.info(f"Service {service_name} is healthy")
                            return True
                    else:
                        # For services without explicit health check, verify they're running
                        logger.info(f"Service {service_name} is running")
                        return True

                await asyncio.sleep(2)

            except docker.errors.NotFound:
                logger.debug(f"Container {container_name} not found yet")
                await asyncio.sleep(2)
            except Exception as e:
                logger.debug(f"Error checking health for {service_name}: {e}")
                await asyncio.sleep(2)

        logger.warning(f"Service {service_name} failed to become healthy within {timeout}s")
        return False

    async def get_service_logs(self, service_name: str, lines: int = 50) -> str:
        """Get recent logs for debugging failed tests."""

        try:
            container_name = f"k2-{service_name}"
            container = self.docker_client.containers.get(container_name)

            if container:
                logs = container.logs(tail=lines, timestamps=True)
                return logs.decode("utf-8")
            else:
                return f"Container {container_name} not found"

        except Exception as e:
            logger.error(f"Error getting logs for {service_name}: {e}")
            return f"Error retrieving logs: {e}"

    async def execute_command(self, service: str, command: str) -> str:
        """Execute command in running service container."""

        try:
            container_name = f"k2-{service}"
            container = self.docker_client.containers.get(container_name)

            if container:
                result = container.exec_run(command)
                return result.output.decode("utf-8")
            else:
                return f"Container {container_name} not found"

        except Exception as e:
            logger.error(f"Error executing command in {service}: {e}")
            return f"Error executing command: {e}"

    async def get_resource_usage(self, service_name: str) -> dict[str, float]:
        """Get current resource usage for monitoring."""

        try:
            container_name = f"k2-{service_name}"
            container = self.docker_client.containers.get(container_name)

            if container:
                stats = container.stats(stream=False)

                # Parse stats for key metrics
                cpu_usage = self._parse_cpu_usage(stats)
                memory_usage = self._parse_memory_usage(stats)

                resource_info = {
                    "cpu_percent": cpu_usage,
                    "memory_bytes": memory_usage,
                    "memory_mb": memory_usage / (1024 * 1024),
                    "timestamp": time.time(),
                }

                # Store in monitoring cache
                self.resource_monitor[service_name] = resource_info

                return resource_info
            else:
                return {"error": f"Container {container_name} not found"}

        except Exception as e:
            logger.error(f"Error getting resource usage for {service_name}: {e}")
            return {"error": str(e)}

    async def _execute_command(self, cmd: list[str], timeout: int = 60) -> dict[str, Any]:
        """Execute shell command with timeout handling."""

        try:
            process = await asyncio.create_subprocess_exec(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            return {
                "returncode": process.returncode,
                "stdout": stdout.decode("utf-8"),
                "stderr": stderr.decode("utf-8"),
            }

        except TimeoutError:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            process.terminate()
            await process.wait()
            return {"returncode": -1, "stderr": "Command timed out"}
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return {"returncode": -1, "stderr": str(e)}

    def _parse_cpu_usage(self, stats: dict[str, Any]) -> float:
        """Parse CPU usage from container stats."""

        try:
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})

            cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get(
                "cpu_usage", {}
            ).get("total_usage", 0)

            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
                "system_cpu_usage", 0
            )

            if system_delta > 0:
                cpu_percent = (
                    (cpu_delta / system_delta)
                    * len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", []))
                    * 100
                )
                return round(cpu_percent, 2)

            return 0.0

        except Exception:
            return 0.0

    def _parse_memory_usage(self, stats: dict[str, Any]) -> float:
        """Parse memory usage from container stats."""

        try:
            memory_stats = stats.get("memory_stats", {})
            return memory_stats.get("usage", 0)
        except Exception:
            return 0.0

    def get_running_services(self) -> dict[str, str]:
        """Get current running services status."""
        return self.running_services.copy()

    def get_start_time(self) -> datetime | None:
        """Get stack start time."""
        return self.start_time

    def get_resource_monitoring_summary(self) -> dict[str, dict[str, float]]:
        """Get resource usage summary for all monitored services."""
        return self.resource_monitor.copy()
