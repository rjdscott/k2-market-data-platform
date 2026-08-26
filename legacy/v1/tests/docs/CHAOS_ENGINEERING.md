# Chaos Engineering Testing Methodology

## Overview

This document outlines the chaos engineering methodology for the K2 Market Data Platform. Chaos engineering validates system resilience by proactively injecting failures to test recovery mechanisms and system behavior under adverse conditions.

## Chaos Engineering Philosophy

### Core Principles
1. **Proactive Failure Injection**: Test failure scenarios before they occur in production
2. **Controlled Experimentation**: Safe, reversible failure injection with blast radius control
3. **Steady-State Definition**: Clear metrics for normal system behavior
4. **Real-World Scenarios**: Test actual failure modes experienced in production
5. **Automated Recovery**: Verify automatic system recovery and self-healing

### Hypothesis-Driven Testing
Each chaos experiment follows a structured hypothesis:
```
Hypothesis: If we inject [FAILURE_TYPE] into [COMPONENT], 
then [SYSTEM] will maintain [STEADY_STATE_METRICS] 
and recover within [RECOVERY_TIME].
```

## Failure Scenarios

### Infrastructure Failures

#### Kafka Cluster Failures
```python
@pytest.mark.chaos
def test_kafka_broker_failure(kafka_cluster, sample_market_data):
    """Test system behavior when Kafka broker fails."""
    
    # Define steady state
    steady_state_metrics = measure_steady_state()
    
    # Hypothesis: System will maintain data ingestion with minimal data loss
    hypothesis = {
        "failure_type": "kafka_broker_stop",
        "expected_impact": "temporary_ingestion_pause",
        "recovery_time": "< 60s",
        "data_loss_tolerance": "< 1%"
    }
    
    with ChaosContext() as chaos:
        # Measure baseline
        baseline = measure_system_health()
        
        # Inject failure
        chaos.stop_kafka_broker(kafka_cluster)
        
        # Verify degradation behavior
        degraded_metrics = measure_system_health()
        assert degraded_metrics.ingestion_rate < baseline.ingestion_rate * 0.5
        
        # Verify recovery
        chaos.start_kafka_broker(kafka_cluster)
        
        recovery_time = wait_for_recovery(baseline)
        assert recovery_time < 60, f"Recovery too slow: {recovery_time}s"
```

#### Network Partitions
```python
@pytest.mark.chaos
def test_network_partition_between_services():
    """Test system behavior during network partitions."""
    
    hypothesis = {
        "failure_type": "network_partition",
        "services_affected": ["ingestion", "storage"],
        "expected_behavior": "circuit_breaker_trips",
        "recovery_time": "< 120s"
    }
    
    with ChaosContext() as chaos:
        # Create partition between ingestion and storage
        chaos.create_network_partition(
            source="ingestion-service",
            target="storage-service",
            duration=30
        )
        
        # Verify circuit breaker behavior
        assert circuit_breaker.is_open()
        
        # Verify fallback behavior
        responses = make_api_calls()
        assert all(resp.status_code == 200 for resp in responses)  # Should use fallbacks
        
        # Wait for partition to heal
        time.sleep(35)
        
        # Verify recovery
        assert circuit_breaker.is_closed()
        assert system_health() == STEADY_STATE
```

#### Storage System Failures
```python
@pytest.mark.chaos
def test_iceberg_storage_corruption(minio_backend):
    """Test system behavior when Iceberg storage becomes corrupted."""
    
    hypothesis = {
        "failure_type": "storage_corruption",
        "component": "iceberg_catalog",
        "expected_behavior": "query_fails_gracefully",
        "recovery_time": "< 300s"
    }
    
    with ChaosContext() as chaos:
        # Backup current state
        backup_state = minio_backend.create_snapshot()
        
        try:
            # Corrupt Iceberg metadata
            chaos.corrupt_iceberg_metadata(minio_backend)
            
            # Verify query failure handling
            with pytest.raises(StorageError) as exc_info:
                query_engine.execute("SELECT * FROM trades")
            
            assert "corruption" in str(exc_info.value).lower()
            
            # Verify API behavior
            response = api_client.get("/api/v1/trades")
            assert response.status_code == 503
            assert "storage_unavailable" in response.json()["error"]
            
        finally:
            # Restore clean state
            minio_backend.restore_snapshot(backup_state)
            
            # Verify recovery
            response = api_client.get("/api/v1/trades")
            assert response.status_code == 200
```

### Resource Exhaustion

