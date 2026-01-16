"""Unit tests-backup for Producer resource cleanup.

Tests context manager support, exception handling, and proper resource cleanup.
"""

from unittest.mock import MagicMock, patch

import pytest

from k2.ingestion.producer import MarketDataProducer


class TestProducerContextManager:
    """Test context manager support for automatic resource cleanup."""

    @pytest.fixture
    def mock_producer_components(self):
        """Mock external dependencies for Producer."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient") as mock_sr,
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder") as mock_builder,
        ):

            # Mock Schema Registry client
            mock_sr_instance = MagicMock()
            mock_sr.return_value = mock_sr_instance

            # Mock Kafka producer
            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0  # No messages remaining
            mock_prod.return_value = mock_prod_instance

            # Mock topic builder
            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance

            yield {
                "schema_registry": mock_sr_instance,
                "producer": mock_prod_instance,
                "topic_builder": mock_builder_instance,
            }

    def test_context_manager_normal_exit(self, mock_producer_components):
        """Test context manager with normal exit (no exceptions)."""
        with MarketDataProducer() as producer:
            # Producer should be usable inside context
            assert producer is not None
            assert producer.producer is not None

        # After context exit, producer should be closed
        assert producer.producer is None

        # Flush should have been called
        mock_producer_components["producer"].flush.assert_called_once()

    def test_context_manager_with_exception(self, mock_producer_components):
        """Test context manager with exception (ensures cleanup still happens)."""
        with pytest.raises(ValueError, match="Test error"):
            with MarketDataProducer() as producer:
                # Simulate exception during usage
                raise ValueError("Test error")

        # Producer should still be closed despite exception
        assert producer.producer is None

        # Flush should have been called
        mock_producer_components["producer"].flush.assert_called_once()

    def test_context_manager_multiple_exits(self, mock_producer_components):
        """Test that __exit__ is idempotent (can be called multiple times)."""
        with MarketDataProducer() as producer:
            pass

        # Manually call __exit__ again (should not error)
        producer.__exit__(None, None, None)

        # Should still be closed
        assert producer.producer is None

    def test_manual_close_then_context_manager(self, mock_producer_components):
        """Test that manually closing before context exit is safe."""
        with MarketDataProducer() as producer:
            # Manually close producer inside context
            producer.close()
            assert producer.producer is None

        # Context exit should handle already-closed producer gracefully
        assert producer.producer is None


class TestProducerClose:
    """Test close() method behavior."""

    @pytest.fixture
    def mock_producer_components(self):
        """Mock external dependencies for Producer."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient") as mock_sr,
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder") as mock_builder,
        ):

            mock_sr_instance = MagicMock()
            mock_sr.return_value = mock_sr_instance

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance

            yield {
                "schema_registry": mock_sr_instance,
                "producer": mock_prod_instance,
                "topic_builder": mock_builder_instance,
            }

    def test_close_flushes_messages(self, mock_producer_components):
        """Test that close() calls flush() before closing."""
        producer = MarketDataProducer()
        producer.close()

        # Flush should be called
        mock_producer_components["producer"].flush.assert_called_once()

        # Producer should be None after close
        assert producer.producer is None

    def test_close_with_remaining_messages(self, mock_producer_components):
        """Test close() behavior when messages remain in queue."""
        # Configure flush to return remaining messages
        mock_producer_components["producer"].flush.return_value = 5

        producer = MarketDataProducer()
        producer.close()

        # Should still close despite remaining messages
        assert producer.producer is None

    def test_close_idempotent(self, mock_producer_components):
        """Test that close() can be called multiple times safely."""
        producer = MarketDataProducer()

        # First close
        producer.close()
        assert producer.producer is None

        # Second close (should not error)
        producer.close()
        assert producer.producer is None

        # Flush should only be called once (first close)
        assert mock_producer_components["producer"].flush.call_count == 1

    def test_close_with_flush_error(self, mock_producer_components):
        """Test close() handles flush errors gracefully."""
        # Configure flush to raise error
        mock_producer_components["producer"].flush.side_effect = Exception("Flush failed")

        producer = MarketDataProducer()

        # Close should not raise exception (should log it)
        producer.close()

        # Producer should still be closed
        assert producer.producer is None


