"""Unit tests-backup for LoadShedder.

Tests priority-based message filtering for graceful degradation:
- MessagePriority levels (CRITICAL, HIGH, NORMAL, LOW)
- Symbol tier classification (Tier 1, Tier 2, Tier 3)
- Load shedding at different degradation levels
- Shed statistics and metrics
"""

from unittest.mock import MagicMock, patch

import pytest

from k2.common.load_shedder import LoadShedder, MessagePriority


class TestPriorityClassification:
    """Test message priority classification."""

    def test_tier_1_symbol_critical_priority(self):
        """Test tier 1 symbols get CRITICAL priority."""
        shedder = LoadShedder()

        priority = shedder.get_priority("BHP", "trades")

        assert priority == MessagePriority.CRITICAL

    def test_tier_2_symbol_high_priority(self):
        """Test tier 2 symbols get HIGH priority."""
        shedder = LoadShedder()

        priority = shedder.get_priority("REA", "trades")

        assert priority == MessagePriority.HIGH

    def test_tier_3_symbol_normal_priority(self):
        """Test tier 3 symbols get NORMAL priority."""
        shedder = LoadShedder()

        priority = shedder.get_priority("UNKNOWN_SYMBOL", "trades")

        assert priority == MessagePriority.NORMAL

    def test_reference_data_always_low_priority(self):
        """Test reference data always gets LOW priority regardless of symbol."""
        shedder = LoadShedder()

        # Even tier 1 symbol gets LOW for reference data
        priority = shedder.get_priority("BHP", "reference_data")

        assert priority == MessagePriority.LOW

    def test_custom_tier_1_symbols(self):
        """Test custom tier 1 symbol classification."""
        custom_tier_1 = {"AAPL", "GOOGL", "MSFT"}
        shedder = LoadShedder(tier_1=custom_tier_1)

        assert shedder.get_priority("AAPL", "trades") == MessagePriority.CRITICAL
        assert shedder.get_priority("BHP", "trades") == MessagePriority.NORMAL  # Not in custom tier

    def test_custom_tier_2_symbols(self):
        """Test custom tier 2 symbol classification."""
        custom_tier_2 = {"TSLA", "NVDA"}
        shedder = LoadShedder(tier_1=set(), tier_2=custom_tier_2)

        assert shedder.get_priority("TSLA", "trades") == MessagePriority.HIGH
        assert shedder.get_priority("REA", "trades") == MessagePriority.NORMAL  # Not in custom tier


class TestLoadSheddingBehavior:
    """Test load shedding at different degradation levels."""

    def test_level_0_processes_everything(self):
        """Test NORMAL level (0) processes all messages."""
        shedder = LoadShedder()

        assert shedder.should_process("BHP", degradation_level=0)
        assert shedder.should_process("REA", degradation_level=0)
        assert shedder.should_process("XYZ", degradation_level=0)
        assert shedder.should_process("BHP", degradation_level=0, data_type="reference_data")

    def test_level_1_processes_everything(self):
        """Test SOFT level (1) still processes all messages."""
        shedder = LoadShedder()

        assert shedder.should_process("BHP", degradation_level=1)
        assert shedder.should_process("REA", degradation_level=1)
        assert shedder.should_process("XYZ", degradation_level=1)
        assert shedder.should_process("BHP", degradation_level=1, data_type="reference_data")

    def test_level_2_drops_low_priority(self):
        """Test GRACEFUL level (2) drops LOW priority."""
        shedder = LoadShedder()

        # Process trades (CRITICAL, HIGH, NORMAL)
        assert shedder.should_process("BHP", degradation_level=2)  # CRITICAL
        assert shedder.should_process("REA", degradation_level=2)  # HIGH
        assert shedder.should_process("XYZ", degradation_level=2)  # NORMAL

        # Drop reference data (LOW)
        assert not shedder.should_process("BHP", degradation_level=2, data_type="reference_data")

    def test_level_3_drops_normal_priority(self):
        """Test AGGRESSIVE level (3) drops NORMAL and below."""
        shedder = LoadShedder()

        # Process CRITICAL and HIGH
        assert shedder.should_process("BHP", degradation_level=3)  # CRITICAL
        assert shedder.should_process("REA", degradation_level=3)  # HIGH

        # Drop NORMAL and LOW
        assert not shedder.should_process("XYZ", degradation_level=3)  # NORMAL
        assert not shedder.should_process("BHP", degradation_level=3, data_type="reference_data")

    def test_level_4_only_critical(self):
        """Test CIRCUIT_BREAK level (4) only processes CRITICAL."""
        shedder = LoadShedder()

        # Only process CRITICAL
        assert shedder.should_process("BHP", degradation_level=4)  # CRITICAL

        # Drop everything else
        assert not shedder.should_process("REA", degradation_level=4)  # HIGH
        assert not shedder.should_process("XYZ", degradation_level=4)  # NORMAL
        assert not shedder.should_process("BHP", degradation_level=4, data_type="reference_data")


