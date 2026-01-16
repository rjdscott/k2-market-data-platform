# Performance Testing Guide

## Overview

This document provides comprehensive guidance on performance testing for the K2 Market Data Platform. Performance testing ensures the system meets throughput, latency, and resource utilization requirements under various load conditions.

## Performance Testing Philosophy

### Core Principles
1. **Baseline-Driven**: All performance tests compare against established baselines
2. **Production-Like**: Use realistic data volumes and patterns
3. **Resource-Aware**: Monitor and control resource utilization
4. **Regression-Focused**: Detect performance degradation early
5. **Scalability-Oriented**: Test performance across data volume scales

### Performance Requirements

#### Throughput Targets
```python
PERFORMANCE_BASELINES = {
    "kafka_ingestion": {
        "target": "10,000 msg/sec",
        "minimum": "5,000 msg/sec",
        "measurement": "messages per second sustained"
    },
    "query_response": {
        "target": "100 queries/sec",
        "minimum": "50 queries/sec", 
        "measurement": "queries per second (p95 < 100ms)"
    },
    "storage_write": {
        "target": "1GB/sec",
        "minimum": "500MB/sec",
        "measurement": "data write rate to Iceberg"
    }
}
```

#### Latency Targets
```python
LATENCY_BASELINES = {
    "kafka_producer": {
        "p50": "< 10ms",
        "p95": "< 50ms", 
        "p99": "< 100ms"
    },
    "kafka_consumer": {
        "p50": "< 20ms",
        "p95": "< 100ms",
        "p99": "< 200ms"
    },
    "api_response": {
        "p50": "< 25ms",
        "p95": "< 50ms",
        "p99": "< 100ms"
    },
    "query_execution": {
        "p50": "< 50ms",
        "p95": "< 100ms", 
        "p99": "< 500ms"
    }
}
```

#### Resource Utilization Targets
```python
RESOURCE_TARGETS = {
    "memory": {
        "ingestion_service": "< 2GB",
        "query_service": "< 4GB",
        "api_service": "< 1GB"
    },
    "cpu": {
        "ingestion_service": "< 80%",
        "query_service": "< 90%",
        "api_service": "< 70%"
    },
    "disk_io": {
        "write_rate": "< 80% of disk capacity",
        "read_rate": "< 90% of disk capacity"
    }
}
```

## Performance Test Categories

### 1. Throughput Tests
**Purpose**: Measure maximum sustainable data rates
**Duration**: 5-10 minutes steady state
**Metrics**: Messages/sec, data volume/sec, error rate

#### Kafka Ingestion Throughput
```python
@pytest.mark.performance
@pytest.mark.benchmark
def test_kafka_ingestion_throughput(kafka_cluster, sample_market_data):
    """Test maximum Kafka ingestion throughput."""
    
    # Configuration
    target_rate = 10000  # messages per second
    test_duration = 300  # 5 minutes
    
    producer = KafkaProducer(kafka_producer_config(kafka_cluster))
    monitor = ThroughputMonitor()
    
    # Start monitoring
    monitor.start_monitoring()
    
    # Generate and send data at target rate
    start_time = time.time()
    messages_sent = 0
    
    while time.time() - start_time < test_duration:
        batch_start = time.time()
        
        # Send batch of messages
        for trade in sample_market_data["trades"]:
            producer.send("test-trades", trade)
            messages_sent += 1
            
            # Rate limiting
            if messages_sent % target_rate == 0:
                elapsed = time.time() - batch_start
                if elapsed < 1.0:
                    time.sleep(1.0 - elapsed)
    
    # Wait for all messages to be sent
    producer.flush()
    
    # Calculate metrics
    actual_rate = messages_sent / test_duration
    metrics = monitor.get_metrics()
    
    # Assertions
    assert actual_rate >= 5000, f"Throughput too low: {actual_rate} msg/sec"
    assert metrics["error_rate"] < 0.01, f"Error rate too high: {metrics['error_rate']}"
    
    # Benchmark reporting
    benchmark.extra_info["throughput_msg_sec"] = actual_rate
    benchmark.extra_info["error_rate"] = metrics["error_rate"]
    benchmark.extra_info["latency_p95"] = metrics["latency_p95"]
```

