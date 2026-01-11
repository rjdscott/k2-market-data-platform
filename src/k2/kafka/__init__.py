"""Kafka topic utilities for K2 platform.

This module provides utilities for building topic names, deriving partition keys,
and managing topic-related configuration based on the exchange + asset class architecture.

Topic naming convention: market.{asset_class}.{data_type}.{exchange}

Examples:
    >>> from k2.kafka import get_topic_builder, DataType
    >>> builder = get_topic_builder()
    >>> builder.build_topic_name('equities', DataType.TRADES, 'asx')
    'market.equities.trades.asx'

    >>> config = builder.get_topic_config('equities', DataType.TRADES, 'asx')
    >>> config.partitions
    30
    >>> config.schema_subject
    'market.equities.trades-value'
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import structlog
import yaml

logger = structlog.get_logger()


class DataType(str, Enum):
    """Kafka data types.

    Represents the three main types of market data flowing through Kafka topics.
    """

    TRADES = "trades"
    QUOTES = "quotes"
    REFERENCE_DATA = "reference_data"


@dataclass
class TopicConfig:
    """Complete topic configuration.

    Attributes:
        asset_class: Asset class (e.g., 'equities', 'crypto')
        data_type: Data type enum (TRADES, QUOTES, REFERENCE_DATA)
        exchange: Exchange code (e.g., 'asx', 'binance')
        partitions: Number of partitions for this topic
        kafka_config: Kafka topic configuration dict (compression, retention, etc.)
        schema_subject: Schema Registry subject name for this topic
        schema_name: Avro schema file name (without .avsc extension)
        partition_key_field: Field name used as partition key
    """

    asset_class: str
    data_type: DataType
    exchange: str
    partitions: int
    kafka_config: dict[str, str]
    schema_subject: str
    schema_name: str
    partition_key_field: str

    @property
    def topic_name(self) -> str:
        """Get fully qualified topic name."""
        return f"market.{self.asset_class}.{self.data_type.value}.{self.exchange}"


class TopicNameBuilder:
    """Build Kafka topic names following K2 naming convention.

    Topic naming: market.{asset_class}.{data_type}.{exchange}

    This class reads topic configuration from config/kafka/topics.yaml and provides
    utilities for building topic names, validating configurations, and introspecting
    the topic structure.

    Examples:
        >>> builder = TopicNameBuilder()
        >>> builder.build_topic_name('equities', DataType.TRADES, 'asx')
        'market.equities.trades.asx'

        >>> config = builder.get_topic_config('equities', DataType.TRADES, 'asx')
        >>> config.partitions
        30

        >>> all_topics = builder.list_all_topics()
        >>> len(all_topics)
        6  # 3 data types × 2 exchanges (ASX, Binance)
    """

    TOPIC_PREFIX = "market"

    def __init__(self, config_path: Path | None = None):
        """Initialize topic name builder.

        Args:
            config_path: Path to topics.yaml config file. If None, uses default location
                        (config/kafka/topics.yaml relative to project root).
        """
        if config_path is None:
            # Default: config/kafka/topics.yaml relative to project root
            config_path = (
                Path(__file__).parent.parent.parent.parent / "config" / "kafka" / "topics.yaml"
            )

        self.config_path = config_path
        self._config: dict | None = None

    @property
    def config(self) -> dict:
        """Lazy load configuration from YAML file.

        Returns:
            Dictionary containing full topic configuration.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If config file is invalid YAML.
        """
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> dict:
        """Load topics configuration from YAML file.

        Returns:
            Parsed configuration dictionary.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If config file is invalid YAML.
        """
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Topics config not found: {self.config_path}\n"
                f"Run scripts/init_infra.py or create the config file.",
            )

        logger.debug("Loading topic configuration", config_path=str(self.config_path))

        with open(self.config_path) as f:
            config = yaml.safe_load(f)

        # Validate required sections exist
        required_sections = ["asset_classes", "data_types", "defaults", "schema_registry"]
        missing = [s for s in required_sections if s not in config]
        if missing:
            raise ValueError(
                f"Invalid config file: missing required sections: {missing}\n"
                f"Config path: {self.config_path}",
            )

        logger.info(
            "Topic configuration loaded successfully",
            asset_classes=list(config["asset_classes"].keys()),
            data_types=list(config["data_types"].keys()),
        )

        return config

    def build_topic_name(
        self,
        asset_class: str,
        data_type: DataType,
        exchange: str,
    ) -> str:
        """Build topic name following convention.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            data_type: Data type enum (TRADES, QUOTES, REFERENCE_DATA)
            exchange: Exchange code (e.g., 'asx', 'binance')

        Returns:
            Topic name: market.{asset_class}.{data_type}.{exchange}

        Raises:
            ValueError: If asset class or exchange not found in config.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> builder.build_topic_name('equities', DataType.TRADES, 'asx')
            'market.equities.trades.asx'
            >>> builder.build_topic_name('crypto', DataType.QUOTES, 'binance')
            'market.crypto.quotes.binance'
        """
        # Validate asset class exists
        if asset_class not in self.config["asset_classes"]:
            available = list(self.config["asset_classes"].keys())
            raise ValueError(f"Unknown asset class: '{asset_class}'\nAvailable: {available}")

        # Validate exchange exists for this asset class
        exchanges = self.config["asset_classes"][asset_class]["exchanges"]
        if exchange not in exchanges:
            available = list(exchanges.keys())
            raise ValueError(
                f"Unknown exchange '{exchange}' for asset class '{asset_class}'\n"
                f"Available: {available}",
            )

        return f"{self.TOPIC_PREFIX}.{asset_class}.{data_type.value}.{exchange}"

    def get_topic_config(
        self,
        asset_class: str,
        data_type: DataType,
        exchange: str,
    ) -> TopicConfig:
        """Get complete topic configuration.

        Returns TopicConfig with all settings merged from:
        - Global defaults
        - Data type configuration
        - Exchange configuration

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            data_type: Data type enum (TRADES, QUOTES, REFERENCE_DATA)
            exchange: Exchange code (e.g., 'asx', 'binance')

        Returns:
            TopicConfig object with complete configuration.

        Raises:
            ValueError: If asset class or exchange not found.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> config = builder.get_topic_config('equities', DataType.TRADES, 'asx')
            >>> config.partitions
            30
            >>> config.schema_subject
            'market.equities.trades-value'
        """
        # Validate and build topic name
        topic_name = self.build_topic_name(asset_class, data_type, exchange)

        # Get configs
        exchange_cfg = self.config["asset_classes"][asset_class]["exchanges"][exchange]
        data_type_cfg = self.config["data_types"][data_type.value]
        defaults = self.config["defaults"]

        # Determine partition count
        # Priority: data_type override > exchange config > error
        if "partitions" in data_type_cfg:
            # Data type specifies partition count (e.g., reference_data = 1)
            partitions = data_type_cfg["partitions"]
        elif "partitions" in exchange_cfg:
            # Use exchange default partition count
            partitions = exchange_cfg["partitions"]
        else:
            raise ValueError(
                f"No partition count found for {topic_name}\n"
                f"Define 'partitions' in data type or exchange config.",
            )

        # Build Kafka config dict
        kafka_config = {
            "replication.factor": str(defaults["replication_factor"]),
            "min.insync.replicas": str(defaults["min_insync_replicas"]),
        }

        # Merge data type-specific config
        if "config" in data_type_cfg:
            kafka_config.update(data_type_cfg["config"])

        # Build schema subject name (asset class level, shared across exchanges)
        subject_pattern = self.config["schema_registry"]["subject_pattern"]
        schema_subject = subject_pattern.format(asset_class=asset_class, data_type=data_type.value)

        return TopicConfig(
            asset_class=asset_class,
            data_type=data_type,
            exchange=exchange,
            partitions=partitions,
            kafka_config=kafka_config,
            schema_subject=schema_subject,
            schema_name=data_type_cfg["schema_name"],
            partition_key_field=data_type_cfg["partition_key"],
        )

    def list_all_topics(self) -> list[str]:
        """List all topic names based on configuration.

        Returns:
            Sorted list of all topic names that should exist.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> topics = builder.list_all_topics()
            >>> 'market.equities.trades.asx' in topics
            True
            >>> len(topics)
            6  # 3 data types × 2 exchanges
        """
        topics = []

        for asset_class, asset_cfg in self.config["asset_classes"].items():
            for exchange in asset_cfg["exchanges"].keys():
                for data_type in DataType:
                    topic_name = self.build_topic_name(asset_class, data_type, exchange)
                    topics.append(topic_name)

        return sorted(topics)

    def list_topics_for_asset_class(self, asset_class: str) -> list[str]:
        """List all topics for a specific asset class.

        Args:
            asset_class: Asset class to filter by (e.g., 'equities', 'crypto')

        Returns:
            Sorted list of topic names for this asset class.

        Raises:
            ValueError: If asset class not found.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> topics = builder.list_topics_for_asset_class('equities')
            >>> all('equities' in t for t in topics)
            True
        """
        if asset_class not in self.config["asset_classes"]:
            available = list(self.config["asset_classes"].keys())
            raise ValueError(f"Unknown asset class: '{asset_class}'\nAvailable: {available}")

        topics = []
        asset_cfg = self.config["asset_classes"][asset_class]

        for exchange in asset_cfg["exchanges"].keys():
            for data_type in DataType:
                topic_name = self.build_topic_name(asset_class, data_type, exchange)
                topics.append(topic_name)

        return sorted(topics)

    def list_topics_for_exchange(self, asset_class: str, exchange: str) -> list[str]:
        """List all topics for a specific exchange.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')

        Returns:
            Sorted list of topic names for this exchange (all data types).

        Raises:
            ValueError: If asset class or exchange not found.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> topics = builder.list_topics_for_exchange('equities', 'asx')
            >>> topics
            ['market.equities.quotes.asx', 'market.equities.reference_data.asx', 'market.equities.trades.asx']
        """
        # Validate exchange exists (build_topic_name will raise ValueError if not)
        _ = self.build_topic_name(asset_class, DataType.TRADES, exchange)

        return sorted(
            [self.build_topic_name(asset_class, data_type, exchange) for data_type in DataType],
        )

    def list_topics_for_data_type(self, data_type: DataType) -> list[str]:
        """List all topics for a specific data type across all exchanges.

        Args:
            data_type: Data type to filter by (TRADES, QUOTES, REFERENCE_DATA)

        Returns:
            Sorted list of topic names for this data type.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> topics = builder.list_topics_for_data_type(DataType.TRADES)
            >>> all('trades' in t for t in topics)
            True
        """
        topics = []

        for asset_class, asset_cfg in self.config["asset_classes"].items():
            for exchange in asset_cfg["exchanges"].keys():
                topic_name = self.build_topic_name(asset_class, data_type, exchange)
                topics.append(topic_name)

        return sorted(topics)

    def parse_topic_name(self, topic_name: str) -> dict[str, str] | None:
        """Parse topic name into components.

        Args:
            topic_name: Topic name to parse (e.g., 'market.equities.trades.asx')

        Returns:
            Dict with keys: prefix, asset_class, data_type, exchange
            None if topic doesn't match expected pattern.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> parsed = builder.parse_topic_name('market.equities.trades.asx')
            >>> parsed['asset_class']
            'equities'
            >>> parsed['exchange']
            'asx'
        """
        parts = topic_name.split(".")

        if len(parts) != 4 or parts[0] != self.TOPIC_PREFIX:
            return None

        return {
            "prefix": parts[0],
            "asset_class": parts[1],
            "data_type": parts[2],
            "exchange": parts[3],
        }

    def get_partition_key_field(self, asset_class: str, data_type: DataType, exchange: str) -> str:
        """Get the partition key field name for a topic.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            data_type: Data type enum
            exchange: Exchange code

        Returns:
            Field name to use as partition key (e.g., 'symbol', 'company_id')

        Examples:
            >>> builder = TopicNameBuilder()
            >>> builder.get_partition_key_field('equities', DataType.TRADES, 'asx')
            'symbol'
        """
        data_type_cfg = self.config["data_types"][data_type.value]
        return data_type_cfg["partition_key"]

    def get_exchange_metadata(self, asset_class: str, exchange: str) -> dict:
        """Get metadata for an exchange.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')

        Returns:
            Dictionary with exchange metadata (name, timezone, trading hours, etc.)

        Raises:
            ValueError: If asset class or exchange not found.

        Examples:
            >>> builder = TopicNameBuilder()
            >>> metadata = builder.get_exchange_metadata('equities', 'asx')
            >>> metadata['name']
            'Australian Securities Exchange'
            >>> metadata['timezone']
            'Australia/Sydney'
        """
        if asset_class not in self.config["asset_classes"]:
            available = list(self.config["asset_classes"].keys())
            raise ValueError(f"Unknown asset class: '{asset_class}'\nAvailable: {available}")

        exchanges = self.config["asset_classes"][asset_class]["exchanges"]
        if exchange not in exchanges:
            available = list(exchanges.keys())
            raise ValueError(
                f"Unknown exchange '{exchange}' for asset class '{asset_class}'\n"
                f"Available: {available}",
            )

        return exchanges[exchange]


# Global singleton instance
_topic_builder: TopicNameBuilder | None = None


def get_topic_builder(config_path: Path | None = None) -> TopicNameBuilder:
    """Get global TopicNameBuilder instance.

    Args:
        config_path: Optional path to topics.yaml. If None, uses default location.
                    Only used on first call (singleton pattern).

    Returns:
        TopicNameBuilder instance.

    Examples:
        >>> from k2.kafka import get_topic_builder
        >>> builder = get_topic_builder()
        >>> builder.build_topic_name('equities', DataType.TRADES, 'asx')
        'market.equities.trades.asx'
    """
    global _topic_builder
    if _topic_builder is None:
        _topic_builder = TopicNameBuilder(config_path=config_path)
    return _topic_builder


# Public API
__all__ = [
    "DataType",
    "TopicConfig",
    "TopicNameBuilder",
    "get_topic_builder",
]
