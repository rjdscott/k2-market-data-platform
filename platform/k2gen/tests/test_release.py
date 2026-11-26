"""k2gen release and k2gen diff (S08) on the good fixture set.

The emitters are stubs while phase 001 fills them, and so are docset.read and model.load (S04).
So most tests compile the good set's documents through two small fakes of S04's readers and a
test-only emitter registered here, never in the package: the compiler is what is under test, not
an emitter. The last test runs the real CLI and the real readers, and skips only while S04's are
stubs.
"""

import dataclasses
import hashlib
import json
import locale
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from k2gen import cli, diff, docset, emit, model, release
from k2gen.model import DocumentSet, Needs, Provenance

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
TAG = "v3.0.0-alpha.3-rc.1"
IMAGES = {"trino": "trinodb/trino@sha256:" + "b" * 64, "airflow-scheduler": "k2-airflow:e7d3799ecc09"}
COMPOSE: dict[str, Any] = {"services": {
    "trino": {"image": "trinodb/trino:484", "command": "a"},
    "airflow-scheduler": {"image": "k2-airflow", "command": "a"},
    "minio": {"image": "minio", "command": "a"},
    "capture-binance": {"image": "k2-capture", "command": "a"},
}}
ETC = "compiled/trino/etc/config.properties"
RULES = "compiled/trino/rules.json"
DDL = model.ddl_path("spark", "k2", "station_readings", "bronze_readings")
DAG = {"dagId": "daily_load", "product": "ops-insight.daily-load", "status": "active", "schedule": "30 1 * * *",
       "pipeline": model.pipeline_path("ops-insight.daily-load"), "tasks": ["resolve", "build", "record"]}


def dags(*entries: dict[str, Any]) -> str:
    return json.dumps({"dags": list(entries)})


def write_set(dst: Path, compose: dict[str, Any]) -> Path:
    """The good fixture set as `k2gen docset` would write it: its documents, a compose file and
    a provenance.json that hashes them."""
    docs = {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in GOOD.rglob("*") if p.is_file()}
    docs[model.COMPOSE] = yaml.safe_dump(compose).encode()
    for path, data in docs.items():
        (dst / path).parent.mkdir(parents=True, exist_ok=True)
        (dst / path).write_bytes(data)
    commit = "0123456789abcdef" * 2 + "01234567"
    prov = {"source": "git", "tag": TAG, "commit": commit, "tagger": "Example Owner <owner@example.org>",
            "taggedAt": "2026-11-26T00:00:00Z", "setSha256": model.set_sha256(docs),
            "documents": [{"path": p, "sha256": hashlib.sha256(docs[p]).hexdigest(), "lastCommit": commit, "pr": None} for p in sorted(docs)]}
    assert model.schema_errors("provenance", prov) == []
    (dst / model.PROVENANCE).write_bytes((json.dumps(prov, indent=2, sort_keys=True) + "\n").encode())
    return dst


def fake_read(set_dir: Path) -> DocumentSet:
    """S04's docset.read, as small as the compiler needs: documents by path, and the provenance."""
    files = {p.relative_to(set_dir).as_posix(): p.read_bytes() for p in set_dir.rglob("*") if p.is_file()}
    p = json.loads(files.pop(model.PROVENANCE))
    return DocumentSet(files, Provenance(p["source"], p["tag"], p["commit"], p["tagger"], p["taggedAt"], p["setSha256"], ()))


def fake_load(docs: DocumentSet, images: Any = None) -> Any:
    """S04's model.load, as small as the compiler needs: each product's id, version, status, path."""
    products = []
    for path, data in docs.documents.items():
        if model.document_kind(path) == "product":
            d = model.parse(path, data)
            products.append(SimpleNamespace(id=d["id"], version=d["version"], status=d["status"], path=path.rsplit("/", 1)[0]))
    return SimpleNamespace(products=tuple(products), provenance=docs.provenance, images=images)


def emitter(name: str, out: dict[str, str] | None) -> SimpleNamespace:
    """A test-only emitter: returns `out` (read at each call), or is a stub when None."""
    def emit_(model: object) -> dict[str, str]:
        if out is None:
            raise NotImplementedError("S99")
        return dict(out)

    return SimpleNamespace(__name__=f"k2gen.emit.{name}", emit=emit_)


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """What the test-only emitter returns; a test sets it before each compile. A stub emitter
    sits beside it, so every compile also proves a stub is skipped."""
    out: dict[str, str] = {}
    monkeypatch.setattr(docset, "read", fake_read)
    monkeypatch.setattr(model, "load", fake_load)
    monkeypatch.setattr(emit, "modules", lambda: [emitter("a_stub", None), emitter("test_only", out)])
    return out


