"""What every domain's mutations share: a variant, the gold trajectory, and
where an added step goes.

`what` says what is wrong in a variant, and so what a judge should say:

| what | wrong because |
|---|---|
| policy | the write breaks a rule of the domain's policy |
| goal | nothing breaks a rule; the customer's request is left undone |
| privacy | another customer's record is read; nothing changes |
| none | nothing wrong lands (the system refused, or a repeat changed nothing) |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tau2_common.judge import Step


@dataclass(frozen=True)
class Variant:
    name: str
    what: str
    steps: list[Step]
    note: str = ""


def gold(task: Any) -> list[Step]:
    """The task's expected actions — the agent's calls, and the customer's own
    tool calls where τ² expects the customer to act — and, where τ² requires
    something said, one closing reply that says it (synthesised: the expected
    actions carry no words)."""
    criteria = task.evaluation_criteria
    steps: list[Step] = [
        ("user_call" if a.requestor == "user" else "call", a.name, dict(a.arguments))
        for a in criteria.actions or []
    ]
    if criteria.communicate_info:
        steps.append(("say", "", " ".join(criteria.communicate_info)))
    return steps


def with_close(steps: list[Step], extra: list[Step]) -> list[Step]:
    """`extra` before the closing reply, so what is said still comes last."""
    if steps and steps[-1][0] == "say":
        return [*steps[:-1], *extra, steps[-1]]
    return [*steps, *extra]


def first(rows: list[dict], **want: Any) -> dict | None:
    """The first row whose fields match: a value, or a predicate on the value."""
    for r in rows:
        if all((v(r.get(k)) if callable(v) else r.get(k) == v) for k, v in want.items()):
            return r
    return None


__all__ = ["Variant", "first", "gold", "with_close"]
