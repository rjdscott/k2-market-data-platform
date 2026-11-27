"""scripts/hold.py: make hold and make unhold, with the run-records client and Airflow faked; and
the holds' writer, svc.operator, whose password file no service or one-shot is given (review A4)."""
import ast
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "platform" / "runs")]
import hold  # noqa: E402
import k2runs  # noqa: E402
from test_secret_mounts import source  # noqa: E402

DAGS = [{"product": "market-capture.kraken-trades", "dagId": "kraken_trades"},
        {"product": "sensor-net.station-readings", "dagId": "station_readings"}]
P = "market-capture.kraken-trades"


class Fake:
    """Stands in for k2runs: `open` is the list of open hold kinds; `log` the order of calls."""
    def __init__(self, open_kinds=()):
        self.open, self.log = list(open_kinds), []

    def install(self, mp):
        mp.setattr(k2runs, "held", lambda c, p: ({"kind": self.open[-1], "held_by": "x", "held_at": "t", "reason": "r"}
                                                  if self.open else None))
        mp.setattr(k2runs, "hold", lambda c, p, r, by, **k: (self.log.append("row"), self.open.append("operator")))

        def release(c, p, by, **k):
            if "operator" not in self.open:
                raise k2runs.HoldError("none")
            self.open.remove("operator")
            self.log.append("release")
        mp.setattr(k2runs, "release", release)
        return self




def run_hold(f, mp, pause):
    f.install(mp)
    return hold.do_hold(None, P, "kraken_trades", "drill", "rob", pause)


def test_resolve_forms_and_unknown():
    for a in ("market-capture/kraken-trades", "market-capture.kraken-trades", "kraken-trades"):
        assert hold.resolve(a, DAGS) == (P, "kraken_trades")
    for a in ("", "nope", "market-capture"):
        with pytest.raises(hold.Refused):
            hold.resolve(a, DAGS)


def test_reason_rules():
    assert hold.check_reason(" drill ") == "drill"
    for r in ("", "   ", "x" * 201):
        with pytest.raises(hold.Refused):
            hold.check_reason(r)
    assert hold.check_reason("x" * 200)


def test_hold_row_then_pause(monkeypatch):
    f = Fake()
    assert run_hold(f, monkeypatch, lambda d: f.log.append(f"pause {d}")) == 0
    assert f.log == ["row", "pause kraken_trades"]


def test_hold_refused_when_open_and_names_it(monkeypatch):
    f = Fake(["operator"])
    paused = []
    with pytest.raises(hold.Refused, match="held already by x"):
        run_hold(f, monkeypatch, paused.append)
    assert f.log == [] and paused == []


def test_pause_failure_keeps_hold_and_exits_nonzero(monkeypatch, capsys):
    f = Fake()

    def boom(d):
        raise RuntimeError("no scheduler")
    assert run_hold(f, monkeypatch, boom) == 1
    assert f.open == ["operator"] and "NOT paused" in capsys.readouterr().err


def test_unhold_without_hold_refused(monkeypatch):
    f = Fake().install(monkeypatch)
    with pytest.raises(hold.Refused):
        hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause"))
    assert f.log == []


def test_unhold_order_and_unpause(monkeypatch):
    f = Fake(["operator"]).install(monkeypatch)
    assert hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause")) == 0
    assert f.log == ["release", "unpause"]


def test_unhold_with_restore_open_leaves_dag_paused(monkeypatch, capsys):
    f = Fake(["operator", "restore"]).install(monkeypatch)
    assert hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause")) == 0
    assert f.log == ["release"] and f.open == ["restore"] and "restore hold is still open" in capsys.readouterr().out


def test_unhold_never_releases_rebuild_or_restore(monkeypatch):
    f = Fake(["rebuild"]).install(monkeypatch)
    with pytest.raises(hold.Refused):
        hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: None)
    assert f.open == ["rebuild"]


