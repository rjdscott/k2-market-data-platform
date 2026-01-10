"""K2 Market Data Platform."""

# Expose submodules for easier imports and to support unittest.mock patching
from . import common
from . import storage
from . import kafka
from . import schemas

__all__ = ['common', 'storage', 'kafka', 'schemas']
