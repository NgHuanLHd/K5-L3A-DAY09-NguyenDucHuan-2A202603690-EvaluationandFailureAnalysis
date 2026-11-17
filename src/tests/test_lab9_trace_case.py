"""Regression test for docs/trace_case.md: seeded_regression C-002@11 (FM-03, ticket over DB).

First wrong step: event #3 `model_override {"delay_days": 5}` -- the ticket's "5 days" replaced the
DB's 2 days, which turned a denial into a 20.000đ refund. The same claim@seed on `fixed` must keep the
DB fact and refund nothing. Run: python -m pytest -q -m student -o addopts= -k trace_case
"""
import pytest

pytestmark = pytest.mark.student


def _ctx(case):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="trace_case", inputs={}, metadata=None, expected_output=case["label"],
                            output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})


@pytest.fixture(scope="module")
def cases():
    from lab9.trace_case import investigate

    return {shape: investigate(shape, "C-002", 11) for shape in ("seeded_regression", "fixed")}


def test_first_wrong_step_is_the_override(cases):
    events = cases["seeded_regression"]["trial"]["events"]
    kinds = [e.get("kind") for e in events]
    first = kinds.index("model_override")
    assert first == 2, kinds                          # event #3 (1-based), right after the DB lookup
    assert kinds[:first] == ["handoff", "investigator"]
    assert events[first]["payload"] == {"delay_days": 5}
    db_delay = events[1]["payload"]["lookup_order"]["order"]["delay_days"]
    assert db_delay == 2                              # the investigator read the right fact


def test_graders_catch_the_regression(cases):
    from lab9 import graders

    bad = _ctx(cases["seeded_regression"])
    assert graders.DbFactKept().evaluate(bad).value is False
    assert graders.RefundAmountCorrect().evaluate(bad).value is False
    assert cases["seeded_regression"]["trial"]["snapshot"]["refund_vnd"] == 20000


def test_fixed_keeps_the_db_fact(cases):
    from lab9 import graders

    ok = _ctx(cases["fixed"])
    assert graders.DbFactKept().evaluate(ok).value is True
    assert graders.RefundAmountCorrect().evaluate(ok).value is True
    assert graders.ReplyMatchesLedger().evaluate(ok).value is True
    assert cases["fixed"]["trial"]["result"]["status"] == "denied"
