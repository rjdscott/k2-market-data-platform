"""W3-11: /catalog/ and /dbt-docs/, the two generated sites, read-only from the k2-site volume.

Each generator writes <site>/<name>/tree-XXXX/ and renames a relative symlink <name>/current onto it
(platform/catalog-site/k2site.py publish, docker/dbt-runner/k2_dbt.py docs); k2-api serves the tree
`current` names at that request, and nothing a link inside it reaches out of the tree.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from k2api import app as api

SITES = {"/catalog/": "catalog", "/dbt-docs/": "dbt-docs"}


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "k2-site"
    root.mkdir()
    monkeypatch.setattr(api, "SITE", root)
    return root


def generate(site: Path, name: str, tree: str, files: dict[str, str]) -> Path:
    """A tree as the generators publish it: the files, then `current` renamed onto the tree."""
    for rel, text in files.items():
        (site / name / tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / name / tree / rel).write_text(text)
    staged = site / name / ".current.new"
    staged.symlink_to(tree)
    staged.rename(site / name / "current")
    return site / name / tree


@pytest.mark.parametrize("prefix", SITES)
def test_before_the_first_generation_a_site_is_503_not_a_crash(client: TestClient, site: Path, prefix: str) -> None:
    r = client.get(prefix)
    assert (r.status_code, r.text) == (503, "not generated yet\n")
    assert r.headers["content-type"].startswith("text/plain")
    (site / SITES[prefix]).mkdir()  # the directory, no tree yet
    assert client.get(prefix + "index.html").status_code == 503
    (site / SITES[prefix] / "current").symlink_to("tree-gone")  # a link to nothing
    assert client.get(prefix).status_code == 503


@pytest.mark.parametrize("prefix", SITES)
def test_the_tree_current_names_is_served_and_a_swap_is_seen_at_the_next_request(client: TestClient, site: Path,
                                                                                  prefix: str) -> None:
    name = SITES[prefix]
    generate(site, name, "tree-a", {"index.html": "<p>one</p>", "fx/ecb-fx/index.html": "<p>fx</p>", "manifest.json": "{}"})
    assert (client.get(prefix).status_code, client.get(prefix).text) == (200, "<p>one</p>")
    assert client.get(prefix + "fx/ecb-fx/").text == "<p>fx</p>"
    r = client.get(prefix + "fx/ecb-fx", follow_redirects=False)  # a directory URL gains its slash
    assert r.status_code in (301, 302, 307) and r.headers["location"].endswith(prefix + "fx/ecb-fx/")
    assert client.get(prefix + "manifest.json").headers["content-type"] == "application/json"
    generate(site, name, "tree-b", {"index.html": "<p>two</p>"})
    assert client.get(prefix).text == "<p>two</p>"
    assert client.get(prefix + "fx/ecb-fx/").status_code == 404  # only the new tree is served
    assert client.get(prefix.rstrip("/"), follow_redirects=False).status_code in (301, 302, 307, 308)


@pytest.mark.parametrize("prefix", SITES)
def test_every_static_answer_carries_the_headers(client: TestClient, site: Path, prefix: str) -> None:
    csp = {"/catalog/": api.CATALOG_CSP, "/dbt-docs/": api.DBT_DOCS_CSP}[prefix]
    answers = [client.get(prefix)]  # 503: nothing generated yet
    generate(site, SITES[prefix], "tree-a", {"index.html": "<p>one</p>"})
    answers += [client.get(prefix), client.head(prefix), client.get(prefix + "nothing.html"), client.post(prefix)]
    assert [r.status_code for r in answers] == [503, 200, 200, 404, 405]
    for r in answers:
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-security-policy"] == csp


def test_the_catalog_runs_no_script_and_the_dbt_docs_fetch_nothing_off_this_origin() -> None:
    catalog = dict(d.split(" ", 1) for d in api.CATALOG_CSP.split("; "))
    assert catalog["default-src"] == "'none'" and "script-src" not in catalog and catalog["frame-ancestors"] == "'none'"
    docs = dict(d.split(" ", 1) for d in api.DBT_DOCS_CSP.split("; "))
    assert docs["default-src"] == "'self'" and docs["connect-src"] == "'self'" and docs["frame-ancestors"] == "'none'"
    assert not [v for v in docs.values() if "http" in v or "*" in v or "unsafe-eval" in v]


@pytest.mark.parametrize("prefix", SITES)
def test_a_link_out_of_the_tree_is_never_followed(client: TestClient, site: Path, tmp_path: Path, prefix: str) -> None:
    secret = tmp_path / "outside" / "api.password"
    secret.parent.mkdir()
    secret.write_text("hunter2")
    tree = generate(site, SITES[prefix], "tree-a", {"index.html": "<p>one</p>"})
    (tree / "evil").symlink_to("/etc")  # a planted current/evil -> /etc
    (tree / "leak").symlink_to(secret)
    generate(site, SITES[prefix], "tree-old", {"old.html": "<p>old</p>"})
    generate(site, SITES[prefix], "tree-a", {})  # tree-a current again; tree-old beside it
    (tree / "up").symlink_to("..")
    assert client.get(prefix + "evil/passwd").status_code == 404
    assert client.get(prefix + "evil/hostname").status_code == 404
    assert client.get(prefix + "leak").status_code == 404
    assert client.get(prefix + "up/tree-old/old.html").status_code == 404  # an older tree, not through this one
    assert client.get(prefix + "..%2F..%2F..%2Foutside%2Fapi.password").status_code == 404
    assert client.get(prefix).text == "<p>one</p>"


@pytest.mark.parametrize("prefix", SITES)
def test_a_current_that_names_anything_but_a_tree_beside_it_is_503(client: TestClient, site: Path, tmp_path: Path,
                                                                  prefix: str) -> None:
    """`current -> /` would serve this container's own files, the secrets it mounts among them."""
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "index.html").write_text("outside")
    (site / SITES[prefix]).mkdir()
    current = site / SITES[prefix] / "current"
    for target in ("/", str(tmp_path / "outside"), "../../outside", ".."):
        current.unlink(missing_ok=True)
        current.symlink_to(target)
        assert client.get(prefix).status_code == 503, target
    current.unlink()
    (site / SITES[prefix] / "tree-x").symlink_to(tmp_path / "outside")  # a "tree" that is itself a link out
    current.symlink_to("tree-x")
    assert client.get(prefix).status_code == 503
