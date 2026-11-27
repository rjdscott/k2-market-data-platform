#!/usr/bin/env bash
# The catalog site against its release (plan 003 Verification; W3-10):
#   bash scripts/verify/catalog-site.sh                      # offline: compile a trial release
#        (ci-validate.sh), generate with --no-facts into a temp dir, check it, then plant a secret
#        in a copy and check that the check fails
#   bash scripts/verify/catalog-site.sh <release dir> <site dir>   # check a generated site; <site
#        dir> is what k2-api serves at /catalog/ (<out>/current)
# Asserts a page per product of the release's catalog index, each linking /dbt-docs/ and holding
# the product's owner and every table name; no script, no src, no href but https: or site-relative;
# and no secret-looking text. It reads no secret file: it only looks for the patterns.
set -euo pipefail
cd "$(dirname "$0")/../.."

check() {  # check <release dir> <site dir>
  python3 - "$1" "$2" <<'PY'
import json, re, sys
from html.parser import HTMLParser
from pathlib import Path

release, site = Path(sys.argv[1]), Path(sys.argv[2])
# The names and shapes of secrets: a password or secret named, a bearer token, a k2 API key
# ("k2_" + token_urlsafe(32), scripts/principal-secret.py), an AWS-style access key, a PEM key.
PATTERNS = [r"(?i)password", r"(?i)secret", r"Bearer\s+\S", r"\bk2_[A-Za-z0-9_-]{32,}",
            r"\b(AKIA|ASIA)[A-Z0-9]{16}\b", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"]
# Phrases a contract description may hold that match a pattern and are not a secret; each added
# by hand with the contract it comes from. Empty: no description today names one.
ALLOW: list[str] = []


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text, self.hrefs, self.srcs, self.tags = [], [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for k, v in attrs:
            if k == "href":
                self.hrefs.append(v or "")
            if k == "src" or k.startswith("on"):
                self.srcs.append(f"{k}={v}")

    def handle_data(self, data):
        self.text.append(data)


fails = []
index = json.loads((release / "compiled/catalog/index.json").read_text())
for entry in index["products"]:
    doc = json.loads((release / f"compiled/catalog/products/{entry['id']}.json").read_text())
    path = site / doc["domain"] / doc["name"] / "index.html"
    if not path.is_file():
        fails.append(f"{entry['id']}: no page at {path.relative_to(site)}")
        continue
    page = Page(path.read_text())
    words = " ".join(page.text)
    if not any(h.startswith("/dbt-docs/") for h in page.hrefs):
        fails.append(f"{entry['id']}: no link to /dbt-docs/")
    if doc["owner"] not in words:
        fails.append(f"{entry['id']}: owner {doc['owner']} not on the page")
    for out in doc["outputs"]:
        table = out["contractId"].rsplit(".", 1)[1]
        if table not in words:
            fails.append(f"{entry['id']}: table {table} not on the page")
pages = sorted(site.rglob("*.html"))
for path in pages:
    raw = path.read_text()
    page = Page(raw)
    where = path.relative_to(site)
    if "script" in page.tags or page.srcs:
        fails.append(f"{where}: a script, a src or an event handler")
    for h in page.hrefs:
        if not (h.startswith("https://") or (h.startswith("/") and not h.startswith("//")) or ":" not in h.split("/")[0]):
            fails.append(f"{where}: href {h[:60]!r} is neither https: nor site-relative")
        if h.startswith("//"):
            fails.append(f"{where}: protocol-relative href {h[:60]!r}")
    text = raw
    for phrase in ALLOW:
        text = text.replace(phrase, "")
    for pattern in PATTERNS:
        for m in re.finditer(pattern, text):
            fails.append(f"{where}: secret-looking text {pattern!r} at offset {m.start()}")  # never the match itself
for f in fails:
    print(f"FAIL {f}")
print(f"catalog-site: {len(index['products'])} products, {len(pages)} pages, {len(fails)} failures")
sys.exit(1 if fails else 0)
PY
}

if [ $# -eq 2 ]; then
  check "$1" "$2"
  exit
fi
[ $# -eq 0 ] || { echo "usage: catalog-site.sh [<release dir> <site dir>]" >&2; exit 2; }

tmp=$(mktemp -d)
trap 'chmod -R u+w "$tmp"; rm -rf "$tmp"' EXIT  # a release's files are read-only
rel=$(bash scripts/verify/ci-validate.sh "$tmp/ci" 2> "$tmp/ci.log" | tail -1) || { cat "$tmp/ci.log" >&2; exit 1; }
# The copy the release ships: the generator always matches the documents it renders.
python3 "$rel/platform/platform/catalog-site/k2site.py" --release "$rel" --out "$tmp/site" --no-facts
check "$rel" "$tmp/site/current"

# The check must bite: a planted bearer token in a copy of one page fails it.
cp -rL "$tmp/site/current" "$tmp/planted"
page=$(find "$tmp/planted" -name index.html -path '*/*/*/index.html' | sort | head -1)
printf '<p>Authorization: Bearer %s</p>\n' "$(printf 'x%.0s' {1..40})" >> "$page"
if check "$rel" "$tmp/planted" > "$tmp/planted.log"; then
  echo "FAIL the check passed a page with a planted bearer token" >&2
  exit 1
fi
grep -q "secret-looking text 'Bearer" "$tmp/planted.log"
echo "catalog-site: a planted bearer token fails the check, as it must"
