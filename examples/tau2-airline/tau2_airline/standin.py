"""The stand-in: τ²'s airline tools, served over MCP, judged by AgentTwin.

Three parts, each kept to what it is for:

- **The surface** is τ²'s own: every tool's name, signature and docstring are
  read from τ²'s tool set, so the agent under test is offered exactly the tools
  τ² offers it. Answers are τ²'s text, byte for byte, errors included
  (`Error: <reason>`, as τ²'s environment words them).
- **The behaviour** is τ²'s own tool code against τ²'s own database — the
  benchmark's system of record, adopted rather than rebuilt (`resolution:
  real`). AgentTwin's declarative projection cannot serve these tools: they
  take lists of objects (flights, passengers, payments) and compute their
  effects (fares, balances, refunds), and its tools take scalars and set
  declared values (finding S-11).
- **The view** is AgentTwin's: after every write the database is converted to
  the rows the AOAS describes (`convert.rows`) and put in `Live`, and the
  write is entered in `Live.effects`. AgentTwin's monitor and oracles read only
  that view, so they judge what the spec says, never τ²'s data model.

The policy says of its system: "The API does not check these for the agent".
So the stand-in refuses only what τ²'s code refuses; the spec's preconditions
are judged afterwards, by the monitor's `WithinSpec`, on the row as the call
found it.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from agenttwin import Live
from tau2_airline.convert import SYSTEM_ENTITIES, rows

DESK = "transfer_to_human_agents"
"""A write in the spec (it hands the customer to a person) that τ²'s database
does not record; the stand-in keeps the desk's row itself."""


def _plain(value: Any) -> Any:
    """What a model would have sent: objects as dicts, as JSON carries them."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


class Standin:
    """One run's stand-in: a τ² environment, and the `Live` view of it."""

    def __init__(self, live: Live, env: Any) -> None:
        self.live = live
        self.env = env
        self.airports = env.tools.list_all_airports()
        self.writes = {
            name
            for name in env.tools.get_tools()
            if env.tools.tool_mutates_state(name) or name == DESK
        }

    # -- the view

    def sync(self) -> None:
        """The database, as the spec's rows. The desk's rows are kept."""
        for entity, found in rows(self.env.tools.db, self.airports).items():
            key = self.live.world.entities[entity].key
            self.live.rows[entity] = {str(r[key]): r for r in found}

    def _landed(self, name: str, arguments: dict[str, Any], answer: Any) -> None:
        if name == DESK:
            desk = self.live.rows.setdefault("transfer", {})
            key = f"TR-{len(desk) + 1}"
            desk[key] = {"id": key, "summary": arguments.get("summary", "")}
        else:
            self.sync()
        entity = self.live.world.action(name)
        key_field = self.live.world.entities[entity[1].entity].key if entity else None
        key = arguments.get(key_field) if key_field else None
        if key is None and isinstance(answer, dict):  # a booking: the row it made
            key = answer.get("reservation_id")
        self.live.effects.append((name, str(key)))

    # -- the surface

    def server(self, wrap: Callable[[str, Callable], Callable] | None = None) -> MCPServer:
        srv = MCPServer("tau2-airline")
        for name in self.env.tools.get_tools():
            method = getattr(self.env.tools, name)
            inner = self._handler(name, method)
            judged = wrap(name, inner) if wrap is not None else inner
            srv.tool(name=name, description=inspect.getdoc(method), structured_output=False)(
                _wire(judged)
            )
        return srv

    def _handler(self, name: str, method: Callable) -> Callable:
        async def handler(ctx: Context | None = None, **arguments: Any) -> Any:
            sent = {k: _plain(v) for k, v in arguments.items()}
            response = self.env.make_tool_call(name, **sent)  # τ²'s code; raises to refuse
            self.env.sync_tools()
            text = self.env.to_json_str(response)
            answer = text if isinstance(response, str) else json.loads(text)
            if name in self.writes:
                self._landed(name, sent, answer)
            return answer

        signature = inspect.signature(method)
        handler.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=[
                *signature.parameters.values(),
                inspect.Parameter(
                    "ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context, default=None
                ),
            ],
            return_annotation=Any,
        )
        handler.__annotations__ = {
            **{p.name: p.annotation for p in signature.parameters.values()},
            "ctx": Context,
            "return": Any,
        }
        handler.__name__ = name
        handler.__doc__ = inspect.getdoc(method)
        return handler


def _wire(handler: Callable) -> Callable:
    """τ²'s words on the wire: the answer as τ²'s environment serialises it, and
    a refusal as `Error: <reason>`, marked as an error."""

    async def wired(**arguments: Any) -> CallToolResult:
        try:
            answer = await handler(**arguments)
        except Exception as e:  # τ²'s tools refuse by raising, and say why
            return CallToolResult(content=[TextContent(type="text", text=f"Error: {e}")], is_error=True)
        text = answer if isinstance(answer, str) else json.dumps(answer, default=str)
        return CallToolResult(content=[TextContent(type="text", text=text)])

    wired.__signature__ = handler.__signature__  # type: ignore[attr-defined]
    wired.__annotations__ = handler.__annotations__
    wired.__name__ = handler.__name__
    wired.__doc__ = handler.__doc__
    return wired


__all__ = ["DESK", "SYSTEM_ENTITIES", "Standin"]