class TestProducerAfterClose:
    """Test that producer cannot be used after close()."""

    @pytest.fixture
    def closed_producer(self):
        """Create a closed producer for testing."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            producer = MarketDataProducer()
            producer.close()
            return producer

    def test_produce_after_close_raises_error(self, closed_producer):
        """Test that produce methods raise error after close()."""
        with pytest.raises(RuntimeError, match="Producer has been closed"):
            closed_producer.produce_trade(
                asset_class="equities",
                exchange="asx",
                record={"symbol": "BHP", "price": 45.50, "quantity": 1000},
            )

    def test_produce_quote_after_close_raises_error(self, closed_producer):
        """Test that produce_quote raises error after close()."""
        with pytest.raises(RuntimeError, match="Producer has been closed"):
            closed_producer.produce_quote(
                asset_class="equities",
                exchange="asx",
                record={"symbol": "BHP", "bid_price": 45.50, "ask_price": 45.60},
            )

    def test_produce_reference_data_after_close_raises_error(self, closed_producer):
        """Test that produce_reference_data raises error after close()."""
        with pytest.raises(RuntimeError, match="Producer has been closed"):
            closed_producer.produce_reference_data(
                asset_class="equities",
                exchange="asx",
                record={"symbol": "BHP", "company_name": "BHP Group"},
            )

    def test_flush_after_close_is_safe(self, closed_producer):
        """Test that flush() after close() doesn't error (logs warning)."""
        # Flush should return 0 and not raise error
        result = closed_producer.flush()
        assert result == 0

    def test_get_stats_after_close(self, closed_producer):
        """Test that get_stats() works after close()."""
        # Stats should still be accessible
        stats = closed_producer.get_stats()
        assert "produced" in stats
        assert "errors" in stats
        assert "retries" in stats


class TestProducerResourceCleanup:
    """Test that resources are properly cleaned up."""

    def test_producer_reference_cleared_on_close(self):
        """Test that producer reference is set to None on close."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            producer = MarketDataProducer()

            # Before close
            assert producer.producer is not None
            assert producer.producer is mock_prod_instance

            # After close
            producer.close()
            assert producer.producer is None

    def test_serializers_cache_retained_after_close(self):
        """Test that serializer cache is retained after close (for stats)."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            producer = MarketDataProducer()

            # Add serializer to cache (simulated)
            producer._serializers["test_subject"] = MagicMock()

            # After close, cache should still exist
            producer.close()
            assert "test_subject" in producer._serializers

    def test_stats_retained_after_close(self):
        """Test that statistics are retained after close."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            producer = MarketDataProducer()

            # Set some stats
            producer._total_produced = 100
            producer._total_errors = 5
            producer._total_retries = 10

            # Close producer
            producer.close()

            # Stats should still be accessible
            stats = producer.get_stats()
            assert stats["produced"] == 100
            assert stats["errors"] == 5
            assert stats["retries"] == 10


class TestProducerExceptionHandling:
    """Test exception handling during cleanup."""

    def test_context_manager_exception_propagated(self):
        """Test that exceptions inside context manager are propagated."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.return_value = 0
            mock_prod.return_value = mock_prod_instance

            with pytest.raises(ValueError, match="Test error"):
                with MarketDataProducer() as producer:
                    raise ValueError("Test error")

    def test_close_error_in_exit_doesnt_suppress_original_exception(self):
        """Test that close() error doesn't suppress original exception."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            # Configure flush to raise error
            mock_prod_instance = MagicMock()
            mock_prod_instance.flush.side_effect = Exception("Flush failed")
            mock_prod.return_value = mock_prod_instance

            # Original exception should still be raised
            with pytest.raises(ValueError, match="Original error"):
                with MarketDataProducer() as producer:
                    raise ValueError("Original error")


class TestProducerCleanupIntegration:
    """Integration tests-backup for cleanup behavior."""

    def test_multiple_producers_independent_cleanup(self):
        """Test that multiple producers clean up independently."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod.return_value = MagicMock()
            mock_prod.return_value.flush.return_value = 0

            # Create two producers
            producer1 = MarketDataProducer()
            producer2 = MarketDataProducer()

            # Close first producer
            producer1.close()

            # First producer should be closed
            assert producer1.producer is None

            # Second producer should still be open
            assert producer2.producer is not None

            # Close second producer
            producer2.close()
            assert producer2.producer is None

    def test_nested_context_managers(self):
        """Test nested context managers work correctly."""
        with (
            patch("k2.ingestion.producer.SchemaRegistryClient"),
            patch("k2.ingestion.producer.Producer") as mock_prod,
            patch("k2.ingestion.producer.get_topic_builder"),
        ):

            mock_prod.return_value = MagicMock()
            mock_prod.return_value.flush.return_value = 0

            with MarketDataProducer() as producer1:
                assert producer1.producer is not None

                with MarketDataProducer() as producer2:
                    assert producer2.producer is not None
                    # Both producers should be open
                    assert producer1.producer is not None

                # producer2 should be closed, producer1 still open
                assert producer2.producer is None
                assert producer1.producer is not None

            # Both should be closed
            assert producer1.producer is None
            assert producer2.producer is None