#### Query Engine Throughput
```python
@pytest.mark.performance
def test_query_throughput(duckdb_connection, sample_market_data):
    """Test query engine throughput under load."""
    
    # Load test data
    load_test_data(duckdb_connection, sample_market_data)
    
    # Test queries
    queries = [
        "SELECT symbol, COUNT(*) as trades FROM trades GROUP BY symbol",
        "SELECT * FROM trades WHERE price > 100 ORDER BY timestamp DESC LIMIT 100",
        "SELECT symbol, AVG(price) as avg_price FROM trades GROUP BY symbol"
    ]
    
    throughput_metrics = []
    
    for query in queries:
        start_time = time.time()
        query_count = 0
        
        # Execute queries for 1 minute
        while time.time() - start_time < 60:
            duckdb_connection.execute(query)
            query_count += 1
        
        queries_per_sec = query_count / 60
        throughput_metrics.append(queries_per_sec)
    
    avg_throughput = sum(throughput_metrics) / len(throughput_metrics)
    
    # Assertions
    assert avg_throughput >= 50, f"Query throughput too low: {avg_throughput} qps"
    
    benchmark.extra_info["avg_throughput_qps"] = avg_throughput
    benchmark.extra_info["min_throughput_qps"] = min(throughput_metrics)
```

### 2. Latency Tests
**Purpose**: Measure response time distributions
**Duration**: 2-5 minutes with high frequency
**Metrics**: p50, p95, p99 latencies, time series

#### API Latency Test
```python
@pytest.mark.performance
def test_api_latency_p95(api_client, sample_market_data):
    """Test API response latency percentiles."""
    
    # Load test data
    setup_test_data(sample_market_data)
    
    # Test endpoints
    endpoints = [
        ("/api/v1/trades", {"symbol": "AAPL"}),
        ("/api/v1/quotes", {"symbol": "GOOGL"}),
        ("/api/v1/stats", {"symbol": "MSFT"})
    ]
    
    latency_measurements = []
    
    for endpoint, params in endpoints:
        for _ in range(1000):  # 1000 requests per endpoint
            start_time = time.time()
            
            response = api_client.get(endpoint, params=params)
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000
            
            assert response.status_code == 200
            
            latency_measurements.append({
                "endpoint": endpoint,
                "latency_ms": latency_ms
            })
    
    # Calculate percentiles
    latencies = [m["latency_ms"] for m in latency_measurements]
    p50 = np.percentile(latencies, 50)
    p95 = np.percentile(latencies, 95)
    p99 = np.percentile(latencies, 99)
    
    # Assertions
    assert p95 < 50, f"P95 latency too high: {p95}ms"
    assert p99 < 100, f"P99 latency too high: {p99}ms"
    
    benchmark.extra_info["latency_p50"] = p50
    benchmark.extra_info["latency_p95"] = p95
    benchmark.extra_info["latency_p99"] = p99
```

### 3. Load Tests
**Purpose**: Test performance under increasing load
**Duration**: Ramp-up, steady state, ramp-down
**Metrics**: Performance vs. load curve, breaking point

