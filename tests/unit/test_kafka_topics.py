"""Unit tests for Kafka topic utilities.

Tests the TopicNameBuilder and SubscriptionBuilder classes to ensure
correct topic name generation, configuration retrieval, and subscription
pattern building.
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from k2.kafka import (
    TopicNameBuilder,
    DataType,
    TopicConfig,
    get_topic_builder,
)
from k2.kafka.patterns import SubscriptionBuilder, get_subscription_builder


class TestDataType:
    """Test DataType enum."""

    def test_data_type_values(self):
        """DataType enum should have correct values."""
        assert DataType.TRADES.value == "trades"
        assert DataType.QUOTES.value == "quotes"
        assert DataType.REFERENCE_DATA.value == "reference_data"

    def test_data_type_string_comparison(self):
        """DataType should support string comparison."""
        assert DataType.TRADES == "trades"
        assert DataType.QUOTES == "quotes"


class TestTopicNameBuilder:
    """Test TopicNameBuilder class."""

    def test_build_topic_name_asx_trades(self):
        """Should build correct topic name for ASX trades."""
        builder = TopicNameBuilder()
        topic = builder.build_topic_name('equities', DataType.TRADES, 'asx')
        assert topic == 'market.equities.trades.asx'

    def test_build_topic_name_binance_quotes(self):
        """Should build correct topic name for Binance quotes."""
        builder = TopicNameBuilder()
        topic = builder.build_topic_name('crypto', DataType.QUOTES, 'binance')
        assert topic == 'market.crypto.quotes.binance'

    def test_build_topic_name_reference_data(self):
        """Should build correct topic name for reference data."""
        builder = TopicNameBuilder()
        topic = builder.build_topic_name('equities', DataType.REFERENCE_DATA, 'asx')
        assert topic == 'market.equities.reference_data.asx'

    def test_build_topic_name_invalid_asset_class(self):
        """Should raise ValueError for unknown asset class."""
        builder = TopicNameBuilder()
        with pytest.raises(ValueError, match="Unknown asset class"):
            builder.build_topic_name('commodities', DataType.TRADES, 'cme')

    def test_build_topic_name_invalid_exchange(self):
        """Should raise ValueError for unknown exchange."""
        builder = TopicNameBuilder()
        with pytest.raises(ValueError, match="Unknown exchange"):
            builder.build_topic_name('equities', DataType.TRADES, 'nasdaq')

    def test_get_topic_config_asx_trades(self):
        """Should return complete topic configuration for ASX trades."""
        builder = TopicNameBuilder()
        config = builder.get_topic_config('equities', DataType.TRADES, 'asx')

        assert isinstance(config, TopicConfig)
        assert config.asset_class == 'equities'
        assert config.data_type == DataType.TRADES
        assert config.exchange == 'asx'
        assert config.partitions == 30
        assert config.schema_subject == 'market.equities.trades-value'
        assert config.schema_name == 'trade'
        assert config.partition_key_field == 'symbol'
        assert config.topic_name == 'market.equities.trades.asx'

    def test_get_topic_config_binance_quotes(self):
        """Should return complete topic configuration for Binance quotes."""
        builder = TopicNameBuilder()
        config = builder.get_topic_config('crypto', DataType.QUOTES, 'binance')

        assert config.asset_class == 'crypto'
        assert config.data_type == DataType.QUOTES
        assert config.exchange == 'binance'
        assert config.partitions == 40
        assert config.schema_subject == 'market.crypto.quotes-value'
        assert config.schema_name == 'quote'

    def test_get_topic_config_reference_data_override(self):
        """Reference data should have 1 partition (override)."""
        builder = TopicNameBuilder()
        config = builder.get_topic_config('equities', DataType.REFERENCE_DATA, 'asx')

        assert config.partitions == 1  # Overridden to 1
        assert config.kafka_config['cleanup.policy'] == 'compact'

    def test_get_topic_config_kafka_settings(self):
        """Topic config should include correct Kafka settings."""
        builder = TopicNameBuilder()
        config = builder.get_topic_config('equities', DataType.TRADES, 'asx')

        assert 'compression.type' in config.kafka_config
        assert config.kafka_config['compression.type'] == 'lz4'
        assert 'retention.ms' in config.kafka_config
        assert config.kafka_config['retention.ms'] == '604800000'  # 7 days
        assert config.kafka_config['replication.factor'] == '1'

    def test_list_all_topics(self):
        """Should list all configured topics."""
        builder = TopicNameBuilder()
        topics = builder.list_all_topics()

        # Should have: equities (asx) + crypto (binance) = 2 exchanges × 3 data types = 6 topics
        assert len(topics) >= 6
        assert 'market.equities.trades.asx' in topics
        assert 'market.equities.quotes.asx' in topics
        assert 'market.equities.reference_data.asx' in topics
        assert 'market.crypto.trades.binance' in topics
        assert 'market.crypto.quotes.binance' in topics
        assert 'market.crypto.reference_data.binance' in topics

        # Should be sorted
        assert topics == sorted(topics)

    def test_list_topics_for_asset_class_equities(self):
        """Should list all topics for equities asset class."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_asset_class('equities')

        assert len(topics) == 3  # ASX: trades, quotes, reference_data
        assert all('equities' in t for t in topics)
        assert 'market.equities.trades.asx' in topics
        assert 'market.equities.quotes.asx' in topics
        assert 'market.equities.reference_data.asx' in topics

    def test_list_topics_for_asset_class_crypto(self):
        """Should list all topics for crypto asset class."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_asset_class('crypto')

        assert len(topics) == 3  # Binance: trades, quotes, reference_data
        assert all('crypto' in t for t in topics)

    def test_list_topics_for_asset_class_invalid(self):
        """Should raise ValueError for unknown asset class."""
        builder = TopicNameBuilder()
        with pytest.raises(ValueError, match="Unknown asset class"):
            builder.list_topics_for_asset_class('commodities')

    def test_list_topics_for_exchange_asx(self):
        """Should list all topics for ASX exchange."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_exchange('equities', 'asx')

        assert len(topics) == 3  # trades, quotes, reference_data
        assert topics == [
            'market.equities.quotes.asx',
            'market.equities.reference_data.asx',
            'market.equities.trades.asx',
        ]

    def test_list_topics_for_exchange_binance(self):
        """Should list all topics for Binance exchange."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_exchange('crypto', 'binance')

        assert len(topics) == 3
        assert all('binance' in t for t in topics)

    def test_list_topics_for_data_type_trades(self):
        """Should list all trades topics across exchanges."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_data_type(DataType.TRADES)

        assert all('trades' in t for t in topics)
        assert 'market.equities.trades.asx' in topics
        assert 'market.crypto.trades.binance' in topics

    def test_list_topics_for_data_type_quotes(self):
        """Should list all quotes topics across exchanges."""
        builder = TopicNameBuilder()
        topics = builder.list_topics_for_data_type(DataType.QUOTES)

        assert all('quotes' in t for t in topics)

    def test_parse_topic_name_valid(self):
        """Should parse valid topic name into components."""
        builder = TopicNameBuilder()
        parsed = builder.parse_topic_name('market.equities.trades.asx')

        assert parsed == {
            'prefix': 'market',
            'asset_class': 'equities',
            'data_type': 'trades',
            'exchange': 'asx',
        }

    def test_parse_topic_name_crypto(self):
        """Should parse crypto topic name."""
        builder = TopicNameBuilder()
        parsed = builder.parse_topic_name('market.crypto.quotes.binance')

        assert parsed['asset_class'] == 'crypto'
        assert parsed['data_type'] == 'quotes'
        assert parsed['exchange'] == 'binance'

    def test_parse_topic_name_invalid_format(self):
        """Should return None for invalid topic name."""
        builder = TopicNameBuilder()
        parsed = builder.parse_topic_name('invalid.topic.name')
        assert parsed is None

    def test_parse_topic_name_wrong_prefix(self):
        """Should return None for wrong prefix."""
        builder = TopicNameBuilder()
        parsed = builder.parse_topic_name('other.equities.trades.asx')
        assert parsed is None

    def test_parse_topic_name_too_few_parts(self):
        """Should return None for too few parts."""
        builder = TopicNameBuilder()
        parsed = builder.parse_topic_name('market.equities.trades')
        assert parsed is None

    def test_get_partition_key_field_trades(self):
        """Should return correct partition key field for trades."""
        builder = TopicNameBuilder()
        field = builder.get_partition_key_field('equities', DataType.TRADES, 'asx')
        assert field == 'symbol'

    def test_get_partition_key_field_quotes(self):
        """Should return correct partition key field for quotes."""
        builder = TopicNameBuilder()
        field = builder.get_partition_key_field('crypto', DataType.QUOTES, 'binance')
        assert field == 'symbol'

    def test_get_partition_key_field_reference_data(self):
        """Should return correct partition key field for reference data."""
        builder = TopicNameBuilder()
        field = builder.get_partition_key_field('equities', DataType.REFERENCE_DATA, 'asx')
        assert field == 'company_id'

    def test_get_exchange_metadata_asx(self):
        """Should return exchange metadata for ASX."""
        builder = TopicNameBuilder()
        metadata = builder.get_exchange_metadata('equities', 'asx')

        assert metadata['name'] == 'Australian Securities Exchange'
        assert metadata['partitions'] == 30
        assert metadata['country'] == 'AU'
        assert metadata['timezone'] == 'Australia/Sydney'

    def test_get_exchange_metadata_binance(self):
        """Should return exchange metadata for Binance."""
        builder = TopicNameBuilder()
        metadata = builder.get_exchange_metadata('crypto', 'binance')

        assert metadata['name'] == 'Binance'
        assert metadata['partitions'] == 40
        assert metadata['country'] == 'GLOBAL'
        assert metadata['timezone'] == 'UTC'

    def test_get_exchange_metadata_invalid(self):
        """Should raise ValueError for unknown exchange."""
        builder = TopicNameBuilder()
        with pytest.raises(ValueError, match="Unknown exchange"):
            builder.get_exchange_metadata('equities', 'nasdaq')

    def test_topic_config_property(self):
        """TopicConfig.topic_name property should return correct value."""
        config = TopicConfig(
            asset_class='equities',
            data_type=DataType.TRADES,
            exchange='asx',
            partitions=30,
            kafka_config={},
            schema_subject='market.equities.trades-value',
            schema_name='trade',
            partition_key_field='symbol',
        )
        assert config.topic_name == 'market.equities.trades.asx'


