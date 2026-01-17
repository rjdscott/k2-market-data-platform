"""
Tests for the degradation demo script.

Tests that the demo runs successfully in both normal and quick modes.
"""

from unittest.mock import patch

import pytest

from k2.common.degradation_manager import DegradationLevel
from scripts.demo_degradation import DegradationDemo


class TestDegradationDemo:
    """Test suite for degradation demo script."""

    def test_demo_initialization(self):
        """Test demo initializes with correct defaults."""
        demo = DegradationDemo()

        assert demo.quick_mode is False
        assert demo.simulated_lag == 0
        assert demo.simulated_heap == 50.0
        assert demo.degradation_manager is not None
        assert demo.load_shedder is not None

    def test_demo_initialization_quick_mode(self):
        """Test demo initializes in quick mode."""
        demo = DegradationDemo(quick_mode=True)

        assert demo.quick_mode is True

    @patch("scripts.demo_degradation.console")
    @patch("time.sleep")
    def test_show_intro(self, mock_sleep, mock_console):
        """Test intro panel displays correctly."""
        demo = DegradationDemo(quick_mode=True)
        demo._show_intro()

        # Verify console.print was called (intro panel)
        assert mock_console.print.called
        # Verify pause was called
        assert mock_sleep.called

    @patch("scripts.demo_degradation.console")
    @patch("time.sleep")
    def test_demonstrate_normal(self, mock_sleep, mock_console):
        """Test normal operation demonstration."""
        demo = DegradationDemo(quick_mode=True)
        demo._demonstrate_normal()

        # Verify simulated values set correctly
        assert demo.simulated_lag == 10_000
        assert demo.simulated_heap == 50.0

        # Verify console output
        assert mock_console.print.called

    @patch("scripts.demo_degradation.console")
    @patch("time.sleep")
    def test_demonstrate_degradation(self, mock_sleep, mock_console):
        """Test degradation sequence demonstration."""
        demo = DegradationDemo(quick_mode=True)
        demo._demonstrate_degradation()

        # Verify final simulated values match last stage
        assert demo.simulated_lag == 1_500_000
        assert demo.simulated_heap == 92.0

        # Verify console output
        assert mock_console.print.called

    @patch("scripts.demo_degradation.console")
    @patch("time.sleep")
    def test_demonstrate_recovery(self, mock_sleep, mock_console):
        """Test recovery sequence demonstration."""
        demo = DegradationDemo(quick_mode=True)

        # Start from degraded state
        demo.simulated_lag = 1_500_000
        demo.simulated_heap = 92.0
        demo.degradation_manager.check_and_degrade(
            lag=demo.simulated_lag,
            heap_pct=demo.simulated_heap,
        )

        # Run recovery
        demo._demonstrate_recovery()

        # Verify recovery values
        assert demo.simulated_lag <= 20_000
        assert demo.simulated_heap <= 50.0

    @patch("scripts.demo_degradation.console")
    def test_show_status_table(self, mock_console):
        """Test status table displays correctly."""
        demo = DegradationDemo()

        demo.simulated_lag = 150_000
        demo.simulated_heap = 75.0
        demo.degradation_manager.check_and_degrade(
            lag=demo.simulated_lag,
            heap_pct=demo.simulated_heap,
        )

        demo._show_status_table()

        # Verify table was printed
        assert mock_console.print.called

    @patch("scripts.demo_degradation.console")
    def test_show_level_behavior(self, mock_console):
        """Test level behavior description."""
        demo = DegradationDemo()

        # Test each level
        for level in DegradationLevel:
            demo._show_level_behavior(level)
            assert mock_console.print.called
            mock_console.reset_mock()

    @patch("scripts.demo_degradation.console")
    def test_show_summary(self, mock_console):
        """Test summary panel displays."""
        demo = DegradationDemo()
        demo._show_summary()

        # Verify summary was printed
        assert mock_console.print.called

    def test_pause_normal_mode(self):
        """Test pause in normal mode."""
        demo = DegradationDemo(quick_mode=False)

        with patch("time.sleep") as mock_sleep:
            demo._pause(3.0)
            mock_sleep.assert_called_once_with(3.0)

    def test_pause_quick_mode(self):
        """Test pause in quick mode (shorter)."""
        demo = DegradationDemo(quick_mode=True)

        with patch("time.sleep") as mock_sleep:
            demo._pause(3.0)
            # In quick mode, max pause is 0.5
            mock_sleep.assert_called_once_with(0.5)

    @patch("scripts.demo_degradation.console")
    @patch("time.sleep")
    def test_full_demo_run(self, mock_sleep, mock_console):
        """Test full demo runs without errors."""
        demo = DegradationDemo(quick_mode=True)

        # Should not raise any exceptions
        demo.run()

        # Verify all phases were called
        assert mock_console.print.call_count > 10

    def test_degradation_manager_integration(self):
        """Test degradation manager transitions during demo."""
        demo = DegradationDemo()

        # Initial state
        level = demo.degradation_manager.check_and_degrade(lag=10_000, heap_pct=50.0)
        assert level == DegradationLevel.NORMAL

        # Trigger SOFT
        level = demo.degradation_manager.check_and_degrade(lag=150_000, heap_pct=75.0)
        assert level == DegradationLevel.SOFT

        # Trigger GRACEFUL
        level = demo.degradation_manager.check_and_degrade(lag=600_000, heap_pct=82.0)
        assert level == DegradationLevel.GRACEFUL

        # Trigger AGGRESSIVE
        level = demo.degradation_manager.check_and_degrade(lag=1_500_000, heap_pct=92.0)
        assert level == DegradationLevel.AGGRESSIVE

    def test_load_shedder_stats_access(self):
        """Test load shedder statistics are accessible."""
        demo = DegradationDemo()

        stats = demo.load_shedder.get_stats()

        # Verify stats have expected keys
        assert "total_checked" in stats
        assert "total_shed" in stats
        assert "shed_rate" in stats
        assert "tier_1_count" in stats
        assert "tier_2_count" in stats

    def test_demo_state_transitions_sequence(self):
        """Test demo follows correct state transition sequence."""
        demo = DegradationDemo()

        # Phase 1: Normal
        demo._demonstrate_normal()
        status = demo.degradation_manager.get_status()
        assert status["level"] in ["NORMAL"]

        # Phase 2: Degradation (simulates increasing load)
        # After degradation sequence, should be at higher level
        demo._demonstrate_degradation()
        status = demo.degradation_manager.get_status()
        assert status["level_value"] >= DegradationLevel.SOFT

    @pytest.mark.parametrize("quick_mode", [True, False])
    def test_demo_runs_in_both_modes(self, quick_mode):
        """Test demo runs successfully in both quick and normal modes."""
        with patch("scripts.demo_degradation.console"), patch("time.sleep"):
            demo = DegradationDemo(quick_mode=quick_mode)
            demo.run()  # Should not raise