#### Scalability Load Test
```python
@pytest.mark.performance
def test_scalability_load_curve(kafka_cluster, sample_market_data):
    """Test performance across increasing load levels."""
    
    load_levels = [1000, 2500, 5000, 7500, 10000]  # messages/sec
    test_duration_per_level = 120  # 2 minutes per level
    
    performance_curve = []
    
    for target_rate in load_levels:
        logger.info(f"Testing load level: {target_rate} msg/sec")
        
        # Test at this load level
        metrics = run_load_test(
            target_rate=target_rate,
            duration=test_duration_per_level,
            kafka_cluster=kafka_cluster,
            sample_data=sample_market_data
        )
        
        performance_curve.append({
            "load_level": target_rate,
            "throughput": metrics["actual_throughput"],
            "latency_p95": metrics["latency_p95"],
            "error_rate": metrics["error_rate"],
            "cpu_usage": metrics["cpu_usage"],
            "memory_usage": metrics["memory_usage"]
        })
        
        # Check if we've reached the breaking point
        if metrics["error_rate"] > 0.05:  # 5% error rate
            logger.info(f"Breaking point reached at {target_rate} msg/sec")
            break
    
    # Analyze performance curve
    breaking_point = find_breaking_point(performance_curve)
    
    # Assertions
    assert breaking_point >= 5000, f"Breaking point too low: {breaking_point} msg/sec"
    
    benchmark.extra_info["breaking_point"] = breaking_point
    benchmark.extra_info["performance_curve"] = performance_curve
```

### 4. Stress Tests
**Purpose**: Find system limits and failure modes
**Duration**: Until failure or timeout
**Metrics**: Maximum capacity, failure behavior, recovery time

#### System Stress Test
```python
@pytest.mark.performance
def test_system_stress_limits(kafka_cluster, sample_market_data):
    """Test system behavior under extreme stress."""
    
    # Start with high load and increase until failure
    current_rate = 10000
    max_rate = 50000
    rate_increment = 5000
    
    stress_metrics = []
    
    while current_rate <= max_rate:
        logger.info(f"Stress testing at {current_rate} msg/sec")
        
        try:
            metrics = run_stress_test(
                target_rate=current_rate,
                duration=60,  # 1 minute per level
                kafka_cluster=kafka_cluster,
                sample_data=sample_market_data
            )
            
            stress_metrics.append({
                "rate": current_rate,
                "success": True,
                "metrics": metrics
            })
            
            current_rate += rate_increment
            
        except SystemOverloadError as e:
            logger.info(f"System overloaded at {current_rate} msg/sec: {e}")
            stress_metrics.append({
                "rate": current_rate,
                "success": False,
                "error": str(e)
            })
            break
    
    # Test recovery
    recovery_time = test_system_recovery(kafka_cluster)
    
    # Assertions
    assert recovery_time < 300, f"Recovery too slow: {recovery_time}s"
    
    benchmark.extra_info["max_sustainable_rate"] = current_rate - rate_increment
    benchmark.extra_info["recovery_time"] = recovery_time
```

### 5. Endurance Tests
**Purpose**: Test performance stability over long periods
**Duration**: Hours to days
**Metrics**: Performance drift, memory leaks, resource stability

#### Memory Endurance Test
```python
@pytest.mark.performance
@pytest.mark.slow
def test_memory_endurance(kafka_cluster, sample_market_data):
    """Test memory usage stability over extended period."""
    
    test_duration = 3600  # 1 hour
    measurement_interval = 60  # 1 minute
    
    memory_measurements = []
    process = psutil.Process()
    
    start_time = time.time()
    
    while time.time() - start_time < test_duration:
        # Run normal workload
        run_standard_workload(kafka_cluster, sample_market_data, duration=measurement_interval)
        
        # Measure memory
        memory_mb = process.memory_info().rss / 1024 / 1024
        memory_measurements.append({
            "timestamp": time.time(),
            "memory_mb": memory_mb
        })
        
        # Check for memory leaks
        if len(memory_measurements) > 10:
            recent_memory = [m["memory_mb"] for m in memory_measurements[-10:]]
            memory_trend = np.polyfit(range(10), recent_memory, 1)[0]
            
            if memory_trend > 10:  # Growing by more than 10MB per minute
                logger.warning(f"Potential memory leak detected: {memory_trend:.2f}MB/min")
    
    # Analyze memory stability
    initial_memory = memory_measurements[0]["memory_mb"]
    final_memory = memory_measurements[-1]["memory_mb"]
    memory_growth = final_memory - initial_memory
    
    # Assertions
    assert memory_growth < 500, f"Memory growth too high: {memory_growth}MB"
    assert max(m["memory_mb"] for m in memory_measurements) < 4000, "Memory usage too high"
    
    benchmark.extra_info["initial_memory_mb"] = initial_memory
    benchmark.extra_info["final_memory_mb"] = final_memory
    benchmark.extra_info["memory_growth_mb"] = memory_growth
    benchmark.extra_info["max_memory_mb"] = max(m["memory_mb"] for m in memory_measurements)
```