class TestTopicNameBuilderSingleton:
    """Test get_topic_builder singleton."""

    def test_get_topic_builder_returns_instance(self):
        """get_topic_builder should return TopicNameBuilder instance."""
        builder = get_topic_builder()
        assert isinstance(builder, TopicNameBuilder)

    def test_get_topic_builder_singleton(self):
        """get_topic_builder should return same instance."""
        builder1 = get_topic_builder()
        builder2 = get_topic_builder()
        assert builder1 is builder2


class TestSubscriptionBuilder:
    """Test SubscriptionBuilder class."""

    def test_subscribe_to_exchange_asx(self):
        """Should subscribe to all ASX topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_exchange('equities', 'asx')

        assert len(topics) == 3
        assert topics == [
            'market.equities.quotes.asx',
            'market.equities.reference_data.asx',
            'market.equities.trades.asx',
        ]

    def test_subscribe_to_exchange_binance(self):
        """Should subscribe to all Binance topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_exchange('crypto', 'binance')

        assert len(topics) == 3
        assert all('binance' in t for t in topics)
        assert all('crypto' in t for t in topics)

    def test_subscribe_to_exchange_filtered_data_types(self):
        """Should subscribe to specific data types only."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_exchange(
            'equities', 'asx',
            data_types=[DataType.TRADES, DataType.QUOTES]
        )

        assert len(topics) == 2
        assert 'market.equities.trades.asx' in topics
        assert 'market.equities.quotes.asx' in topics
        assert 'market.equities.reference_data.asx' not in topics

    def test_subscribe_to_asset_class_equities(self):
        """Should subscribe to all equities topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_asset_class('equities')

        assert len(topics) == 3  # ASX: 3 data types
        assert all('equities' in t for t in topics)

    def test_subscribe_to_asset_class_crypto(self):
        """Should subscribe to all crypto topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_asset_class('crypto')

        assert len(topics) == 3  # Binance: 3 data types
        assert all('crypto' in t for t in topics)

    def test_subscribe_to_asset_class_filtered_data_types(self):
        """Should subscribe to specific data types within asset class."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_asset_class(
            'crypto',
            data_types=[DataType.TRADES]
        )

        assert len(topics) == 1
        assert topics == ['market.crypto.trades.binance']

    def test_subscribe_to_asset_class_filtered_exchanges(self):
        """Should subscribe to specific exchanges within asset class."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_asset_class(
            'equities',
            exchanges=['asx']
        )

        assert len(topics) == 3  # ASX: 3 data types
        assert all('asx' in t for t in topics)

    def test_subscribe_to_data_type_trades(self):
        """Should subscribe to all trades topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_data_type(DataType.TRADES)

        assert all('trades' in t for t in topics)
        assert 'market.equities.trades.asx' in topics
        assert 'market.crypto.trades.binance' in topics

    def test_subscribe_to_data_type_quotes(self):
        """Should subscribe to all quotes topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_data_type(DataType.QUOTES)

        assert all('quotes' in t for t in topics)

    def test_subscribe_to_data_type_filtered_asset_classes(self):
        """Should subscribe to data type within specific asset classes."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_data_type(
            DataType.TRADES,
            asset_classes=['equities']
        )

        assert all('trades' in t for t in topics)
        assert all('equities' in t for t in topics)
        assert 'market.equities.trades.asx' in topics
        assert 'market.crypto.trades.binance' not in topics

    def test_subscribe_to_data_type_filtered_exchanges(self):
        """Should subscribe to data type for specific exchanges."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_data_type(
            DataType.TRADES,
            exchanges={'equities': ['asx'], 'crypto': ['binance']}
        )

        assert 'market.equities.trades.asx' in topics
        assert 'market.crypto.trades.binance' in topics

    def test_subscribe_to_specific_topics(self):
        """Should subscribe to specific topic list."""
        builder = SubscriptionBuilder()
        specs = [
            ('equities', DataType.TRADES, 'asx'),
            ('crypto', DataType.QUOTES, 'binance'),
        ]
        topics = builder.subscribe_to_specific_topics(specs)

        assert len(topics) == 2
        assert topics == [
            'market.crypto.quotes.binance',
            'market.equities.trades.asx',
        ]

    def test_subscribe_to_all(self):
        """Should subscribe to all topics."""
        builder = SubscriptionBuilder()
        topics = builder.subscribe_to_all()

        # Should have all 6 topics (2 exchanges × 3 data types)
        assert len(topics) >= 6
        assert 'market.equities.trades.asx' in topics
        assert 'market.crypto.quotes.binance' in topics


class TestSubscriptionBuilderSingleton:
    """Test get_subscription_builder singleton."""

    def test_get_subscription_builder_returns_instance(self):
        """get_subscription_builder should return SubscriptionBuilder instance."""
        builder = get_subscription_builder()
        assert isinstance(builder, SubscriptionBuilder)
