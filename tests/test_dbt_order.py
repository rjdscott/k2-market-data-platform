"""scripts/dbt_order.py: the full order and one selector's closure, from fixture pipeline documents."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dbt_order import order  # noqa: E402


def p(name, *inputs):
    # a product is "<domain>.<name>"; an input port's contractId is "<product>.<table>"
    return {"product": f"dom.{name}", "build": {"selector": name}, "inputs": [{"contractId": f"dom.{i}.t"} for i in inputs]}


PIPES = [p("c", "b"), p("b", "a"), p("a"), p("x"), p("d", "a", "elsewhere")]


def test_full_order_puts_inputs_first():
    o = order(PIPES)
    assert sorted(o) == ["a", "b", "c", "d", "x"]
    assert o.index("a") < o.index("b") < o.index("c") and o.index("a") < o.index("d")


def test_closure_is_transitive_and_nothing_else():
    assert order(PIPES, "c") == ["a", "b", "c"]
    assert order(PIPES, "x") == ["x"]
    assert order(PIPES, "d") == ["a", "d"]


def test_unknown_selector_lists_the_valid_ones():
    with pytest.raises(KeyError) as e:
        order(PIPES, "nope")
    assert e.value.args[0] == ["a", "b", "c", "d", "x"]
