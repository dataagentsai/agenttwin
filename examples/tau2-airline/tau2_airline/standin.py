"""The stand-in: τ²'s airline tools, served over MCP, judged by AgentTwin.

The shared stand-in (`tau2_common.standin`) with the airline's view: τ²'s
database as the AOAS's rows (`convert.rows`). Its docstring says what is τ²'s
(the surface and the behaviour) and what is AgentTwin's (the view).

The policy says of its system: "The API does not check these for the agent".
So the stand-in refuses only what τ²'s code refuses; the spec's preconditions
are judged afterwards, by the monitor's `WithinSpec`, on the row as the call
found it.
"""

from __future__ import annotations

from typing import Any

from agenttwin import Live
from tau2_airline.convert import SYSTEM_ENTITIES, rows
from tau2_common import standin as common
from tau2_common.standin import DESK


def view(env: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    return rows(env.tools.db, env.tools.list_all_airports())


class Standin(common.Standin):
    """One run's stand-in: a τ² airline environment, and the `Live` view of it."""

    def __init__(self, live: Live, env: Any) -> None:
        super().__init__(live, env, view, "tau2-airline")


__all__ = ["DESK", "SYSTEM_ENTITIES", "Standin", "view"]
