"""Emits compiled/catalog/index.json and compiled/catalog/products/<product id>.json
(contracts/schema/catalog-product.schema.json): reader names only, never a __base name.

Filled by step S18.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S18")
