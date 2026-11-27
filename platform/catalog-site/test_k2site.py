"""k2site: pages from a small release on disk, parsed with html.parser, never whole-file goldens."""

import copy
import json
import os
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import k2site
import pytest
import trino

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
T = datetime(2026, 11, 27, 15, 40, tzinfo=timezone.utc)


def column(name: str, **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": "varchar", "required": True, "primaryKey": False, "description": f"{name} column"} | kw


def output(table: str, product: str = "fx-reference.rates", **kw: Any) -> dict[str, Any]:
    domain, fence = (x.replace("-", "_") for x in product.split("."))
    return {"contractId": f"{product}.{table}", "relation": f"{domain}.{fence}.{table}", "build": "dbt",
            "testedRef": "audited", "contractFile": f"documents/contracts/x/{table}.odcs.yaml", "layer": "gold",
            "version": "1.0.0", "purpose": f"The {table} table.", "columns": [column("day", primaryKey=True, primaryKeyPosition=1), column("note")],
            "retentionDays": None, "roles": [], "readers": [],
            "dbtDocs": f"dbt-docs/#!/model/model.k2.{fence}__{table}"} | kw


def product(pid: str = "fx-reference.rates", **kw: Any) -> dict[str, Any]:
    domain, name = pid.split(".")
    return {"id": pid, "name": name, "domain": domain, "version": "1.0.0", "status": "active", "type": "sourceAligned",
            "owner": f"{domain}-team", "purpose": f"The {name} product. A second sentence.", "issued": "2026-11-27T04:15:06Z",
            "periodicity": "30 15 * * 1-5", "productFile": "documents/contracts/x/product.odps.yaml", "inputs": [],
            "outputs": [output("daily", pid)], "joinKeys": [], "lineage": {"nodes": [], "edges": []}, "tag": "v0.0.0-ci.1"} | kw


def release(tmp_path: Path, *docs: dict[str, Any]) -> Path:
    root = tmp_path / "release"
    (root / "compiled/catalog/products").mkdir(parents=True, exist_ok=True)
    (root / "compiled/trino").mkdir(parents=True, exist_ok=True)
    index = {"tag": "v0.0.0-ci.1", "products": sorted(
        ({k: d[k] for k in ("id", "name", "domain", "status", "owner", "purpose")} for d in docs), key=lambda d: d["id"])}
    (root / k2site.CATALOG_INDEX).write_text(json.dumps(index))
    views = []
    for d in docs:
        (root / f"compiled/catalog/products/{d['id']}.json").write_text(json.dumps(d))
        for o in d["outputs"]:
            if o["relation"] and o["build"] in ("dbt", "code"):
                c, s, t = o["relation"].split(".")
                views.append({"relation": o["relation"], "base": f"{c}.{s}__base.{t}"})
    (root / k2site.VIEWS).write_text(json.dumps({"views": views}))
    return root


NO_FACTS = {"available": False, "reason": "not read (--no-facts)"}


def facts(pid: str = "fx-reference.rates", table: str = "daily", **pf: Any) -> dict[str, Any]:
    base = {"outputs": {f"{pid}.{table}": {"snapshot": 42, "publishedAt": T}},
            "lastRun": {"runId": "rates/scheduled__1", "at": T, "ok": True, "failedTasks": "", "lastOkAt": T},
            "tests": {"invocation": "inv-1", "at": T, "counts": {"pass": 7}}}
    return {"available": True, "products": {pid: base | pf}}


class Page(HTMLParser):
    """Text, links, sources, tags and attributes of one page."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []
        self._style = False
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs += attrs
        if tag not in ("a", "code", "span"):  # a block or cell starts: a word break
            self.text.append(" ")
        self._style = tag == "style"
        for k, v in attrs:
            (self.hrefs if k == "href" else self.srcs if k == "src" else []).append(v or "")

    def handle_endtag(self, tag: str) -> None:
        self._style = False

    def handle_data(self, data: str) -> None:
        if not self._style:
            self.text.append(data)

    @property
    def words(self) -> str:
        return " ".join("".join(self.text).split())


def site(tmp_path: Path, *docs: dict[str, Any], f: dict[str, Any] = NO_FACTS) -> tuple[Path, list[str]]:
    out = tmp_path / "site"
    problems = k2site.generate(release(tmp_path, *docs), f, out, NOW)
    return out / k2site.CURRENT, problems


def test_a_page_per_product_and_an_index_by_domain(tmp_path: Path) -> None:
    docs = [product("fx-reference.rates"), product("reference-data.calendars"), product("reference-data.currencies")]
    current, problems = site(tmp_path, *docs)
    assert problems == []
    assert sorted(str(p.relative_to(current)) for p in current.rglob("*.html")) == [
        "fx-reference/rates/index.html", "index.html", "reference-data/calendars/index.html", "reference-data/currencies/index.html"]
    index = Page((current / "index.html").read_text())
    assert {"fx-reference/rates/index.html", "reference-data/calendars/index.html"} <= set(index.hrefs)
    assert index.words.index("fx-reference") < index.words.index("reference-data") < index.words.index("calendars")
    assert "The calendars product." in index.words and "A second sentence" not in index.words  # one line of purpose
    page = Page((current / "reference-data/calendars/index.html").read_text())
    assert "reference-data-team" in page.words and "daily" in page.words and "/dbt-docs/" in page.hrefs
    assert "/dbt-docs/#!/model/model.k2.calendars__daily" in page.hrefs
    assert "../../index.html" in page.hrefs


def test_hostile_values_are_escaped_and_a_javascript_url_is_not_a_link(tmp_path: Path) -> None:
    evil = '<script>alert("x")</script> "quoted" \'single\' & <b onclick=x>'
    doc = product(purpose=evil, limitations=evil, licence="javascript:alert(1)",
                  links=[{"type": "runbook", "url": "javascript:alert(2)", "description": evil},
                         {"type": "glossary", "url": "//evil.example/x"}],
                  examples=[{"description": evil, "sql": "SELECT '<x>' FROM t WHERE a < 1 AND b > 2"}])
    doc["outputs"][0]["columns"][1]["description"] = evil
    current, _ = site(tmp_path, doc)
    for path in current.rglob("*.html"):
        text = path.read_text()
        parsed = Page(text)
        assert "script" not in parsed.tags and "b" not in parsed.tags
        assert not any(k.startswith("on") for k, _ in parsed.attrs)
        assert not any(h.startswith(("javascript:", "//")) for h in parsed.hrefs)
    page = Page((current / "fx-reference/rates/index.html").read_text())
    assert '<script>alert("x")</script> "quoted" \'single\'' in page.words  # shown as text, exactly
    assert "(javascript:alert(2), not linked)" in page.words
    assert "SELECT '<x>' FROM t WHERE a < 1 AND b > 2" in page.words  # the example as the document gives it


def test_links_are_site_relative_or_the_documents_own_https(tmp_path: Path) -> None:
    doc = product(licence="https://example.org/terms", links=[{"type": "licence", "url": "https://example.org/terms"}])
    current, _ = site(tmp_path, doc, product("reference-data.calendars"))
    for path in current.rglob("*.html"):
        parsed = Page(path.read_text())
        assert parsed.srcs == []  # no image, script or frame of any origin
        for href in parsed.hrefs:
            assert href == "https://example.org/terms" or (href.startswith("/") and not href.startswith("//")) \
                or not (":" in href or href.startswith("//")), href


@pytest.mark.parametrize(("policies", "words"), [
    ({"delay": {"column": "event_ts", "minutes": 20}}, "Readers with role analyst see only rows whose event_ts is at least 20 minutes old."),
    ({"delay": {"column": "event_ts", "minutes": 1}}, "see only rows whose event_ts is at least 1 minute old."),
    ({"mask": ["note"]}, "see column note reads as null."),
    ({"mask": ["note", "day"]}, "see columns note and day read as null."),
    ({"rowScope": {"column": "region", "values": ["north", "south"]}}, "see only rows whose region is north or south."),
    ({"delay": {"column": "event_ts", "minutes": 20}, "mask": ["note"]},
     "see only rows whose event_ts is at least 20 minutes old; column note reads as null."),
    (None, "Readers with role analyst see every row and column. Held by p.reader-a."),
])
def test_each_policy_kind_in_words(tmp_path: Path, policies: dict[str, Any] | None, words: str) -> None:
    doc = product()
    doc["outputs"][0]["roles"] = [{"role": "analyst", "inWords": "x"} | ({"policies": policies} if policies else {})]
    doc["outputs"][0]["readers"] = [{"principal": "p.reader-a", "role": "analyst"}]
    current, problems = site(tmp_path, doc)
    assert problems == []
    assert words in Page((current / "fx-reference/rates/index.html").read_text()).words


def test_the_policy_kinds_are_the_schemas() -> None:
    """A kind added to the contract schema fails here until the page can say it in words."""
    schema = json.loads((REPO / "contracts/schema/k2-v2.schema.json").read_text())
    assert set(schema["$defs"]["rolePolicy"]["properties"]) == set(k2site.POLICY_KINDS)


def test_an_unknown_policy_kind_is_shown_and_fails_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = product()
    doc["outputs"][0]["roles"] = [{"role": "analyst", "inWords": "x", "policies": {"sample": {"percent": 5}}}]
    current, problems = site(tmp_path, doc)
    assert problems == ["fx-reference.rates.daily role analyst: unrendered policy sample"]
    assert "unrendered policy: sample" in Page((current / "fx-reference/rates/index.html").read_text()).words
    rel = release(tmp_path / "cli", doc)
    assert k2site.main(["--release", str(rel), "--out", str(tmp_path / "cli-site"), "--no-facts"]) == 2
    assert "unrendered policy sample" in capsys.readouterr().err


def test_a_product_never_built_says_not_yet_published(tmp_path: Path) -> None:
    doc = product()
    never = {"available": True, "products": {doc["id"]: {"outputs": {f"{doc['id']}.daily": {"snapshot": None, "publishedAt": None}},
                                                         "lastRun": None, "tests": None}}}
    current, _ = site(tmp_path, doc, f=never)
    words = Page((current / "fx-reference/rates/index.html").read_text()).words
    assert "Newest tested publish not yet published" in words
    assert "no test results" in words and "No successful run recorded" in words
    assert "not yet published" in Page((current / "index.html").read_text()).words


def test_published_facts_and_freshness_in_words(tmp_path: Path) -> None:
    current, _ = site(tmp_path, product(), f=facts())
    words = Page((current / "fx-reference/rates/index.html").read_text()).words
    assert "2026-11-27 15:40 UTC, snapshot 42" in words
    assert "rates/scheduled__1, finished 2026-11-27 15:40 UTC: passed" in words
    assert "tests passing 7 pass (dbt invocation inv-1" in words
    assert "Current: the newest successful run finished 2026-11-27 15:40 UTC, after the run due 2026-11-27 15:30 UTC." in words
    index = Page((current / "index.html").read_text()).words
    assert "2026-11-27 15:40 UTC tests passing" in index


def test_failing_tests_and_a_failed_run_say_so_in_words(tmp_path: Path) -> None:
    f = facts(tests={"invocation": "inv-2", "at": T, "counts": {"pass": 5, "fail": 2, "error": 1}},
              lastRun={"runId": "r/2", "at": T, "ok": False, "failedTasks": "build", "lastOkAt": None})
    current, _ = site(tmp_path, product(), f=f)
    words = Page((current / "fx-reference/rates/index.html").read_text()).words
    assert "tests failing: 3" in words and "failed at build" in words
    assert "tests failing: 3" in Page((current / "index.html").read_text()).words


def test_state_badge_and_offset_gaps_are_shown_and_escaped(tmp_path: Path) -> None:
    gap = {"at": T, "partitions": [{"partition": 3, "fromOffset": 10, "toOffset": 19}], "reason": "<script>alert(1)</script>"}
    current, _ = site(tmp_path, product(), f=facts(state="partial: market-capture.kraken-trades.silver_trade", gaps=[gap]))
    raw = (current / "fx-reference/rates/index.html").read_text()
    words = Page(raw).words
    assert "State partial: market-capture.kraken-trades.silver_trade" in words
    assert "partition 3 offsets 10 to 19; reason: <script>alert(1)</script>" in words  # as text
    assert "<script>alert" not in raw and "&lt;script&gt;" in raw


def test_state_says_not_built_when_there_is_none(tmp_path: Path) -> None:
    current, _ = site(tmp_path, product(), f=facts(state=None, gaps=[]))
    words = Page((current / "fx-reference/rates/index.html").read_text()).words
    assert "State not built yet" in words and "Offset gaps" not in words


@pytest.mark.parametrize(("last_ok", "status", "cron", "start"), [
    (datetime(2026, 11, 27, 15, 35, tzinfo=timezone.utc), "active", "30 15 * * 1-5", "Current:"),
    (datetime(2026, 11, 26, 15, 35, tzinfo=timezone.utc), "active", "30 15 * * 1-5", "Due: the run due 2026-11-27 15:30 UTC"),
    (datetime(2026, 11, 20, 15, 35, tzinfo=timezone.utc), "active", "30 15 * * 1-5", "Late:"),
    (None, "paused", None, "Paused:"),
    (None, "active", None, "Nothing schedules"),
])
def test_freshness(last_ok: datetime | None, status: str, cron: str | None, start: str) -> None:
    doc = product(status=status, periodicity=cron) if cron else {k: v for k, v in product(status=status).items() if k != "periodicity"}
    pf = {"lastRun": {"lastOkAt": last_ok}}
    assert k2site.freshness(doc, pf, NOW).startswith(start)


def test_cron_fires_before() -> None:
    fri = datetime(2026, 11, 27, 15, 29, tzinfo=timezone.utc)  # a Friday
    assert k2site.fires_before("30 15 * * 1-5", fri) == [datetime(2026, 11, 26, 15, 30, tzinfo=timezone.utc),
                                                         datetime(2026, 11, 25, 15, 30, tzinfo=timezone.utc)]
    mon = datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)
    assert k2site.fires_before("30 15 * * 1-5", mon)[0] == datetime(2026, 11, 27, 15, 30, tzinfo=timezone.utc)  # skips the weekend
    assert k2site.fires_before("*/10 * * * *", NOW) == [NOW, datetime(2026, 11, 27, 15, 50, tzinfo=timezone.utc)]
    assert k2site.fires_before("0 5 * * 1", NOW)[0] == datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)
    assert k2site.fires_before("0 4 1 * 0", NOW)[0] == datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)  # day 1 OR a Sunday


def test_facts_unavailable_is_a_visible_line_and_fails_only_when_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rel = release(tmp_path, product())

    def refused() -> None:
        raise k2site.FactsUnavailable("no connection settings (OSError)")

    monkeypatch.setattr(k2site, "connect", refused)
    assert k2site.main(["--release", str(rel), "--out", str(tmp_path / "a")]) == 0
    assert k2site.main(["--release", str(rel), "--out", str(tmp_path / "b"), "--strict"]) == 1
    for name in ("index.html", "fx-reference/rates/index.html"):
        words = Page((tmp_path / "a" / k2site.CURRENT / name).read_text()).words
        assert "Run-time facts unavailable at" in words and "Trino did not answer (no connection settings (OSError))" in words
    assert "fx-reference-team" in Page((tmp_path / "a/current/fx-reference/rates/index.html").read_text()).words


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    docs = [product(), product("reference-data.calendars")]
    a, _ = site(tmp_path / "1", *copy.deepcopy(docs), f=facts())
    b, _ = site(tmp_path / "2", *copy.deepcopy(docs), f=facts())
    files = sorted(str(p.relative_to(a)) for p in a.rglob("*") if p.is_file())
    assert files == sorted(str(p.relative_to(b)) for p in b.rglob("*") if p.is_file())
    assert all((a / f).read_bytes() == (b / f).read_bytes() for f in files)


def test_the_swap_renames_one_symlink_and_keeps_the_tree_before(tmp_path: Path) -> None:
    out = tmp_path / "site"
    first = k2site.publish({"index.html": "one"}, out)
    second = k2site.publish({"index.html": "two"}, out)
    assert os.readlink(out / "current") == second.name and (out / "current/index.html").read_text() == "two"
    assert first.exists()  # a reader mid-request on the old tree still finds it
    assert oct((second / "index.html").stat().st_mode & 0o777) == "0o644" and oct(second.stat().st_mode & 0o777) == "0o755"
    third = k2site.publish({"index.html": "three"}, out)
    assert not first.exists() and second.exists() and os.readlink(out / "current") == third.name
    assert sorted(p.name for p in out.iterdir()) == sorted(["current", second.name, third.name])


def test_a_real_directory_where_the_symlink_goes_is_refused(tmp_path: Path) -> None:
    (tmp_path / "site/current").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="not a symlink"):
        k2site.publish({"index.html": "x"}, tmp_path / "site")


class Cursor:
    def __init__(self, conn: "Conn") -> None:
        self.conn = conn
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: list[Any]) -> None:
        self.conn.calls.append((sql, params))
        for needle, answer in self.conn.answers.items():
            if needle in sql:
                if isinstance(answer, Exception):
                    raise answer
                self.rows = answer
                return
        raise AssertionError(f"unexpected query {sql}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class Conn:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, list[Any]]] = []

    def cursor(self) -> Cursor:
        return Cursor(self)


def not_found(name: str = "TABLE_NOT_FOUND") -> trino.exceptions.TrinoUserError:
    return trino.exceptions.TrinoUserError({"errorName": name, "message": "m"}, "q")


def test_read_facts_binds_values_and_reads_the_bases_refs(tmp_path: Path) -> None:
    connector = output("bronze", relation="lake.rates.bronze", build="connector", testedRef="main", dbtDocs=None)
    rel = release(tmp_path, product(outputs=[output("daily"), connector]), product("reference-data.calendars"))
    conn = Conn({'"fx_reference"."rates__base"."daily$refs"': [(42,)], '"lake"."rates"."bronze$refs"': [(7,)],
                 '"reference_data"."calendars__base"."daily$refs"': not_found("SCHEMA_NOT_FOUND"),
                 'min("recorded_at")': [(T,)],
                 "WITH runs AS": [("fx-reference.rates", "r/1", T, True, "", T)],
                 '"task" = \'build\'': [],
                 "WITH newest AS": [("fx-reference.rates", "inv-1", T, "pass", 7), ("fx-reference.rates", "inv-1", T, "fail", 1)]})
    got = k2site.read_facts(conn, rel)
    pf = got["products"]["fx-reference.rates"]
    assert pf["outputs"] == {"fx-reference.rates.daily": {"snapshot": 42, "publishedAt": T},
                             "fx-reference.rates.bronze": {"snapshot": 7, "publishedAt": T}}
    assert pf["tests"] == {"invocation": "inv-1", "at": T, "counts": {"pass": 7, "fail": 1}}
    assert pf["lastRun"]["ok"] is True
    never = got["products"]["reference-data.calendars"]
    assert never["outputs"]["reference-data.calendars.daily"] == {"snapshot": None, "publishedAt": None}
    refs = [p for s, p in conn.calls if "$refs" in s]
    assert ["audited", "TAG"] in refs and ["main", "BRANCH"] in refs
    assert [p for s, p in conn.calls if "min(" in s] == [["fx-reference.rates", 42], ["fx-reference.rates", 7]]
    assert [p for s, p in conn.calls if "WITH runs" in s] == [["fx-reference.rates", "reference-data.calendars"]]
    for sql, _ in conn.calls:  # no value is ever part of a statement: only names from the release
        assert "fx-reference.rates" not in sql and "42" not in sql and "TAG" not in sql


def test_read_facts_with_no_run_records_yet(tmp_path: Path) -> None:
    rel = release(tmp_path, product())
    conn = Conn({"$refs": not_found(), "WITH runs AS": not_found(), "WITH newest AS": not_found(), '"task" = \'build\'': not_found()})
    pf = k2site.read_facts(conn, rel)["products"]["fx-reference.rates"]
    assert pf == {"outputs": {"fx-reference.rates.daily": {"snapshot": None, "publishedAt": None}}, "lastRun": None, "tests": None, "state": None, "gaps": []}


@pytest.mark.parametrize("error", [trino.exceptions.HttpError("503"), ConnectionRefusedError(), not_found("PERMISSION_DENIED")])
def test_read_facts_unreachable_or_refused_is_unavailable(tmp_path: Path, error: Exception) -> None:
    with pytest.raises(k2site.FactsUnavailable):
        k2site.read_facts(Conn({"$refs": error}), release(tmp_path, product()))


def test_a_name_that_is_not_the_catalogs_grammar_is_never_sent() -> None:
    with pytest.raises(ValueError):
        k2site._quoted('a.b"; DROP TABLE x; --.c')
