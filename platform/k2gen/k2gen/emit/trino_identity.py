"""Emits compiled/trino/etc/password-authenticator.properties: the file authenticator,
file.password-file=/run/k2-secrets/trino/password.db, file.refresh-period=5s. The password file
itself is ${K2_SECRETS}/trino/password.db (model.SECRET_FILES), never in a release.
Filled by step S13.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S13")
