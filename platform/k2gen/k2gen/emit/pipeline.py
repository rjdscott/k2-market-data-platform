"""Emits compiled/products/<product id>/pipeline.json (contracts/schema/pipeline.schema.json),
compiled/airflow/dags.json and compiled/oneshots.json (per kind: image, limits, the A4 and A7
environment allow-lists, networks; fetch never on k2-catalog).

Filled by step S16.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S16")
