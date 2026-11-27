"""W3-3: `Authorization: Bearer <key>` to a principal through api/keys.json, served only for a
principal of the release's compiled/api/principals.json; the key is never logged or echoed."""

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import DELAYED, FULL, KEYS, RELEASE_ID, SERVICE, UNSERVED, bearer, write_keys
from fastapi.testclient import TestClient

from k2api import app as api
from k2api import keys

DATA = "/v1/data/sensor-net/station-readings/silver_readings"
PRODUCT = "/v1/products/sensor-net/station-readings"


def caller(releases: Path, header: str | None, required: bool = True) -> str | None:
    request = SimpleNamespace(headers={} if header is None else {"authorization": header})
    return api.caller(request, releases / RELEASE_ID, required)  # type: ignore[arg-type]


def assert_unauthorized(r) -> None:
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": r.json()["message"]} and r.json()["message"]
    assert r.headers["www-authenticate"] == "Bearer"


def test_a_served_key_is_its_principal(releases: Path, keys_file: Path) -> None:
    assert caller(releases, f"Bearer {FULL}") == "p.analyst-full"
    assert caller(releases, f"bearer {DELAYED}") == "p.analyst-delayed"  # the scheme is case-insensitive (RFC 9110)


def test_no_key_is_anonymous_where_a_key_is_optional(releases: Path, keys_file: Path) -> None:
    assert caller(releases, None, required=False) is None


def test_a_data_route_without_a_key_is_401_with_the_frozen_body(client: TestClient) -> None:
    assert_unauthorized(client.get(DATA))


@pytest.mark.parametrize("header", [
    FULL,  # no scheme
    f"Basic {FULL}", f"Token {FULL}", f"Bearer: {FULL}", f"Bearer  {FULL}", f"Bearer {FULL} ",
    f"Bearer {FULL}x", f"Bearer {FULL[:-1]}", "Bearer ", "Bearer", "",
    f"Bearer {FULL}\tx", "Bearer k2_" + "F" * 300, "Bearer k2_' OR '1'='1",
])
def test_a_key_in_the_wrong_form_or_unknown_is_401(client: TestClient, header: str) -> None:
    assert_unauthorized(client.get(DATA, headers={"Authorization": header}))
    assert_unauthorized(client.get(PRODUCT, headers={"Authorization": header}))


def test_a_key_whose_principal_the_release_does_not_serve_is_401(client: TestClient) -> None:
    for key in (UNSERVED, SERVICE):  # in keys.json, not in principals.json: never acted as
        assert_unauthorized(client.get(DATA, headers=bearer(key)))
        assert_unauthorized(client.get(PRODUCT, headers=bearer(key)))


def test_a_rotated_key_needs_no_restart(releases: Path, keys_file: Path) -> None:
    assert caller(releases, f"Bearer {FULL}") == "p.analyst-full"
    rotated = "k2_" + "R" * 43
    write_keys(keys_file, {rotated: "p.analyst-full"})  # make api-key ROTATE=1 drops the old key
    os.utime(keys_file, ns=(1, 1))  # a new mtime, whatever the clock's granularity
    assert caller(releases, f"Bearer {rotated}") == "p.analyst-full"
    with pytest.raises(api.Refused):
        caller(releases, f"Bearer {FULL}")


def test_a_key_file_that_does_not_parse_keeps_the_last_map(releases: Path, keys_file: Path) -> None:
    assert caller(releases, f"Bearer {FULL}") == "p.analyst-full"
    keys_file.write_text('{"half a rewrite')
    assert caller(releases, f"Bearer {FULL}") == "p.analyst-full"


def test_no_key_file_serves_no_key(releases: Path, keys_file: Path) -> None:
    keys_file.unlink()
    with pytest.raises(api.Refused):
        caller(releases, f"Bearer {FULL}")


def test_entries_that_are_not_a_sha256_hex_are_ignored(releases: Path, keys_file: Path) -> None:
    keys_file.write_text(f'{{"{FULL}": "p.analyst-full", "{"0" * 64}": 7}}')  # the key itself, not its hash
    with pytest.raises(api.Refused):
        caller(releases, f"Bearer {FULL}")


def test_the_key_is_never_logged_nor_echoed(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    answers = []
    for key in KEYS:
        for header in (f"Bearer {key}", f"Basic {key}", f"Bearer {key}x"):
            for path in (DATA, PRODUCT, "/v1/release"):
                answers.append(client.get(path, headers={"Authorization": header}))
    for key in KEYS:
        assert all(key not in r.text and key not in str(r.headers) for r in answers)
        assert key not in caplog.text
        assert all(key not in str(record.args) for record in caplog.records)
    assert keys.principal(f"Bearer {FULL}") == "p.analyst-full"  # the keys were live all along
