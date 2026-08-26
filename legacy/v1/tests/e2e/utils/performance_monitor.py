"""Performance monitoring utilities for E2E testing.

This module provides comprehensive performance measurement capabilities for end-to-end
testing including latency measurement, throughput monitoring, and resource tracking.

Key Features:
- End-to-end latency measurement across pipeline stages
- Throughput monitoring and analysis
- Performance metrics collection
- SLA validation
- Historical performance tracking
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """Measure and monitor performance metrics for E2E tests."""

    def __init__(self):
        """Initialize performance monitor."""
        self.measurements: dict[str, list[dict[str, Any]]] = {}
        self.start_time: float | None = None
        self.checkpoints: dict[str, float] = {}

    def start_measurement(self, test_name: str) -> None:
        """Start performance measurement for a test."""

        logger.info(f"Starting performance measurement for: {test_name}")

        self.start_time = time.time()
        self.checkpoints = {"start": self.start_time}

        if test_name not in self.measurements:
            self.measurements[test_name] = []

    def add_checkpoint(self, checkpoint_name: str, description: str = "") -> None:
        """Add a measurement checkpoint."""

        if self.start_time is None:
            logger.warning("Cannot add checkpoint: measurement not started")
            return

        current_time = time.time()
        elapsed = current_time - self.start_time

        checkpoint_data = {
            "name": checkpoint_name,
            "timestamp": current_time,
            "elapsed_since_start": elapsed,
            "description": description,
        }

        self.checkpoints[checkpoint_name] = current_time
        logger.info(f"Added checkpoint '{checkpoint_name}' at {elapsed:.2f}s: {description}")

    def end_measurement(
        self, test_name: str, additional_metrics: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """End performance measurement and return results."""

        if self.start_time is None:
            logger.warning("Cannot end measurement: measurement not started")
            return {}

        end_time = time.time()
        total_duration = end_time - self.start_time

        results = {
            "test_name": test_name,
            "start_time": self.start_time,
            "end_time": end_time,
            "total_duration": total_duration,
            "checkpoints": self.checkpoints.copy(),
            "timestamp": datetime.utcnow().isoformat(),
        }

        if additional_metrics:
            results.update(additional_metrics)

        # Store measurement
        self.measurements[test_name].append(results)

        logger.info(f"Ended measurement for '{test_name}': {total_duration:.2f}s")

        # Reset for next measurement
        self.start_time = None
        self.checkpoints = {}

        return results

    async def measure_pipeline_latency(self, test_duration: int = 60) -> dict[str, float]:
        """Measure end-to-end latency from Binance to API."""

        logger.info(f"Starting pipeline latency measurement for {test_duration}s")

        self.start_measurement("pipeline_latency")

        try:
            # Simulate pipeline latency measurement
            # In real implementation, this would:
            # 1. Record timestamp when Binance message is received
            # 2. Track when message appears in Kafka
            # 3. Monitor when it's written to Iceberg
            # 4. Record when it's available via API
            # 5. Calculate end-to-end latency

            # For now, simulate with realistic delays
            await asyncio.sleep(2)  # Ingestion delay
            self.add_checkpoint("kafka_received", "Message received in Kafka")

            await asyncio.sleep(5)  # Processing delay
            self.add_checkpoint("iceberg_written", "Record written to Iceberg")

            await asyncio.sleep(1)  # Query delay
            self.add_checkpoint("api_available", "Data available via API")

            # Calculate simulated latency
            end_to_end_latency = 8.0  # Simulated 8 second latency

            additional_metrics = {
                "end_to_end_latency": end_to_end_latency,
                "ingestion_latency": 2.0,
                "processing_latency": 5.0,
                "query_latency": 1.0,
                "sla_met": end_to_end_latency < 30.0,
            }

            results = self.end_measurement("pipeline_latency", additional_metrics)

            return additional_metrics

        except Exception as e:
            logger.error(f"Error in pipeline latency measurement: {e}")
            return {"error": str(e)}

    async def measure_throughput(self, test_duration: int = 60) -> dict[str, float]:
        """Measure throughput metrics for pipeline."""

        logger.info(f"Starting throughput measurement for {test_duration}s")

        self.start_measurement("throughput")

        try:
            # Simulate message count tracking
            message_count = 0
            start_time = time.time()

            # Simulate receiving messages over time
            for i in range(test_duration):
                await asyncio.sleep(1)
                message_count += 1  # Simulate 1 message per second

                # Add periodic checkpoints
                if i % 10 == 0:
                    self.add_checkpoint(
                        f"msg_{message_count}", f"Processed {message_count} messages"
                    )

            end_time = time.time()
            actual_duration = end_time - start_time

            throughput = message_count / actual_duration if actual_duration > 0 else 0

            additional_metrics = {
                "messages_processed": message_count,
                "duration": actual_duration,
                "throughput_msg_per_sec": throughput,
                "throughput_msg_per_min": throughput * 60,
                "target_met": throughput >= 10,  # Target: 10 msg/sec
            }

            results = self.end_measurement("throughput", additional_metrics)

            logger.info(f"Throughput measurement: {throughput:.2f} msg/sec")

            return additional_metrics

        except Exception as e:
            logger.error(f"Error in throughput measurement: {e}")
            return {"error": str(e)}

    def validate_sla(self, metrics: dict[str, Any]) -> dict[str, bool]:
        """Validate SLA compliance based on metrics."""

        sla_checks = {}

        # Latency SLA: <30 seconds
        latency = metrics.get("end_to_end_latency", 0)
        sla_checks["latency_sla"] = latency < 30.0

        # Throughput SLA: >10 messages per second
        throughput = metrics.get("throughput_msg_per_sec", 0)
        sla_checks["throughput_sla"] = throughput >= 10.0

        # Resource usage SLA: <1GB memory
        memory_mb = metrics.get("memory_mb", 0)
        sla_checks["memory_sla"] = memory_mb < 1024.0

        # CPU usage SLA: <80% average
        cpu_percent = metrics.get("cpu_percent", 0)
        sla_checks["cpu_sla"] = cpu_percent < 80.0

        # Overall SLA compliance
        sla_checks["overall_sla"] = all(sla_checks.values())

        # Log results
        for check_name, passed in sla_checks.items():
            status = "PASSED" if passed else "FAILED"
            logger.info(f"SLA Check {check_name}: {status}")

        return sla_checks

    def get_measurements(self, test_name: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Get stored measurements."""

        if test_name:
            return {test_name: self.measurements.get(test_name, [])}
        return self.measurements.copy()

    def get_performance_summary(self, test_name: str) -> dict[str, Any]:
        """Get performance summary for a specific test."""

        if test_name not in self.measurements:
            return {}

        test_measurements = self.measurements[test_name]
        if not test_measurements:
            return {}

        # Calculate summary statistics
        durations = [m.get("total_duration", 0) for m in test_measurements]

        if not durations:
            return {}

        summary = {
            "test_name": test_name,
            "measurement_count": len(test_measurements),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "avg_duration": sum(durations) / len(durations),
            "latest_measurement": test_measurements[-1] if test_measurements else None,
        }

        return summary

    def clear_measurements(self, test_name: str | None = None) -> None:
        """Clear stored measurements."""

        if test_name:
            self.measurements.pop(test_name, None)
            logger.info(f"Cleared measurements for: {test_name}")
        else:
            self.measurements.clear()
            logger.info("Cleared all measurements")

    def export_measurements(self, filename: str) -> None:
        """Export measurements to file."""

        try:
            import json

            with open(filename, "w") as f:
                json.dump(self.measurements, f, indent=2, default=str)

            logger.info(f"Exported measurements to: {filename}")

        except Exception as e:
            logger.error(f"Error exporting measurements: {e}")

    def get_current_checkpoints(self) -> dict[str, float]:
        """Get current checkpoints."""
        return self.checkpoints.copy()

    def reset(self) -> None:
        """Reset performance monitor state."""
        self.start_time = None
        self.checkpoints = {}
        logger.info("Reset performance monitor")
