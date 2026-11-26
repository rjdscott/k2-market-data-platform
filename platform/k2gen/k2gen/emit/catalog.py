"""Emits model.CATALOG_INDEX and model.catalog_path(<product id>) for every product
(contracts/schema/catalog-product.schema.json): reader names only (model.reader_relation), never a
__base name; dbtDocs is model.dbt_docs for a dbt output, null otherwise; a build serving output
has relation null and carries its serves.
Filled by step S18.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S18")
