"""
scripts/screenshots/check.py, the gallery's release check: fed a temporary
images directory, so no stack, browser or committed image is needed.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "screenshots"))
import check  # noqa: E402
import shots  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def gallery_dir(tmp_path, captured_at="2026-12-01T09:00:00Z", skip=()):
    images, gallery = tmp_path / "images", tmp_path / "dashboards.md"
    images.mkdir()
    rows = []
    for s in shots.SHOTS:
        if s["file"] in skip:
            continue
        (images / s["file"]).write_bytes(PNG)
        rows.append({"file": s["file"], "url": s["url"], "captured_at": captured_at, "git_sha": "abc1234"})
    (images / "manifest.json").write_text(json.dumps({"shots": rows}))
    gallery.write_text(shots.render_gallery(shots.load_manifest(images / "manifest.json")))
    return images, gallery


def test_a_complete_current_gallery_passes(tmp_path):
    images, gallery = gallery_dir(tmp_path)
    assert check.problems(date(2026, 12, 1), images, gallery) == []


def test_an_image_older_than_since_fails(tmp_path):
    images, gallery = gallery_dir(tmp_path, captured_at="2026-11-30T23:59:59Z")
    found = check.problems(date(2026, 12, 1), images, gallery)
    assert len(found) == len(shots.SHOTS)
    assert all("captured 2026-11-30, before 2026-12-01" in p for p in found)


def test_a_missing_image_fails_and_is_named(tmp_path):
    first = shots.SHOTS[0]["file"]
    images, gallery = gallery_dir(tmp_path)
    (images / first).unlink()
    assert check.problems(date(2026, 12, 1), images, gallery) == [f"{first}: missing"]


def test_a_shot_never_captured_fails_twice(tmp_path):
    last = shots.SHOTS[-1]["file"]
    images, gallery = gallery_dir(tmp_path, skip={last})
    assert check.problems(date(2026, 12, 1), images, gallery) == [f"{last}: missing", f"{last}: not in manifest.json"]


def test_a_file_that_is_not_a_png_fails(tmp_path):
    first = shots.SHOTS[0]["file"]
    images, gallery = gallery_dir(tmp_path)
    (images / first).write_bytes(b"<html>login</html>")
    assert check.problems(date(2026, 12, 1), images, gallery) == [f"{first}: not a PNG"]


def test_a_hand_edited_gallery_fails(tmp_path):
    images, gallery = gallery_dir(tmp_path)
    gallery.write_text(gallery.read_text().replace("Captured 2026-12-01", "Captured 2027-01-01", 1))
    assert check.problems(date(2026, 12, 1), images, gallery) == ["dashboards.md: out of date with manifest.json; run python3 scripts/screenshots/shots.py"]


def test_the_gallery_captions_callouts_and_links_every_runbook():
    rec = {"file": "x", "url": "u", "captured_at": "2026-12-01T09:00:00Z", "git_sha": "abc1234"}
    page = shots.render_gallery({s["file"]: rec for s in shots.SHOTS})
    hero = shots.SHOTS[0]
    assert f"![{hero['title']}](../images/dashboards/{hero['file']})" in page
    assert "*Captured 2026-12-01 at `abc1234`.* **1** every scrape target, up or down." in page
    for s in shots.SHOTS:
        assert (shots.ROOT / s["leads_to"]).exists(), s["leads_to"]
    numbers = [[c[0] for c in s.get("callouts", [])] for s in shots.SHOTS]
    assert all(ns == list(range(1, len(ns) + 1)) for ns in numbers)
    assert len({s["file"] for s in shots.SHOTS}) == len(shots.SHOTS)
