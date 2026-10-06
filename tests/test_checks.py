"""The `reply` check, table-driven: one phrase, or a list meaning any of them.

A list exists because the gates judged generation run 2 on phrases only the
reference agent uses (*"Nobody has picked up"* against *"Nobody from the
support desk picked up"*): the AOAS says what the customer must be told, and a
list of ways to say it tests that without testing one implementation's taste.
"""

from __future__ import annotations

import pytest

from agenttwin.checks import Check

# [name, kind, text, reply, passes]
REPLIES = [
    ("one phrase, said", "says", "nobody", "Nobody came.", True),
    ("one phrase, case-blind", "says", "NOBODY", "nobody came", True),
    ("one phrase, not said", "says", "nobody", "A colleague has it.", False),
    ("any of a list, said", "says", ["nobody", "no one"], "No one picked it up.", True),
    ("none of a list said", "says", ["nobody", "no one"], "A colleague has it.", False),
    ("never says one, and it is not said", "never_says", "voucher", "No discounts.", True),
    ("never says one, and it is said", "never_says", "voucher", "Here is a voucher.", False),
    ("never says any of a list, one is said", "never_says", ["%", "code"], "Use code X.", False),
    ("never says any of a list, none is said", "never_says", ["%", "code"], "No discounts.", True),
]


@pytest.mark.parametrize(("name", "kind", "text", "reply", "passes"), REPLIES, ids=[r[0] for r in REPLIES])
def test_a_reply_check(name: str, kind: str, text: str | list[str], reply: str, passes: bool) -> None:
    check = Check.model_validate({"reply": kind, "text": text})
    assert check.evaluate(None, {}, reply).passed is passes  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [None, "", []], ids=["absent", "empty", "an empty list"])
def test_a_reply_check_needs_something_to_look_for(text: object) -> None:
    with pytest.raises(ValueError, match="needs the text"):
        Check.model_validate({"reply": "says", "text": text})


# [name, check, calls made, passes] — `at_least` is a floor; `times` an exact count
CALLS = [
    ("no count: at least once, and it was", {"called": "get_order"}, 1, True),
    ("no count: at least once, and it was not", {"called": "get_order"}, 0, False),
    ("an exact count, met", {"called": "get_order", "times": 3}, 3, True),
    ("an exact count, one short", {"called": "get_order", "times": 3}, 2, False),
    ("an exact count, one over", {"called": "get_order", "times": 3}, 4, False),
    ("a floor, met exactly", {"called": "get_order", "at_least": 2}, 2, True),
    ("a floor, passed", {"called": "get_order", "at_least": 2}, 3, True),
    ("a floor, one short", {"called": "get_order", "at_least": 2}, 1, False),
]


@pytest.mark.parametrize(("name", "spec", "made", "passes"), CALLS, ids=[c[0] for c in CALLS])
def test_a_called_check(name: str, spec: dict, made: int, passes: bool) -> None:
    check = Check.model_validate(spec)
    assert check.evaluate(None, {}, "", {"get_order": made}).passed is passes  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "spec",
    [{"called": "get_order", "times": 2, "at_least": 2}, {"effect": "issue_refund", "at_least": 1}],
    ids=["with times", "on an effect"],
)
def test_at_least_belongs_on_a_called_check_alone(spec: dict) -> None:
    with pytest.raises(ValueError, match="at_least goes on a called check"):
        Check.model_validate(spec)


class _Handled:
    def __init__(self, escalation_id: str, outcome: str) -> None:
        self.escalation_id, self.outcome = escalation_id, outcome


# [name, what the desk recorded, handed_off wanted, passes] — escalations, not looks
HANDOFFS = [
    ("none", [], 0, True),
    ("one, picked up at once", [_Handled("E-1", "resolved")], 1, True),
    ("one, waited on and then picked up", [_Handled("E-1", "waiting"), _Handled("E-1", "resolved")], 1, True),
    ("two", [_Handled("E-1", "resolved"), _Handled("E-2", "resolved")], 2, True),
    ("two looks at one is not two", [_Handled("E-1", "waiting"), _Handled("E-1", "resolved")], 2, False),
]


@pytest.mark.parametrize(("name", "handed", "want", "passes"), HANDOFFS, ids=[h[0] for h in HANDOFFS])
def test_handed_off_counts_escalations(name: str, handed: list, want: int, passes: bool) -> None:
    check = Check.model_validate({"handed_off": want})
    outcome = check.evaluate(None, {}, "", {}, {"handed": tuple(handed)})  # type: ignore[arg-type]
    assert outcome.passed is passes


class _Carried:
    def __init__(self, escalation_id: str, context: str) -> None:
        self.escalation_id, self.outcome, self.context = escalation_id, "waiting", context


HANDED = (_Carried("E-1", "They raised 3 things: hinge on AB-10003; charged twice; warranty?"),)

# [name, what must be mentioned, what the desk was handed, passes] — every one, not any
MENTIONS = [
    ("every concern carried", ["hinge", "charged twice", "warranty"], HANDED, True),
    ("one alone, carried", "charged twice", HANDED, True),
    ("case does not matter", ["HINGE"], HANDED, True),
    ("one of three missing", ["hinge", "refund", "warranty"], HANDED, False),
    ("nothing handed", ["hinge"], (), False),
]


@pytest.mark.parametrize(("name", "wanted", "handed", "passes"), MENTIONS, ids=[m[0] for m in MENTIONS])
def test_what_a_person_was_handed(name: str, wanted, handed, passes: bool) -> None:
    check = Check.model_validate({"handoff_mentions": wanted})
    outcome = check.evaluate(None, {}, "", {}, {"handed": handed})  # type: ignore[arg-type]
    assert outcome.passed is passes
