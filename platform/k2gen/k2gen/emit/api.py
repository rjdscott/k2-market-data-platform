"""Emits compiled/api/principals.json: the principals k2-api may act as. Key hashes stay under
${K2_SECRETS}, never in a release (the ruling on Q4).

Filled by step S14.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S14")
