"""Local secrets never enter git (docs/development/phase-001-interfaces.md, A10).

They live in ${K2_SECRETS:-$HOME/k2-secrets}, outside any checkout, laid out as the secrets
table of that page and of k2gen.model.SECRET_FILES says: trino/password.db, api/keys.json,
principals/<id>.password, tls/{ca.pem,ca.key,trino.pem}, alertmanager/slack-webhook-url,
canary/<canary>.password. The checkout's .env holds the stack's own secrets, Trino's internal
shared secret among them. .gitignore keeps a copy that lands inside a checkout out all the
same, by directory and by each file's own name, wherever it is in the tree. Both halves hold:
git ignores every such name, and nothing tracked has one (a `git add -f` gets past .gitignore,
not past this).
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ["k2-secrets/svc.runs.password", "k2-secrets/tls/ca.pem", "k2-secrets/alertmanager/slack-webhook-url",
           "dbt/k2-secrets/tls/server.key", "pub_access_canary.password", "scripts/dbt_x.password",
           "k2-secrets/trino/password.db", "k2-secrets/api/keys.json", "trino/password.db", "copy/password.db",
           "alertmanager/slack-webhook-url", "tls/trino.pem", "tls/ca.key", "principals/dbt_x.password",
           "api/keys.json", "keys.json", "docs/keys.json", ".env", "docker/.env", ".env.local", ".env.prod",
           "scripts/.env.bak", ".envrc", "notes.key", "certs/other.pem"]
NOT_SECRET = [".env.example", "environment.md", "docs/env.md", "keys.json.md", "tests/fixtures/keys.yaml"]
NAMES = {"password.db", "keys.json", "slack-webhook-url"}


def is_secret(path: str) -> bool:
    p = Path(path)
    return ("k2-secrets" in p.parts or p.name in NAMES or p.suffix in (".password", ".pem", ".key")
            or (p.name.startswith(".env") and p.name != ".env.example"))


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True, text=True).stdout


def test_git_ignores_the_secrets_layout_anywhere_in_a_checkout():
    assert all(is_secret(s) for s in SAMPLES) and not any(is_secret(s) for s in NOT_SECRET)
    # --no-index: the patterns alone decide, tracked or not.
    assert git("check-ignore", "--no-index", *SAMPLES).split() == SAMPLES
    assert git("check-ignore", "--no-index", *NOT_SECRET).split() == []


def test_no_tracked_file_is_a_secret():
    assert [f for f in git("ls-files", "-z").split("\0") if f and is_secret(f)] == []
