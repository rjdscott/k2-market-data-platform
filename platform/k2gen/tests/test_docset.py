"""docset (S04): the document set of an annotated tag, in a throwaway git repository.

The repository holds the good fixture set, a synthetic compose file and a parser under
domains/, plus files outside the document roots. Tag v0.1.0 has every root; v0.2.0, a later
commit, has contracts/ alone, so the optional roots are skipped.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from k2gen import cli, docset, model
from k2gen.model import DocumentError

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
TAGGER = "Example Owner <owner@example.org>"
# Isolated from the user's git config (signing, hooks), and dated, so the tag is the same every run.
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "Example Author", "GIT_AUTHOR_EMAIL": "author@example.org",
       "GIT_COMMITTER_NAME": "Example Owner", "GIT_COMMITTER_EMAIL": "owner@example.org",
       "GIT_AUTHOR_DATE": "2026-01-02 14:04:05 +1100", "GIT_COMMITTER_DATE": "2026-01-02 14:04:05 +1100"}
PARSER = "domains/sensor-net/parsers/station_csv.py"
PRINCIPALS = "contracts/access/principals.yaml"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, subject: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / model.COMPOSE).write_text("services: {}\n")
    (root / PARSER).parent.mkdir(parents=True)
    (root / PARSER).write_text("def parse(data: bytes) -> list[dict]:\n    return []\n")
    for outside in ("README.md", "docker-compose.yml.bak", "contracts-old/domains.yaml", "tests/access/other.yaml"):
        (root / outside).parent.mkdir(parents=True, exist_ok=True)
        (root / outside).write_text("Outside every document root, though named like one.\n")
    git(root, "init", "-q")
    first = commit(root, "feat: the fixture set (#7)")
    (root / PRINCIPALS).write_text((root / PRINCIPALS).read_text() + "# a later edit\n")
    second = commit(root, "fix: a principals comment, no pull request")
    git(root, "tag", "-a", "v0.1.0", "-m", "Release v0.1.0")
    for gone in (model.COMPOSE, model.EXPECTATIONS, PARSER):
        (root / gone).unlink()
    commit(root, "chore: only contracts/ is left (#9)")
    git(root, "tag", "-a", "v0.2.0", "-m", "Release v0.2.0")
    git(root, "tag", "v0.3.0")  # lightweight: no tagger
    (root / "commits").write_text(f"{first}\n{second}\n")  # untracked: read by the tests, never in a set
    return root


def test_a_tag_gives_every_document_under_the_roots_and_its_provenance(repo: Path) -> None:
    first, second = (repo / "commits").read_text().split()
    docs = docset.load("v0.1.0", repo)
    expected = sorted(p.relative_to(GOOD).as_posix() for p in GOOD.rglob("*") if p.is_file()) + [model.COMPOSE, PARSER]
    assert sorted(docs.documents) == sorted(expected)  # nothing outside the roots, however it is named
    assert docs.documents[model.COMPOSE] == b"services: {}\n"
    p = docs.provenance
    assert (p.source, p.tag, p.commit, p.tagger, p.tagged_at) == ("git", "v0.1.0", second, TAGGER, "2026-01-02T03:04:05Z")
    assert p.set_sha256 == model.set_sha256(docs.documents)
    assert [d.path for d in p.documents] == sorted(docs.documents)
    by_path = {d.path: d for d in p.documents}
    assert (by_path[PRINCIPALS].last_commit, by_path[PRINCIPALS].pr) == (second, None)
    assert (by_path[model.DOMAINS].last_commit, by_path[model.DOMAINS].pr) == (first, 7)
    assert by_path[PARSER].sha256 == hashlib.sha256(docs.documents[PARSER]).hexdigest()


def test_a_root_the_tag_lacks_is_skipped(repo: Path) -> None:
    docs = docset.load("v0.2.0", repo)
    assert all(p.startswith("contracts/") for p in docs.documents) and model.DOMAINS in docs.documents
    assert {d.pr for d in docs.provenance.documents} == {7, None}  # the last commit removed, and so touched, none of them
    assert model.load(docs).expectations == ()


def test_a_tag_gives_the_run_code_under_its_roots_and_nothing_beside_them(repo: Path) -> None:
    factory, dockerfile = repo / "docker/airflow/dags/factory.py", repo / "docker/airflow/Dockerfile"
    factory.parent.mkdir(parents=True)
    factory.write_text("DAGS = []\n")
    dockerfile.write_text("FROM scratch\n")
    commit(repo, "feat: the DAG factory (#11)")
    git(repo, "tag", "-a", "v0.4.0", "-m", "Release v0.4.0")
    docs = docset.load("v0.4.0", repo)
    assert docs.documents["docker/airflow/dags/factory.py"] == b"DAGS = []\n"
    assert "docker/airflow/Dockerfile" not in docs.documents


def test_a_tag_archived_twice_gives_identical_trees(repo: Path, tmp_path: Path) -> None:
    for out in ("a", "b"):
        assert cli.main(["docset", "--tag", "v0.1.0", "--out", str(tmp_path / out), "--repo", str(repo)]) == 0
    diff = subprocess.run(["diff", "-r", str(tmp_path / "a"), str(tmp_path / "b")], capture_output=True, text=True)
    assert (diff.returncode, diff.stdout) == (0, "")
    a, b = (json.loads((tmp_path / out / model.PROVENANCE).read_text()) for out in ("a", "b"))
    assert a["setSha256"] == b["setSha256"] == docset.load("v0.1.0", repo).provenance.set_sha256
    assert model.schema_errors("provenance", a) == []


def test_a_dirty_working_tree_does_not_leak_into_a_tagged_set(repo: Path) -> None:
    clean = docset.load("v0.1.0", repo)
    git(repo, "checkout", "-q", "v0.1.0")
    (repo / model.DOMAINS).write_text("domains: {}\n")  # modified, unstaged
    (repo / PRINCIPALS).write_text("principals: []\n")
    git(repo, "add", PRINCIPALS)  # modified, staged
    (repo / "contracts" / "stray.yaml").write_text("x: 1\n")  # untracked
    assert docset.load("v0.1.0", repo) == clean


def test_the_set_round_trips_through_write_and_read(repo: Path, tmp_path: Path) -> None:
    docs = docset.load("v0.1.0", repo)
    docset.write(docs, tmp_path / "set")
    assert docset.read(tmp_path / "set") == docs
    assert len(model.load(docset.read(tmp_path / "set")).products) == 2


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda s: (s / model.DOMAINS).write_text("domains: {}\n"), f"{model.DOMAINS}: its bytes are not the ones listed"),
        (lambda s: (s / "contracts" / "extra.yaml").write_text("x: 1\n"), "contracts/extra.yaml: in the set, but not listed"),
        (lambda s: (s / model.COMPOSE).unlink(), f"{model.COMPOSE}: listed, but not in the set"),
        (lambda s: (s / model.PROVENANCE).unlink(), "no provenance.json, so not a document set"),
        (lambda s: (s / model.PROVENANCE).write_text("{"), "provenance.json: Expecting property name"),
        (lambda s: (s / model.PROVENANCE).write_text(json.dumps({**json.loads((s / model.PROVENANCE).read_text()), "setSha256": "0" * 64})),
         "setSha256 is not the set's"),
        (lambda s: (s / model.PROVENANCE).write_text(json.dumps({**json.loads((s / model.PROVENANCE).read_text()), "tag": "latest"})),
         "provenance.json: provenance.schema.json: $.tag: 'latest' does not match"),
        (lambda s: (s / "contracts" / "link.yaml").symlink_to(s / model.DOMAINS), "a symlink"),
    ],
)
def test_read_refuses_a_set_that_is_not_what_was_written(repo: Path, tmp_path: Path, change: object, why: str) -> None:
    docset.write(docset.load("v0.1.0", repo), tmp_path / "set")
    change(tmp_path / "set")  # type: ignore[operator]
    with pytest.raises(DocumentError) as refused:
        docset.read(tmp_path / "set")
    assert why in str(refused.value), refused.value


def test_write_refuses_a_directory_that_is_not_empty(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "set").mkdir()
    (tmp_path / "set" / "old.txt").write_text("stale\n")
    with pytest.raises(ValueError, match="not empty"):
        docset.write(docset.load("v0.1.0", repo), tmp_path / "set")


def test_write_refuses_a_path_outside_the_set(repo: Path, tmp_path: Path) -> None:
    docs = docset.load("v0.1.0", repo)
    escaping = {**docs.documents, "../escape.yaml": b"x: 1\n"}
    record = docs.provenance
    listed = (*record.documents, model.DocumentProvenance("../escape.yaml", hashlib.sha256(b"x: 1\n").hexdigest(), record.commit, None))
    forged = model.DocumentSet(escaping, model.Provenance(record.source, record.tag, record.commit, record.tagger,
                                                          record.tagged_at, model.set_sha256(escaping), listed))
    with pytest.raises(DocumentError, match="does not match"):
        docset.write(forged, tmp_path / "set")
    assert not (tmp_path / "escape.yaml").exists() and not (tmp_path / "set").exists()


@pytest.mark.parametrize(("tag", "why"), [("v0.3.0", "not an annotated tag"), ("v9.9.9", "git cat-file")])
def test_only_an_annotated_tag_is_a_release(repo: Path, tag: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        docset.load(tag, repo)


def test_a_symlink_at_the_tag_is_refused(repo: Path) -> None:
    git(repo, "checkout", "-q", "v0.1.0")
    (repo / "contracts" / "link.yaml").symlink_to("domains.yaml")
    commit(repo, "chore: a symlink")
    git(repo, "tag", "-a", "v0.4.0", "-m", "Release v0.4.0")
    with pytest.raises(ValueError, match="contracts/link.yaml is a symlink"):
        docset.load("v0.4.0", repo)
