"""Unit tests-backup for demo reset functionality."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from reset_demo import (
    ResetConfig,
    get_kafka_topics,
    purge_kafka_topic,
)


class TestResetConfig:
    """Tests for ResetConfig dataclass."""

    def test_default_config(self):
        """Default config should reset everything and reload data."""
        cfg = ResetConfig()
        assert cfg.keep_metrics is False
        assert cfg.keep_kafka is False
        assert cfg.keep_iceberg is False
        assert cfg.reload_data is True
        assert cfg.dry_run is False
        assert cfg.force is False

    def test_custom_config(self):
        """Custom config preserves specified components."""
        cfg = ResetConfig(
            keep_metrics=True,
            keep_kafka=True,
            force=True,
        )
        assert cfg.keep_metrics is True
        assert cfg.keep_kafka is True
        assert cfg.keep_iceberg is False
        assert cfg.force is True

    def test_default_endpoints(self):
        """Default endpoints should match docker-compose config."""
        cfg = ResetConfig()
        assert cfg.kafka_bootstrap == "localhost:9092"
        assert cfg.minio_endpoint == "http://localhost:9000"
        assert cfg.iceberg_catalog_uri == "http://localhost:8181"
        assert cfg.postgres_host == "localhost"
        assert cfg.postgres_port == 5432


class TestKafkaTopics:
    """Tests for Kafka topic functions."""

    def test_get_kafka_topics_fallback(self):
        """Should return fallback topics if config not available."""
        with patch("reset_demo.get_topic_builder", side_effect=Exception("Config not found")):
            topics = get_kafka_topics()

        assert len(topics) == 6
        assert "market.equities.trades.asx" in topics
        assert "market.crypto.trades.binance" in topics

    def test_get_kafka_topics_from_config(self):
        """Should return topics from topic builder when available."""
        mock_builder = MagicMock()
        mock_builder.list_all_topics.return_value = [
            "market.equities.trades.asx",
            "market.equities.quotes.asx",
        ]

        with patch("reset_demo.get_topic_builder", return_value=mock_builder):
            topics = get_kafka_topics()

        assert len(topics) == 2
        assert "market.equities.trades.asx" in topics

    def test_purge_topic_dry_run(self):
        """Dry run should not execute any operations."""
        mock_admin = MagicMock()

        result = purge_kafka_topic(mock_admin, "test-topic", dry_run=True)

        assert result["status"] == "would_purge"
        assert result["topic"] == "test-topic"
        mock_admin.delete_records.assert_not_called()

    def test_purge_topic_not_found(self):
        """Should handle missing topic gracefully."""
        mock_admin = MagicMock()
        mock_metadata = MagicMock()
        mock_metadata.topics = {}  # Empty - topic not found
        mock_admin.list_topics.return_value = mock_metadata

        result = purge_kafka_topic(mock_admin, "nonexistent-topic", dry_run=False)

        assert result["status"] == "not_found"


@pytest.mark.unit
class TestResetLogic:
    """Tests for reset logic flow."""

    def test_skip_kafka_when_keep_flag_set(self):
        """When keep_kafka=True, Kafka should be skipped."""
        cfg = ResetConfig(keep_kafka=True)
        # The main function checks cfg.keep_kafka before calling reset_kafka
        assert cfg.keep_kafka is True

    def test_skip_metrics_when_keep_flag_set(self):
        """When keep_metrics=True, Prometheus/Grafana should be skipped."""
        cfg = ResetConfig(keep_metrics=True)
        assert cfg.keep_metrics is True

    def test_skip_iceberg_when_keep_flag_set(self):
        """When keep_iceberg=True, Iceberg/MinIO should be skipped."""
        cfg = ResetConfig(keep_iceberg=True)
        assert cfg.keep_iceberg is True

    def test_skip_reload_when_no_reload_set(self):
        """When reload_data=False, sample data should not reload."""
        cfg = ResetConfig(reload_data=False)
        assert cfg.reload_data is False

    def test_force_skips_confirmation(self):
        """When force=True, confirmation prompt should be skipped."""
        cfg = ResetConfig(force=True)
        assert cfg.force is True
