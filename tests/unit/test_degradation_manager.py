"""Unit tests for DegradationManager.

Tests the 5-level degradation cascade system:
- NORMAL (0): Full processing
- SOFT (1): Skip enrichment
- GRACEFUL (2): Drop low-priority symbols
- AGGRESSIVE (3): Spill to disk
- CIRCUIT_BREAK (4): Stop accepting new data

Key test areas:
- Degradation level transitions based on lag and heap thresholds
- Hysteresis prevents flapping (recovery requires lower threshold)
- Cooldown period enforces stability
- Symbol filtering at GRACEFUL and above
- Metrics recording for transitions
"""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from k2.common.degradation_manager import (
    DegradationLevel,
    DegradationManager,
    DegradationThresholds,
)


class TestDegradationLevelTransitions:
    """Test degradation level transitions based on thresholds."""

    def test_normal_level_low_lag(self):
        """Test system stays NORMAL with low lag."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=1000, heap_pct=50.0)

        assert level == DegradationLevel.NORMAL

    def test_soft_degradation_threshold(self):
        """Test transition to SOFT at lag threshold."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=100_000, heap_pct=50.0)

        assert level == DegradationLevel.SOFT

    def test_graceful_degradation_threshold(self):
        """Test transition to GRACEFUL at lag threshold."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        assert level == DegradationLevel.GRACEFUL

    def test_aggressive_degradation_threshold(self):
        """Test transition to AGGRESSIVE at lag threshold."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)

        assert level == DegradationLevel.AGGRESSIVE

    def test_circuit_break_threshold(self):
        """Test transition to CIRCUIT_BREAK at lag threshold."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=5_000_000, heap_pct=50.0)

        assert level == DegradationLevel.CIRCUIT_BREAK

    def test_heap_triggers_degradation(self):
        """Test heap pressure triggers degradation even with low lag."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=1000, heap_pct=85.0)

        assert level == DegradationLevel.GRACEFUL

    def test_heap_triggers_circuit_break(self):
        """Test heap pressure triggers circuit break."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=1000, heap_pct=95.0)

        assert level == DegradationLevel.CIRCUIT_BREAK

    def test_custom_thresholds(self):
        """Test degradation with custom thresholds."""
        custom_thresholds = DegradationThresholds(
            lag_soft=50_000,
            lag_graceful=200_000,
            lag_aggressive=500_000,
            lag_circuit_break=2_000_000,
        )
        manager = DegradationManager(thresholds=custom_thresholds)

        level = manager.check_and_degrade(lag=60_000, heap_pct=50.0)

        assert level == DegradationLevel.SOFT


class TestHysteresis:
    """Test hysteresis prevents flapping between levels."""

    def test_recovery_requires_lower_threshold(self):
        """Test recovery requires lag to drop below threshold * factor."""
        manager = DegradationManager()

        # Degrade to GRACEFUL
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)
        assert manager.level == DegradationLevel.GRACEFUL

        # Lag drops slightly below threshold - should NOT recover (hysteresis)
        level = manager.check_and_degrade(lag=450_000, heap_pct=50.0)

        assert level == DegradationLevel.GRACEFUL  # Still degraded

    def test_recovery_succeeds_below_hysteresis_threshold(self):
        """Test recovery succeeds when lag drops below hysteresis threshold."""
        manager = DegradationManager()

        # Degrade to GRACEFUL (lag threshold 500K)
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        # Wait for cooldown
        time.sleep(0.1)
        manager._last_degradation_time = datetime.utcnow() - timedelta(seconds=31)

        # Lag drops below recovery threshold (500K * 0.5 = 250K)
        level = manager.check_and_degrade(lag=240_000, heap_pct=50.0)

        assert level == DegradationLevel.SOFT  # Recovered one level

    def test_cooldown_prevents_immediate_recovery(self):
        """Test cooldown period prevents immediate recovery."""
        manager = DegradationManager()

        # Degrade to GRACEFUL
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)
        assert manager.level == DegradationLevel.GRACEFUL

        # Lag drops well below threshold immediately
        level = manager.check_and_degrade(lag=50_000, heap_pct=50.0)

        # Should NOT recover due to cooldown
        assert level == DegradationLevel.GRACEFUL

    def test_recovery_after_cooldown_expires(self):
        """Test recovery works after cooldown period expires."""
        thresholds = DegradationThresholds(recovery_cooldown_seconds=1)
        manager = DegradationManager(thresholds=thresholds)

        # Degrade to GRACEFUL
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        # Wait for cooldown to expire
        time.sleep(1.1)

        # Lag drops below recovery threshold
        level = manager.check_and_degrade(lag=50_000, heap_pct=50.0)

        assert level == DegradationLevel.NORMAL  # Fully recovered


class TestSymbolFiltering:
    """Test symbol filtering at GRACEFUL and above."""

    def test_all_symbols_processed_at_normal(self):
        """Test all symbols processed at NORMAL level."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1000, heap_pct=50.0)

        assert manager.should_process_symbol("BHP")  # Critical symbol
        assert manager.should_process_symbol("XYZ")  # Non-critical symbol

    def test_all_symbols_processed_at_soft(self):
        """Test all symbols processed at SOFT level."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=100_000, heap_pct=50.0)

        assert manager.should_process_symbol("BHP")
        assert manager.should_process_symbol("XYZ")

    def test_only_critical_at_graceful(self):
        """Test only critical symbols processed at GRACEFUL."""
        manager = DegradationManager(critical_symbols=["BHP", "CBA"])
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        assert manager.should_process_symbol("BHP")  # Critical - should process
        assert not manager.should_process_symbol("XYZ")  # Non-critical - should drop

    def test_only_critical_at_aggressive(self):
        """Test only critical symbols at AGGRESSIVE."""
        manager = DegradationManager(critical_symbols=["BHP"])
        manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)

        assert manager.should_process_symbol("BHP")
        assert not manager.should_process_symbol("CBA")

    def test_custom_critical_symbols(self):
        """Test custom critical symbols list."""
        manager = DegradationManager(critical_symbols=["AAPL", "GOOGL"])
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        assert manager.should_process_symbol("AAPL")
        assert manager.should_process_symbol("GOOGL")
        assert not manager.should_process_symbol("BHP")


class TestOperationalMethods:
    """Test operational helper methods."""

    def test_is_accepting_data_at_normal(self):
        """Test accepting data at NORMAL level."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1000, heap_pct=50.0)

        assert manager.is_accepting_data()

    def test_is_accepting_data_at_aggressive(self):
        """Test accepting data at AGGRESSIVE level."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)

        assert manager.is_accepting_data()

    def test_not_accepting_data_at_circuit_break(self):
        """Test NOT accepting data at CIRCUIT_BREAK."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=5_000_000, heap_pct=50.0)

        assert not manager.is_accepting_data()

    def test_skip_enrichment_at_soft(self):
        """Test enrichment should be skipped at SOFT."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=100_000, heap_pct=50.0)

        assert manager.should_skip_enrichment()

    def test_no_skip_enrichment_at_normal(self):
        """Test enrichment not skipped at NORMAL."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1000, heap_pct=50.0)

        assert not manager.should_skip_enrichment()

    def test_spill_to_disk_at_aggressive(self):
        """Test spill to disk triggered at AGGRESSIVE."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)

        assert manager.should_spill_to_disk()

    def test_no_spill_at_graceful(self):
        """Test spill not triggered at GRACEFUL."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        assert not manager.should_spill_to_disk()

    def test_get_status(self):
        """Test get_status returns current state."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        status = manager.get_status()

        assert status["level"] == "GRACEFUL"
        assert status["level_value"] == 2
        assert status["is_accepting_data"] is True
        assert status["critical_symbols_count"] == 20  # Default ASX top 20
        assert status["history_count"] == 1

    def test_reset(self):
        """Test reset returns system to NORMAL."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)
        assert manager.level == DegradationLevel.AGGRESSIVE

        manager.reset()

        assert manager.level == DegradationLevel.NORMAL