class TestDegradationDemoEdgeCases:
    """Test edge cases and error conditions."""

    @patch("scripts.demo_degradation.console")
    def test_status_table_with_extreme_lag(self, mock_console):
        """Test status table with extreme lag values."""
        demo = DegradationDemo()

        demo.simulated_lag = 10_000_000  # 10M messages
        demo.simulated_heap = 99.0
        demo.degradation_manager.check_and_degrade(
            lag=demo.simulated_lag,
            heap_pct=demo.simulated_heap,
        )

        # Should not raise
        demo._show_status_table()

    @patch("scripts.demo_degradation.console")
    def test_level_behavior_with_unknown_level(self, mock_console):
        """Test behavior display with unknown level."""
        demo = DegradationDemo()

        # Create a mock level (shouldn't exist but test robustness)
        class MockLevel:
            name = "UNKNOWN"

        # Should not crash, just show no behaviors
        demo._show_level_behavior(MockLevel())

    def test_pause_with_zero_seconds(self):
        """Test pause handles zero seconds gracefully."""
        demo = DegradationDemo()

        with patch("time.sleep") as mock_sleep:
            demo._pause(0.0)
            mock_sleep.assert_called_once_with(0.0)


class TestDegradationDemoMetrics:
    """Test metrics integration in demo."""

    def test_degradation_level_metric_changes(self):
        """Test that degradation level metric changes during demo."""
        demo = DegradationDemo()

        # Check initial level
        initial_status = demo.degradation_manager.get_status()
        initial_level = initial_status["level_value"]

        # Trigger degradation
        demo.simulated_lag = 600_000
        demo.simulated_heap = 82.0
        demo.degradation_manager.check_and_degrade(
            lag=demo.simulated_lag,
            heap_pct=demo.simulated_heap,
        )

        # Check level changed
        new_status = demo.degradation_manager.get_status()
        new_level = new_status["level_value"]

        assert new_level > initial_level

    def test_load_shedding_statistics_tracked(self):
        """Test that load shedding statistics are tracked."""
        demo = DegradationDemo()

        # Initially no messages checked
        stats = demo.load_shedder.get_stats()
        assert stats["total_checked"] == 0
        assert stats["total_shed"] == 0

        # Simulate checking messages
        demo.load_shedder.should_process("BHP", DegradationLevel.GRACEFUL, "trades")
        demo.load_shedder.should_process("UNKNOWN", DegradationLevel.AGGRESSIVE, "trades")

        # Stats should update
        stats = demo.load_shedder.get_stats()
        assert stats["total_checked"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