#### Memory Pressure
```python
@pytest.mark.chaos
def test_memory_exhaustion():
    """Test system behavior under memory pressure."""
    
    hypothesis = {
        "failure_type": "memory_exhaustion",
        "target_component": "ingestion_service",
        "expected_behavior": "graceful_degradation",
        "recovery_time": "< 180s"
    }
    
    with ChaosContext() as chaos:
        # Consume memory to 90% of allocation
        chaos.consume_memory("ingestion-service", target_percentage=90)
        
        # Verify load shedding behavior
        ingestion_metrics = monitor_ingestion_service()
        assert ingestion_metrics.load_shedding_active is True
        
        # Verify critical functionality remains
        critical_trades = send_trades(priority="high")
        assert all(trade.acknowledged for trade in critical_trades)
        
        # Release memory pressure
        chaos.release_memory("ingestion-service")
        
        # Verify full recovery
        wait_for_condition(
            lambda: monitor_ingestion_service().load_shedding_active is False,
            timeout=180
        )
```

#### CPU Saturation
```python
@pytest.mark.chaos
def test_cpu_saturation():
    """Test system behavior under CPU saturation."""
    
    hypothesis = {
        "failure_type": "cpu_saturation",
        "target_component": "query_service",
        "expected_behavior": "query_degradation",
        "recovery_time": "< 120s"
    }
    
    with ChaosContext() as chaos:
        # Saturate CPU
        chaos.saturate_cpu("query-service", duration=60)
        
        # Verify query performance degradation
        start_time = time.time()
        response = api_client.get("/api/v1/trades?symbol=AAPL")
        query_time = time.time() - start_time
        
        assert query_time < 5.0, "Query should still complete within reasonable time"
        assert response.status_code == 503 or response.headers.get("X-Degraded") == "true"
        
        # Wait for CPU to recover
        time.sleep(70)
        
        # Verify performance recovery
        start_time = time.time()
        response = api_client.get("/api/v1/trades?symbol=AAPL")
        recovery_time = time.time() - start_time
        
        assert recovery_time < 1.0, "Performance should recover"
        assert response.status_code == 200
```

### Data-Specific Failures

#### Schema Incompatibility
```python
@pytest.mark.chaos
def test_schema_incompatibility():
    """Test system behavior when data schema becomes incompatible."""
    
    hypothesis = {
        "failure_type": "schema_incompatibility",
        "component": "kafka_schema_registry",
        "expected_behavior": "new_data_rejected",
        "recovery_time": "< 300s"
    }
    
    with ChaosContext() as chaos:
        # Get current schema
        current_schema = schema_registry.get_latest_schema("trades")
        
        # Deploy incompatible schema change
        incompatible_schema = create_incompatible_schema(current_schema)
        chaos.deploy_schema(incompatible_schema, "trades_v2_invalid")
        
        # Verify new data rejection
        producer = KafkaProducer(kafka_producer_config)
        
        with pytest.raises(SchemaValidationError) as exc_info:
            producer.send("trades", create_trade_with_new_field())
        
        # Verify old data still accepted
        old_trade = create_trade_with_old_schema()
        producer.send("trades", old_trade)  # Should succeed
        
        # Deploy compatible schema
        compatible_schema = create_compatible_schema(current_schema)
        chaos.deploy_schema(compatible_schema, "trades_v2_valid")
        
        # Verify new data accepted again
        new_trade = create_trade_with_new_field()
        producer.send("trades", new_trade)  # Should succeed
```

#### Data Corruption Injection
```python
@pytest.mark.chaos
def test_data_corruption_in_pipeline():
    """Test system behavior when data gets corrupted in pipeline."""
    
    hypothesis = {
        "failure_type": "data_corruption",
        "pipeline_stage": "kafka_to_iceberg",
        "expected_behavior": "corrupted_data_rejected",
        "recovery_time": "< 60s"
    }
    
    with ChaosContext() as chaos:
        # Enable data corruption injection
        chaos.enable_data_corruption(
            pipeline="kafka_to_iceberg",
            corruption_rate=0.1,  # 10% corruption rate
            corruption_type="price_value_swap"
        )
        
        # Send test data
        test_trades = generate_test_trades(count=100)
        for trade in test_trades:
            kafka_producer.send("trades", trade)
        
        # Wait for processing
        time.sleep(30)
        
        # Verify corrupted data rejected
        stored_trades = query_stored_trades()
        corruption_detected = any(
            not validate_trade_integrity(trade) 
            for trade in stored_trades
        )
        assert not corruption_detected, "Corrupted data should be rejected"
        
        # Check dead letter queue
        dlq_trades = get_dead_letter_queue_messages()
        assert len(dlq_trades) > 0, "Corrupted trades should go to DLQ"
        
        # Disable corruption
        chaos.disable_data_corruption("kafka_to_iceberg")
        
        # Verify normal operation resumes
        clean_trades = generate_test_trades(count=50)
        for trade in clean_trades:
            kafka_producer.send("trades", trade)
        
        time.sleep(15)
        new_stored_trades = query_stored_trades(limit=50)
        assert len(new_stored_trades) == 50
```

