"""`make screenshots`: every page in shots.py, from the live stack, into
docs/images/dashboards/<file>.png, then manifest.json beside them and the
gallery, docs/operations/dashboards.md.

Runs inside k2-screenshots:1.65.2 (the Dockerfile beside this file) on the host
network, so every URL is the localhost one an operator opens. Credentials come
from the environment the Makefile passes by name: GRAFANA_PASSWORD,
MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, K2_QUANT_PASSWORD, AIRFLOW_PASSWORD. They
live in this process only: nothing here prints one, writes one, or saves a
cookie, and a page whose text contains one fails instead of being saved.

Read-only. It logs in, opens pages and runs one SELECT as ClickHouse's
read-only `quant` user. It clicks nothing that triggers, pauses or changes a
setting; the two UI preferences it needs (MinIO's licence notice, Airflow's grid
width) are set in this browser's own localStorage.

A page that times out, shows a spinner, reads "No data" where shots.py does not
allow it, or shows a credential is not saved: the run carries on, then exits 1
naming each one.

    python3 scripts/screenshots/capture.py [--only 04-lake] [--out DIR] [--gallery FILE]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import shots
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

WIDTH = 1600
TIMEOUT_MS = 30_000
GRAFANA_PARAMS = "?orgId=1&kiosk&theme=dark&from=now-6h&to=now"
SECRETS = ("GRAFANA_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "K2_QUANT_PASSWORD", "AIRFLOW_PASSWORD")
# Visible busy indicators: Grafana's panel loading bar, Chakra's spinner (Airflow).
BUSY = "[aria-label='Panel loading bar'], .chakra-spinner"
# Per-browser preferences, set before any page script runs: MinIO's AGPL notice
# acknowledged in this browser (the server-side /license/ack is never called),
# and Airflow's grid panel widened from 20% to 35% of the page.
INIT_JS = """try {
  localStorage.setItem('agpl_minio_license_consent', 'true');
  localStorage.setItem('react-resizable-panels:grid-ltr',
    JSON.stringify({'details-panel,main-panel': {expandToSizes: {}, layout: [35, 65]}}));
} catch (e) {}"""
# Numbered markers: an outline round the target and a numbered disc at (bx, by).
DRAW_JS = """marks => marks.forEach(m => {
  const add = css => { const d = document.createElement('div'); d.style.cssText =
    'position:fixed;z-index:2147483647;pointer-events:none;box-sizing:border-box;' + css;
    document.body.appendChild(d); return d; };
  if (m.width > 0) add(`left:${m.x}px;top:${m.y}px;width:${m.width}px;height:${m.height}px;` +
    'border:3px solid #ffb000;border-radius:6px');
  add(`left:${m.bx}px;top:${m.by}px;width:32px;height:32px;` +
    'border-radius:50%;background:#ffb000;color:#111;border:2px solid #111;display:flex;' +
    'align-items:center;justify-content:center;font:700 17px/1 sans-serif').textContent = m.n;
})"""
GRAFANA_EMPTY_JS = """() => [...document.querySelectorAll("section[data-testid^='data-testid Panel header ']")]
  .filter(s => /\\bNo data\\b/.test(s.innerText) || s.querySelector("[data-testid*='Panel status error']"))
  .map(s => s.getAttribute('data-testid').replace('data-testid Panel header ', ''))"""
PAGE_TEXT_JS = """() => [document.body.innerText,
  ...[...document.querySelectorAll('input, textarea')].filter(e => e.type !== 'password').map(e => e.value)
].join('\\n')"""
VISIBLE_JS = """sel => [...document.querySelectorAll(sel)].filter(e => {
  const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length"""


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"capture: {name} is not set (the Makefile passes it from .env)")
    return value


def airflow_base():
    return f"http://localhost:{os.environ.get('K2_AIRFLOW_PORT', '8090')}"


def airflow_url(url):
    return url.replace("http://localhost:8090", airflow_base())


# ── logins, once per app, only for the apps the selected shots need ─────────
def login(ctx, app):
    if app == "grafana":
        r = ctx.request.post("http://localhost:3000/login", data={"user": "admin", "password": env("GRAFANA_PASSWORD")})
    elif app == "minio":
        r = ctx.request.post("http://localhost:9001/api/v1/login",
                             data={"accessKey": env("MINIO_ROOT_USER"), "secretKey": env("MINIO_ROOT_PASSWORD")})
    elif app == "airflow":
        page = ctx.new_page()
        page.goto(airflow_base() + "/auth/login")
        page.fill("input[name=username]", "admin")
        page.fill("input[name=password]", env("AIRFLOW_PASSWORD"))
        page.click("button[type=submit]")
        page.wait_for_url(lambda u: "/auth/login" not in u, timeout=TIMEOUT_MS)
        page.close()
        return
    if not r.ok:
        raise RuntimeError(f"{app} login answered HTTP {r.status}")


# ── pages that need more than a goto; each returns the URL it captured ──────
def open_lakekeeper(page, shot):
    whs = page.request.get("http://localhost:18181/management/v1/warehouse").json()["warehouses"]
    wid = next(w["id"] for w in whs if w["name"] == "k2")
    page.goto("http://localhost:18181/ui/")  # the home page selects the project
    page.wait_for_load_state("networkidle")
    url = f"http://localhost:18181/ui/warehouse/{wid}/namespace/gold"
    page.goto(url)
    return url


def open_clickhouse(page, shot):
    page.goto(shot["url"] + "?theme=dark")
    page.fill("#user", "quant")
    page.fill("#password", env("K2_QUANT_PASSWORD"))
    page.fill("#query", "-- gold.trades, fed live from Redpanda: trades per venue in the last hour\n"
                        "SELECT exchange, count() AS trades_last_hour, max(exchange_ts) AS newest\n"
                        "FROM gold.trades\nWHERE exchange_ts > now() - INTERVAL 1 HOUR\n"
                        "GROUP BY exchange\nORDER BY exchange")
    page.click("#run")
    return shot["url"]  # play puts the query, never the password, in its own URL


def open_airflow_task_log(page, shot):
    # The latest *successful* run: the newest column in the grid may still be
    # queued, with no log yet. The UI's own session token, held in memory only.
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "_token")
    r = page.request.get(airflow_base() + "/api/v2/dags/lake_ingest/dagRuns",
                         params={"state": "success", "order_by": "-start_date", "limit": 1},
                         headers={"Authorization": f"Bearer {token}"})
    if not r.ok:
        raise RuntimeError(f"Airflow API answered HTTP {r.status}")
    runs = r.json()["dag_runs"]
    if not runs:
        raise RuntimeError("lake_ingest has no successful run")
    url = f"{airflow_base()}/dags/lake_ingest/runs/{runs[0]['dag_run_id']}/tasks/ingest"
    page.goto(url)
    return url


OPEN = {"lakekeeper": open_lakekeeper, "clickhouse": open_clickhouse, "airflow_task_log": open_airflow_task_log}


def wait_quiet(page):
    """Until no busy indicator has been visible for two checks a second apart."""
    deadline, quiet = time.monotonic() + TIMEOUT_MS / 1000, 0
    while quiet < 2:
        if time.monotonic() > deadline:
            raise RuntimeError("still loading after 30 s")
        quiet = quiet + 1 if page.evaluate(VISIBLE_JS, BUSY) == 0 else 0
        time.sleep(1)


def mark(n, target, side, page):
    """Where callout n goes. A box taller than 60 px (a panel, a card) gets its
    disc inside its top-right corner, which is empty on every page here; a
    thin one (a row, a heading) gets it beside, right unless side says left.
    A bare (x, y) gets a disc and no outline."""
    if not isinstance(target, str):
        return {"n": n, "x": target[0], "y": target[1], "width": 0, "height": 0, "bx": target[0], "by": target[1]}
    try:
        box = page.locator(target).first.bounding_box(timeout=TIMEOUT_MS)
    except PlaywrightTimeout:
        box = None
    if not box:
        raise RuntimeError(f"callout {n}: {target!r} is not on the page")
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    if h > 60 and not side:
        bx, by = x + w - 40, y + 4
    else:
        bx, by = (max(x - 38, 2) if side == "left" else x + w + 6), y + h / 2 - 16
    return {"n": n, **box, "bx": bx, "by": by}


def capture(page, shot, out, secrets):
    page.set_viewport_size({"width": WIDTH, "height": shot["height"]})
    if shot.get("open"):
        url = OPEN[shot["open"]](page, shot)
    else:
        url = airflow_url(shot["url"])
        page.goto(url + GRAFANA_PARAMS if shot["app"] == "grafana" else url)
    try:
        page.wait_for_selector(shot["ready"], timeout=TIMEOUT_MS)
    except PlaywrightTimeout:
        raise RuntimeError(f"{shot['ready']!r} never appeared") from None
    wait_quiet(page)
    if shot["app"] == "grafana":
        empty = [t for t in page.evaluate(GRAFANA_EMPTY_JS) if t not in shot.get("no_data", [])]
        if empty:
            raise RuntimeError(f"panels with no data or an error: {empty}")
    text = page.evaluate(PAGE_TEXT_JS)
    if any(s in text for s in secrets):
        raise RuntimeError("a credential is visible on the page")  # never say which
    marks = [mark(n, target, side[0] if side else None, page) for n, target, _, *side in shot.get("callouts", [])]
    page.evaluate(DRAW_JS, marks)
    tmp = out / f".{shot['file']}"
    page.screenshot(path=tmp, animations="disabled", caret="hide", mask_color="#000000",
                    mask=[page.locator(m) for m in shot.get("mask", [])])
    tmp.replace(out / shot["file"])
    return url


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="capture only the shots whose file name contains this")
    ap.add_argument("--out", type=Path, default=shots.IMAGES)
    ap.add_argument("--gallery", type=Path, default=shots.GALLERY)
    args = ap.parse_args()
    selected = [s for s in shots.SHOTS if not args.only or args.only in s["file"]]
    if not selected:
        sys.exit(f"capture: no shot matches {args.only!r}")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest = shots.load_manifest(manifest_path)
    # Only values long enough not to match by accident ("admin" is everywhere).
    secrets = [v for k in SECRETS if len(v := os.environ.get(k, "")) >= 6]
    sha = os.environ.get("K2_GIT_SHA") or subprocess.run(
        ["git", "describe", "--always", "--dirty"], capture_output=True, text=True).stdout.strip() or "unknown"
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": WIDTH, "height": 1000}, color_scheme="dark",
                                  device_scale_factor=1, timezone_id="UTC")
        ctx.add_init_script(INIT_JS)
        ctx.set_default_timeout(TIMEOUT_MS)
        logged_in = set()
        for shot in selected:
            page = None
            try:
                if shot["app"] and shot["app"] not in logged_in:
                    login(ctx, shot["app"])
                    logged_in.add(shot["app"])
                page = ctx.new_page()
                url = capture(page, shot, args.out, secrets)
                manifest[shot["file"]] = {"file": shot["file"], "url": url, "git_sha": sha,
                                          "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                print(f"ok    {shot['file']}  {(args.out / shot['file']).stat().st_size // 1024} KiB")
            except Exception as e:  # one bad page must not cost the others
                msg = str(e).splitlines()[0]
                failed.append(f"{shot['file']}: {msg}")
                print(f"FAIL  {shot['file']}: {msg}")
            finally:
                if page:
                    page.close()
        browser.close()
    rows = [manifest[f] for f in sorted(manifest)]
    manifest_path.write_text(json.dumps({"shots": rows}, indent=2) + "\n")
    args.gallery.write_text(shots.render_gallery(manifest))
    print(f"wrote {manifest_path} and {args.gallery}")
    if failed:
        sys.exit("capture: not saved, the previous image (if any) is unchanged:\n  " + "\n  ".join(failed))


if __name__ == "__main__":
    main()
