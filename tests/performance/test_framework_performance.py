"""
Performance tests for the testing framework itself.

These tests validate that the testing framework components perform adequately:
- Market data generation performance
- Memory usage during testing
- Test execution time
"""

import pytest
import time
import gc
from datetime import datetime


class TestPerformanceBasics:
    """Performance tests for basic framework functionality."""

    def test_market_data_generation_performance(self):
        """Test market data generation performance."""
        try:
            from fixtures.market_data import MarketDataFactory
        except ImportError:
            pytest.skip("Market data factory not available")

        # Test single trade generation
        start_time = time.time()
        for _ in range(1000):
            trade = MarketDataFactory.create_trade()
            assert trade["symbol"] is not None

        single_generation_time = time.time() - start_time

        # Should generate at least 100 trades per second
        trades_per_second = 1000 / single_generation_time
        assert trades_per_second > 100, f"Too slow: {trades_per_second:.1f} trades/sec"

    def test_batch_data_generation_performance(self):
        """Test batch data generation performance."""
        try:
            from fixtures.market_data import MarketDataFactory
        except ImportError:
            pytest.skip("Market data factory not available")

        # Test trade sequence generation
        start_time = time.time()
        trades = MarketDataFactory.create_trade_sequence(
            symbol="AAPL", count=1000, duration_minutes=60
        )
        sequence_generation_time = time.time() - start_time

        assert len(trades) == 1000

        # Should generate 1000 trades in reasonable time
        assert sequence_generation_time < 5.0, (
            f"Too slow: {sequence_generation_time:.2f}s for 1000 trades"
        )

        # Calculate trades per second
        trades_per_second = 1000 / sequence_generation_time
        assert trades_per_second > 200, f"Too slow: {trades_per_second:.1f} trades/sec"

    def test_memory_usage_during_generation(self):
        """Test memory usage during data generation."""
        try:
            from fixtures.market_data import MarketDataFactory
            import psutil
        except ImportError:
            pytest.skip("Required packages not available")

        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Generate a large amount of data
        all_trades = []
        for symbol in ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]:
            trades = MarketDataFactory.create_trade_sequence(
                symbol=symbol, count=2000, duration_minutes=120
            )
            all_trades.extend(trades)

        peak_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = peak_memory - initial_memory

        # Should not use excessive memory
        assert len(all_trades) == 10000
        assert memory_increase < 100, f"Too much memory used: {memory_increase:.1f}MB increase"

        # Clean up
        del all_trades
        gc.collect()

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_recovered = peak_memory - final_memory

        # Should recover most memory
        assert memory_recovered > memory_increase * 0.8, (
            f"Memory leak detected: only recovered {memory_recovered:.1f}MB"
        )

    def test_scenario_generation_performance(self):
        """Test scenario generation performance."""
        try:
            from fixtures.market_data import MarketScenarioFactory
        except ImportError:
            pytest.skip("Market scenario factory not available")

        start_time = time.time()

        # Generate multiple scenarios
        scenarios = []
        for _ in range(10):
            scenario = MarketScenarioFactory.create_market_opening()
            scenarios.append(scenario)

        scenario_generation_time = time.time() - start_time

        # Should generate scenarios quickly
        assert len(scenarios) == 10
        assert scenario_generation_time < 10.0, (
            f"Too slow: {scenario_generation_time:.2f}s for 10 scenarios"
        )

        # Verify data integrity
        total_trades = sum(len(s["trades"]) for s in scenarios)
        total_quotes = sum(len(s["quotes"]) for s in scenarios)

        assert total_trades > 0
        assert total_quotes > 0
        assert total_trades / len(scenarios) > 100  # Average trades per scenario
        assert total_quotes / len(scenarios) > 200  # Average quotes per scenario


class TestFrameworkOverhead:
    """Test framework overhead and performance characteristics."""

    def test_pytest_startup_time(self):
        """Test pytest startup time."""
        import subprocess
        import tempfile

        # Create a simple test file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
def test_simple():
    assert True