## Chaos Experiment Framework

### ChaosContext Manager
```python
class ChaosContext:
    """Context manager for chaos experiments."""
    
    def __init__(self, blast_radius: str = "isolated", timeout: int = 300):
        self.blast_radius = blast_radius
        self.timeout = timeout
        self.experiments = []
        self.rollback_stack = []
    
    def __enter__(self):
        logger.info(f"Starting chaos experiment with blast radius: {self.blast_radius}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Rollback all changes
        logger.info("Rolling back chaos experiments")
        for rollback_func in reversed(self.rollback_stack):
            try:
                rollback_func()
            except Exception as e:
                logger.error(f"Rollback failed: {e}")
        
        # Verify system recovery
        if not self.verify_system_recovery():
            logger.error("System failed to recover from chaos experiment")
            raise ChaosExperimentError("System recovery failed")
        
        logger.info("Chaos experiment completed successfully")
    
    def stop_kafka_broker(self, kafka_cluster):
        """Stop Kafka broker experiment."""
        experiment = KafkaBrokerExperiment(kafka_cluster)
        self.experiments.append(experiment)
        self.rollback_stack.append(lambda: experiment.start())
        experiment.stop()
    
    def create_network_partition(self, source: str, target: str, duration: int):
        """Create network partition experiment."""
        experiment = NetworkPartitionExperiment(source, target, duration)
        self.experiments.append(experiment)
        self.rollback_stack.append(lambda: experiment.heal())
        experiment.inject()
    
    def verify_system_recovery(self) -> bool:
        """Verify system has recovered from chaos experiments."""
        recovery_checks = [
            self.check_services_healthy(),
            self.check_data_integrity(),
            self.check_performance_baselines()
        ]
        
        return all(recovery_checks)
```

### Experiment Classes
```python
class KafkaBrokerExperiment:
    """Chaos experiment for Kafka broker failures."""
    
    def __init__(self, kafka_cluster):
        self.kafka_cluster = kafka_cluster
        self.original_state = None
    
    def stop(self):
        """Stop Kafka broker."""
        self.original_state = {
            "running": True,
            "container_id": self.kafka_cluster.kafka_container.id
        }
        
        self.kafka_cluster.kafka_container.stop()
        logger.info("Kafka broker stopped for chaos experiment")
    
    def start(self):
        """Start Kafka broker."""
        if self.original_state and self.original_state["running"]:
            self.kafka_cluster.kafka_container.start()
            self.kafka_cluster.wait_for_health()
            logger.info("Kafka broker restarted")


class NetworkPartitionExperiment:
    """Chaos experiment for network partitions."""
    
    def __init__(self, source: str, target: str, duration: int):
        self.source = source
        self.target = target
        self.duration = duration
        self.tc_rules = []
    
    def inject(self):
        """Inject network partition using tc (traffic control)."""
        # Add network delay and packet loss between services
        delay_rule = f"tc qdisc add dev eth0 root netem delay 1000ms loss 50%"
        self.tc_rules.append(delay_rule)
        
        # Apply rule (simplified - in reality would be more sophisticated)
        self._apply_network_rule(delay_rule)
        logger.info(f"Network partition injected: {self.source} -> {self.target}")
    
    def heal(self):
        """Heal network partition."""
        for rule in self.tc_rules:
            self._remove_network_rule(rule)
        logger.info("Network partition healed")
```

## Steady State Monitoring

### Baseline Metrics
```python
class SteadyStateMonitor:
    """Monitor system steady state for chaos experiments."""
    
    def __init__(self):
        self.baseline_metrics = {}
        self.monitoring_active = False
    
    def establish_baseline(self, duration: int = 300):
        """Establish baseline metrics."""
        logger.info(f"Establishing baseline for {duration}s")
        
        start_time = time.time()
        metrics_samples = []
        
        while time.time() - start_time < duration:
            sample = self._collect_current_metrics()
            metrics_samples.append(sample)
            time.sleep(10)
        
        self.baseline_metrics = self._calculate_baseline(metrics_samples)
        logger.info(f"Baseline established: {self.baseline_metrics}")
    
    def verify_steady_state(self, tolerance: float = 0.1) -> bool:
        """Verify system is in steady state."""
        current_metrics = self._collect_current_metrics()
        
        for metric, baseline_value in self.baseline_metrics.items():
            current_value = current_metrics.get(metric, 0)
            deviation = abs(current_value - baseline_value) / baseline_value
            
            if deviation > tolerance:
                logger.warning(f"Metric {metric} deviated {deviation:.2%} from baseline")
                return False
        
        return True
    
    def _collect_current_metrics(self) -> Dict[str, float]:
        """Collect current system metrics."""
        return {
            "ingestion_rate": self._get_ingestion_rate(),
            "query_latency_p95": self._get_query_latency_p95(),
            "error_rate": self._get_error_rate(),
            "memory_usage": self._get_memory_usage(),
            "cpu_usage": self._get_cpu_usage()
        }
```