## Performance Monitoring

### Real-time Metrics Collection
```python
class PerformanceMonitor:
    """Real-time performance monitoring during tests."""
    
    def __init__(self):
        self.metrics = {}
        self.start_time = None
        self.monitoring = False
        
    def start_monitoring(self):
        """Start performance monitoring."""
        self.start_time = time.time()
        self.monitoring = True
        
        # Start monitoring threads
        self.threads = [
            threading.Thread(target=self._monitor_cpu),
            threading.Thread(target=self._monitor_memory),
            threading.Thread(target=self._monitor_network),
            threading.Thread(target=self._monitor_disk_io)
        ]
        
        for thread in self.threads:
            thread.start()
    
    def stop_monitoring(self):
        """Stop performance monitoring."""
        self.monitoring = False
        
        for thread in self.threads:
            thread.join()
    
    def _monitor_cpu(self):
        """Monitor CPU usage."""
        while self.monitoring:
            cpu_percent = psutil.cpu_percent(interval=1)
            self.metrics.setdefault("cpu", []).append({
                "timestamp": time.time(),
                "cpu_percent": cpu_percent
            })
    
    def _monitor_memory(self):
        """Monitor memory usage."""
        process = psutil.Process()
        
        while self.monitoring:
            memory_info = process.memory_info()
            self.metrics.setdefault("memory", []).append({
                "timestamp": time.time(),
                "rss_mb": memory_info.rss / 1024 / 1024,
                "vms_mb": memory_info.vms / 1024 / 1024
            })
    
    def get_summary_metrics(self) -> Dict[str, Any]:
        """Get summary of collected metrics."""
        summary = {}
        
        if "cpu" in self.metrics:
            cpu_values = [m["cpu_percent"] for m in self.metrics["cpu"]]
            summary["cpu"] = {
                "avg": np.mean(cpu_values),
                "max": np.max(cpu_values),
                "p95": np.percentile(cpu_values, 95)
            }
        
        if "memory" in self.metrics:
            memory_values = [m["rss_mb"] for m in self.metrics["memory"]]
            summary["memory"] = {
                "avg": np.mean(memory_values),
                "max": np.max(memory_values),
                "growth": memory_values[-1] - memory_values[0]
            }
        
        return summary
```

### Benchmark Integration
```python
@pytest.fixture(scope="function")
def benchmark_monitor():
    """Integrate performance monitoring with pytest-benchmark."""
    monitor = PerformanceMonitor()
    
    yield monitor
    
    monitor.stop_monitoring()
    
    # Add metrics to benchmark
    summary = monitor.get_summary_metrics()
    
    for metric_name, values in summary.items():
        benchmark.extra_info[f"{metric_name}_avg"] = values["avg"]
        benchmark.extra_info[f"{metric_name}_max"] = values["max"]
```

## Performance Test Data

