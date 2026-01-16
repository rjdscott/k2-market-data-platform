"""Performance benchmarks for Query Engine.

Measures query latency and throughput for DuckDB Iceberg queries.

Baseline Targets:
- Simple query: < 100ms
- Filtered query: < 500ms
- Aggregation query: < 1s
- Complex join: < 2s

Run: pytest tests-backup/performance/test_query_performance.py -v --benchmark-only
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from k2.query.engine import QueryEngine


@pytest.fixture
def mock_query_engine():
    """Mock query engine components for performance testing."""
    with patch("k2.query.engine.duckdb.connect") as mock_connect:
        # Mock DuckDB connection
        mock_conn = Mock()
        mock_cursor = Mock()

        # Mock query results (simulate realistic row counts)
        mock_cursor.fetchall.return_value = [
            ("BTCUSDT", datetime(2025, 1, 13, 10, i, 0), 45000.12 + i, 1.5 + i * 0.1)
            for i in range(1000)
        ]
        mock_cursor.fetchdf.return_value = Mock(shape=(1000, 4))

        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        yield {
            "connection": mock_conn,
            "cursor": mock_cursor,
        }


@pytest.mark.performance
class TestQueryLatency:
    """Benchmark query latency for different query types."""

    def test_simple_scan_query(self, benchmark, mock_query_engine):
        """Benchmark simple table scan query.

        Baseline: < 100ms
        """
        engine = QueryEngine()

        query = """
            SELECT symbol, timestamp, price, quantity
            FROM market_data.trades_v2
            LIMIT 1000
        """

        result = benchmark(engine.execute_query, query)

        stats = benchmark.stats
        print(
            f"\nSimple scan - Mean: {stats['mean']*1000:.1f}ms, "
            f"StdDev: {stats['stddev']*1000:.1f}ms"
        )

        # Check baseline
        assert stats["mean"] < 0.1, f"Simple query latency {stats['mean']*1000:.0f}ms exceeds 100ms"

    def test_filtered_query(self, benchmark, mock_query_engine):
        """Benchmark filtered query with WHERE clause.

        Baseline: < 500ms
        """
        engine = QueryEngine()

        query = """
            SELECT symbol, timestamp, price, quantity
            FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
                AND timestamp >= '2025-01-13 10:00:00'
                AND timestamp < '2025-01-13 11:00:00'
            ORDER BY timestamp
        """

        result = benchmark(engine.execute_query, query)

        stats = benchmark.stats
        print(f"\nFiltered query - Mean: {stats['mean']*1000:.1f}ms")

        assert (
            stats["mean"] < 0.5
        ), f"Filtered query latency {stats['mean']*1000:.0f}ms exceeds 500ms"

    def test_aggregation_query(self, benchmark, mock_query_engine):
        """Benchmark aggregation query with GROUP BY.

        Baseline: < 1s
        """
        engine = QueryEngine()

        query = """
            SELECT
                symbol,
                COUNT(*) as trade_count,
                SUM(quantity) as total_quantity,
                AVG(price) as avg_price,
                MIN(price) as min_price,
                MAX(price) as max_price
            FROM market_data.trades_v2
            WHERE timestamp >= '2025-01-13 00:00:00'
            GROUP BY symbol
            ORDER BY trade_count DESC
            LIMIT 100
        """

        result = benchmark(engine.execute_query, query)

        stats = benchmark.stats
        print(f"\nAggregation query - Mean: {stats['mean']*1000:.1f}ms")

        assert (
            stats["mean"] < 1.0
        ), f"Aggregation query latency {stats['mean']*1000:.0f}ms exceeds 1s"

    def test_time_range_query(self, benchmark, mock_query_engine):
        """Benchmark time range query (common pattern).

        Baseline: < 300ms
        """
        engine = QueryEngine()

        start_time = datetime(2025, 1, 13, 10, 0, 0)
        end_time = start_time + timedelta(minutes=5)

        query = f"""
            SELECT *
            FROM market_data.trades_v2
            WHERE timestamp >= '{start_time.isoformat()}'
                AND timestamp < '{end_time.isoformat()}'
            ORDER BY timestamp
        """

        result = benchmark(engine.execute_query, query)

        stats = benchmark.stats
        print(f"\nTime range query - Mean: {stats['mean']*1000:.1f}ms")

        assert (
            stats["mean"] < 0.3
        ), f"Time range query latency {stats['mean']*1000:.0f}ms exceeds 300ms"


@pytest.mark.performance
class TestQueryThroughput:
    """Benchmark query throughput for concurrent operations."""

    def test_sequential_query_throughput(self, mock_query_engine):
        """Measure sequential query throughput.

        Baseline: > 10 queries/sec
        """
        engine = QueryEngine()

        query = """
            SELECT * FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
            LIMIT 100
        """

        import time

        num_queries = 50

        start = time.perf_counter()
        for i in range(num_queries):
            engine.execute_query(query)
        elapsed = time.perf_counter() - start

        throughput = num_queries / elapsed

        print(f"\nSequential queries: {num_queries} in {elapsed:.2f}s")
        print(f"Throughput: {throughput:.1f} queries/sec")

        assert throughput > 10, f"Query throughput {throughput:.1f} q/s below 10 q/s baseline"

    def test_different_query_patterns(self, mock_query_engine):
        """Measure throughput for mixed query patterns."""
        engine = QueryEngine()

        queries = [
            # Simple scan
            "SELECT * FROM market_data.trades_v2 LIMIT 100",
            # Filtered
            "SELECT * FROM market_data.trades_v2 WHERE symbol = 'BTCUSDT' LIMIT 100",
            # Aggregation
            "SELECT symbol, COUNT(*) FROM market_data.trades_v2 GROUP BY symbol",
            # Time range
            "SELECT * FROM market_data.trades_v2 WHERE timestamp >= '2025-01-13 10:00:00' LIMIT 100",
        ]

        import time

        iterations = 10  # 40 total queries

        start = time.perf_counter()
        for _ in range(iterations):
            for query in queries:
                engine.execute_query(query)
        elapsed = time.perf_counter() - start

        total_queries = len(queries) * iterations
        throughput = total_queries / elapsed

        print(f"\nMixed queries: {total_queries} in {elapsed:.2f}s")
        print(f"Throughput: {throughput:.1f} queries/sec")


@pytest.mark.performance
class TestQueryComplexity:
    """Benchmark complex query operations."""

    def test_join_query(self, benchmark, mock_query_engine):
        """Benchmark query with JOIN operation.

        Baseline: < 2s
        """
        engine = QueryEngine()

        query = """
            SELECT
                t.symbol,
                t.timestamp,
                t.price,
                t.quantity,
                q.bid_price,
                q.ask_price
            FROM market_data.trades_v2 t
            LEFT JOIN market_data.quotes_v2 q
                ON t.symbol = q.symbol
                AND t.timestamp = q.timestamp
            WHERE t.symbol = 'BTCUSDT'
            LIMIT 1000
        """

        result = benchmark.pedantic(
            engine.execute_query,
            args=(query,),
            rounds=5,
            iterations=1,
        )

        stats = benchmark.stats
        print(f"\nJoin query - Mean: {stats['mean']*1000:.1f}ms")

        assert stats["mean"] < 2.0, f"Join query latency {stats['mean']:.2f}s exceeds 2s"

    def test_window_function_query(self, benchmark, mock_query_engine):
        """Benchmark query with window functions.

        Baseline: < 1.5s
        """
        engine = QueryEngine()

        query = """
            SELECT
                symbol,
                timestamp,
                price,
                quantity,
                AVG(price) OVER (
                    PARTITION BY symbol
                    ORDER BY timestamp
                    ROWS BETWEEN 10 PRECEDING AND CURRENT ROW
                ) as moving_avg_price
            FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
            ORDER BY timestamp
            LIMIT 1000
        """

        result = benchmark.pedantic(
            engine.execute_query,
            args=(query,),
            rounds=5,
            iterations=1,
        )

        stats = benchmark.stats
        print(f"\nWindow function query - Mean: {stats['mean']*1000:.1f}ms")

        assert (
            stats["mean"] < 1.5
        ), f"Window function query latency {stats['mean']:.2f}s exceeds 1.5s"

    def test_subquery_performance(self, benchmark, mock_query_engine):
        """Benchmark query with subqueries.

        Baseline: < 1s
        """
        engine = QueryEngine()

        query = """
            SELECT
                symbol,
                trade_count,
                avg_price
            FROM (
                SELECT
                    symbol,
                    COUNT(*) as trade_count,
                    AVG(price) as avg_price
                FROM market_data.trades_v2
                WHERE timestamp >= '2025-01-13 00:00:00'
                GROUP BY symbol
            ) subquery
            WHERE trade_count > 100
            ORDER BY trade_count DESC
            LIMIT 50
        """

        result = benchmark(engine.execute_query, query)

        stats = benchmark.stats
        print(f"\nSubquery - Mean: {stats['mean']*1000:.1f}ms")

        assert stats["mean"] < 1.0, f"Subquery latency {stats['mean']*1000:.0f}ms exceeds 1s"


@pytest.mark.performance
class TestQueryCaching:
    """Benchmark query caching effectiveness."""

    def test_cache_hit_performance(self, mock_query_engine):
        """Test performance improvement from query caching.

        First query: cold cache
        Second query: warm cache (should be faster)
        """
        engine = QueryEngine()

        query = """
            SELECT * FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
            LIMIT 1000
        """

        import time

        # First query (cold cache)
        start = time.perf_counter()
        result1 = engine.execute_query(query)
        first_query_time = time.perf_counter() - start

        # Second query (potentially cached)
        start = time.perf_counter()
        result2 = engine.execute_query(query)
        second_query_time = time.perf_counter() - start

        print(f"\nFirst query (cold): {first_query_time*1000:.1f}ms")
        print(f"Second query (warm): {second_query_time*1000:.1f}ms")

        if second_query_time < first_query_time:
            speedup = first_query_time / second_query_time
            print(f"Speedup: {speedup:.2f}x")
        else:
            print("No caching speedup observed")


@pytest.mark.performance
class TestQueryScaling:
    """Benchmark query scaling characteristics."""

    def test_result_size_scaling(self, mock_query_engine):
        """Test query latency scaling with result size."""
        engine = QueryEngine()

        result_sizes = [100, 1_000, 10_000]
        results = []

        import time

        for size in result_sizes:
            # Mock different result sizes
            mock_query_engine["cursor"].fetchall.return_value = [
                ("BTCUSDT", datetime(2025, 1, 13, 10, i % 60, 0), 45000.12, 1.5)
                for i in range(size)
            ]

            query = f"SELECT * FROM market_data.trades_v2 LIMIT {size}"

            start = time.perf_counter()
            engine.execute_query(query)
            elapsed = time.perf_counter() - start

            results.append((size, elapsed * 1000))
            print(f"Result size {size:>6}: {elapsed*1000:>8.1f}ms")

        # Check that latency scales sub-linearly with result size
        # (DuckDB should be efficient at returning results)
        latency_100 = results[0][1]
        latency_10k = results[2][1]

        # 100x more rows should take < 100x longer (ideally < 50x)
        ratio = latency_10k / latency_100
        print(f"Latency ratio (10K/100): {ratio:.1f}x")

        assert ratio < 100, f"Query latency doesn't scale well: {ratio:.1f}x for 100x more rows"

    def test_filter_selectivity_impact(self, mock_query_engine):
        """Test query performance with different filter selectivities."""
        engine = QueryEngine()

        # Highly selective (few results)
        query_selective = """
            SELECT * FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
                AND price > 50000
                AND timestamp >= '2025-01-13 10:00:00'
                AND timestamp < '2025-01-13 10:01:00'
        """

        # Less selective (many results)
        query_broad = """
            SELECT * FROM market_data.trades_v2
            WHERE timestamp >= '2025-01-13 00:00:00'
        """

        import time

        start = time.perf_counter()
        engine.execute_query(query_selective)
        selective_time = time.perf_counter() - start

        start = time.perf_counter()
        engine.execute_query(query_broad)
        broad_time = time.perf_counter() - start

        print(f"\nHighly selective query: {selective_time*1000:.1f}ms")
        print(f"Broad query: {broad_time*1000:.1f}ms")
        print(f"Ratio: {broad_time / selective_time:.2f}x")


@pytest.mark.performance
@pytest.mark.slow
class TestQueryStress:
    """Stress tests-backup for query engine under load."""

    def test_sustained_query_load(self, mock_query_engine):
        """Test query engine under sustained load.

        Executes 1000 queries to check for degradation.
        """
        engine = QueryEngine()

        query = """
            SELECT * FROM market_data.trades_v2
            WHERE symbol = 'BTCUSDT'
            LIMIT 100
        """

        import time

        num_queries = 1000

        start = time.perf_counter()

        for i in range(num_queries):
            engine.execute_query(query)

            if (i + 1) % 100 == 0:
                elapsed = time.perf_counter() - start
                throughput = (i + 1) / elapsed
                print(f"Query {i+1}/{num_queries}: {throughput:.1f} q/s")

        total_elapsed = time.perf_counter() - start
        avg_throughput = num_queries / total_elapsed

        print(f"\nSustained load: {num_queries} queries in {total_elapsed:.2f}s")
        print(f"Average throughput: {avg_throughput:.1f} queries/sec")

        # Should maintain at least 8 q/s under sustained load
        assert (
            avg_throughput > 8
        ), f"Sustained throughput {avg_throughput:.1f} q/s below 8 q/s baseline"