### Recovery Verification
```python
class RecoveryVerifier:
    """Verify system recovery after chaos experiments."""
    
    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self.recovery_criteria = {
            "services_healthy": True,
            "data_consistency": True,
            "performance_within_10_percent": True,
            "no_error_spikes": True
        }
    
    def verify_recovery(self) -> Dict[str, bool]:
        """Verify all recovery criteria are met."""
        results = {}
        
        # Service health check
        results["services_healthy"] = self._check_services_healthy()
        
        # Data consistency check
        results["data_consistency"] = self._check_data_consistency()
        
        # Performance recovery check
        results["performance_within_10_percent"] = self._check_performance_recovery()
        
        # Error rate check
        results["no_error_spikes"] = self._check_error_rates()
        
        return results
    
    def _check_services_healthy(self) -> bool:
        """Check if all services are healthy."""
        services = ["kafka", "schema_registry", "minio", "api", "ingestion", "query"]
        
        for service in services:
            if not is_service_healthy(service):
                logger.error(f"Service {service} is not healthy")
                return False
        
        return True
```

## Safety Mechanisms

### Blast Radius Control
```python
class BlastRadiusController:
    """Control blast radius of chaos experiments."""
    
    def __init__(self, environment: str):
        self.environment = environment
        self.isolation_zones = self._define_isolation_zones()
    
    def _define_isolation_zones(self) -> Dict[str, List[str]]:
        """Define isolation zones for different environments."""
        if self.environment == "production":
            return {
                "safe": ["monitoring", "logging"],
                "caution": ["canary_services"],
                "dangerous": ["core_services", "databases"]
            }
        elif self.environment == "staging":
            return {
                "safe": ["all_services"],
                "caution": [],
                "dangerous": []
            }
        else:  # development/testing
            return {
                "safe": ["all_services"],
                "caution": [],
                "dangerous": []
            }
    
    def can_experiment(self, target_service: str, experiment_type: str) -> bool:
        """Check if experiment is allowed for target service."""
        risk_level = self._assess_risk(experiment_type)
        allowed_zones = self.isolation_zones.get(risk_level, [])
        
        return target_service in allowed_zones
```

### Automated Rollback
```python
class AutomatedRollback:
    """Automatically rollback if chaos experiment goes wrong."""
    
    def __init__(self, health_check_interval: int = 30):
        self.health_check_interval = health_check_interval
        self.rollback_triggers = {
            "error_rate_above_20_percent": True,
            "availability_below_95_percent": True,
            "response_time_above_5_seconds": True
        }
    
    def monitor_experiment(self, chaos_context: ChaosContext):
        """Monitor chaos experiment and rollback if needed."""
        import threading
        
        def monitor():
            while chaos_context.is_active():
                if self._should_rollback():
                    logger.error("Automated rollback triggered")
                    chaos_context.emergency_rollback()
                    break
                
                time.sleep(self.health_check_interval)
        
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        
        return monitor_thread
```

## Best Practices

### Experiment Design
1. **Start Small**: Begin with low-risk experiments
2. **Document Hypotheses**: Clear expected outcomes
3. **Monitor Continuously**: Real-time health monitoring
4. **Have Rollback Plans**: Automated recovery mechanisms
5. **Learn from Failures**: Document and share findings

### Safety Guidelines
1. **Never in Production**: Chaos only in isolated environments
2. **Blast Radius Control**: Limit potential impact
3. **Time Windows**: Run during low-traffic periods
4. **Manual Override**: Always have emergency stop
5. **Communication**: Notify all stakeholders

### Success Metrics
- **Recovery Time**: How quickly system recovers
- **Data Loss**: Amount of data lost during failure
- **User Impact**: Impact on end-user experience
- **Learning**: Insights gained from experiment
- **Improvements**: System improvements made based on results

## Conclusion

Chaos engineering is essential for building resilient systems. This methodology provides a structured approach to testing system failure scenarios while maintaining safety and control.

Key takeaways:
- Proactive failure testing prevents production outages
- Controlled experimentation with clear hypotheses
- Automated safety mechanisms prevent catastrophic failures
- Continuous learning drives system improvements
- Culture of resilience engineering

---

**Document Version**: 1.0  
**Last Updated**: 2025-01-16  
**Next Review**: 2025-02-01  
**Author**: K2 Platform Team