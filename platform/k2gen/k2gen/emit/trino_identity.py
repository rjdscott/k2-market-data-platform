"""Emits compiled/trino/etc/password-authenticator.properties: Trino's file password
authenticator (ADR-060), reading /run/k2-secrets/trino/password.db and rereading it every 5 s.
Filled by step S13.

The pair with S11's config.properties (HTTPS only on 8443, `http-server.authentication.type=
PASSWORD`, the internal shared secret from the environment) is all Trino 484 needs for password
login: Trino accepts a password only over HTTPS, and this file names the authenticator. The
password file itself, one `<user>:<bcrypt, cost 10>` line per principal, is
${K2_SECRETS}/trino/password.db (model.SECRET_FILES), mounted read-only by its directory, and
never in a release: no hash is compiled, so this file is the same for every document set.
It is read once, at start (`model.NEEDS_BY_PREFIX`: a change needs `trinoRestart`); the password
file it names is reread every REFRESH, so a password made, rotated or removed (S14's
scripts/principal-secret.py, which writes in place under a lock) takes effect within one refresh
and no restart. A refresh also drops the authenticator's cache of verified passwords, so a
rotated password stops working at the same time (measured, docs/runbooks/tls-and-passwords.md).
"""

from k2gen.model import SECRET_FILES, Model

PATH = "compiled/trino/etc/password-authenticator.properties"
PASSWORD_FILE = next(f.mount for f in SECRET_FILES if f.path == "trino/password.db")
REFRESH = "5s"  # A10; release-verify and scripts/verify/identity.sh wait two periods

FILE = f"""\
# Compiled by k2gen (emit/trino_identity.py). Never edited.
# Password login over HTTPS (config.properties): Trino's file authenticator. The file holds a
# bcrypt line per principal and lives under ${{K2_SECRETS}}, never in a release; it is reread
# every {REFRESH}, so a new, rotated or removed password needs no restart.
password-authenticator.name=file
file.password-file={PASSWORD_FILE}
file.refresh-period={REFRESH}
"""


def emit(model: Model) -> dict[str, str]:
    return {PATH: FILE}