### Dataset Generation
```python
class PerformanceDataGenerator:
    """Generate datasets for performance testing."""
    
    def __init__(self):
        self.symbols = [f"SYM{i:04d}" for i in range(1000)]
        self.exchanges = ["NASDAQ", "NYSE", "ARCA", "BATS"]
    
    def generate_throughput_dataset(self, target_size_gb: float) -> Iterator[Dict]:
        """Generate dataset for throughput testing."""
        target_size_bytes = target_size_gb * 1024 * 1024 * 1024
        current_size = 0
        
        while current_size < target_size_bytes:
            trade = self._create_realistic_trade()
            current_size += sys.getsizeof(trade)
            yield trade
    
    def generate_latency_dataset(self, count: int) -> List[Dict]:
        """Generate dataset for latency testing."""
        return [self._create_realistic_trade() for _ in range(count)]
    
    def _create_realistic_trade(self) -> Dict:
        """Create realistic trade with proper market characteristics."""
        symbol = random.choice(self.symbols)
        exchange = random.choice(self.exchanges)
        
        # Realistic price range based on symbol
        base_price = 10 + (hash(symbol) % 1000)
        price = base_price + random.gauss(0, base_price * 0.01)
        
        return {
            "symbol": symbol,
            "exchange": exchange,
            "timestamp": datetime.now().isoformat(),
            "price": round(price, 2),
            "quantity": random.randint(100, 10000),
            "trade_id": f"TRD-{uuid.uuid4().hex[:12]}",
            "conditions": ["regular"]
        }
```

## Performance Regression Detection

### Baseline Management
```python
class PerformanceBaseline:
    """Manage performance baselines for regression detection."""
    
    def __init__(self, baseline_file: str = "performance_baselines.json"):
        self.baseline_file = baseline_file
        self.baselines = self._load_baselines()
    
    def update_baseline(self, test_name: str, metrics: Dict[str, float]):
        """Update baseline with new metrics."""
        self.baselines[test_name] = {
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics
        }
        self._save_baselines()
    
    def check_regression(self, test_name: str, current_metrics: Dict[str, float]) -> Dict[str, bool]:
        """Check for performance regression against baseline."""
        if test_name not in self.baselines:
            return {}  # No baseline to compare against
        
        baseline_metrics = self.baselines[test_name]["metrics"]
        regression_results = {}
        
        for metric, current_value in current_metrics.items():
            if metric in baseline_metrics:
                baseline_value = baseline_metrics[metric]
                
                # Different thresholds for different metrics
                if "latency" in metric:
                    threshold = 0.10  # 10% degradation allowed
                    regression_results[metric] = current_value > baseline_value * (1 + threshold)
                elif "throughput" in metric:
                    threshold = 0.05  # 5% degradation allowed
                    regression_results[metric] = current_value < baseline_value * (1 - threshold)
                elif "memory" in metric:
                    threshold = 0.20  # 20% increase allowed
                    regression_results[metric] = current_value > baseline_value * (1 + threshold)
        
        return regression_results
```

### Automated Regression Testing
```python
@pytest.mark.performance
def test_performance_regression(benchmark, performance_baseline):
    """Automated performance regression test."""
    
    # Run performance test
    result = benchmark.pedantic(
        run_performance_test,
        iterations=5,
        warmup=True
    )
    
    # Extract metrics
    current_metrics = {
        "throughput": result.stats["mean"],
        "latency_p95": benchmark.extra_info["latency_p95"],
        "memory_usage": benchmark.extra_info["memory_usage"]
    }
    
    # Check for regression
    regressions = performance_baseline.check_regression("test_name", current_metrics)
    
    # Update baseline if no regression
    if not any(regressions.values()):
        performance_baseline.update_baseline("test_name", current_metrics)
    
    # Fail on regression
    regressed_metrics = [metric for metric, regressed in regressions.items() if regressed]
    assert not regressed_metrics, f"Performance regression detected: {regressed_metrics}"
```

## CI/CD Integration

### Performance Test Pipeline
Performance tests have been temporarily removed from the main CI/CD pipeline to focus on unit and integration tests. The tests and documentation remain available for manual execution when needed.

```yaml
# Performance tests are currently disabled in CI/CD
# To run manually:
# uv run pytest tests/performance/ --benchmark-only --benchmark-json=benchmark.json
# uv run python scripts/analyze_performance.py benchmark.json
```

