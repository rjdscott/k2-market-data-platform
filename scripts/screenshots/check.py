"""Release check for the dashboard gallery: every image shots.py lists exists,
is a PNG, was captured on or after --since, and docs/operations/dashboards.md
is the page those captures render (so a caption date cannot drift from its
image). Exit 1 naming each problem.

    python3 scripts/screenshots/check.py --since 2026-12-01

Stdlib only, no stack, no browser: it reads files.
"""

import argparse
import sys
from datetime import date

import shots

PNG = b"\x89PNG\r\n\x1a\n"


def problems(since, images=shots.IMAGES, gallery=shots.GALLERY):
    manifest = shots.load_manifest(images / "manifest.json")
    out = []
    for s in shots.SHOTS:
        f, rec = s["file"], manifest.get(s["file"])
        path = images / f
        if not path.exists():
            out.append(f"{f}: missing")
        elif path.read_bytes()[:8] != PNG:
            out.append(f"{f}: not a PNG")
        if not rec:
            out.append(f"{f}: not in manifest.json")
        elif date.fromisoformat(rec["captured_at"][:10]) < since:
            out.append(f"{f}: captured {rec['captured_at'][:10]}, before {since}")
    if not gallery.exists() or gallery.read_text() != shots.render_gallery(manifest):
        out.append(f"{gallery.name}: out of date with manifest.json; run python3 scripts/screenshots/shots.py")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, type=date.fromisoformat, help="oldest acceptable capture date, YYYY-MM-DD")
    found = problems(ap.parse_args().since)
    for p in found:
        print(f"FAIL: {p}")
    if not found:
        print(f"PASS: {len(shots.SHOTS)} gallery images present and current")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
