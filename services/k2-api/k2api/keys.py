"""API keys: `Authorization: Bearer k2_<secret>` to a principal, through ${K2_SECRETS}/api/keys.json,
`{"<sha256 hex of the whole key>": "<principal>"}` (k2gen.model.SECRET_FILES; written by
scripts/principal-secret.py, `make api-key`). Never a file in git or in a release.

The file is reread whenever it changes, so a new or rotated key needs no restart: its writer
rewrites it in place under an exclusive flock (a rename would never reach the container through
Docker Desktop), and this reads it under a shared one. The key itself is hashed at once and is
never stored, logged or put in a message.
"""

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re

KEYS_FILE = "/run/k2-secrets/api/keys.json"  # the directory is mounted, read-only
BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] ([A-Za-z0-9_-]{1,256})")  # make api-key: k2_ and token_urlsafe(32)
DIGEST = re.compile(r"[0-9a-f]{64}")

log = logging.getLogger("k2api.keys")
_seen: dict = {"stamp": None, "keys": {}}


def _keys() -> dict[str, str]:
    try:
        st = os.stat(KEYS_FILE)
    except FileNotFoundError:
        return {}
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    if stamp != _seen["stamp"]:
        with open(KEYS_FILE) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH)
            except OSError:  # ponytail: a file system without locks; a torn read fails to parse below
                pass
            text = f.read()
        try:
            loaded = json.loads(text) if text.strip() else {}
        except ValueError:
            log.warning("api/keys.json does not parse; keeping the map read before")
            return _seen["keys"]
        if not isinstance(loaded, dict):
            loaded = {}
        _seen.update(stamp=stamp, keys={h: p for h, p in loaded.items()
                                         if DIGEST.fullmatch(h) and isinstance(p, str)})
    return _seen["keys"]


def principal(authorization: str) -> str | None:
    """The principal of the key in an Authorization header, or None for any other header."""
    match = BEARER.fullmatch(authorization)
    if not match:
        return None
    digest = hashlib.sha256(match.group(1).encode()).hexdigest()
    found = None
    for h, p in _keys().items():  # every entry, each in constant time: no early exit to time
        if hmac.compare_digest(h, digest):
            found = p
    return found