def build(tmp: Path, name: str, previous: Path | None = None, images: dict[str, str] = IMAGES,
          compose: dict[str, Any] = COMPOSE, out: Path | None = None) -> tuple[str, Path]:
    set_dir = write_set(tmp / f"set-{name}", compose)
    images_file = tmp / f"images-{name}.json"
    images_file.write_text(json.dumps(images))
    out = out or tmp / "releases"
    rid = release.release(set_dir, previous, images_file, out, set_dir / model.COMPOSE)
    return rid, out / rid


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_good_set_compiles_with_a_stub_and_a_test_emitter(tmp_path: Path, emitted: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    emitted[RULES] = "{}"
    rid, root = build(tmp_path, "a")
    assert "SKIP emitter a_stub: a stub until step S99" in capsys.readouterr().err
    manifest = json.loads((root / model.MANIFEST).read_bytes())
    assert model.schema_errors("manifest", manifest) == []
    on_disk = tree(root)
    assert manifest["files"] == {p: hashlib.sha256(b).hexdigest() for p, b in on_disk.items() if p != model.MANIFEST}
    assert rid == manifest["release"] == model.release_id(TAG, manifest["files"], IMAGES) and rid.startswith(f"{TAG}-")
    assert on_disk[model.PROVENANCE] == on_disk[f"{model.DOCUMENTS_DIR}/{model.PROVENANCE}"] == (tmp_path / "set-a" / model.PROVENANCE).read_bytes()
    assert on_disk[model.RELEASE_COMPOSE] == on_disk[f"{model.DOCUMENTS_DIR}/{model.COMPOSE}"]
    assert on_disk[f"{model.DOCUMENTS_DIR}/contracts/domains.yaml"] == (GOOD / "contracts/domains.yaml").read_bytes()
    assert [p["id"] for p in manifest["products"]] == ["ops-insight.daily-load", "sensor-net.station-readings"]
    assert manifest["builtAt"] == "2026-11-26T00:00:00Z" and manifest["previous"] is None
    assert release.read(root).manifest.files == manifest["files"]
    assert sorted(p.name for p in (tmp_path / "releases").iterdir()) == [rid]  # no temp left behind


def test_the_sealed_tree_is_read_only(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted[RULES] = "{}"
    _, root = build(tmp_path, "a")
    entries = [root, *root.rglob("*")]
    assert len(entries) > 10
    assert all(stat.S_IMODE(p.stat().st_mode) & 0o222 == 0 for p in entries)
    assert {stat.S_IMODE(p.stat().st_mode) for p in entries} == {0o444, 0o555}


def test_two_machines_compile_one_set_to_one_tree(tmp_path: Path, emitted: dict[str, str]) -> None:
    # Different roots, umask and locale: under LC_ALL=C Python's default text encoding is ASCII,
    # so a write that leaned on the locale would fail on the non-ASCII byte below.
    emitted[RULES] = '{"note": "°C"}'
    saved, mask = locale.setlocale(locale.LC_ALL), os.umask(0o022)
    try:
        rid_a, root_a = build(tmp_path / "one", "a", out=tmp_path / "one" / "releases")
        os.umask(0o077)
        locale.setlocale(locale.LC_ALL, "C")
        assert sys.flags.utf8_mode or locale.getpreferredencoding(False) == "ANSI_X3.4-1968"
        rid_b, root_b = build(tmp_path / "two", "b", out=tmp_path / "elsewhere" / "deeper")
    finally:
        os.umask(mask)
        locale.setlocale(locale.LC_ALL, saved)
    assert rid_a == rid_b
    assert tree(root_a) == tree(root_b)  # manifest.json included: no clock, no absolute path
    assert {p.relative_to(root_a): p.stat().st_mode for p in root_a.rglob("*")} == {p.relative_to(root_b): p.stat().st_mode for p in root_b.rglob("*")}


def test_compiling_again_reports_the_existing_release_and_never_rewrites(tmp_path: Path, emitted: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    emitted[RULES] = "{}"
    rid, root = build(tmp_path, "a")
    before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in [root, *root.rglob("*")]}
    capsys.readouterr()
    assert build(tmp_path, "again")[0] == rid
    assert f"{rid} exists, identical; nothing written" in capsys.readouterr().err
    assert {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in [root, *root.rglob("*")]} == before
    # The same set against another previous is still the same release; its first needs stand.
    emitted[RULES] = "{1}"
    other_rid, other = build(tmp_path, "other", out=tmp_path / "other")
    emitted[RULES] = "{}"
    assert build(tmp_path, "third", previous=other)[0] == rid
    assert "(its needs are against None)" in capsys.readouterr().err and json.loads((root / model.MANIFEST).read_bytes())["previous"] is None


def test_different_content_under_an_existing_id_is_an_error(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted[RULES] = "{}"
    _, root = build(tmp_path, "a")
    target = root / RULES
    target.chmod(0o644)
    target.write_text("{tampered}")
    with pytest.raises(ValueError, match="files differ from its manifest: compiled/trino/rules.json"):
        build(tmp_path, "again")
    assert target.read_text() == "{tampered}"  # refused, not repaired


def test_a_crash_leaves_only_a_temp_directory_that_the_next_run_cleans(tmp_path: Path, emitted: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    emitted[RULES] = "{}"

    def crash(src: object, dst: object) -> None:
        raise KeyboardInterrupt  # what a kill between seal and rename leaves

    with monkeypatch.context() as m:
        m.setattr(os, "rename", crash)
        with pytest.raises(KeyboardInterrupt):
            build(tmp_path, "a")
    (temp,) = (tmp_path / "releases").iterdir()
    assert temp.name.startswith(".v3.0.0") and temp.name.endswith(".tmp") and stat.S_IMODE(temp.stat().st_mode) == 0o555
    rid, _ = build(tmp_path, "b")
    assert [p.name for p in (tmp_path / "releases").iterdir()] == [rid]


@pytest.mark.parametrize(("first", "second", "why"), [
    ("compiled/x", "compiled/x", "emitted by both"),
    ("compiled/x", "compiled/x/y", "both a file and a directory"),
])
def test_two_emitters_on_one_path_are_refused(tmp_path: Path, emitted: dict[str, str], monkeypatch: pytest.MonkeyPatch, first: str, second: str, why: str) -> None:
    monkeypatch.setattr(emit, "modules", lambda: [emitter("a", {first: "1"}), emitter("b", {second: "2"})])
    with pytest.raises(ValueError, match=why):
        build(tmp_path, "a")
    assert not (tmp_path / "releases").exists()


@pytest.mark.parametrize("path", ["/tmp/k2-escape", "compiled/../../k2-escape", "compiled/./k2-escape", "compiled//k2-escape", "compiled/k2-escape/"])
def test_an_emitted_path_escaping_the_release_root_is_refused(tmp_path: Path, emitted: dict[str, str], path: str) -> None:
    emitted[path] = "x"
    with pytest.raises(ValueError, match="not a path under compiled/|not a plain relative path"):
        build(tmp_path, "a")
    assert not (tmp_path / "releases").exists() and not (tmp_path / "k2-escape").exists()


def test_a_document_path_escaping_the_release_root_is_refused(tmp_path: Path, emitted: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def read(set_dir: Path) -> DocumentSet:
        docs = fake_read(set_dir)
        documents = {**docs.documents, "../k2-escape.yaml": b"x"}
        return DocumentSet(documents, dataclasses.replace(docs.provenance, set_sha256=model.set_sha256(documents)))

    monkeypatch.setattr(docset, "read", read)
    with pytest.raises(ValueError, match="documents/../k2-escape.yaml: not a plain relative path"):
        build(tmp_path, "a")
    assert not (tmp_path / "k2-escape.yaml").exists()


def test_a_symlink_in_a_release_is_refused(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted[RULES] = "{}"
    _, root = build(tmp_path, "a")
    (root / "compiled").chmod(0o755)
    (root / "compiled" / "link").symlink_to("/etc")
    with pytest.raises(ValueError, match="symlink"):
        release.read(root)
    with pytest.raises(ValueError, match="symlink"):
        build(tmp_path, "again")  # the existing release under its id
    with pytest.raises(ValueError, match="symlink"):
        build(tmp_path, "next", previous=root, out=tmp_path / "next")


def test_a_set_whose_documents_do_not_match_its_provenance_is_refused(tmp_path: Path, emitted: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docset, "read", lambda d: (lambda s: DocumentSet({**s.documents, "contracts/extra.yaml": b"x"}, s.provenance))(fake_read(d)))
    with pytest.raises(ValueError, match="do not hash to its provenance's setSha256"):
        build(tmp_path, "a")


@pytest.mark.parametrize("ref", ["trinodb/trino:484", "trinodb/trino", "trinodb/trino@sha256:abc", "k2-airflow:latest"])
def test_an_image_without_a_digest_is_refused(tmp_path: Path, emitted: dict[str, str], ref: str) -> None:
    with pytest.raises(ValueError, match="by digest"):
        build(tmp_path, "a", images={**IMAGES, "trino": ref})
    assert not (tmp_path / "releases").exists()


def test_an_emitted_file_is_validated_against_its_schema(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted[model.STORAGE_PLAN] = json.dumps({"warehouses": [], "landing": {"bucket": "k2-landing", "prefixes": []}})
    with pytest.raises(ValueError, match="compiled/lake/storage-plan.json: storage-plan.schema.json"):
        build(tmp_path, "a")
    emitted[model.STORAGE_PLAN] = json.dumps({"warehouses": [{"name": "k2", "remoteSigningEnabled": False, "namespaces": []}],
                                              "landing": {"bucket": "k2-landing", "prefixes": []}})
    build(tmp_path, "b")


@pytest.mark.parametrize("path", ["compiled/a b", "compiled/lake/ddl/spark/NotATable.sql"])
def test_a_manifest_that_fails_its_schema_is_refused(tmp_path: Path, emitted: dict[str, str], path: str) -> None:
    emitted[path] = "x"
    with pytest.raises(ValueError, match="manifest.json fails its schema"):
        build(tmp_path, "a")
    assert not (tmp_path / "releases").exists()


@pytest.mark.parametrize(("files", "ddl"), [({RULES: "{}", ETC: "a", model.DAGS: dags(DAG), DDL: "CREATE TABLE x"}, ("k2.station_readings.bronze_readings",)),
                                            ({}, ())])  # every emitter a stub: still a restart and a drain
def test_the_first_release_needs_everything(tmp_path: Path, emitted: dict[str, str], files: dict[str, str], ddl: tuple[str, ...]) -> None:
    emitted.update(files)
    _, root = build(tmp_path, "a")
    assert release.read(root).manifest.needs == Needs(("airflow-scheduler", "trino"), True, True, ddl)


SCALED = {**COMPOSE["services"], "trino": {"image": "trinodb/trino:484", "command": "b"}}
MOVED_MINIO = {**COMPOSE["services"], "minio": {"image": "minio", "command": "b"}, "capture-binance": {"image": "k2-capture", "command": "b"}}
NONE = Needs((), False, False, ())


@pytest.mark.parametrize(("before", "after", "need"), [
    ({"emitted": {RULES: "a"}}, {"emitted": {RULES: "b"}}, NONE),  # rules-only: read through the pointer
    ({}, {"compose": {"services": SCALED}}, Needs(("trino",), False, False, ())),
    ({}, {"compose": {"services": MOVED_MINIO}}, NONE),  # never recreated by deploy (A1)
    ({}, {"compose": {"services": {**COMPOSE["services"], "k2-api": {"image": "k2-api"}}}}, Needs(("k2-api",), False, False, ())),
    ({}, {"images": {**IMAGES, "trino": "trinodb/trino@sha256:" + "c" * 64}}, Needs(("trino",), False, False, ())),
    ({"emitted": {ETC: "a"}}, {"emitted": {ETC: "b"}}, Needs((), True, False, ())),
    ({"emitted": {ETC: "a"}}, {}, Needs((), True, False, ())),  # an etc file removed
    ({"emitted": {model.DAGS: dags(DAG)}}, {"emitted": {model.DAGS: dags({**DAG, "tasks": ["resolve", "build"]})}}, Needs((), False, True, ())),
    ({"emitted": {model.DAGS: dags(DAG)}}, {"emitted": {model.DAGS: dags(DAG, {**DAG, "dagId": "new_one"})}}, NONE),  # a new DAG drains nothing
    ({"emitted": {model.DAGS: dags(DAG)}}, {"emitted": {model.DAGS: dags()}}, Needs((), False, True, ())),
    ({"emitted": {DDL: "a"}}, {"emitted": {DDL: "a", RULES: "b"}}, NONE),  # unchanged DDL is not reapplied
    ({"emitted": {DDL: "a"}}, {"emitted": {DDL: "b"}}, Needs((), False, False, ("k2.station_readings.bronze_readings",))),
    ({}, {"emitted": {DDL: "a"}}, Needs((), False, False, ("k2.station_readings.bronze_readings",))),
])
def test_needs_follow_what_changed_since_the_previous_release(tmp_path: Path, emitted: dict[str, str], before: dict[str, Any], after: dict[str, Any], need: Needs) -> None:
    emitted.update(before.get("emitted", {}))
    _, old = build(tmp_path, "old", images=before.get("images", IMAGES), compose=before.get("compose", COMPOSE))
    emitted.clear()
    emitted.update(after.get("emitted", {}))
    _, new = build(tmp_path, "new", previous=old, images=after.get("images", IMAGES), compose=after.get("compose", COMPOSE))
    manifest = release.read(new).manifest
    assert manifest.needs == need and manifest.previous == old.name


def test_diff_of_a_release_with_itself_says_nothing_changes(tmp_path: Path, emitted: dict[str, str]) -> None:
    rid, root = build(tmp_path, "a")
    assert diff.diff(root, root) == f"## Release `{rid}` to `{rid}`\n\nNothing changes: both releases hold the same files and images.\n"


def plan(warehouses: dict[str, list[str]], prefixes: list[str]) -> str:
    return json.dumps({"warehouses": [{"name": w, "remoteSigningEnabled": False, "namespaces": ns} for w, ns in sorted(warehouses.items())],
                       "landing": {"bucket": "k2-landing", "prefixes": prefixes}})


def test_diff_prints_files_needs_images_and_storage_plan_in_one_order(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted.update({RULES: "a", ETC: "a", model.STORAGE_PLAN: plan({"k2": ["old_ns"], "gone": []}, ["d/old/"])})
    old_id, old = build(tmp_path, "old")
    emitted.clear()
    emitted.update({RULES: "b", DDL: "c", model.STORAGE_PLAN: plan({"k2": ["new_ns", "old_ns"], "sensor_net": ["x", "x__base"]}, ["d/old/"])})
    images = {**IMAGES, "trino": "trinodb/trino@sha256:" + "c" * 64}
    new_id, new = build(tmp_path, "new", previous=old, images=images, compose={"services": MOVED_MINIO})
    text = diff.diff(old, new)
    assert text == diff.diff(old, new)
    assert text == "\n".join([
        f"## Release `{old_id}` to `{new_id}`", "",
        "### What deploying it needs", "", "| Need | |", "|---|---|",
        "| Recreate | `trino` |", "| Trino restart | yes |", "| DAG drain (a DAG's tasks changed) | no |",
        "| DDL | `k2.station_readings.bronze_readings` |", "",
        "Changed but never recreated by deploy (A1), so recreated by hand: `capture-binance`, `minio`.", "",
        "### Files: 1 added, 1 removed, 6 changed", "",
        "| Change | Path | Need |", "|---|---|---|",
        f"| added | `{DDL}` | ddl |",
        f"| changed | `{model.STORAGE_PLAN}` | none |",
        f"| removed | `{ETC}` | trinoRestart |",
        f"| changed | `{RULES}` | none |",
        f"| changed | `{model.RELEASE_COMPOSE}` | recreate |",
        f"| changed | `{model.DOCUMENTS_DIR}/{model.COMPOSE}` | none |",
        f"| changed | `{model.DOCUMENTS_DIR}/{model.PROVENANCE}` | none |",
        f"| changed | `{model.PROVENANCE}` | none |", "",
        "### Images", "", "| Service | Old | New |", "|---|---|---|",
        f"| `trino` | `{IMAGES['trino']}` | `{images['trino']}` |", "",
        "### Storage plan", "", "| Change | Kind | Name |", "|---|---|---|",
        "| added | namespace | `k2.new_ns` |",
        "| added | namespace | `sensor_net.x` |",
        "| added | namespace | `sensor_net.x__base` |",
        "| removed | warehouse | `gone` |",
        "| added | warehouse | `sensor_net` |", "",
        "Deploy only adds: a removed row stays until `scripts/drop.sh` removes it.", ""])


def test_diff_of_a_rules_only_change_needs_only_the_pointer(tmp_path: Path, emitted: dict[str, str]) -> None:
    emitted[RULES] = "a"
    _, old = build(tmp_path, "old")
    emitted[RULES] = "b"
    _, new = build(tmp_path, "new", previous=old)
    text = diff.diff(old, new)
    assert "Nothing beyond the pointer swap" in text and f"| changed | `{RULES}` | none |" in text
    assert "### Files: 0 added, 0 removed, 1 changed" in text and "No change to the storage plan." in text


def test_cli_release_on_the_good_set_twice_prints_one_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The real readers and emitters, through the CLI; skipped only while S04's are stubs."""
    set_dir = write_set(tmp_path / "set", yaml.safe_load((model.REPO / model.COMPOSE).read_bytes()))
    try:
        model.load(docset.read(set_dir), IMAGES)
    except NotImplementedError as stub:
        pytest.skip(f"docset.read or model.load is a stub until step {stub}")
    (tmp_path / "images.json").write_text(json.dumps(IMAGES))
    args = ["release", "--set", str(set_dir), "--previous", "none", "--images", str(tmp_path / "images.json"), "--out", str(tmp_path / "releases")]
    assert cli.main(args) == 0 and cli.main(args) == 0
    first, second = capsys.readouterr().out.split()
    assert first == second and release.read(tmp_path / "releases" / first).id == first
