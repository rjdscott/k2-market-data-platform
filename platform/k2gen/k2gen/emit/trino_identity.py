"""Emits compiled/trino/password-authenticator.properties (file authenticator, 5 s refresh). The password
file itself holds hashes, so it lives under ${K2_SECRETS}, never in a release (the ruling on Q4).

Filled by step S13.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S13")
