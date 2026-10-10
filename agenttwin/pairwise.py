"""The adapter for **allpairspy**: a pairwise-covering set of combinations.

**Outside the core.** `agenttwin.coverage` decides what the dimensions are,
which combinations are possible and what counts as covered; it takes the
covering function as an argument (`Covering`) and never imports this module.
This file is the default binding for that seam, the way a stack file binds a
harness port, and the only place `allpairspy` is named (ADOPTION.md, *The one
constraint*).

Install with the `pairwise` extra: `uv add 'agenttwin[pairwise]'`. Without it,
`python -m agenttwin scaffold --pairwise` says so and stops; `coverage`, which
only counts, does not need it.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Allowed = Callable[[Mapping[str, Any]], bool]
"""`(partial combination) -> bool`: may these values sit in one scenario."""


def allpairs(dimensions: Mapping[str, Sequence[Any]], allowed: Allowed) -> list[dict[str, Any]]:
    """Every pair of values from two different dimensions, in at least one row,
    with no row `allowed` refuses. The covering function `coverage` expects."""
    try:
        from allpairspy import AllPairs
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "pairwise generation needs allpairspy: install agenttwin with the `pairwise` extra"
        ) from exc

    names = list(dimensions)
    if len(names) < 2:
        return [{names[0]: v} for v in dimensions[names[0]]] if names else []

    def keep(values: list[Any]) -> bool:
        return allowed(dict(zip(names, values, strict=False)))

    rows = AllPairs(OrderedDict((n, list(dimensions[n])) for n in names), filter_func=keep)
    return [dict(zip(names, row, strict=True)) for row in rows]


__all__ = ["Allowed", "allpairs"]
