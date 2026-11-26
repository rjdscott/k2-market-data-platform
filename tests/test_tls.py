"""scripts/tls.sh (ADR-060): the local CA and Trino's server certificate under ${K2_SECRETS}/tls/.

Each run is on a temporary K2_SECRETS outside any checkout, with the host's openssl. What a
client relies on is asserted from the certificates themselves, not from the script's words: the
names, the CA's name constraint, the lifetimes, the key type, the modes, the inode a rotation
keeps (a container's file mount sees an in-place write, never a rename), and that no command
ever prints a key.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TLS = ROOT / "scripts" / "tls.sh"


def tls(secrets: Path, *argv: str, **env: str) -> subprocess.CompletedProcess:
    out = subprocess.run(["bash", str(TLS), *argv], capture_output=True, text=True,
                         env={**os.environ, "K2_SECRETS": str(secrets), **env})
    printed = out.stdout + out.stderr
    for key in secrets.rglob("*.key"):
        body = [line for line in key.read_text().splitlines() if line and not line.startswith("-----")]
        assert not [line for line in body if line in printed], f"{argv}: printed {key.name}"
    pem = secrets / "tls" / "trino.pem"
    if pem.exists():
        body = re.search(r"PRIVATE KEY-----\n(.*?)\n-----END", pem.read_text(), re.S)
        assert body and not [line for line in body.group(1).splitlines() if line in printed], f"{argv}: printed Trino's key"
    assert "PRIVATE KEY" not in printed
    return out


def x509(path: Path, *args: str) -> str:
    return subprocess.run(["openssl", "x509", "-in", str(path), "-noout", *args], capture_output=True, text=True, check=True).stdout


def lifetime_days(path: Path) -> int:
    from datetime import datetime
    dates = dict(line.split("=", 1) for line in x509(path, "-startdate", "-enddate").splitlines())
    parse = lambda s: datetime.strptime(s, "%b %d %H:%M:%S %Y %Z")  # noqa: E731
    return round((parse(dates["notAfter"]) - parse(dates["notBefore"])).total_seconds() / 86400)


def expired_under_the_ca(secrets: Path, work: Path) -> bytes:
    """A key and a certificate for Trino's names from the CA under `secrets`, valid on 2026-01-01 only."""
    work.mkdir()
    run = lambda *a: subprocess.run(a, cwd=work, capture_output=True, check=True)  # noqa: E731
    run("openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "e.key")
    run("openssl", "req", "-new", "-key", "e.key", "-subj", "/CN=trino", "-out", "e.csr")
    (work / "e.ext").write_text("basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:trino,DNS:localhost\n")
    (work / "index").write_text("")
    (work / "serial").write_text("01\n")
    (work / "ca.cnf").write_text(f"[ca]\ndefault_ca=t\n[t]\ndatabase={work}/index\nnew_certs_dir={work}\nserial={work}/serial\n"
                                 "default_md=sha256\npolicy=p\nunique_subject=no\n[p]\ncommonName=supplied\n")
    run("openssl", "ca", "-batch", "-notext", "-config", "ca.cnf", "-cert", str(secrets / "tls/ca.pem"), "-keyfile",
        str(secrets / "tls/ca.key"), "-in", "e.csr", "-out", "e.crt", "-extfile", "e.ext",
        "-startdate", "20260101000000Z", "-enddate", "20260102000000Z")
    return (work / "e.key").read_bytes() + (work / "e.crt").read_bytes()


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    root = tmp_path / "k2-secrets"
    out = tls(root, "init")
    assert out.returncode == 0, out.stderr
    return root


def test_init_makes_a_name_constrained_ca_and_a_server_certificate_for_exactly_the_names_clients_use(secrets: Path) -> None:
    ca, server, key = secrets / "tls/ca.pem", secrets / "tls/trino.pem", secrets / "tls/ca.key"
    assert sorted(p.name for p in (secrets / "tls").iterdir()) == ["ca.key", "ca.pem", "trino.pem"]
    assert oct((secrets / "tls").stat().st_mode & 0o777) == "0o700"
    assert oct(key.stat().st_mode & 0o777) == oct(server.stat().st_mode & 0o777) == "0o600"
    assert re.search(r"Subject Alternative Name:\s*\n\s*DNS:trino, DNS:localhost\n", x509(server, "-ext", "subjectAltName"))
    constraints = x509(ca, "-ext", "basicConstraints,nameConstraints")
    assert "critical" in constraints and "CA:TRUE, pathlen:0" in constraints
    assert re.findall(r"DNS:(\S+)", constraints) == ["trino", "localhost", ".trino", ".localhost"]
    assert "CA:FALSE" in x509(server, "-ext", "basicConstraints") and "TLS Web Server Authentication" in x509(server, "-ext", "extendedKeyUsage")
    assert (lifetime_days(ca), lifetime_days(server)) == (1826, 90)
    for f in (ca, server):
        assert "ASN1 OID: prime256v1" in x509(f, "-text")
    verify = subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(server)], capture_output=True, text=True)
    assert verify.returncode == 0, verify.stderr
    assert "BEGIN PRIVATE KEY" in server.read_text() and server.read_text().index("PRIVATE KEY") < server.read_text().index("CERTIFICATE")


