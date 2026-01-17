"""Table Mapping Configuration for K2 Market Data Platform.

This module provides centralized table name resolution with support for:
- Logical-to-physical table name mapping
- Environment-specific table configurations
- Migration-safe fallback table resolution
- Schema version management (v1, v2, future versions)

Architecture:
- Logical names: "trades", "quotes", "reference_data" (what code uses)
- Physical names: "market_data.trades", "market_data.trades_v2" (what exists in Iceberg)
- Configuration-driven mapping with environment-specific overrides

Examples:
    resolver = TableResolver()

    # Resolve logical table name to physical name
    physical_name = resolver.resolve_table_name("trades")
    # Returns: "market_data.trades" or "market_data.trades_v2" based on config

    # Get all mapped tables
    tables = resolver.get_all_mappings()
    # Returns: {"trades": "market_data.trades", "quotes": "market_data.quotes"}

    # Check table existence with fallback
    table_name = resolver.resolve_with_fallback("trades", ["market_data.trades_v2"])
    # Tries configured table first, then fallbacks if not found
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from k2.common.config import config
from k2.common.logging import get_logger

logger = get_logger(__name__, component="table_resolver")


class SchemaVersion(str, Enum):
    """Supported schema versions."""

    V1 = "v1"  # Legacy ASX-specific schemas
    V2 = "v2"  # Industry-standard hybrid schemas


class TableType(str, Enum):
    """Table types for logical naming."""

    TRADES = "trades"
    QUOTES = "quotes"
    REFERENCE_DATA = "reference_data"
    MARKET_SUMMARIES = "market_summaries"


@dataclass
class TableMapping:
    """Table mapping configuration with metadata."""

    logical_name: str  # Logical name used in code (e.g., "trades")
    physical_name: str  # Physical name in Iceberg (e.g., "market_data.trades_v2")
    schema_version: SchemaVersion  # Schema version (v1, v2)
    description: str = ""  # Human-readable description
    is_default: bool = False  # Whether this is the default mapping for logical name
    environment_overrides: dict[str, str] = field(default_factory=dict)  # env -> physical_name
    fallback_tables: list[str] = field(
        default_factory=list
    )  # Fallback tables if primary doesn't exist


class TableResolver:
    """Centralized table name resolution with configuration-driven mapping.

    Provides logical-to-physical table name resolution with support for:
    - Environment-specific table configurations
    - Migration-safe fallback resolution
    - Schema version management
    - Table existence validation

    Usage:
        resolver = TableResolver()

        # Resolve logical name to physical name
        physical_name = resolver.resolve_table_name("trades")

        # Resolve with fallback tables
        table_name = resolver.resolve_with_fallback("trades", ["market_data.trades_v2"])

        # Get all mappings
        mappings = resolver.get_all_mappings()
    """

    def __init__(self, environment: str | None = None):
        """Initialize table resolver.

        Args:
            environment: Environment name (defaults to config environment)
        """
        self.environment = environment or config.environment
        self._mappings = self._load_mappings()
        self._reverse_cache = {}  # physical_name -> logical_name cache

        logger.info(
            "Table resolver initialized",
            environment=self.environment,
            mappings_count=len(self._mappings),
        )

    def _load_mappings(self) -> dict[str, TableMapping]:
        """Load table mappings from configuration.

        Returns:
            Dictionary mapping logical names to TableMapping objects
        """
        # Default mappings - can be overridden by configuration
        default_mappings = {
            str(TableType.TRADES): TableMapping(
                logical_name=str(TableType.TRADES),
                physical_name="market_data.trades",  # Default without version suffix
                schema_version=SchemaVersion.V2,
                description="Trade records with price/quantity/timestamp",
                is_default=True,
                fallback_tables=["market_data.trades_v2", "market_data.trades_v1"],
            ),
            str(TableType.QUOTES): TableMapping(
                logical_name=str(TableType.QUOTES),
                physical_name="market_data.quotes",
                schema_version=SchemaVersion.V2,
                description="Quote records with bid/ask data",
                is_default=True,
                fallback_tables=["market_data.quotes_v2", "market_data.quotes_v1"],
            ),
            str(TableType.REFERENCE_DATA): TableMapping(
                logical_name=str(TableType.REFERENCE_DATA),
                physical_name="market_data.reference_data",
                schema_version=SchemaVersion.V2,
                description="Reference data for symbols/instruments",
                is_default=True,
                fallback_tables=["market_data.reference_data_v2"],
            ),
            str(TableType.MARKET_SUMMARIES): TableMapping(
                logical_name=str(TableType.MARKET_SUMMARIES),
                physical_name="market_data.market_summaries",
                schema_version=SchemaVersion.V2,
                description="OHLCV market summaries",
                is_default=True,
                fallback_tables=["market_data.market_summaries_v2"],
            ),
        }

        # Apply environment-specific overrides from config if available
        # This allows different table names for different environments
        # TODO: Add table_mappings config to DatabaseConfig when needed

        return default_mappings

    def resolve_table_name(
        self,
        logical_name: str | TableType,
        schema_version: SchemaVersion | None = None,
        use_fallback: bool = False,
    ) -> str:
        """Resolve logical table name to physical name.

        Args:
            logical_name: Logical table name (e.g., "trades" or TableType.TRADES)
            schema_version: Optional schema version preference
            use_fallback: Whether to try fallback tables if primary doesn't exist

        Returns:
            Physical table name (e.g., "market_data.trades_v2")

        Raises:
            ValueError: If logical name is not configured
        """
        logical_key = str(logical_name)
        if logical_key not in self._mappings:
            available = list(self._mappings.keys())
            raise ValueError(f"Unknown logical table name: {logical_name}. Available: {available}")

        mapping = self._mappings[logical_key]

        # Apply environment-specific override if exists
        if self.environment in mapping.environment_overrides:
            physical_name = mapping.environment_overrides[self.environment]
            logger.debug(
                "Using environment-specific table override",
                logical_name=logical_key,
                environment=self.environment,
                physical_name=physical_name,
            )
        else:
            physical_name = mapping.physical_name

        # Apply schema version preference if specified
        if schema_version and mapping.schema_version != schema_version:
            # Look for alternative mapping with preferred schema version
            for alt_mapping in self._mappings.values():
                if (
                    alt_mapping.logical_name == logical_key
                    and alt_mapping.schema_version == schema_version
                ):
                    physical_name = alt_mapping.physical_name
                    break

        if use_fallback:
            return self.resolve_with_fallback(logical_key, [physical_name])

        logger.debug(
            "Resolved table name",
            logical_name=logical_key,
            physical_name=physical_name,
            schema_version=schema_version,
        )

        return physical_name

        logger.debug(
            "Resolved table name",
            logical_name=logical_name,
            physical_name=physical_name,
            schema_version=schema_version,
        )

        return physical_name

    def resolve_with_fallback(
        self,
        logical_name: str,
        candidate_tables: list[str],
        catalog: Any = None,
    ) -> str:
        """Resolve table name with fallback validation.

        Args:
            logical_name: Logical table name for context
            candidate_tables: List of candidate physical table names
            catalog: Optional Iceberg catalog for existence checking

        Returns:
            First existing physical table name from candidates
        """
        mapping = self._mappings.get(logical_name)
        if not mapping:
            raise ValueError(f"Unknown logical table name: {logical_name}")

        # Combine primary table with fallbacks
        all_candidates = candidate_tables + mapping.fallback_tables

        for candidate in all_candidates:
            try:
                # If catalog provided, check table existence
                if catalog:
                    catalog.load_table(candidate)
                    logger.debug(
                        "Table found in catalog",
                        table=candidate,
                        logical_name=logical_name,
                    )
                    return candidate
                else:
                    # No catalog available - return first candidate
                    logger.debug(
                        "No catalog provided - returning first candidate",
                        table=candidate,
                        logical_name=logical_name,
                    )
                    return candidate
            except Exception:
                logger.debug(
                    "Table not found or not accessible",
                    table=candidate,
                    logical_name=logical_name,
                )
                continue

        # If no table found, return the first candidate (will fail later if invalid)
        logger.warning(
            "No existing table found - returning first candidate",
            logical_name=logical_name,
            candidates_tried=all_candidates,
            fallback=all_candidates[0],
        )
        return all_candidates[0]

    def get_all_mappings(self) -> dict[str, TableMapping]:
        """Get all table mappings.

        Returns:
            Dictionary mapping logical names to TableMapping objects
        """
        return self._mappings.copy()

    def get_mapping(self, logical_name: str) -> TableMapping:
        """Get mapping for a specific logical name.

        Args:
            logical_name: Logical table name

        Returns:
            TableMapping object

        Raises:
            ValueError: If logical name is not configured
        """
        logical_key = str(logical_name)
        if logical_key not in self._mappings:
            available = list(self._mappings.keys())
            raise ValueError(f"Unknown logical table name: {logical_name}. Available: {available}")

        return self._mappings[logical_key]

    def get_schema_version(self, logical_name: str | TableType) -> SchemaVersion:
        """Get schema version for a logical table name.

        Args:
            logical_name: Logical table name

        Returns:
            Schema version (v1, v2)
        """
        mapping = self.get_mapping(logical_name)
        return mapping.schema_version

    def is_v2_table(self, logical_name: str | TableType) -> bool:
        """Check if a logical table uses v2 schema.

        Args:
            logical_name: Logical table name

        Returns:
            True if v2 schema, False otherwise
        """
        return self.get_schema_version(logical_name) == SchemaVersion.V2


# Global resolver instance - shared across application
_global_resolver: TableResolver | None = None


def get_table_resolver() -> TableResolver:
    """Get global table resolver instance.

    Returns:
        Shared TableResolver instance
    """
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = TableResolver()
    return _global_resolver


def resolve_table_name(
    logical_name: str | TableType,
    schema_version: SchemaVersion | None = None,
    use_fallback: bool = False,
) -> str:
    """Convenience function to resolve table name using global resolver.

    Args:
        logical_name: Logical table name
        schema_version: Optional schema version preference
        use_fallback: Whether to try fallback tables

    Returns:
        Physical table name
    """
    resolver = get_table_resolver()
    return resolver.resolve_table_name(
        logical_name=logical_name,
        schema_version=schema_version,
        use_fallback=use_fallback,
    )