class TestShedStatistics:
    """Test shed statistics calculation."""

    def test_get_shed_stats_level_0(self):
        """Test shed stats at NORMAL level - nothing shed."""
        shedder = LoadShedder()
        symbols = ["BHP", "REA", "XYZ", "ABC"]

        stats = shedder.get_shed_stats(symbols, degradation_level=0)

        assert stats["total"] == 4
        assert stats["processed"] == 4
        assert stats["shed"] == 0
        assert stats["shed_percentage"] == 0.0

    def test_get_shed_stats_level_4(self):
        """Test shed stats at CIRCUIT_BREAK level - only tier 1 processed."""
        tier_1 = {"BHP", "CBA"}
        shedder = LoadShedder(tier_1=tier_1, tier_2=set())
        symbols = ["BHP", "CBA", "REA", "XYZ"]

        stats = shedder.get_shed_stats(symbols, degradation_level=4)

        assert stats["total"] == 4
        assert stats["processed"] == 2  # Only BHP, CBA
        assert stats["shed"] == 2
        assert stats["shed_percentage"] == 50.0

    def test_get_shed_stats_empty_list(self):
        """Test shed stats with empty symbol list."""
        shedder = LoadShedder()

        stats = shedder.get_shed_stats([], degradation_level=3)

        assert stats["total"] == 0
        assert stats["processed"] == 0
        assert stats["shed"] == 0
        assert stats["shed_percentage"] == 0.0

    def test_get_shed_stats_all_tier_1(self):
        """Test shed stats when all symbols are tier 1."""
        tier_1 = {"BHP", "CBA", "CSL"}
        shedder = LoadShedder(tier_1=tier_1, tier_2=set())
        symbols = ["BHP", "CBA", "CSL"]

        stats = shedder.get_shed_stats(symbols, degradation_level=4)

        assert stats["total"] == 3
        assert stats["processed"] == 3
        assert stats["shed"] == 0


class TestMetrics:
    """Test Prometheus metrics recording."""

    @patch("k2.common.load_shedder.get_metric")
    def test_shed_metric_recorded(self, mock_get_metric):
        """Test shed metric is incremented when message shed."""
        mock_counter = MagicMock()
        mock_get_metric.return_value = mock_counter

        shedder = LoadShedder()

        # Shed a tier 3 message at level 4
        shedder.should_process("XYZ", degradation_level=4)

        # Verify metric called
        mock_get_metric.assert_called_with("messages_shed_total")
        mock_counter.labels.assert_called_with(
            service="k2-platform",
            environment="dev",
            component="load_shedder",
            symbol_tier="tier_3",
            reason="non_critical",
        )
        mock_counter.labels().inc.assert_called_once()

    @patch("k2.common.load_shedder.get_metric")
    def test_no_metric_when_processed(self, mock_get_metric):
        """Test no shed metric when message is processed."""
        shedder = LoadShedder()

        # Process a tier 1 message at level 4
        shedder.should_process("BHP", degradation_level=4)

        # Metric should not be called (message processed)
        mock_get_metric.assert_not_called()

    @patch("k2.common.load_shedder.get_metric")
    def test_metric_with_correct_tier_label(self, mock_get_metric):
        """Test shed metric uses correct tier label."""
        mock_counter = MagicMock()
        mock_get_metric.return_value = mock_counter

        tier_2 = {"REA"}
        shedder = LoadShedder(tier_1=set(), tier_2=tier_2)

        # Shed a tier 2 message
        shedder.should_process("REA", degradation_level=4)

        # Verify tier_2 label
        mock_counter.labels.assert_called_with(
            service="k2-platform",
            environment="dev",
            component="load_shedder",
            symbol_tier="tier_2",
            reason="non_critical",
        )

    @patch("k2.common.load_shedder.get_metric")
    def test_metric_handles_missing_registry(self, mock_get_metric):
        """Test shed metric handles KeyError gracefully."""
        mock_get_metric.side_effect = KeyError("Metric not registered")

        shedder = LoadShedder()

        # Should not raise exception
        result = shedder.should_process("XYZ", degradation_level=4)

        assert result is False


