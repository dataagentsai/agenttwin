"""How each fault answers, call by call — table-driven.

A decline is a refusal for good: the same typed answer on its call and every
call after, which is what makes "retried five times, try again tomorrow"
visible (T-095). A channel error is one failure that may clear. Both say what
they are as a field, `kind`, so a caller never infers it from the words
(AHC-0043).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from agenttwin.perturbation import ChannelError, Decline, Timeline, perturbed


async def _issue(**arguments):
    return {"allowed": True, "reason": "allowed", "id": arguments.get("id")}


_issue.__signature__ = inspect.signature(_issue)  # type: ignore[attr-defined]  # as the projection's handlers carry


def answers(fault, calls: int) -> list[str]:
    """What the caller got on each call: the fault's `kind`, or `ok`."""
    wrapped = perturbed(None, Timeline(fault))("issue_refund", _issue)  # type: ignore[arg-type]

    async def run() -> list[str]:
        got = []
        for _ in range(calls):
            result = await wrapped(id="AB-1")
            got.append(result.get("kind", "ok") if not result["allowed"] else "ok")
        return got

    return asyncio.run(run())


# [name, fault, calls made, what each call answered]
CASES = [
    ("a decline refuses its call and every one after", Decline(tool="issue_refund"), 3,
     ["declined", "declined", "declined"]),
    ("a decline from the second call lets the first through", Decline(tool="issue_refund", on_call=2), 3,
     ["ok", "declined", "declined"]),
    ("a decline on another tool touches nothing here", Decline(tool="cancel_order"), 2,
     ["ok", "ok"]),
    ("a channel error fails once and clears", ChannelError(tool="issue_refund"), 3,
     ["transient", "ok", "ok"]),
]  # fmt: skip


@pytest.mark.parametrize(("name", "fault", "calls", "expected"), CASES, ids=[c[0] for c in CASES])
def test_how_a_fault_answers_each_call(name: str, fault, calls: int, expected: list[str]) -> None:
    assert answers(fault, calls) == expected
