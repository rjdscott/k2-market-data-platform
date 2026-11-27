"""scripts/principal-secret.py through its make targets (mk/principals.mk), with K2_SECRETS in a
temporary directory: a secret is printed once on a terminal or written to a service's or a test
principal's 0600 file, only its hash reaches password.db or keys.json, a rotation rewrites one
fixed slot at its own offset, a removal blanks its slots and moves no other line, a password.db
that would not parse is never written, concurrent runs serialise, an unknown principal is
refused, a removal refuses a known one or the canary, status names the stale and the malformed
lines and exits 1 on either, the directories go back to 700, a checkout git cannot read is
still refused, and nothing reaches the checkout.

The bcrypt hashes are checked by the pinned bcrypt the targets use, in a uv subprocess, since the
root test environment carries none. The login a real Trino 484 accepts, and the one it refuses,
are in docs/runbooks/create-or-rotate-a-principal.md, run on a throwaway server.
"""

import hashlib
import json
import os
import pty
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BCRYPT = "bcrypt==5.0.0"  # mk/principals.mk's pin
SECRET = re.compile(r"^[A-Za-z0-9_-]{43}$", re.M)  # secrets.token_urlsafe(32)
KEY = re.compile(r"^k2_[A-Za-z0-9_-]{43}$", re.M)
LINE = re.compile(r"^(?P<user>[^:\n]+):(?P<hash>\$2y\$10\$[./A-Za-z0-9]{53}) *$")
SLOT = 128  # principal-secret.SLOT: one password.db line, padded with spaces, its newline included
SERVICES = ("svc.runs", "svc.catalog", "svc.platform", "svc.api", "dbt_access_canary", "pub_access_canary")
CANARY = "k2_release_0123456789ab:$2y$10$" + "C" * 53 + "\n"  # a line deploy (S19) owns: kept as it is
GONE = "dbt_gone:$2y$10$" + "D" * 53 + "\n"  # a builder whose product is gone, still matching dbt_.*


def run(secrets: Path, *args: str, tty: bool = False) -> tuple[int, str, str]:
    """`make <args>` with K2_SECRETS=secrets: (exit, stdout, stderr). With tty, stdout is a terminal."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PRINCIPAL", "ROTATE", "TO_FILE", "FORCE")}
    env["K2_SECRETS"] = str(secrets)
    argv = ["make", "-s", "--no-print-directory", "-C", str(ROOT), *args]
    if not tty:
        p = subprocess.run(argv, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    leader, follower = pty.openpty()
    p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=follower, stderr=subprocess.PIPE)
    os.close(follower)
    out = b""
    while True:
        try:
            chunk = os.read(leader, 4096)
        except OSError:  # EIO: every holder of the terminal has exited
            break
        if not chunk:
            break
        out += chunk
    os.close(leader)
    err = p.stderr.read()
    p.wait()
    return p.returncode, out.decode().replace("\r\n", "\n"), err.decode()


def bcrypt_checks(pairs: list[tuple[str, str]]) -> list[bool]:
    """bcrypt.checkpw for each (secret, hash), the secrets passed on stdin."""
    code = "import bcrypt, json, sys; print(json.dumps([bcrypt.checkpw(s.encode(), h.encode()) for s, h in json.load(sys.stdin)]))"
    p = subprocess.run(["uv", "run", "--no-project", "-q", "--with", BCRYPT, "python", "-I", "-c", code],
                       input=json.dumps(pairs), capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def lines(secrets: Path) -> dict[str, str]:
    """password.db as {user: hash}, every line one well-formed bcrypt line but the canary's and
    the blank slots of removed users."""
    found = {}
    for line in (secrets / "trino/password.db").read_text().splitlines():
        if line.rstrip() in (CANARY.rstrip("\n"), ""):
            continue
        m = LINE.fullmatch(line)
        assert m, f"not a Trino bcrypt line: {line.split(':')[0]}"
        assert m["user"] not in found, f"{m['user']} twice"
        found[m["user"]] = m["hash"]
    return found


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def slots(db: Path) -> list[bytes]:
    """password.db's fixed slots, each SLOT bytes with its newline last and nowhere else."""
    data = db.read_bytes()
    assert len(data) % SLOT == 0, f"{len(data)} bytes: not whole slots"
    out = [data[i:i + SLOT] for i in range(0, len(data), SLOT)]
    assert all(x.index(b"\n") == SLOT - 1 for x in out), "a slot with a newline inside it"
    return out


