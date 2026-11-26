"""Emits model.pipeline_path(<product id>) (contracts/schema/pipeline.schema.json) for every
product with a schedule, model.DAGS (dags.schema.json) and model.ONESHOTS (oneshots.schema.json:
per kind the image from model.images, limits, env names, networks, secret mounts; fetch never on
k2-catalog). Inputs at model.input_relation; maintenance reads model.RETENTION, not a copy.
Filled by step S16.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S16")
