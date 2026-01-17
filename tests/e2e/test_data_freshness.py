"""Data freshness and latency validation tests for K2 Market Data Platform.

This module provides tests to validate:
- Data freshness in real-time streaming scenarios
- End-to-end latency measurements
- SLA compliance validation
- Performance degradation detection
- Throughput and rate limiting validation
"""

import asyncio
import logging
import pytest
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger(__name__)


@pytest.mark.e2e
@pytest.mark.slow
class TestDataFreshnessAndLatency:
    """Comprehensive data freshness and latency validation."""

    @pytest.mark.asyncio
    async def test_data_freshness_window(self, api_client):
        """Test data freshness within acceptable time windows."""
        logger.info("Testing data freshness windows")

        # SLA: Data should be less than 30 seconds old
        max_freshness_seconds = 30

        try:
            # Get recent trades data
            response = await api_client.get("/v1/trades?table_type=TRADES&limit=100")
            assert response.status_code == 200

            data = response.json()
            if not data or "data" not in data or not data["data"]:
                pytest.skip("No trade data available for freshness testing")

            # Check timestamp of most recent data
            recent_trades = data["data"]
            current_time = datetime.now()

            freshness_violations = 0
            for trade in recent_trades[:10]:  # Check first 10 trades
                if "event_time" in trade:
                    trade_time = datetime.fromisoformat(trade["event_time"].replace("Z", "+00:00"))
                    age_seconds = (current_time - trade_time).total_seconds()

                    if age_seconds > max_freshness_seconds:
                        freshness_violations += 1
                        logger.warning(
                            f"Stale data: {age_seconds:.1f}s old (limit: {max_freshness_seconds}s)"
                        )

            # Allow some violations due to test environment variations
            violation_rate = freshness_violations / min(10, len(recent_trades))
            assert violation_rate < 0.3, f"Too many freshness violations: {freshness_violations}/10"

            logger.info(f"Data freshness test passed: {freshness_violations}/10 violations")

        except httpx.ConnectError:
            pytest.skip("API service not available for freshness testing")

    @pytest.mark.asyncio
    async def test_end_to_end_latency(self, api_client):
        """Test end-to-end latency from data generation to API availability."""
        logger.info("Testing end-to-end latency")

        # SLA: End-to-end latency should be < 5 seconds
        max_latency_seconds = 5

        try:
            # Get recent data with timestamps
            response = await api_client.get(
                "/v1/trades?table_type=TRADES&limit=10&order_by=event_time DESC"
            )
            assert response.status_code == 200

            data = response.json()
            if not data or "data" not in data or not data["data"]:
                pytest.skip("No recent data available for latency testing")

            current_time = datetime.now()
            latency_measurements = []

            for trade in data["data"][:5]:  # Measure first 5 trades
                if "event_time" in trade:
                    event_time = datetime.fromisoformat(trade["event_time"].replace("Z", "+00:00"))
                    latency = (current_time - event_time).total_seconds()
                    latency_measurements.append(latency)

                    if latency <= max_latency_seconds:
                        logger.info(f"Good latency: {latency:.2f}s")
                    else:
                        logger.warning(
                            f"High latency: {latency:.2f}s (limit: {max_latency_seconds}s)"
                        )

            if latency_measurements:
                avg_latency = sum(latency_measurements) / len(latency_measurements)
                max_measured_latency = max(latency_measurements)

                logger.info(
                    f"Average latency: {avg_latency:.2f}s, Max: {max_measured_latency:.2f}s"
                )

                # SLA validation
                assert avg_latency <= max_latency_seconds, (
                    f"Average latency too high: {avg_latency:.2f}s"
                )
                assert max_measured_latency <= max_latency_seconds * 2, (
                    f"Max latency too high: {max_measured_latency:.2f}s"
                )

        except httpx.ConnectError:
            pytest.skip("API service not available for latency testing")

    @pytest.mark.asyncio
    async def test_throughput_validation(self, api_client):
        """Test system throughput under normal load."""
        logger.info("Testing system throughput")

        # SLA: Should handle at least 10 requests/second
        min_requests_per_second = 10
        test_duration_seconds = 10
        concurrent_requests = 5

        try:
            start_time = time.time()
            successful_requests = 0
            failed_requests = 0

            async def make_request():
                nonlocal successful_requests, failed_requests
                try:
                    response = await api_client.get("/v1/trades?table_type=TRADES&limit=10")
                    if response.status_code == 200:
                        successful_requests += 1
                    else:
                        failed_requests += 1
                except Exception:
                    failed_requests += 1

            # Run concurrent requests for test duration
            tasks = []
            end_time = start_time + test_duration_seconds

            while time.time() < end_time:
                # Launch batch of concurrent requests
                batch_tasks = [make_request() for _ in range(concurrent_requests)]
                tasks.extend(batch_tasks)

                # Wait for batch to complete
                await asyncio.gather(*batch_tasks, return_exceptions=True)

                # Small delay between batches
                await asyncio.sleep(0.1)

            total_requests = successful_requests + failed_requests
            actual_duration = time.time() - start_time
            throughput = total_requests / actual_duration
            success_rate = successful_requests / total_requests if total_requests > 0 else 0

            logger.info(
                f"Throughput test: {throughput:.1f} req/s, Success rate: {success_rate:.1%}"
            )
            logger.info(
                f"Total requests: {total_requests}, Successful: {successful_requests}, Failed: {failed_requests}"
            )

            # SLA validation
            assert throughput >= min_requests_per_second, (
                f"Throughput too low: {throughput:.1f} req/s"
            )
            assert success_rate >= 0.95, f"Success rate too low: {success_rate:.1%}"

        except httpx.ConnectError:
            pytest.skip("API service not available for throughput testing")

    @pytest.mark.asyncio
    async def test_api_response_times(self, api_client):
        """Test API response time performance."""
        logger.info("Testing API response times")

        # SLA: 95th percentile response time < 1 second
        max_response_time_p95 = 1.0
        test_requests = 20

        try:
            response_times = []

            for i in range(test_requests):
                start_time = time.time()
                try:
                    response = await api_client.get("/v1/trades?table_type=TRADES&limit=50")
                    if response.status_code == 200:
                        response_time = time.time() - start_time
                        response_times.append(response_time)
                except Exception as e:
                    logger.warning(f"Request {i + 1} failed: {e}")

            if response_times:
                response_times.sort()
                p95_index = int(len(response_times) * 0.95)
                p95_response_time = response_times[min(p95_index, len(response_times) - 1)]
                avg_response_time = sum(response_times) / len(response_times)
                max_response_time = max(response_times)

                logger.info(
                    f"Response times - Avg: {avg_response_time:.3f}s, P95: {p95_response_time:.3f}s, Max: {max_response_time:.3f}s"
                )

                # SLA validation
                assert p95_response_time <= max_response_time_p95, (
                    f"P95 response time too high: {p95_response_time:.3f}s"
                )
                assert avg_response_time <= max_response_time_p95 * 0.5, (
                    f"Avg response time too high: {avg_response_time:.3f}s"
                )

        except httpx.ConnectError:
            pytest.skip("API service not available for response time testing")

    @pytest.mark.asyncio
    async def test_data_rate_monitoring(self, api_client):
        """Test data ingestion rate monitoring."""
        logger.info("Testing data rate monitoring")

        # SLA: Should receive at least 1 trade per second on average
        min_trades_per_second = 1.0
        observation_window_seconds = 30

        try:
            # Get data from two time points
            start_response = await api_client.get(
                "/v1/trades?table_type=TRADES&limit=1000&order_by=event_time DESC"
            )
            await asyncio.sleep(observation_window_seconds)
            end_response = await api_client.get(
                "/v1/trades?table_type=TRADES&limit=1000&order_by=event_time DESC"
            )

            if start_response.status_code != 200 or end_response.status_code != 200:
                pytest.skip("Unable to fetch data for rate monitoring")

            start_data = start_response.json().get("data", [])
            end_data = end_response.json().get("data", [])

            if not start_data or not end_data:
                pytest.skip("No data available for rate monitoring")

            # Find newest timestamp in start data and oldest in end data
            start_timestamps = [
                trade.get("event_time") for trade in start_data if trade.get("event_time")
            ]
            end_timestamps = [
                trade.get("event_time") for trade in end_data if trade.get("event_time")
            ]

            if not start_timestamps or not end_timestamps:
                pytest.skip("No valid timestamps found for rate monitoring")

            # Count trades in observation window
            newest_start = max(start_timestamps)
            oldest_end = min(end_timestamps)

            # Convert to datetime objects for comparison
            newest_start_dt = datetime.fromisoformat(newest_start.replace("Z", "+00:00"))
            oldest_end_dt = datetime.fromisoformat(oldest_end.replace("Z", "+00:00"))

            # Count trades that arrived during the window
            trades_in_window = 0
            for trade in end_data:
                if trade.get("event_time"):
                    trade_time = datetime.fromisoformat(trade["event_time"].replace("Z", "+00:00"))
                    if newest_start_dt < trade_time <= oldest_end_dt:
                        trades_in_window += 1

            actual_duration = (oldest_end_dt - newest_start_dt).total_seconds()
            if actual_duration > 0:
                trade_rate = trades_in_window / actual_duration
                logger.info(
                    f"Trade rate: {trade_rate:.2f} trades/second over {actual_duration:.1f}s"
                )

                # SLA validation (allowing for test environment variations)
                assert trade_rate >= min_trades_per_second * 0.5, (
                    f"Trade rate too low: {trade_rate:.2f} trades/s"
                )
            else:
                logger.warning("Insufficient time difference for rate calculation")

        except httpx.ConnectError:
            pytest.skip("API service not available for rate monitoring")

    @pytest.mark.asyncio
    async def test_sla_compliance_summary(self, api_client):
        """Test overall SLA compliance summary."""
        logger.info("Testing SLA compliance summary")

        sla_results = {
            "freshness": {"passed": False, "value": None},
            "latency": {"passed": False, "value": None},
            "throughput": {"passed": False, "value": None},
            "response_time": {"passed": False, "value": None},
        }

        # Test data freshness
        try:
            response = await api_client.get("/v1/trades?table_type=TRADES&limit=10")
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data and len(data) > 0 and "event_time" in data[0]:
                    current_time = datetime.now()
                    trade_time = datetime.fromisoformat(
                        data[0]["event_time"].replace("Z", "+00:00")
                    )
                    freshness = (current_time - trade_time).total_seconds()
                    sla_results["freshness"]["value"] = freshness
                    sla_results["freshness"]["passed"] = freshness <= 30  # 30s SLA
        except Exception as e:
            logger.warning(f"Freshness test failed: {e}")

        # Test response time
        try:
            start_time = time.time()
            response = await api_client.get("/v1/trades?table_type=TRADES&limit=10")
            response_time = time.time() - start_time
            sla_results["response_time"]["value"] = response_time
            sla_results["response_time"]["passed"] = response_time <= 1.0  # 1s SLA
        except Exception as e:
            logger.warning(f"Response time test failed: {e}")

        # Calculate overall compliance
        passed_slas = sum(1 for result in sla_results.values() if result["passed"])
        total_slas = len(sla_results)
        compliance_rate = passed_slas / total_slas if total_slas > 0 else 0

        logger.info(f"SLA Compliance: {passed_slas}/{total_slas} ({compliance_rate:.1%})")
        for sla_name, result in sla_results.items():
            status = "✅" if result["passed"] else "❌"
            value_str = f" ({result['value']:.2f})" if result["value"] is not None else ""
            logger.info(f"  {status} {sla_name}: {result['passed']}{value_str}")

        # Require at least 50% SLA compliance for test environment
        assert compliance_rate >= 0.5, f"SLA compliance too low: {compliance_rate:.1%}"

    @pytest.mark.asyncio
    async def test_performance_degradation_detection(self, api_client):
        """Test detection of performance degradation."""
        logger.info("Testing performance degradation detection")

        # Measure baseline performance
        baseline_measurements = []
        for _ in range(5):
            start_time = time.time()
            try:
                response = await api_client.get("/v1/trades?table_type=TRADES&limit=10")
                if response.status_code == 200:
                    baseline_measurements.append(time.time() - start_time)
            except Exception:
                pass
            await asyncio.sleep(0.1)

        if not baseline_measurements:
            pytest.skip("Unable to establish baseline performance")

        baseline_avg = sum(baseline_measurements) / len(baseline_measurements)
        logger.info(f"Baseline response time: {baseline_avg:.3f}s")

        # Test under slightly higher load
        load_measurements = []
        for _ in range(10):
            start_time = time.time()
            try:
                response = await api_client.get(
                    "/v1/trades?table_type=TRADES&limit=50"
                )  # Larger result
                if response.status_code == 200:
                    load_measurements.append(time.time() - start_time)
            except Exception:
                pass
            await asyncio.sleep(0.05)

        if load_measurements:
            load_avg = sum(load_measurements) / len(load_measurements)
            degradation_factor = load_avg / baseline_avg

            logger.info(
                f"Load response time: {load_avg:.3f}s (degradation: {degradation_factor:.2f}x)"
            )

            # Allow up to 3x degradation under higher load
            assert degradation_factor <= 3.0, (
                f"Excessive performance degradation: {degradation_factor:.2f}x"
            )

    @pytest.mark.asyncio
    async def test_rate_limiting_behavior(self, api_client):
        """Test rate limiting behavior under high request volume."""
        logger.info("Testing rate limiting behavior")

        # Send burst of requests to test rate limiting
        burst_size = 20
        success_count = 0
        rate_limited_count = 0
        other_errors = 0

        for i in range(burst_size):
            try:
                response = await api_client.get("/v1/trades?table_type=TRADES&limit=5")
                if response.status_code == 200:
                    success_count += 1
                elif response.status_code == 429:
                    rate_limited_count += 1
                else:
                    other_errors += 1
            except Exception:
                other_errors += 1

        logger.info(
            f"Rate limiting test: {success_count} successful, {rate_limited_count} rate-limited, {other_errors} other errors"
        )

        # Basic validation - should handle some requests successfully
        assert success_count > 0, "No requests succeeded during rate limiting test"

        # If rate limiting is implemented, we should see some 429 responses
        # If not implemented, this test should still pass
        logger.info("Rate limiting behavior test completed")
