"""K2 Market Data Platform."""

# Expose submodules for easier imports and to support unittest.mock patching
from . import common, kafka, schemas, storage

__all__ = ["common", "kafka", "schemas", "storage"]
