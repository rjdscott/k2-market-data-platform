"""emit/trino_identity.py (S13): Trino's password authenticator file, compiled from the good fixture
set and from the repository's own documents, each held to its golden file (golden/trino_identity/
and golden/trino_identity/repository/), and the settings password login rests on asserted one by
one, with S11's config.properties beside it, so a golden regenerated carelessly still fails here.

K2GEN_UPDATE_GOLDEN=1 rewrites this emitter's golden files and nothing else; review the diff as code.
"""

import os
import shutil
from pathlib import Path

import pytest
from test_emit_trino_etc import SETS, props

from k2gen import model
from k2gen.emit import trino_etc, trino_identity

GOLDEN = Path(__file__).parent / "golden" / "trino_identity"
ROOTS = {"good": GOLDEN, "repository": GOLDEN / "repository"}
PATH = "compiled/trino/etc/password-authenticator.properties"


def compiled(name: str) -> dict[str, str]:
    return trino_identity.emit(model.load(SETS[name][0]()))


@pytest.mark.parametrize("name", SETS)
def test_each_set_compiles_to_its_golden_file(name: str) -> None:
    files, root = compiled(name), ROOTS[name]
    if os.environ.get("K2GEN_UPDATE_GOLDEN"):
        shutil.rmtree(root / "compiled", ignore_errors=True)
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
    golden = {p.relative_to(root).as_posix(): p.read_text() for p in sorted((root / "compiled").rglob("*")) if p.is_file()}
    assert files == golden


@pytest.mark.parametrize("name", SETS)
def test_one_file_in_etc_and_no_password_or_hash_in_the_release(name: str) -> None:
    files = compiled(name)
    assert list(files) == [PATH] and model.need_of(PATH) == "trinoRestart"
    text = files[PATH]
    assert "$2" not in text, "no bcrypt hash: password.db lives under ${K2_SECRETS}"
    m = model.load(SETS[name][0]())
    assert not [p.id for p in m.principals if p.id in text], "the file names no principal: they live in password.db"


def test_the_file_authenticator_reads_password_db_where_trino_mounts_it_every_five_seconds() -> None:
    auth = props(compiled("good")[PATH])
    db = next(f for f in model.SECRET_FILES if f.path == "trino/password.db")
    assert auth == {"password-authenticator.name": "file", "file.password-file": db.mount, "file.refresh-period": "5s"}
    assert db.mount == "/run/k2-secrets/trino/password.db" and db.services == ("trino",)


def test_password_login_is_on_and_only_over_https_in_the_etc_it_sits_in() -> None:
    """The authenticator is used only because config.properties (S11) asks for PASSWORD, and Trino
    takes a password only over HTTPS: both halves of the pair, in the etc this file joins."""
    m = model.load(SETS["good"][0]())
    etc = trino_etc.emit(m)
    assert not set(etc) & set(trino_identity.emit(m)), "S11 emits no authenticator file of its own"
    config = props(etc["compiled/trino/etc/config.properties"])
    assert config["http-server.authentication.type"] == "PASSWORD"
    assert config["http-server.http.enabled"] == "false" and config["http-server.https.enabled"] == "true"
    assert "http-server.authentication.allow-insecure-over-http" not in config
    assert config["internal-communication.shared-secret"] == "${ENV:K2_TRINO_SHARED_SECRET}"
