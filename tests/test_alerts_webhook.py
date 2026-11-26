"""scripts/alerts.sh install-webhook (make alerts-install-webhook), on a temporary K2_SECRETS: the
URL comes from stdin and is printed nowhere, tracing included; a K2_SECRETS inside a checkout is
refused even where git cannot read the checkout; the file is 600 and its directories go back to
700 on every run. Delivery itself is `make alert-test`'s and the stand-in's (scripts/alerts.sh)."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://hooks.slack.com/services/T000/B000/zzcanaryzz"


def install(secrets: Path, *bash_flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", *bash_flags, str(ROOT / "scripts/alerts.sh"), "install-webhook"], cwd=ROOT,
                          input=URL + "\n", capture_output=True, text=True, env={**os.environ, "K2_SECRETS": str(secrets)})


def test_installed_at_600_under_700_directories_and_never_printed(tmp_path: Path) -> None:
    secrets = tmp_path / "k2-secrets"
    (secrets / "alertmanager").mkdir(parents=True)
    for d in (secrets, secrets / "alertmanager"):
        d.chmod(0o755)  # as Docker makes a missing mount source
    out = install(secrets)
    assert out.returncode == 0, out.stderr
    f = secrets / "alertmanager/slack-webhook-url"
    assert f.read_text() == URL + "\n" and "zzcanaryzz" not in out.stdout + out.stderr
    assert [p.stat().st_mode & 0o777 for p in (secrets, secrets / "alertmanager", f)] == [0o700, 0o700, 0o600]


def test_refused_under_xtrace_without_printing_the_url(tmp_path: Path) -> None:
    out = install(tmp_path / "k2-secrets", "-x")
    assert out.returncode != 0 and "xtrace" in out.stderr and "zzcanaryzz" not in out.stdout + out.stderr
    assert not (tmp_path / "k2-secrets").exists()


def test_refused_inside_a_checkout_git_cannot_read(tmp_path: Path) -> None:
    fake = tmp_path / "checkout"
    fake.mkdir()
    (fake / ".git").write_text("gitdir: /nonexistent/worktree\n")  # git answers nothing here, as for another uid's
    out = install(fake / "k2-secrets")
    assert out.returncode == 2 and "inside a git checkout" in out.stderr and not (fake / "k2-secrets").exists()
