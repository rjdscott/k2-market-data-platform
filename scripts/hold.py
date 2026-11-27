#!/usr/bin/env python3
"""make hold / make unhold: an operator hold on one product (plan 004, step W1-11).

  make hold PRODUCT=<domain/name | domain.name | name> REASON="<text>"
  make unhold PRODUCT=<...>

Runs on the host alone: over TLS on localhost:${K2_TRINO_PORT:-8443} as svc.operator, the holds'
one writer (k2gen.model.HOLDS_WRITER: INSERT on the holds and nothing else, its reads through the
read-only reader catalog), its password read from ${K2_SECRETS:-~/k2-secrets}/principals/svc.operator.password
by k2runs.connect, never printed and never an argument. The operator makes that file once, with
`make password PRINCIPAL=svc.operator TO_FILE=1`; no service or one-shot mounts it (tests/test_hold.py),
so nothing holding svc.runs's password, which every dbt-runner one-shot and the scheduler hold, can
write a hold. Without it the command refuses, naming that make line. The DAG is paused and unpaused
with `docker exec k2-airflow-scheduler airflow dags pause|unpause <dag> -y`.

The hold row is written FIRST and is the truth: a held product's DAG refuses to run in its resolve
task anyway, so a failed pause leaves the hold and exits 1. An unhold writes the release row, then
unpauses the DAG only when no other hold (rebuild, restore) is still open. It never touches those.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "platform" / "runs"))
import k2runs  # noqa: E402

SCHEDULER = "k2-airflow-scheduler"
MAX_REASON = 200
WRITER = "svc.operator"  # k2gen.model.HOLDS_WRITER; the rules name it alone as the holds' writer


class Refused(Exception):
    """An argument or state the command refuses; exit 2."""


def resolve(arg: str, dags: list[dict[str, Any]]) -> tuple[str, str]:
    """(product id, DAG id) of `arg` in the release's dags.json entries: `domain/name`, `domain.name`
    or a bare name that exactly one product has."""
    arg = arg.strip().replace("/", ".")
    if not arg:
        raise Refused("PRODUCT is empty")
    hits = [d for d in dags if d["product"] == arg or d["product"].split(".", 1)[-1] == arg]
    if not hits:
        raise Refused(f"{arg} is not a product of the live release ({', '.join(sorted(d['product'] for d in dags))})")
    if len(hits) > 1:
        raise Refused(f"{arg} is ambiguous: {', '.join(sorted(d['product'] for d in hits))}")
    return hits[0]["product"], hits[0]["dagId"]


def check_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise Refused("REASON is empty: a hold carries its reason")
    if len(reason) > MAX_REASON:
        raise Refused(f"REASON is {len(reason)} characters; at most {MAX_REASON}")
    return reason


def do_hold(conn: Any, product: str, dag: str, reason: str, by: str, pause: Callable[[str], None]) -> int:
    open_ = k2runs.held(conn, product)
    if open_ and open_["kind"] == "operator":
        raise Refused(f"{product} is held already by {open_['held_by']} at {open_['held_at']}: {open_['reason']}")
    k2runs.hold(conn, product, reason, by)  # the row first
    try:
        pause(dag)
    except Exception as e:
        print(f"hold: LOUD FAILURE: {product} is held, but DAG {dag} was NOT paused ({e}); its resolve task "
              f"refuses every run, and `make unhold PRODUCT={product}` ends the hold", file=sys.stderr)
        return 1
    print(f"held {product} (DAG {dag} paused): no deploy will unpause it; `make unhold PRODUCT={product}` ends it")
    return 0


def do_unhold(conn: Any, product: str, dag: str, by: str, unpause: Callable[[str], None]) -> int:
    open_ = k2runs.held(conn, product)
    if not open_ or open_["kind"] != "operator":
        # held() names the newest open hold of any kind; release() refuses when no operator one is open
        try:
            k2runs.release(conn, product, by)
        except k2runs.HoldError:
            raise Refused(f"{product} has no operator hold open") from None
    else:
        k2runs.release(conn, product, by)
    other = k2runs.held(conn, product)
    if other:
        print(f"released the operator hold on {product}; DAG {dag} stays paused: a {other['kind']} hold is still open")
        return 0
    try:
        unpause(dag)
    except Exception as e:
        print(f"unhold: {product} is released, but DAG {dag} was NOT unpaused ({e}); "
              f"`docker exec {SCHEDULER} airflow dags unpause {dag} -y`", file=sys.stderr)
        return 1
    print(f"released {product} (DAG {dag} unpaused)")
    return 0


def password_file(secrets: Path) -> Path:
    """The writer's password file (k2gen.model.password_file), or a refusal naming how to make it."""
    path = secrets / "principals" / f"{WRITER}.password"
    if not path.is_file():
        raise Refused(f"no password file for {WRITER} at {path}: the operator makes it once with "
                      f"`make password PRINCIPAL={WRITER} TO_FILE=1` (host-held: no container mounts it)")
    return path


def airflow_argv(verb: str, dag: str) -> list[str]:
    return ["docker", "exec", SCHEDULER, "airflow", "dags", verb, dag, "-y"]


def airflow(verb: str, dag: str) -> None:
    done = subprocess.run(airflow_argv(verb, dag), capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"airflow dags {verb} exit {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}")


def live_dags(releases: Path) -> list[dict[str, Any]]:
    pointer = releases / "pointers" / "airflow"
    path = pointer.resolve() / "compiled" / "airflow" / "dags.json"
    if not path.is_file():
        raise Refused(f"no live release at {pointer} (make deploy points it at one)")
    return list(json.loads(path.read_text())["dags"])


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("hold", "unhold"):
        print("usage: hold.py hold <product> <reason> | unhold <product>", file=sys.stderr)
        return 2
    verb, arg = argv[0], argv[1]
    env = os.environ
    try:
        reason = check_reason(argv[2] if len(argv) > 2 else "") if verb == "hold" else ""
        product, dag = resolve(arg, live_dags(Path(env.get("K2_RELEASES") or Path.home() / "k2-releases")))
        secrets = Path(env.get("K2_SECRETS") or Path.home() / "k2-secrets")
        conn = k2runs.connect(host="localhost", port=int(env.get("K2_TRINO_PORT", "8443")), user=WRITER,
                              password_file=str(password_file(secrets)), ca=str(secrets / "tls" / "ca.pem"))
        by = getpass.getuser()
        if verb == "hold":
            return do_hold(conn, product, dag, reason, by, lambda d: airflow("pause", d))
        return do_unhold(conn, product, dag, by, lambda d: airflow("unpause", d))
    except (Refused, k2runs.HoldError) as e:
        print(f"{verb}: REFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"{verb}: FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