def test_no_secret_in_any_argv(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(hold.subprocess, "run", lambda argv, **k: calls.append(argv) or type("D", (), {"returncode": 0})())
    hold.airflow("pause", "kraken_trades")
    hold.airflow("unpause", "kraken_trades")
    assert calls and all("password" not in " ".join(a) and "secret" not in " ".join(a) for a in calls)
    assert calls[0] == ["docker", "exec", "k2-airflow-scheduler", "airflow", "dags", "pause", "kraken_trades", "-y"]


def test_live_dags_reads_airflow_pointer(tmp_path):
    rel = tmp_path / "rels" / "R1" / "compiled" / "airflow"
    rel.mkdir(parents=True)
    (rel / "dags.json").write_text(json.dumps({"dags": DAGS}))
    (tmp_path / "rels" / "pointers").mkdir()
    (tmp_path / "rels" / "pointers" / "airflow").symlink_to("../R1")
    assert hold.live_dags(tmp_path / "rels") == DAGS
    with pytest.raises(hold.Refused):
        hold.live_dags(tmp_path / "none")


def releases(tmp_path):
    rel = tmp_path / "rels" / "R1" / "compiled" / "airflow"
    rel.mkdir(parents=True)
    (rel / "dags.json").write_text(json.dumps({"dags": DAGS}))
    (tmp_path / "rels" / "pointers").mkdir()
    (tmp_path / "rels" / "pointers" / "airflow").symlink_to("../R1")
    return tmp_path / "rels"


def test_hold_connects_as_svc_operator_and_refuses_without_its_password_file(monkeypatch, tmp_path, capsys):
    secrets = tmp_path / "secrets"
    monkeypatch.setenv("K2_RELEASES", str(releases(tmp_path)))
    monkeypatch.setenv("K2_SECRETS", str(secrets))
    calls = []
    monkeypatch.setattr(k2runs, "connect", lambda **k: calls.append(k) or "conn")
    monkeypatch.setattr(hold, "do_hold", lambda conn, *a: calls.append(conn) or 0)
    # no file: refused before any connection, naming the one command that makes it
    assert hold.main(["hold", P, "drill"]) == 2
    err = capsys.readouterr().err
    assert "make password PRINCIPAL=svc.operator TO_FILE=1" in err and calls == []
    # svc.runs's file, which every dbt-runner one-shot and the scheduler hold, is never used instead
    (secrets / "principals").mkdir(parents=True)
    (secrets / "principals" / "svc.runs.password").write_text("x")
    assert hold.main(["hold", P, "drill"]) == 2 and calls == []
    (secrets / "principals" / "svc.operator.password").write_text("x")
    assert hold.main(["hold", P, "drill"]) == 0
    assert calls[0]["user"] == "svc.operator" and calls[0]["password_file"] == str(secrets / "principals" / "svc.operator.password")
    assert calls[1] == "conn"


def test_the_writer_is_the_models_holds_writer():
    tree = ast.parse((ROOT / "platform/k2gen/k2gen/model.py").read_text())
    found = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "HOLDS_WRITER"}
    assert found == {"HOLDS_WRITER": "svc.operator"} and hold.WRITER == "svc.operator"


def oneshot_secrets() -> set[str]:
    """Every secret path oneshot.ALLOWED lets any one-shot kind mount, its assignments run alone
    (the module imports Airflow, which these tests do not have)."""
    ns: dict = {}
    for n in ast.parse((ROOT / "docker/airflow/dags/oneshot.py").read_text()).body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("SECRETS_MOUNT", "CA", "ALLOWED"):
            exec(compile(ast.Module([n], []), "oneshot.py", "exec"), ns)
    return {s for kind in ns["ALLOWED"].values() for s in kind["secrets"]}


def test_no_service_or_one_shot_is_given_the_holds_writers_password():
    # The compose file: no service mounts svc.operator's file, nor the principals directory whole.
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name, svc in compose["services"].items():
        for v in svc.get("volumes") or []:
            src = source(v)
            assert "svc.operator" not in src and not src.rstrip("/").endswith("/principals"), (name, src)
    # A DAG's one-shot: svc.operator's mount (k2gen.model.password_mount) is in no kind's allow-list,
    # so oneshot.py refuses it as a principal of any run.
    mounts = oneshot_secrets()
    assert mounts and "/run/k2-secrets/runs.password" in mounts and "/run/k2-secrets/operator.password" not in mounts
    assert not [m for m in mounts if "operator" in m]
