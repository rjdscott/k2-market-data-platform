"""Load shedder for priority-based message filtering.

Works with DegradationManager to implement graceful degradation
by shedding low-priority messages under load.
"""

from dataclasses import dataclass
from enum import IntEnum

from k2.common.logging import get_logger
from k2.common.metrics_registry import get_metric

logger = get_logger(__name__, component="load_shedder")


class MessagePriority(IntEnum):
    """Message priority levels."""

    CRITICAL = 1  # Top symbols, trades
    HIGH = 2  # Top 100 symbols
    NORMAL = 3  # All other symbols
    LOW = 4  # Reference data updates


@dataclass
class SymbolTier:
    """Symbol classification by liquidity tier."""

    tier_1: set[str]  # Top 20 - Always process
    tier_2: set[str]  # Top 100 - Process at HIGH and above
    tier_3: set[str]  # All others - Process at NORMAL only


class LoadShedder:
    """Load shedder for priority-based message filtering.

    Usage:
        shedder = LoadShedder()

        # Check if message should be processed
        if shedder.should_process(symbol, degradation_level):
            process_message(msg)
        else:
            # Shed load - skip this message
            pass
    """

    # Default ASX tier classification
    DEFAULT_TIER_1 = {
        "BHP",
        "CBA",
        "CSL",
        "NAB",
        "WBC",
        "ANZ",
        "WES",
        "MQG",
        "RIO",
        "TLS",
        "WOW",
        "FMG",
        "NCM",
        "STO",
        "WPL",
        "AMC",
        "GMG",
        "TCL",
        "COL",
        "ALL",
    }

    DEFAULT_TIER_2 = {
        "REA",
        "QAN",
        "BXB",
        "ORG",
        "JHX",
        "CPU",
        "SHL",
        "ASX",
        "IAG",
        "APA",
        "S32",
        "MIN",
        "SGP",
        "AGL",
        "RMD",
        "SEK",
        "ORI",
        "QBE",
        "MPL",
        "AZJ",
        "WHC",
        "LLC",
        "SCG",
        "EVN",
        "JBH",
        "DXS",
        "GPT",
        "MGR",
        "NST",
        "BOQ",
        "ILU",
        "ALX",
        "VCX",
        "SDF",
        "CHC",
        "TAH",
        "ALU",
        "IPL",
        "HVN",
        "ORA",
        # Abbreviated - would include top 100
    }

    def __init__(
        self,
        tier_1: set[str] | None = None,
        tier_2: set[str] | None = None,
    ):
        self.tier_1 = tier_1 if tier_1 is not None else self.DEFAULT_TIER_1
        self.tier_2 = tier_2 if tier_2 is not None else self.DEFAULT_TIER_2

        # Metrics tracking
        self._total_checked = 0
        self._total_shed = 0

        logger.info(
            "Load shedder initialized",
            tier_1_count=len(self.tier_1),
            tier_2_count=len(self.tier_2),
        )

    def get_priority(self, symbol: str, data_type: str = "trades") -> MessagePriority:
        """Get message priority based on symbol and data type.

        Args:
            symbol: Trading symbol
            data_type: 'trades', 'quotes', or 'reference_data'

        Returns:
            MessagePriority level
        """
        # Reference data is always LOW priority
        if data_type == "reference_data":
            return MessagePriority.LOW

        # Tier classification
        if symbol in self.tier_1:
            return MessagePriority.CRITICAL
        if symbol in self.tier_2:
            return MessagePriority.HIGH
        return MessagePriority.NORMAL

    def should_process(
        self,
        symbol: str,
        degradation_level: int,
        data_type: str = "trades",
    ) -> bool:
        """Check if message should be processed at current degradation level.

        Args:
            symbol: Trading symbol
            degradation_level: Current DegradationLevel value (0-4)
            data_type: 'trades', 'quotes', or 'reference_data'

        Returns:
            True if message should be processed, False to shed
        """
        self._total_checked += 1
        priority = self.get_priority(symbol, data_type)

        # Level 0 (NORMAL): Process everything
        if degradation_level == 0:
            return True

        # Level 1 (SOFT): Still process everything (but skip enrichment)
        if degradation_level == 1:
            return True

        # Level 2 (GRACEFUL): Drop LOW priority
        if degradation_level == 2:
            should_process = priority <= MessagePriority.NORMAL
            if not should_process:
                self._record_shed(symbol, "low_priority", degradation_level)
            return should_process

        # Level 3 (AGGRESSIVE): Only CRITICAL and HIGH
        if degradation_level == 3:
            should_process = priority <= MessagePriority.HIGH
            if not should_process:
                self._record_shed(symbol, "normal_priority", degradation_level)
            return should_process

        # Level 4 (CIRCUIT_BREAK): Only CRITICAL
        if degradation_level == 4:
            should_process = priority == MessagePriority.CRITICAL
            if not should_process:
                self._record_shed(symbol, "non_critical", degradation_level)
            return should_process

        return False

    def _record_shed(self, symbol: str, reason: str, degradation_level: int):
        """Record message shed metrics."""
        self._total_shed += 1

        # Determine tier for metric labels
        if symbol in self.tier_1:
            tier = "tier_1"
        elif symbol in self.tier_2:
            tier = "tier_2"
        else:
            tier = "tier_3"

        try:
            shed_metric = get_metric("messages_shed_total")
            shed_metric.labels(
                service="k2-platform",
                environment="dev",
                component="load_shedder",
                symbol_tier=tier,
                reason=reason,
            ).inc()
        except KeyError:
            # Metric not registered yet - will be added
            pass

        logger.debug(
            "Message shed",
            symbol=symbol,
            tier=tier,
            reason=reason,
            degradation_level=degradation_level,
        )

    def get_shed_stats(
        self,
        symbols: list[str],
        degradation_level: int,
    ) -> dict:
        """Get statistics on what would be shed at given degradation level.

        Useful for understanding impact of degradation.
        """
        processed = sum(1 for s in symbols if self.should_process(s, degradation_level))
        shed = len(symbols) - processed

        return {
            "total": len(symbols),
            "processed": processed,
            "shed": shed,
            "shed_percentage": (shed / len(symbols) * 100) if symbols else 0,
        }

    def get_stats(self) -> dict:
        """Get load shedder statistics."""
        return {
            "total_checked": self._total_checked,
            "total_shed": self._total_shed,
            "shed_rate": (
                (self._total_shed / self._total_checked * 100) if self._total_checked > 0 else 0
            ),
            "tier_1_count": len(self.tier_1),
            "tier_2_count": len(self.tier_2),
        }