def test_a_rerun_changes_nothing_and_rotate_reissues_in_place_under_the_same_ca(secrets: Path) -> None:
    ca, server = secrets / "tls/ca.pem", secrets / "tls/trino.pem"
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in (secrets / "tls").iterdir()}
    again = tls(secrets, "init")
    assert again.returncode == 0 and "kept" in again.stdout
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in (secrets / "tls").iterdir()} == before
    serial = x509(server, "-serial")
    out = tls(secrets, "rotate")
    assert out.returncode == 0, out.stderr
    assert server.stat().st_ino == before[server][0] and x509(server, "-serial") != serial
    assert server.read_bytes() != before[server][1] and ca.read_bytes() == before[ca][1]
    assert subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(server)], capture_output=True).returncode == 0
    assert oct(server.stat().st_mode & 0o777) == "0o600"


def test_an_existing_ca_is_replaced_only_with_force_and_in_place(secrets: Path) -> None:
    ca, key = secrets / "tls/ca.pem", secrets / "tls/ca.key"
    fingerprint, inode = x509(ca, "-fingerprint", "-sha256"), ca.stat().st_ino
    (secrets / "tls/trino.pem").unlink()
    partial = tls(secrets, "init")  # the CA is kept; only the missing server certificate is made
    assert partial.returncode == 0 and x509(ca, "-fingerprint", "-sha256") == fingerprint
    assert (secrets / "tls/trino.pem").exists()
    key_bytes = key.read_bytes()
    key.write_bytes(b"")
    refused = tls(secrets, "init")
    assert refused.returncode == 2 and "FORCE=1" in refused.stderr and x509(ca, "-fingerprint", "-sha256") == fingerprint
    key.write_bytes(key_bytes)
    forced = tls(secrets, "init", "--force")
    assert forced.returncode == 0, forced.stderr
    assert x509(ca, "-fingerprint", "-sha256") != fingerprint and ca.stat().st_ino == inode
    assert subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(secrets / "tls/trino.pem")], capture_output=True).returncode == 0


def test_status_prints_names_and_days_and_fails_under_the_threshold_or_on_any_fault(secrets: Path, tmp_path: Path) -> None:
    ok = tls(secrets, "status")
    assert ok.returncode == 0, ok.stdout
    assert "names     DNS:trino,DNS:localhost" in ok.stdout and re.search(r"\((89|90) days left\)", ok.stdout)
    assert tls(secrets, "status", MIN_DAYS="100").returncode == 1
    os.chmod(secrets / "tls/ca.key", 0o644)
    loose = tls(secrets, "status")
    assert loose.returncode == 1 and "not 600" in loose.stdout
    os.chmod(secrets / "tls/ca.key", 0o600)
    expired = expired_under_the_ca(secrets, tmp_path / "expired")
    (secrets / "tls/trino.pem").write_bytes(expired)
    stale = tls(secrets, "status")
    assert stale.returncode == 1 and "FAIL: expired" in stale.stdout and "not signed" not in stale.stdout, stale.stdout
    other = tmp_path / "other"
    assert tls(other, "init").returncode == 0
    (secrets / "tls/trino.pem").write_bytes((other / "tls/trino.pem").read_bytes())  # another CA's certificate
    foreign = tls(secrets, "status")
    assert foreign.returncode == 1 and "not signed by" in foreign.stdout