class TestMetrics:
    """Test Prometheus metrics recording."""

    @patch("k2.common.degradation_manager.DEGRADATION_LEVEL")
    def test_degradation_level_metric_set(self, mock_gauge):
        """Test degradation level gauge is set."""
        manager = DegradationManager()

        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        # Metric should be set to 2 (GRACEFUL)
        mock_gauge.labels.assert_called_with(
            service="k2-platform",
            environment="dev",
            component="degradation_manager",
        )
        mock_gauge.labels().set.assert_called_with(2)

    @patch("k2.common.degradation_manager.get_metric")
    def test_transition_metric_recorded(self, mock_get_metric):
        """Test transition counter is incremented."""
        mock_counter = MagicMock()
        mock_get_metric.return_value = mock_counter

        manager = DegradationManager()

        # Trigger transition from NORMAL to GRACEFUL
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        # Verify transition metric called
        mock_get_metric.assert_called_with("degradation_transitions_total")
        mock_counter.labels.assert_called_with(
            service="k2-platform",
            environment="dev",
            component="degradation_manager",
            from_level="normal",
            to_level="graceful",
        )
        mock_counter.labels().inc.assert_called_once()


class TestThreadSafety:
    """Test thread-safe operations."""

    def test_level_property_thread_safe(self):
        """Test level property uses lock for thread safety."""
        manager = DegradationManager()
        manager.check_and_degrade(lag=500_000, heap_pct=50.0)

        # Access level property (uses lock internally)
        level = manager.level

        assert level == DegradationLevel.GRACEFUL

    def test_multiple_degradation_checks(self):
        """Test multiple degradation checks maintain state correctly."""
        manager = DegradationManager()

        # Series of checks
        level1 = manager.check_and_degrade(lag=100_000, heap_pct=50.0)
        level2 = manager.check_and_degrade(lag=500_000, heap_pct=50.0)
        level3 = manager.check_and_degrade(lag=1_000_000, heap_pct=50.0)

        assert level1 == DegradationLevel.SOFT
        assert level2 == DegradationLevel.GRACEFUL
        assert level3 == DegradationLevel.AGGRESSIVE
        assert manager.level == DegradationLevel.AGGRESSIVE


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_exact_threshold_triggers_degradation(self):
        """Test exact threshold value triggers degradation."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=100_000, heap_pct=50.0)

        assert level == DegradationLevel.SOFT

    def test_zero_lag(self):
        """Test zero lag keeps system NORMAL."""
        manager = DegradationManager()

        level = manager.check_and_degrade(lag=0, heap_pct=0.0)

        assert level == DegradationLevel.NORMAL

    def test_none_heap_uses_psutil(self):
        """Test None heap_pct causes automatic measurement via psutil."""
        manager = DegradationManager()

        with patch("k2.common.degradation_manager.psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 75.0
            level = manager.check_and_degrade(lag=1000, heap_pct=None)

        # Should use measured heap (75%) → SOFT level
        assert level == DegradationLevel.SOFT

    def test_degradation_history_trimmed(self):
        """Test degradation history is trimmed to 100 entries."""
        thresholds = DegradationThresholds(recovery_cooldown_seconds=0)
        manager = DegradationManager(thresholds=thresholds)

        # Generate 150 transitions by manually forcing state changes
        # Use alternating lag values to trigger transitions
        for i in range(150):
            # Alternate between NORMAL and SOFT to force transitions
            lag = 100_000 if i % 2 == 0 else 1000
            manager.check_and_degrade(lag=lag, heap_pct=50.0)
            # Force last_degradation_time to past to allow recovery
            manager._last_degradation_time = datetime.utcnow() - timedelta(seconds=1)

        # History should be trimmed to last 100
        assert len(manager._degradation_history) <= 100
