"""Kafka subscription patterns for producers and consumers.

Provides utilities for building topic subscription lists based on
asset class, exchange, and data type filters.

Examples:
    Subscribe to all ASX topics:
        >>> from k2.kafka.patterns import get_subscription_builder
        >>> builder = get_subscription_builder()
        >>> topics = builder.subscribe_to_exchange('equities', 'asx')
        >>> topics
        ['market.equities.quotes.asx', 'market.equities.reference_data.asx', 'market.equities.trades.asx']

    Subscribe to all crypto exchanges:
        >>> topics = builder.subscribe_to_asset_class('crypto')

    Subscribe to all trades across all exchanges:
        >>> from k2.kafka import DataType
        >>> topics = builder.subscribe_to_data_type(DataType.TRADES)
"""
from typing import Dict, List, Optional

import structlog

from k2.kafka import DataType, get_topic_builder

logger = structlog.get_logger()


class SubscriptionBuilder:
    """Build Kafka subscription patterns for consumers.

    This class provides convenient methods for building topic subscription lists
    based on different filtering criteria (exchange, asset class, data type).

    Examples:
        >>> builder = SubscriptionBuilder()
        >>> # Subscribe to all ASX topics
        >>> asx_topics = builder.subscribe_to_exchange('equities', 'asx')
        >>> # Subscribe to all trades
        >>> trade_topics = builder.subscribe_to_data_type(DataType.TRADES)
    """

    def __init__(self):
        """Initialize subscription builder."""
        self.topic_builder = get_topic_builder()

    def subscribe_to_exchange(
        self,
        asset_class: str,
        exchange: str,
        data_types: Optional[List[DataType]] = None,
    ) -> List[str]:
        """Subscribe to all data types for a specific exchange.

        Use this pattern when your consumer needs all data from a single exchange.
        Example: ASX-only trading strategy consuming trades, quotes, and reference data.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            exchange: Exchange code (e.g., 'asx', 'binance')
            data_types: Optional list of data types to filter (default: all data types)

        Returns:
            Sorted list of topic names for this exchange.

        Examples:
            >>> builder = SubscriptionBuilder()
            >>> topics = builder.subscribe_to_exchange('equities', 'asx')
            >>> topics
            ['market.equities.quotes.asx', 'market.equities.reference_data.asx', 'market.equities.trades.asx']

            >>> # Subscribe only to trades and quotes (exclude reference data)
            >>> topics = builder.subscribe_to_exchange('equities', 'asx', [DataType.TRADES, DataType.QUOTES])
        """
        if data_types is None:
            data_types = list(DataType)

        topics = [
            self.topic_builder.build_topic_name(asset_class, dt, exchange)
            for dt in data_types
        ]

        logger.debug(
            "Built exchange subscription",
            asset_class=asset_class,
            exchange=exchange,
            data_types=[dt.value for dt in data_types],
            topic_count=len(topics),
            topics=topics,
        )

        return sorted(topics)

    def subscribe_to_asset_class(
        self,
        asset_class: str,
        data_types: Optional[List[DataType]] = None,
        exchanges: Optional[List[str]] = None,
    ) -> List[str]:
        """Subscribe to all exchanges for an asset class.

        Use this pattern when your consumer needs data from all exchanges within
        an asset class. Example: Cross-exchange arbitrage bot for crypto.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto')
            data_types: Optional list of data types to filter (default: all)
            exchanges: Optional list of exchanges to filter (default: all)

        Returns:
            Sorted list of topic names for this asset class.

        Examples:
            >>> builder = SubscriptionBuilder()
            >>> # All crypto topics across all exchanges
            >>> topics = builder.subscribe_to_asset_class('crypto')

            >>> # Only crypto trades, all exchanges
            >>> topics = builder.subscribe_to_asset_class('crypto', data_types=[DataType.TRADES])

            >>> # All data types but only Binance
            >>> topics = builder.subscribe_to_asset_class('crypto', exchanges=['binance'])
        """
        if data_types is None:
            data_types = list(DataType)

        asset_cfg = self.topic_builder.config['asset_classes'][asset_class]

        if exchanges is None:
            exchanges = list(asset_cfg['exchanges'].keys())

        topics = []
        for exchange in exchanges:
            for dt in data_types:
                topic = self.topic_builder.build_topic_name(asset_class, dt, exchange)
                topics.append(topic)

        logger.debug(
            "Built asset class subscription",
            asset_class=asset_class,
            exchanges=exchanges,
            data_types=[dt.value for dt in data_types],
            topic_count=len(topics),
            topics=topics,
        )

        return sorted(topics)

    def subscribe_to_data_type(
        self,
        data_type: DataType,
        asset_classes: Optional[List[str]] = None,
        exchanges: Optional[Dict[str, List[str]]] = None,
    ) -> List[str]:
        """Subscribe to a specific data type across exchanges.

        Use this pattern when your consumer only cares about one type of data
        (e.g., only trades, only quotes) across multiple exchanges/asset classes.
        Example: Trade execution monitor that only needs trades.

        Args:
            data_type: Data type (TRADES, QUOTES, REFERENCE_DATA)
            asset_classes: Optional list of asset classes to filter (default: all)
            exchanges: Optional dict mapping asset class to exchange list.
                      Example: {'equities': ['asx', 'nyse'], 'crypto': ['binance']}
                      If None, subscribes to all exchanges for all asset classes.

        Returns:
            Sorted list of topic names for this data type.

        Examples:
            >>> builder = SubscriptionBuilder()
            >>> # All trades across all exchanges
            >>> topics = builder.subscribe_to_data_type(DataType.TRADES)

            >>> # Only equities trades
            >>> topics = builder.subscribe_to_data_type(DataType.TRADES, asset_classes=['equities'])

            >>> # Trades from specific exchanges
            >>> topics = builder.subscribe_to_data_type(
            ...     DataType.TRADES,
            ...     exchanges={'equities': ['asx'], 'crypto': ['binance']}
            ... )
        """
        if asset_classes is None:
            asset_classes = list(self.topic_builder.config['asset_classes'].keys())

        topics = []

        for asset_class in asset_classes:
            asset_cfg = self.topic_builder.config['asset_classes'][asset_class]

            # Determine which exchanges to use for this asset class
            if exchanges is not None and asset_class in exchanges:
                # Use specified exchanges for this asset class
                exchange_list = exchanges[asset_class]
            else:
                # Use all exchanges for this asset class
                exchange_list = list(asset_cfg['exchanges'].keys())

            for exchange in exchange_list:
                topic = self.topic_builder.build_topic_name(asset_class, data_type, exchange)
                topics.append(topic)

        logger.debug(
            "Built data type subscription",
            data_type=data_type.value,
            asset_classes=asset_classes,
            topic_count=len(topics),
            topics=topics,
        )

        return sorted(topics)

    def subscribe_to_specific_topics(
        self,
        topic_specs: List[tuple],
    ) -> List[str]:
        """Subscribe to a specific list of topics by specification.

        Use this pattern for very targeted subscriptions where you know exactly
        which topics you need.

        Args:
            topic_specs: List of (asset_class, data_type, exchange) tuples

        Returns:
            Sorted list of topic names.

        Examples:
            >>> builder = SubscriptionBuilder()
            >>> specs = [
            ...     ('equities', DataType.TRADES, 'asx'),
            ...     ('crypto', DataType.QUOTES, 'binance'),
            ... ]
            >>> topics = builder.subscribe_to_specific_topics(specs)
        """
        topics = []

        for asset_class, data_type, exchange in topic_specs:
            topic = self.topic_builder.build_topic_name(asset_class, data_type, exchange)
            topics.append(topic)

        logger.debug(
            "Built specific topic subscription",
            topic_count=len(topics),
            topics=topics,
        )

        return sorted(topics)

    def subscribe_to_all(self) -> List[str]:
        """Subscribe to all topics in the system.

        Use with caution - this subscribes to ALL topics across ALL exchanges
        and asset classes. High data volume.

        Returns:
            Sorted list of all topic names.

        Examples:
            >>> builder = SubscriptionBuilder()
            >>> all_topics = builder.subscribe_to_all()
        """
        topics = self.topic_builder.list_all_topics()

        logger.warning(
            "Subscribing to ALL topics - high data volume",
            topic_count=len(topics),
        )

        return topics


# Global singleton
_subscription_builder: Optional['SubscriptionBuilder'] = None


def get_subscription_builder() -> SubscriptionBuilder:
    """Get global SubscriptionBuilder instance.

    Returns:
        SubscriptionBuilder instance.

    Examples:
        >>> from k2.kafka.patterns import get_subscription_builder
        >>> builder = get_subscription_builder()
        >>> topics = builder.subscribe_to_exchange('equities', 'asx')
    """
    global _subscription_builder
    if _subscription_builder is None:
        _subscription_builder = SubscriptionBuilder()
    return _subscription_builder


# Public API
__all__ = [
    'SubscriptionBuilder',
    'get_subscription_builder',
]
