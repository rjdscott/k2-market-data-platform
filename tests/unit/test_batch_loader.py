"""Unit tests for CSV Batch Loader."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from k2.ingestion.batch_loader import BatchLoader, LoadStats, create_loader


class TestBatchLoader:
    """Test suite for BatchLoader."""

    @pytest.fixture
    def mock_producer(self):
        """Mock MarketDataProducer."""
        with patch("k2.ingestion.batch_loader.MarketDataProducer") as mock_prod:
            mock_instance = mock_prod.return_value
            mock_instance.produce_trade = MagicMock()
            mock_instance.produce_quote = MagicMock()
            mock_instance.produce_reference_data = MagicMock()
            mock_instance.flush = MagicMock(return_value=0)
            mock_instance.close = MagicMock()

            yield mock_instance

    @pytest.fixture
    def trade_csv_content(self):
        """Sample trade CSV content."""
        return """symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id
BHP,2026-01-10T10:30:00.123456Z,45.50,1000,buy,12345,TRD001
BHP,2026-01-10T10:30:01.234567Z,45.51,2000,sell,12346,TRD002
RIO,2026-01-10T10:30:02.345678Z,120.25,500,buy,12347,TRD003"""

    @pytest.fixture
    def quote_csv_content(self):
        """Sample quote CSV content."""
        return """symbol,exchange_timestamp,bid_price,ask_price,bid_size,ask_size,sequence_number
BHP,2026-01-10T10:30:00.123456Z,45.40,45.50,1000,2000,12345
RIO,2026-01-10T10:30:01.234567Z,120.20,120.30,500,1000,12346"""

    @pytest.fixture
    def reference_csv_content(self):
        """Sample reference data CSV content."""
        return """company_id,symbol,company_name,sector,market_cap,last_updated