def slot(line: str) -> bytes:
    return line.rstrip("\n").ljust(SLOT - 1).encode() + b"\n"


def module(code: str) -> subprocess.CompletedProcess:
    """`code` with `ps` (scripts/principal-secret.py) loaded, in k2gen's environment with bcrypt."""
    head = ("import importlib.util, sys\nspec = importlib.util.spec_from_file_location('ps', 'scripts/principal-secret.py')\n"
            "ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)\n")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return subprocess.run(["uv", "run", "--project", "platform/k2gen", "--locked", "-q", "--with", BCRYPT, "python", "-c", head + code],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def checkout() -> tuple[str, str]:
    return (subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout,
            subprocess.run(["git", "diff"], cwd=ROOT, capture_output=True, text=True).stdout)


def tracked_files_holding(text: str) -> str:
    p = subprocess.run(["git", "grep", "-l", "-F", "-e", text], cwd=ROOT, capture_output=True, text=True)  # -e: a secret may start with a dash
    assert p.returncode in (0, 1), p.stderr
    return p.stdout


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    return tmp_path / "k2-secrets"


def test_a_person_password_is_printed_once_and_only_its_bcrypt_is_kept(secrets: Path) -> None:
    before = checkout()
    code, out, err = run(secrets, "password", "PRINCIPAL=p.canary-full", tty=True)
    assert code == 0, err
    (secret,) = SECRET.findall(out)
    assert out.count(secret) == 1 and secret not in err
    stored = lines(secrets)
    assert list(stored) == ["p.canary-full"] and secret not in (secrets / "trino/password.db").read_text()
    assert bcrypt_checks([(secret, stored["p.canary-full"]), (secret + "x", stored["p.canary-full"])]) == [True, False]
    assert [p.relative_to(secrets).as_posix() for p in secrets.rglob("*")] == ["trino", "trino/password.db"]
    assert (mode(secrets), mode(secrets / "trino"), mode(secrets / "trino/password.db")) == (0o700, 0o700, 0o600)
    assert checkout() == before and tracked_files_holding(secret) == ""


def test_a_service_password_goes_to_its_file_and_is_printed_nowhere(secrets: Path) -> None:
    code, out, err = run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")
    assert code == 0, err
    secret = (secrets / "principals/svc.runs.password").read_text()
    assert SECRET.fullmatch(secret) and out == "" and secret not in err
    assert bcrypt_checks([(secret, lines(secrets)["svc.runs"])]) == [True]
    assert (mode(secrets / "principals"), mode(secrets / "principals/svc.runs.password")) == (0o700, 0o600)


def test_an_api_key_is_printed_once_and_only_its_sha256_is_kept(secrets: Path) -> None:
    code, out, err = run(secrets, "api-key", "PRINCIPAL=p.canary-delayed", tty=True)
    assert code == 0, err
    (key,) = KEY.findall(out)
    assert out.count(key) == 1 and key not in err
    keys = secrets / "api/keys.json"
    assert json.loads(keys.read_text()) == {hashlib.sha256(key.encode()).hexdigest(): "p.canary-delayed"}
    assert key not in keys.read_text() and tracked_files_holding(key) == ""
    assert (mode(secrets / "api"), mode(keys)) == (0o700, 0o600)


def test_a_rotation_replaces_one_line_in_place_and_nothing_else(secrets: Path) -> None:
    (secrets / "trino").mkdir(parents=True)
    db = secrets / "trino/password.db"
    db.write_text(CANARY)  # a file not yet in slots: the first write lays it out, once
    for service in ("svc.runs", "svc.api"):
        assert run(secrets, "password", f"PRINCIPAL={service}", "TO_FILE=1")[0] == 0
    with db.open("ab") as f:  # deploy's lines, either side, in slots as its write() leaves them
        f.write(slot(CANARY.replace("0123456789ab", "ba9876543210")))
    old_text, old_secret, inode = db.read_text(), (secrets / "principals/svc.runs.password").read_text(), db.stat().st_ino
    old_slots = slots(db)

    code, _, err = run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")
    assert code != 0 and "ROTATE=1" in err and db.read_text() == old_text
    code, _, err = run(secrets, "password", "PRINCIPAL=svc.runs", tty=True)  # its containers would keep the old one
    assert code != 0 and "TO_FILE=1 ROTATE=1" in err and db.read_text() == old_text

    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1", "ROTATE=1")[0] == 0
    new_secret = (secrets / "principals/svc.runs.password").read_text()
    new_slots = slots(db)  # the same slots at the same offsets: only svc.runs's changed
    changed = [i for i, (a, b) in enumerate(zip(old_slots, new_slots)) if a != b]
    assert len(old_slots) == len(new_slots) == 4 and changed == [1]
    assert old_slots[1].startswith(b"svc.runs:") and new_slots[1].startswith(b"svc.runs:")
    assert db.stat().st_ino == inode and new_secret != old_secret  # rewritten in place, never renamed over
    assert bcrypt_checks([(new_secret, lines(secrets)["svc.runs"]), (old_secret, lines(secrets)["svc.runs"])]) == [True, False]

    first = KEY.findall(run(secrets, "api-key", "PRINCIPAL=p.canary-full", tty=True)[1])
    code, _, err = run(secrets, "api-key", "PRINCIPAL=p.canary-full", tty=True)
    assert code != 0 and "ROTATE=1" in err
    second = KEY.findall(run(secrets, "api-key", "PRINCIPAL=p.canary-full", "ROTATE=1", tty=True)[1])
    assert json.loads((secrets / "api/keys.json").read_text()) == {hashlib.sha256(second[0].encode()).hexdigest(): "p.canary-full"}
    assert first != second


def test_concurrent_runs_serialise_on_the_file(secrets: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"K2_SECRETS": str(secrets)}

    def start(*args: str) -> subprocess.Popen:
        return subprocess.Popen(["make", "-s", "--no-print-directory", "-C", str(ROOT), "password", *args],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    procs = [start(f"PRINCIPAL={s}", "TO_FILE=1") for s in SERVICES]
    assert [p.wait() for p in procs] == [0] * len(SERVICES), [p.stderr.read() for p in procs]
    procs = [start("PRINCIPAL=svc.runs", "TO_FILE=1", "ROTATE=1") for _ in range(8)]
    assert [p.wait() for p in procs] == [0] * 8, [p.stderr.read() for p in procs]
    stored = lines(secrets)
    assert sorted(stored) == sorted(SERVICES)
    # Each line is the hash of the password its file holds: the line and the file of one run, never two.
    assert bcrypt_checks([((secrets / f"principals/{s}.password").read_text(), stored[s]) for s in SERVICES]) == [True] * len(SERVICES)


def test_a_directory_another_run_made_meanwhile_is_not_an_error(tmp_path: Path) -> None:
    """The race the concurrent test hit: two runs both see a directory missing, both mkdir."""
    made = module(f"""
import pathlib
real = pathlib.Path.exists
pathlib.Path.exists = lambda self: False if self.name == 'k2-secrets' else real(self)  # the check lost the race
d = pathlib.Path({str(tmp_path / 'k2-secrets')!r}); d.mkdir()
ps.directory(d / 'trino' / 'password.db')
""")
    assert made.returncode == 0, made.stderr


@pytest.mark.parametrize("args, why", [
    (("password", "PRINCIPAL=p.nobody"), "not a principal"),
    (("password", "PRINCIPAL=k2_release_0123456789ab", "TO_FILE=1"), "not a principal"),  # deploy's (S19)
    (("password", "PRINCIPAL=../../x", "TO_FILE=1"), "not a principal"),
    (("password",), "PRINCIPAL=<id>"),
    (("api-key", "PRINCIPAL=svc.api"), "never acts as it"),
    (("api-key", "PRINCIPAL=dbt_access_canary"), "never acts as it"),
    (("password", "PRINCIPAL=p.canary-full"), "not a terminal"),
    (("api-key", "PRINCIPAL=p.canary-full"), "not a terminal"),
    (("principal-remove",), "PRINCIPAL=<id>"),
    (("principal-remove", "PRINCIPAL=../../x", "FORCE=1"), "not a principal id"),
])
def test_a_refused_run_writes_nothing(secrets: Path, args: tuple[str, ...], why: str) -> None:
    code, out, err = run(secrets, *args)
    assert code != 0 and why in err and out == ""
    assert not secrets.exists() or [p for p in secrets.rglob("*") if p.is_file()] == []


def test_secrets_inside_a_checkout_are_refused() -> None:
    inside = ROOT / "k2-secrets-refused-by-test"
    code, _, err = run(inside, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")
    assert code != 0 and "inside a git checkout" in err and not inside.exists()


def test_a_removal_drops_one_principal_in_place_and_nothing_else(secrets: Path) -> None:
    for service in ("svc.runs", "svc.api"):
        assert run(secrets, "password", f"PRINCIPAL={service}", "TO_FILE=1")[0] == 0
    db, keys, gone_file = secrets / "trino/password.db", secrets / "api/keys.json", secrets / "principals/dbt_gone.password"
    first, second = (x.rstrip() + "\n" for x in db.read_text().splitlines())
    db.write_text(CANARY + first + GONE + second + GONE)  # by hand, not in slots; truncated in place: the same inode
    keys.parent.mkdir(mode=0o700)
    keys.write_text(json.dumps({"a" * 64: "p.canary-full", "b" * 64: "dbt_gone", "c" * 64: "dbt_gone"}))
    gone_file.write_text("x" * 43)
    inode, keys_inode = db.stat().st_ino, keys.stat().st_ino

    code, out, err = run(secrets, "principal-remove", "PRINCIPAL=dbt_gone")
    assert code == 0, err
    assert slots(db) == [slot(CANARY), slot(first), slot(second)]  # every other line, in order, now in slots
    assert (db.stat().st_ino, keys.stat().st_ino) == (inode, keys_inode)  # rewritten in place, never renamed over
    assert json.loads(keys.read_text()) == {"a" * 64: "p.canary-full"} and not gone_file.exists()
    assert "2 line(s) of trino/password.db" in err and "2 key(s) of api/keys.json" in err
    assert "principals/dbt_gone.password" in err and "$2y$" not in out + err and "b" * 64 not in out + err

    code, _, err = run(secrets, "principal-remove", "PRINCIPAL=dbt_gone")
    assert code == 0 and "nothing to remove" in err and slots(db) == [slot(CANARY), slot(first), slot(second)]

    # In slots, a removal blanks the user's slot and moves nothing; the next new user takes it.
    with db.open("ab") as f:
        f.write(slot(GONE))
    assert run(secrets, "password", "PRINCIPAL=svc.platform", "TO_FILE=1")[0] == 0
    before = slots(db)
    assert [x.split(b":")[0] for x in before] == [b"k2_release_0123456789ab", b"svc.runs", b"svc.api", b"dbt_gone", b"svc.platform"]
    assert run(secrets, "principal-remove", "PRINCIPAL=dbt_gone")[0] == 0
    assert slots(db) == before[:3] + [slot("")] + before[4:] and db.stat().st_ino == inode
    assert run(secrets, "password", "PRINCIPAL=svc.catalog", "TO_FILE=1")[0] == 0
    assert len(slots(db)) == 5 and slots(db)[3].startswith(b"svc.catalog:") and slots(db)[4] == before[4]


def test_a_removal_of_a_known_principal_needs_force(secrets: Path) -> None:
    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")[0] == 0
    db, stored = secrets / "trino/password.db", secrets / "principals/svc.runs.password"
    old_text, old_secret = db.read_text(), stored.read_text()

    code, _, err = run(secrets, "principal-remove", "PRINCIPAL=svc.runs")
    assert code != 0 and "still known" in err and "FORCE=1" in err
    assert db.read_text() == old_text and stored.read_text() == old_secret

    code, _, err = run(secrets, "principal-remove", "PRINCIPAL=svc.runs", "FORCE=1")
    assert code == 0, err
    assert db.read_text().strip() == "" and not stored.exists()  # its slot blank, which Trino skips


def test_a_removal_of_the_release_canary_is_refused(secrets: Path) -> None:
    (secrets / "trino").mkdir(parents=True)
    db = secrets / "trino/password.db"
    db.write_text(CANARY)
    code, out, err = run(secrets, "principal-remove", "PRINCIPAL=k2_release_0123456789ab", "FORCE=1")
    assert code != 0 and "release canary" in err and out == "" and db.read_text() == CANARY


def test_status_lists_names_and_never_a_hash(secrets: Path) -> None:
    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")[0] == 0
    db = secrets / "trino/password.db"
    db.write_text(db.read_text() + CANARY + "p.gone:$2y$10$" + "D" * 53 + "\n" + GONE)
    (secrets / "principals/svc.old.password").write_text("x" * 43)
    code, out, err = run(secrets, "principals-status")
    assert code != 0, err  # a STALE line is a failure, not advice (the script exits 1, make 2)
    rows = {f[0]: f[1:] for f in (line.split() for line in out.splitlines()) if f and f[0] in ("svc.runs", "svc.api", "p.canary-full")}
    assert rows["svc.runs"] == ["yes", "yes", "-"] and rows["svc.api"] == ["no", "no", "-"]
    assert rows["p.canary-full"] == ["no", "no", "no"]  # a test principal: its file column counts
    assert "release canaries" in out and "k2_release_0123456789ab" in out
    stale = {line.split()[1]: line for line in out.splitlines() if line.startswith("STALE ")}
    assert sorted(stale) == ["dbt_gone", "p.gone", "svc.old"]
    assert stale["dbt_gone"].startswith("STALE dbt_gone in trino/password.db: ") and "pattern rules" in stale["dbt_gone"]
    assert "Trino still logs it in" in stale["p.gone"] and "pattern rules" not in stale["p.gone"]
    assert stale["svc.old"].startswith("STALE svc.old in principals/svc.old.password: ") and "Trino" not in stale["svc.old"]
    assert "$2y$" not in out and (secrets / "principals/svc.runs.password").read_text() not in out



def test_status_exits_0_when_nothing_is_stale_or_malformed(secrets: Path) -> None:
    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")[0] == 0
    code, out, err = run(secrets, "principals-status")
    assert code == 0 and "STALE" not in out and "MALFORMED" not in out, out + err


NAMELESS = "zz-pasted-secret-zz\n"  # a line with no `:`: whatever it is, it is never printed


def test_a_password_db_that_would_not_parse_is_never_written_and_status_names_each_bad_line(secrets: Path) -> None:
    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")[0] == 0
    db = secrets / "trino/password.db"
    good = db.read_text()
    for bad, why in [("svc.api:$2y$04$" + "A" * 53 + "\n", "svc.api's hash is not a cost-10"),  # Trino refuses below 8
                     ("svc.api:$2y$10$" + "A" * 52 + "\n", "svc.api's hash is not a cost-10"),
                     ("svc.runs:$2y$10$" + "B" * 53 + "\n", "svc.runs again (first at line 1)"),
                     (NAMELESS, "no `:` between a user and a hash")]:
        db.write_text(good + bad)
        code, out, err = run(secrets, "password", "PRINCIPAL=svc.catalog", "TO_FILE=1")
        assert code != 0 and "refused" in err and why in err and "line 2" in err, err
        assert db.read_text() == good + bad and not (secrets / "principals/svc.catalog.password").exists()
        code, out, err = run(secrets, "principals-status")
        assert code != 0 and f"MALFORMED trino/password.db line 2: {why}" in out, out
        assert "zz-pasted" not in out + err and "A" * 20 not in out + err and "B" * 20 not in out + err
    # A user's bad line is repaired by removing that user; a nameless one only by hand.
    db.write_text(good + "svc.api:$2y$04$" + "A" * 53 + "\n")
    code, _, err = run(secrets, "principal-remove", "PRINCIPAL=svc.api", "FORCE=1")
    assert code == 0, err
    assert [x.split(":")[0] for x in db.read_text().splitlines() if x.strip()] == ["svc.runs"]
    assert run(secrets, "password", "PRINCIPAL=svc.catalog", "TO_FILE=1")[0] == 0


def test_write_refuses_a_new_text_that_would_not_parse_and_keeps_the_file() -> None:
    out = module("""
import tempfile
from pathlib import Path
d = Path(tempfile.mkdtemp())
db = d / 'trino/password.db'
good = 'svc.runs:$2y$10$' + 'A' * 53 + '\\n'
with ps.locked(db) as fd:
    ps.write(fd, good)
for text in ['svc.runs:$2y$10$' + 'A' * 53 + '\\nsvc.runs:$2y$10$' + 'B' * 53 + '\\n', '# comment\\n' + good,
             'x' * 70 + ':$2y$10$' + 'A' * 53 + '\\n', good + 'svc.api:plain\\n']:
    try:
        with ps.locked(db) as fd:
            ps.write(fd, text)
    except SystemExit as e:
        assert 'would not parse' in str(e), e
    else:
        raise AssertionError(text)
    assert ps.entries(db.read_text()) == {'svc.runs': '$2y$10$' + 'A' * 53}
print('ok')
""")
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr


def test_a_test_principal_gets_a_password_file_and_a_person_with_a_real_grant_never(secrets: Path) -> None:
    code, out, err = run(secrets, "password", "PRINCIPAL=p.canary-full", "TO_FILE=1")
    assert code == 0 and out == "", err
    stored = secrets / "principals/p.canary-full.password"
    assert SECRET.fullmatch(stored.read_text()) and mode(stored) == 0o600
    assert bcrypt_checks([(stored.read_text(), lines(secrets)["p.canary-full"])]) == [True]
    code, out, _ = run(secrets, "principals-status")
    assert re.search(r"^p\.canary-full +yes +yes +no$", out, re.M), out
    # Who is a test principal: granted on the access canary and on no other product.
    decided = module("""
sys.path.insert(0, 'scripts')
from types import SimpleNamespace as N
import access_verify as av
g = lambda *who: [N(principal=w) for w in who]
m = N(products=[N(id=av.CANARY_PRODUCT, grants=g('p.canary-only', 'p.mixed', 'svc.x')),
                N(id='market.trades', grants=g('p.mixed', 'p.real'))])
held = av.platform_held(m)
print(sorted(held))
import tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
for who in ('p.mixed', 'p.real'):
    try:
        ps.password(root, who, False, True, frozenset(held))
    except SystemExit as e:
        print(who, 'refused' if 'printed, never stored' in str(e) else e)
ps.password(root, 'p.canary-only', False, True, frozenset(held))
print(sorted(p.name for p in (root / 'principals').iterdir()))
""")
    assert decided.returncode == 0, decided.stderr
    assert decided.stdout.splitlines() == ["['p.canary-only', 'svc.x']", "p.mixed refused", "p.real refused",
                                           "['p.canary-only.password']"], decided.stdout


def test_a_checkout_git_cannot_read_is_still_refused(tmp_path: Path) -> None:
    # A .git that git cannot use (here a worktree file pointing nowhere; another uid's checkout,
    # "dubious ownership", is the same to git): it answers nothing, which once read as "outside".
    fake = tmp_path / "checkout"
    fake.mkdir()
    (fake / ".git").write_text("gitdir: /nonexistent/worktree\n")
    code, _, err = run(fake / "k2-secrets", "password", "PRINCIPAL=svc.runs", "TO_FILE=1")
    assert code != 0 and "inside a git checkout" in err and not (fake / "k2-secrets").exists()


def test_every_run_puts_the_directories_back_to_700(secrets: Path) -> None:
    assert run(secrets, "password", "PRINCIPAL=svc.runs", "TO_FILE=1")[0] == 0
    (secrets / "api").mkdir(mode=0o755)
    for d in (secrets, secrets / "trino", secrets / "principals", secrets / "api"):
        d.chmod(0o755)
    assert run(secrets, "principals-status")[0] == 0
    assert [mode(d) for d in (secrets, secrets / "trino", secrets / "principals", secrets / "api")] == [0o700] * 4