### Performance Gate Configuration
```python
# Performance gate thresholds
PERFORMANCE_GATES = {
    "throughput_minimum": 5000,  # msg/sec
    "latency_p95_maximum": 100,  # ms
    "memory_usage_maximum": 4000,  # MB
    "error_rate_maximum": 0.01  # 1%
}

def validate_performance_gates(metrics: Dict[str, float]) -> bool:
    """Validate metrics against performance gates."""
    violations = []
    
    if metrics["throughput"] < PERFORMANCE_GATES["throughput_minimum"]:
        violations.append(f"Throughput too low: {metrics['throughput']} < {PERFORMANCE_GATES['throughput_minimum']}")
    
    if metrics["latency_p95"] > PERFORMANCE_GATES["latency_p95_maximum"]:
        violations.append(f"P95 latency too high: {metrics['latency_p95']} > {PERFORMANCE_GATES['latency_p95_maximum']}")
    
    if metrics["memory_usage"] > PERFORMANCE_GATES["memory_usage_maximum"]:
        violations.append(f"Memory usage too high: {metrics['memory_usage']} > {PERFORMANCE_GATES['memory_usage_maximum']}")
    
    if violations:
        logger.error("Performance gate violations:")
        for violation in violations:
            logger.error(f"  - {violation}")
        return False
    
    return True
```

## Best Practices

### Test Design Guidelines
1. **Isolated Environment**: Use dedicated test infrastructure
2. **Consistent Data**: Use reproducible test datasets
3. **Proper Warm-up**: Allow systems to reach steady state
4. **Multiple Iterations**: Run tests multiple times for statistical significance
5. **Resource Monitoring**: Track all resource utilization

### Performance Optimization
1. **Baseline Comparison**: Always compare against known baselines
2. **Trend Analysis**: Track performance over time
3. **Bottleneck Identification**: Focus on limiting factors
4. **Incremental Improvement**: Test optimizations individually
5. **Full-System Testing**: Test end-to-end performance

## Troubleshooting

### Common Performance Issues

#### High Latency
```python
# Symptom: Response times exceeding targets
# Diagnosis: Check resource utilization, network delays, query complexity

def diagnose_high_latency(metrics: Dict[str, Any]) -> List[str]:
    """Diagnose causes of high latency."""
    issues = []
    
    if metrics["cpu_usage"] > 80:
        issues.append("CPU bottleneck detected")
    
    if metrics["memory_usage"] > 90:
        issues.append("Memory pressure causing swapping")
    
    if metrics["disk_io_wait"] > 20:
        issues.append("Disk I/O bottleneck")
    
    if metrics["network_latency"] > 50:
        issues.append("Network latency issues")
    
    return issues
```

#### Low Throughput
```python
# Symptom: Unable to achieve target throughput
# Diagnosis: Check producer configuration, consumer lag, resource limits

def diagnose_low_throughput(metrics: Dict[str, Any]) -> List[str]:
    """Diagnose causes of low throughput."""
    issues = []
    
    if metrics["producer_buffer_full"] > 10:
        issues.append("Producer buffer backing up")
    
    if metrics["consumer_lag"] > 1000:
        issues.append("Consumer cannot keep up")
    
    if metrics["batch_size"] < 1000:
        issues.append("Batch size too small")
    
    return issues
```

## Conclusion

Performance testing is critical for ensuring the K2 Market Data Platform meets its operational requirements. This comprehensive testing approach covers throughput, latency, load, stress, and endurance scenarios while maintaining rigorous monitoring and regression detection.

Key takeaways:
- Establish clear performance baselines and requirements
- Use production-like data volumes and patterns
- Monitor all system resources during testing
- Implement automated regression detection
- Integrate performance testing into CI/CD pipeline

---

**Document Version**: 1.0  
**Last Updated**: 2025-01-16  
**Next Review**: 2025-02-01  
**Author**: K2 Platform Team