class TestStatisticsTracking:
    """Test internal statistics tracking."""

    def test_get_stats_initial(self):
        """Test initial stats are zero."""
        shedder = LoadShedder()

        stats = shedder.get_stats()

        assert stats["total_checked"] == 0
        assert stats["total_shed"] == 0
        assert stats["shed_rate"] == 0.0
        assert stats["tier_1_count"] == 20  # Default ASX tier 1
        assert stats["tier_2_count"] > 0  # Default ASX tier 2

    def test_get_stats_after_checks(self):
        """Test stats accumulate after checks."""
        shedder = LoadShedder()

        # Process some messages
        shedder.should_process("BHP", degradation_level=4)  # Processed
        shedder.should_process("XYZ", degradation_level=4)  # Shed
        shedder.should_process("ABC", degradation_level=4)  # Shed

        stats = shedder.get_stats()

        assert stats["total_checked"] == 3
        assert stats["total_shed"] == 2
        assert stats["shed_rate"] == pytest.approx(66.67, rel=0.1)

    def test_get_stats_no_division_by_zero(self):
        """Test shed_rate calculation handles zero checked."""
        shedder = LoadShedder()

        stats = shedder.get_stats()

        assert stats["shed_rate"] == 0.0  # No division by zero


class TestSymbolTierClassification:
    """Test symbol tier classification logic."""

    def test_tier_1_classification(self):
        """Test tier 1 symbols are correctly classified."""
        shedder = LoadShedder()

        assert shedder.get_priority("BHP", "trades") == MessagePriority.CRITICAL
        assert shedder.get_priority("CBA", "trades") == MessagePriority.CRITICAL
        assert shedder.get_priority("CSL", "trades") == MessagePriority.CRITICAL

    def test_tier_2_classification(self):
        """Test tier 2 symbols are correctly classified."""
        shedder = LoadShedder()

        assert shedder.get_priority("REA", "trades") == MessagePriority.HIGH
        assert shedder.get_priority("QAN", "trades") == MessagePriority.HIGH

    def test_tier_3_classification(self):
        """Test tier 3 (unknown) symbols get NORMAL priority."""
        shedder = LoadShedder()

        assert shedder.get_priority("UNKNOWN", "trades") == MessagePriority.NORMAL
        assert shedder.get_priority("NEWCO", "trades") == MessagePriority.NORMAL

    def test_overlapping_tiers_prefer_higher(self):
        """Test symbol in multiple tiers uses higher tier."""
        # Symbol in both tier 1 and tier 2 - tier 1 takes precedence
        tier_1 = {"BHP"}
        tier_2 = {"BHP", "REA"}  # BHP also in tier 2
        shedder = LoadShedder(tier_1=tier_1, tier_2=tier_2)

        priority = shedder.get_priority("BHP", "trades")

        assert priority == MessagePriority.CRITICAL  # Tier 1 priority


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_tier_sets(self):
        """Test load shedder with empty tier sets."""
        shedder = LoadShedder(tier_1=set(), tier_2=set())

        # All symbols should be tier 3 (NORMAL priority)
        assert shedder.get_priority("BHP", "trades") == MessagePriority.NORMAL
        assert shedder.get_priority("AAPL", "trades") == MessagePriority.NORMAL

    def test_unknown_data_type(self):
        """Test unknown data types default to trade priority."""
        shedder = LoadShedder()

        # Unknown data type should use trade priority rules
        priority = shedder.get_priority("BHP", "unknown_type")

        assert priority == MessagePriority.CRITICAL  # Tier 1 symbol

    def test_case_sensitivity(self):
        """Test symbol comparison is case-sensitive."""
        tier_1 = {"BHP"}
        shedder = LoadShedder(tier_1=tier_1, tier_2=set())

        assert shedder.get_priority("BHP", "trades") == MessagePriority.CRITICAL
        assert (
            shedder.get_priority("bhp", "trades") == MessagePriority.NORMAL
        )  # Lowercase not in tier

    def test_quotes_data_type(self):
        """Test quotes data type uses same priority as trades."""
        shedder = LoadShedder()

        priority_trades = shedder.get_priority("BHP", "trades")
        priority_quotes = shedder.get_priority("BHP", "quotes")

        assert priority_trades == priority_quotes == MessagePriority.CRITICAL
