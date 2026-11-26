"""Local secrets never enter git (docs/development/phase-001-interfaces.md, A10).

They live in ${K2_SECRETS:-$HOME/k2-secrets}, outside any checkout: <principal>.password
files, tls/, the Slack webhook URL file. .gitignore keeps a copy that lands inside a
checkout out all the same. Both halves hold: git ignores the layout, and nothing
tracked matches it (a `git add -f` gets past .gitignore, not past this).
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ["k2-secrets/svc.runs.password", "k2-secrets/tls/ca.pem", "k2-secrets/slack-webhook-url",
           "dbt/k2-secrets/tls/server.key", "pub_access_canary.password", "scripts/dbt_x.password"]


def is_secret(path: str) -> bool:
    return "k2-secrets" in Path(path).parts or path.endswith(".password")


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True, text=True).stdout


def test_git_ignores_the_secrets_layout_anywhere_in_a_checkout():
    assert all(is_secret(s) for s in SAMPLES)
    # --no-index: the patterns alone decide, tracked or not.
    assert git("check-ignore", "--no-index", *SAMPLES).split() == SAMPLES


def test_no_tracked_file_is_a_secret():
    assert [f for f in git("ls-files", "-z").split("\0") if f and is_secret(f)] == []