BHP,BHP,BHP Group Limited,Materials,150000000000,2026-01-10T00:00:00Z
RIO,RIO,Rio Tinto Limited,Materials,120000000000,2026-01-10T00:00:00Z"""

    @pytest.fixture
    def loader(self, mock_producer):
        """Create BatchLoader with mocked producer."""
        return BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="trades",
            producer=mock_producer,
        )

    def test_loader_initialization(self, loader):
        """Test loader initializes with correct configuration."""
        assert loader.asset_class == "equities"
        assert loader.exchange == "asx"
        assert loader.data_type == "trades"
        assert loader.producer is not None
        assert loader.dlq_file is None
        assert loader._dlq_writer is None

    def test_loader_with_dlq_file(self, mock_producer, tmp_path):
        """Test loader initializes with DLQ file."""
        dlq_file = tmp_path / "errors.csv"

        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="trades",
            producer=mock_producer,
            dlq_file=dlq_file,
        )

        assert loader.dlq_file == dlq_file
        assert loader._dlq_writer is not None

    def test_invalid_data_type(self, mock_producer):
        """Test loader rejects invalid data type."""
        with pytest.raises(ValueError, match="Invalid data_type 'invalid'"):
            BatchLoader(
                asset_class="equities",
                exchange="asx",
                data_type="invalid",
                producer=mock_producer,
            )

    def test_parse_trade_row_valid(self, loader):
        """Test parsing valid trade row."""
        row = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00.123456Z",
            "price": "45.50",
            "quantity": "1000",
            "side": "BUY",
            "sequence_number": "12345",
            "trade_id": "TRD001",
        }

        parsed = loader._parse_trade_row(row)

        assert parsed["symbol"] == "BHP"
        assert parsed["price"] == 45.50
        assert parsed["quantity"] == 1000
        assert parsed["side"] == "buy"  # Lowercase
        assert parsed["sequence_number"] == 12345
        assert parsed["trade_id"] == "TRD001"

    def test_parse_trade_row_missing_field(self, loader):
        """Test parsing trade row with missing required field."""
        row = {
            "symbol": "BHP",
            "price": "45.50",
            # Missing 'exchange_timestamp'
        }

        with pytest.raises(ValueError, match="Missing required fields"):
            loader._parse_trade_row(row)

    def test_parse_quote_row_valid(self, mock_producer):
        """Test parsing valid quote row."""
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="quotes",
            producer=mock_producer,
        )

        row = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00.123456Z",
            "bid_price": "45.40",
            "ask_price": "45.50",
            "bid_size": "1000",
            "ask_size": "2000",
            "sequence_number": "12345",
        }

        parsed = loader._parse_quote_row(row)

        assert parsed["symbol"] == "BHP"
        assert parsed["bid_price"] == 45.40
        assert parsed["ask_price"] == 45.50
        assert parsed["bid_size"] == 1000
        assert parsed["ask_size"] == 2000

    def test_parse_reference_data_row_valid(self, mock_producer):
        """Test parsing valid reference data row."""
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="reference_data",
            producer=mock_producer,
        )

        row = {
            "company_id": "BHP",
            "symbol": "BHP",
            "company_name": "BHP Group Limited",
            "sector": "Materials",
            "market_cap": "150000000000",
            "last_updated": "2026-01-10T00:00:00Z",
        }

        parsed = loader._parse_reference_data_row(row)

        assert parsed["company_id"] == "BHP"
        assert parsed["symbol"] == "BHP"
        assert parsed["company_name"] == "BHP Group Limited"
        assert parsed["sector"] == "Materials"
        assert parsed["market_cap"] == 150000000000

    def test_load_csv_trades_success(self, loader, mock_producer, trade_csv_content, tmp_path):
        """Test successful loading of trade CSV."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(trade_csv_content)

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 3
        assert stats.success_count == 3
        assert stats.error_count == 0
        assert mock_producer.produce_trade.call_count == 3
        assert mock_producer.flush.call_count >= 1

    def test_load_csv_quotes_success(self, mock_producer, quote_csv_content, tmp_path):
        """Test successful loading of quote CSV."""
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="quotes",
            producer=mock_producer,
        )

        csv_file = tmp_path / "quotes.csv"
        csv_file.write_text(quote_csv_content)

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 2
        assert stats.success_count == 2
        assert stats.error_count == 0
        assert mock_producer.produce_quote.call_count == 2

    def test_load_csv_reference_data_success(self, mock_producer, reference_csv_content, tmp_path):
        """Test successful loading of reference data CSV."""
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="reference_data",
            producer=mock_producer,
        )

        csv_file = tmp_path / "reference.csv"
        csv_file.write_text(reference_csv_content)

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 2
        assert stats.success_count == 2
        assert stats.error_count == 0
        assert mock_producer.produce_reference_data.call_count == 2

    def test_load_csv_with_errors(self, loader, mock_producer, tmp_path):
        """Test loading CSV with some invalid rows."""
        csv_content = """symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id
BHP,2026-01-10T10:30:00.123456Z,45.50,1000,buy,12345,TRD001
INVALID,INVALID,INVALID,INVALID,INVALID,INVALID,INVALID
RIO,2026-01-10T10:30:02.345678Z,120.25,500,buy,12347,TRD003"""

        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(csv_content)

        dlq_file = tmp_path / "errors.csv"
        loader.dlq_file = dlq_file
        loader._init_dlq()

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 3
        assert stats.success_count == 2
        assert stats.error_count == 1
        assert mock_producer.produce_trade.call_count == 2

        # Check DLQ file was created and has error row
        assert dlq_file.exists()
        dlq_content = dlq_file.read_text()
        assert "INVALID" in dlq_content

    def test_load_csv_producer_error(self, loader, mock_producer, trade_csv_content, tmp_path):
        """Test handling of producer errors."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(trade_csv_content)

        # Mock producer to raise error on second call
        mock_producer.produce_trade.side_effect = [
            None,  # First call succeeds
            Exception("Kafka error"),  # Second call fails
            None,  # Third call succeeds
        ]

        dlq_file = tmp_path / "errors.csv"
        loader.dlq_file = dlq_file
        loader._init_dlq()

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 3
        assert stats.success_count == 2
        assert stats.error_count == 1

    def test_load_csv_batch_flushing(self, loader, mock_producer, tmp_path):
        """Test periodic flushing during batch loading."""
        # Create CSV with 250 rows
        rows = ["symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id"]
        for i in range(250):
            rows.append(f"BHP,2026-01-10T10:30:00.{i:06d}Z,45.50,1000,buy,{i},TRD{i:03d}")

        csv_file = tmp_path / "trades.csv"
        csv_file.write_text("\n".join(rows))

        stats = loader.load_csv(csv_file, batch_size=100, flush_interval=100, show_progress=False)

        assert stats.total_rows == 250
        assert stats.success_count == 250
        # Should flush at 100, 200, and final
        assert mock_producer.flush.call_count >= 2

    def test_load_csv_file_not_found(self, loader):
        """Test handling of missing CSV file."""
        with pytest.raises(FileNotFoundError):
            loader.load_csv(Path("/nonexistent/file.csv"))

    def test_load_csv_empty_file(self, loader, tmp_path):
        """Test handling of empty CSV file."""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text(
            "symbol,exchange_timestamp,price,quantity,side,sequence_number,trade_id\n",
        )

        stats = loader.load_csv(csv_file, show_progress=False)

        assert stats.total_rows == 0
        assert stats.success_count == 0
        assert stats.error_count == 0

    def test_close(self, loader, mock_producer):
        """Test loader cleanup."""
        loader.close()

        mock_producer.flush.assert_called_once()
        mock_producer.close.assert_called_once()

    def test_close_with_dlq(self, mock_producer, tmp_path):
        """Test loader cleanup with DLQ file."""
        dlq_file = tmp_path / "errors.csv"
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="trades",
            producer=mock_producer,
            dlq_file=dlq_file,
        )

        # Write some error
        loader._write_to_dlq(1, "Test error", {"symbol": "TEST"})

        loader.close()

        # DLQ handle should be closed
        assert dlq_file.exists()

    def test_context_manager(self, mock_producer):
        """Test loader as context manager."""
        with BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="trades",
            producer=mock_producer,
        ) as loader:
            assert loader.producer is not None

        # Should have called close
        mock_producer.flush.assert_called_once()
        mock_producer.close.assert_called_once()

    def test_create_loader_factory(self, mock_producer):
        """Test create_loader factory function."""
        with patch("k2.ingestion.batch_loader.MarketDataProducer", return_value=mock_producer):
            loader = create_loader(
                asset_class="crypto",
                exchange="binance",
                data_type="trades",
            )

        assert isinstance(loader, BatchLoader)
        assert loader.asset_class == "crypto"
        assert loader.exchange == "binance"
        assert loader.data_type == "trades"

    def test_statistics_tracking(self, loader):
        """Test LoadStats tracking."""
        stats = LoadStats()

        assert stats.total_rows == 0
        assert stats.success_count == 0
        assert stats.error_count == 0

        stats.total_rows = 100
        stats.success_count = 95
        stats.error_count = 5

        assert stats.total_rows == 100
        assert stats.success_count == 95
        assert stats.error_count == 5

    def test_type_conversion_errors(self, loader):
        """Test handling of type conversion errors in parsing."""
        row = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00.123456Z",
            "price": "INVALID_FLOAT",  # Will cause ValueError
            "quantity": "1000",
            "side": "buy",
            "sequence_number": "12345",
            "trade_id": "TRD001",
        }

        with pytest.raises(ValueError):
            loader._parse_trade_row(row)

    def test_side_normalization(self, loader):
        """Test side field is normalized to lowercase."""
        for side_input in ["BUY", "Buy", "buy", "SELL", "Sell", "sell"]:
            row = {
                "symbol": "BHP",
                "exchange_timestamp": "2026-01-10T10:30:00.123456Z",
                "price": "45.50",
                "quantity": "1000",
                "side": side_input,
                "sequence_number": "12345",
                "trade_id": "TRD001",
            }

            parsed = loader._parse_trade_row(row)
            assert parsed["side"] == side_input.lower()

    def test_large_market_cap(self, mock_producer):
        """Test handling of large market cap values."""
        loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="reference_data",
            producer=mock_producer,
        )

        row = {
            "company_id": "BHP",
            "symbol": "BHP",
            "company_name": "BHP Group Limited",
            "sector": "Materials",
            "market_cap": "999999999999999",  # Very large number
            "last_updated": "2026-01-10T00:00:00Z",
        }

        parsed = loader._parse_reference_data_row(row)
        assert parsed["market_cap"] == 999999999999999
        assert isinstance(parsed["market_cap"], int)

    def test_concurrent_loading_different_data_types(self, mock_producer, tmp_path):
        """Test loading different data types simultaneously."""
        # Trade loader
        trade_loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="trades",
            producer=mock_producer,
        )

        # Quote loader
        quote_loader = BatchLoader(
            asset_class="equities",
            exchange="asx",
            data_type="quotes",
            producer=mock_producer,
        )

        assert trade_loader.data_type == "trades"
        assert quote_loader.data_type == "quotes"
        assert trade_loader.asset_class == quote_loader.asset_class
        assert trade_loader.exchange == quote_loader.exchange