def test_refused_inside_a_git_checkout_and_rotate_needs_a_ca(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    inside = tls(repo / "k2-secrets", "init")
    assert inside.returncode == 2 and "inside a git checkout" in inside.stderr and not (repo / "k2-secrets").exists()
    none = tls(tmp_path / "empty", "rotate")
    assert none.returncode == 2 and "make tls-init first" in none.stderr


def forged(secrets: Path, work: Path, san: str) -> subprocess.CompletedProcess:
    """`openssl verify` of a leaf the CA's own key signs for `san`: what a stolen ca.key could make."""
    work.mkdir(exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=work, capture_output=True, check=True)  # noqa: E731
    run("openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "f.key")
    run("openssl", "req", "-new", "-key", "f.key", "-subj", "/CN=forged", "-out", "f.csr")
    (work / "f.ext").write_text(f"basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName={san}\n")
    run("openssl", "x509", "-req", "-in", "f.csr", "-CA", str(secrets / "tls/ca.pem"), "-CAkey", str(secrets / "tls/ca.key"),
        "-days", "1", "-set_serial", "0x0f", "-extfile", "f.ext", "-out", "f.crt")
    return subprocess.run(["openssl", "verify", "-CAfile", str(secrets / "tls/ca.pem"), str(work / "f.crt")], capture_output=True, text=True)


@pytest.mark.parametrize("san, verifies", [
    ("DNS:trino", True), ("DNS:localhost", True), ("DNS:trino,DNS:localhost", True),
    ("IP:10.9.8.7", False), ("IP:127.0.0.1", False), ("IP:::1", False), ("DNS:trino,IP:10.9.8.7", False),
    ("DNS:evil.trino", False), ("DNS:attacker.localhost", False), ("DNS:example.com", False), ("DNS:trino.example.com", False),
    # Not constrained, and harmless to TLS: no client takes a server by an email name (ADR-060's Outcome).
    ("email:someone@example.org", True),
])
def test_the_ca_key_signs_only_the_names_clients_use(secrets: Path, tmp_path: Path, san: str, verifies: bool) -> None:
    out = forged(secrets, tmp_path / "forged", san)
    assert (out.returncode == 0) is verifies, out.stdout + out.stderr


def test_status_fails_a_ca_made_before_the_ip_and_subdomain_exclusions(secrets: Path, tmp_path: Path) -> None:
    work = tmp_path / "old-ca"
    work.mkdir()
    run = lambda *a: subprocess.run(a, cwd=work, capture_output=True, check=True)  # noqa: E731
    (work / "ca.ext").write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                                 "nameConstraints=critical,permitted;DNS:trino,permitted;DNS:localhost\n")
    run("openssl", "req", "-new", "-key", str(secrets / "tls/ca.key"), "-subj", "/CN=old", "-out", "ca.csr")
    run("openssl", "x509", "-req", "-in", "ca.csr", "-signkey", str(secrets / "tls/ca.key"), "-days", "30", "-extfile", "ca.ext", "-out", "ca.pem")
    (secrets / "tls/ca.pem").write_bytes((work / "ca.pem").read_bytes())  # the same key, the constraint of before
    out = tls(secrets, "status", MIN_DAYS="1")
    assert out.returncode == 1 and "the CA's name constraint is not exactly" in out.stdout and "FORCE=1" in out.stdout, out.stdout


def test_a_checkout_git_cannot_read_is_still_refused_and_modes_are_kept(tmp_path: Path) -> None:
    fake = tmp_path / "checkout"
    fake.mkdir()
    (fake / ".git").write_text("gitdir: /nonexistent/worktree\n")  # git answers nothing here, as for another uid's
    inside = tls(fake / "k2-secrets", "init")
    assert inside.returncode == 2 and "inside a git checkout" in inside.stderr and not (fake / "k2-secrets").exists()
    root = tmp_path / "k2-secrets"
    assert tls(root, "init").returncode == 0
    os.chmod(root, 0o755)
    os.chmod(root / "tls", 0o755)
    assert tls(root, "status").returncode == 1  # status reports a looser tls/ and changes nothing
    assert tls(root, "rotate").returncode == 0
    assert oct(root.stat().st_mode & 0o777) == oct((root / "tls").stat().st_mode & 0o777) == "0o700"
