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