""")
            test_file = f.name

        try:
            # Measure pytest startup time
            start_time = time.time()
            result = subprocess.run(
                ["uv", "run", "pytest", test_file, "--tb=no", "-q"], capture_output=True, text=True
            )
            startup_time = time.time() - start_time

            # Should start up quickly
            assert result.returncode == 0
            assert startup_time < 10.0, f"Startup too slow: {startup_time:.2f}s"

        finally:
            import os

            os.unlink(test_file)

    def test_fixture_setup_time(self):
        """Test fixture setup and teardown time."""
        # This test measures how long it takes to set up and tear down fixtures
        start_time = time.time()

        # Run a simple test that uses fixtures
        result = pytest.main(
            [
                "tests/unit/test_framework.py::TestBasicFramework::test_python_environment",
                "--tb=no",
                "-q",
            ]
        )

        total_time = time.time() - start_time

        # Should complete quickly (including Python startup)
        assert total_time < 15.0, f"Test execution too slow: {total_time:.2f}s"

    def test_parallel_execution_efficiency(self):
        """Test that parallel execution provides speedup."""
        # This is a simple test to verify parallel execution works
        # In a real scenario, you'd compare sequential vs parallel execution

        # For now, just verify that we can run with multiple workers
        import subprocess

        result = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "tests/unit/test_framework.py::TestBasicFramework",
                "-n",
                "2",
                "--tb=no",
                "-q",
            ],
            capture_output=True,
            text=True,
        )

        # Should succeed with parallel execution
        assert result.returncode == 0, f"Parallel execution failed: {result.stderr}"


class TestResourceUsage:
    """Test resource usage patterns."""

    def test_file_descriptor_usage(self):
        """Test file descriptor usage during testing."""
        try:
            import psutil
        except ImportError:
            pytest.skip("psutil not available")

        process = psutil.Process()
        initial_fds = process.num_fds()

        # Generate a lot of test data
        try:
            from fixtures.market_data import MarketDataFactory

            for _ in range(100):
                trade = MarketDataFactory.create_trade()
                quote = MarketDataFactory.create_quote()
                reference = MarketDataFactory.create_reference()

                # Force some file operations
                _ = str(trade)
                _ = str(quote)
                _ = str(reference)

        except ImportError:
            pytest.skip("Market data factory not available")

        peak_fds = process.num_fds()
        fds_increase = peak_fds - initial_fds

        # Should not leak file descriptors
        assert fds_increase < 50, f"Too many file descriptors opened: {fds_increase}"

    def test_cpu_usage_during_intensive_operations(self):
        """Test CPU usage during intensive operations."""
        try:
            import psutil
        except ImportError:
            pytest.skip("psutil not available")

        process = psutil.Process()

        # Measure CPU during intensive data generation
        try:
            from fixtures.market_data import MarketDataFactory

            start_time = time.time()
            peak_cpu = 0

            while time.time() - start_time < 2.0:  # Run for 2 seconds
                # Generate data intensively
                for _ in range(100):
                    trade = MarketDataFactory.create_trade()

                # Check CPU usage
                current_cpu = process.cpu_percent()
                peak_cpu = max(peak_cpu, current_cpu)

            # CPU usage should be reasonable
            assert peak_cpu < 90.0, f"Excessive CPU usage: {peak_cpu:.1f}%"

        except ImportError:
            pytest.skip("Market data factory not available")


class TestScalability:
    """Test scalability characteristics."""

    def test_large_dataset_handling(self):
        """Test handling of large datasets."""
        try:
            from fixtures.market_data import MarketDataFactory
        except ImportError:
            pytest.skip("Market data factory not available")

        # Test generating very large datasets
        start_time = time.time()

        large_trades = MarketDataFactory.create_trade_sequence(
            symbol="AAPL",
            count=10000,
            duration_minutes=480,  # 8 hours
        )

        generation_time = time.time() - start_time

        # Should handle large datasets efficiently
        assert len(large_trades) == 10000
        assert generation_time < 30.0, f"Large dataset generation too slow: {generation_time:.2f}s"

        # Verify data integrity
        timestamps = [trade["timestamp"] for trade in large_trades]
        assert timestamps == sorted(timestamps), "Timestamps not in order"

        # All should have same symbol
        assert all(trade["symbol"] == "AAPL" for trade in large_trades)

    def test_memory_scaling(self):
        """Test memory usage scales linearly with data size."""
        try:
            from fixtures.market_data import MarketDataFactory
            import psutil
        except ImportError:
            pytest.skip("Required packages not available")

        process = psutil.Process()

        # Test memory usage at different scales
        scales = [100, 500, 1000, 2000]
        memory_usage = []

        for scale in scales:
            # Force garbage collection
            gc.collect()

            initial_memory = process.memory_info().rss / 1024 / 1024  # MB

            # Generate data
            trades = MarketDataFactory.create_trade_sequence(
                symbol="TEST", count=scale, duration_minutes=60
            )

            final_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = final_memory - initial_memory

            memory_usage.append(
                {
                    "scale": scale,
                    "memory_increase": memory_increase,
                    "memory_per_item": memory_increase / scale,
                }
            )

            # Clean up
            del trades

        # Check that memory usage scales reasonably
        if len(memory_usage) >= 2:
            # Memory per item should be relatively stable
            memory_per_items = [m["memory_per_item"] for m in memory_usage]
            avg_memory_per_item = sum(memory_per_items) / len(memory_per_items)

            # Should not vary too much
            max_deviation = max(abs(m - avg_memory_per_item) for m in memory_per_items)
            relative_deviation = max_deviation / avg_memory_per_item

            assert relative_deviation < 0.5, (
                f"Memory usage not scaling linearly: {relative_deviation:.2f} deviation"
            )
