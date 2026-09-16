"""World state → MCP tool responses.

**This is the component nobody has written.** Mock servers exist; synthetic data
generators exist; fault injectors exist. What does not exist is the thing that
takes one declared world and *projects* it into several tool surfaces, so joins
hold by construction rather than by reconciliation.

The OpenUSD move: one scene, many render delegates. A world is described once;
the projection is the delegate that renders it as MCP.

## Why this is the first thing built

If projecting a shared world into a tool surface turns out awkward, the
single-world premise is wrong — and that is a week-one finding rather than a
month-three one. It did not turn out awkward, and the evidence is that the same
34 golden cases pass against a projected server and a hand-written one.

## What the agent cannot tell

The projected server speaks MCP, declares `outputSchema`, carries side-effect and
scope metadata, and answers on the same wire. The agent binds to it by changing a
URL. **The agent never knows; the harness always does** — the run record carries
the resolution, so a verdict is interpretable even though the agent could not
distinguish the two.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from agenttwin.world import Action, Input, World

SESSION_META = "aoas/session"
"""Where a caller presents its session in a call's `_meta`. A binding between
the agent's transport and this stand-in — the order system's contract says the
caller's identity reaches it; which key carries it is realisation."""

IDEMPOTENCY_META = "aoas/idempotency-key"
"""Where a caller presents the key that makes a repeat recognisable here — at
the far end, where the effect lands (F-017). Same binding as `SESSION_META`."""

META_SIDE_EFFECT = "side_effect"
META_REQUIRED_SCOPE = "required_scope"


Authorise = Callable[
    [str, dict[str, Any], dict[str, object]], Awaitable[Mapping[str, object] | None]
]
"""(operation, arguments, call metadata) → the session the call acts under.
Raise to refuse the call outright; return `None` for a caller with no session."""


class IncoherentWorld(Exception):
    """Time moved the world somewhere it declared it could not be."""


@dataclass
class Live:
    """The world as it is *now* — mutated by whatever the agent does.

    Separate from the declared `World`, which is immutable, so `world₀` survives
    the run and can be diffed against `world₁`. A simulation that mutated its own
    declaration would have nothing to compare against.
    """

    world: World
    rows: dict[str, dict[str, dict]] = field(default_factory=dict)
    effects: list[tuple[str, str]] = field(default_factory=list)
    answered: dict[str, dict] = field(default_factory=dict)
    """Every keyed write's answer, by idempotency key. A repeated key is the same
    request, and gets the same answer without the effect landing twice."""

    @classmethod
    def start(cls, world: World) -> Live:
        rows = {
            entity: {r[world.entities[entity].key]: copy.deepcopy(dict(r)) for r in records}
            for entity, records in world.records.items()
        }
        return cls(world=world, rows=rows)

    def snapshot(self) -> dict[str, dict[str, dict]]:
        return copy.deepcopy(self.rows)

    def get(self, entity: str, key: str) -> dict | None:
        return self.rows.get(entity, {}).get(key)

    def count(self, action: str) -> int:
        return sum(1 for a, _ in self.effects if a == action)

    def advance(self, days: int) -> tuple[str, ...]:
        """Move the world's clock, and every counter that runs with it.

        Without this a world is frozen: a return window can never close while a
        customer is still talking, so the rule this agent argues about most is
        the one no simulation could reach. The counters that move are the ones
        the specification declared `advances: days`, and each moves only where
        its `advances_when` holds — an order nobody delivered is not *n* days
        since delivery, it has no age at all.

        Returns what moved, because a scenario that advanced time and changed
        nothing did not test what it thought.
        """
        moved: list[str] = []
        for name, entity in self.world.entities.items():
            counters = [
                (field_name, spec)
                for field_name, spec in entity.fields.items()
                if spec.advances == "days"
            ]
            if not counters:
                continue
            for key, row in self.rows.get(name, {}).items():
                for field_name, spec in counters:
                    if spec.advances_when is not None and not spec.advances_when.holds(row):
                        continue
                    row[field_name] = int(row.get(field_name, 0)) + days
                    moved.append(f"{name}.{key}.{field_name}")
                # A world that advanced into a state it declared impossible is a
                # broken scenario reading as a finding about the agent — the
                # same failure `StaleRead` refuses, at the fourth place a world
                # can move.
                broken = entity.violations(row)
                if broken:
                    raise IncoherentWorld(
                        f"advancing {days} day(s) put {name} {key} in a state it declared "
                        f"impossible — {broken[0].name!r}: {broken[0].because}"
                    )
        return tuple(moved)


class UnknownRecord(Exception):
    """Asked about something the world does not contain.

    Distinct from a refusal. "No such order" is a different answer from "that
    order cannot be cancelled", and collapsing them teaches the model that
    absence and prohibition are the same thing.
    """


def project(
    live: Live,
    *,
    name: str = "ecom",
    wrap=None,
    scopes: Mapping[str, str] | None = None,
    authorise: Authorise | None = None,
) -> MCPServer:
    """Build an MCP server from a live world.

    Every tool is generated from the declaration: its schema from the entity, its
    metadata from the action, its behaviour from `allowed_when` and `sets`. There
    is no per-tool code here, which is the whole claim — a second world needs a
    second YAML file and nothing else.

    `scopes` names the authority each operation requires — operation to scope
    name. It is the **binding's** vocabulary, not the world's: which operations
    are privileged is the agent specification's business, what the privilege is
    called belongs to whatever issues credentials. Worlds carried this in an
    `x_binding` block until the binding spec existed; it exists now, so the
    caller supplies it and a world that is handed none projects an ungated
    surface, which is a legitimate thing to simulate.

    `authorise` is how the stand-in holds a caller to what a real order system
    would: given the operation, its arguments and the call's metadata, it returns
    the session the call may act under, or raises to refuse it. Without one, the
    session is whatever the caller's metadata asserts, which is what a
    simulation of the world alone needs. With one, it is whatever a verified
    credential says, and an asserted `customer_id` is ignored (reference-agent
    T-002). The check itself is the caller's, because what verifies a credential
    is a binding, not the world.
    """
    srv = MCPServer(name)
    system = live.world.systems.get(name)
    if system is None:
        raise KeyError(f"world {live.world.name!r} declares no system {name!r}")

    required = dict(scopes or {})
    for action_name, action in system.actions.items():
        _register(srv, live, action_name, action, wrap, required.get(action_name), authorise)

    return srv


def _register(
    srv: MCPServer,
    live: Live,
    action_name: str,
    action: Action,
    wrap=None,
    scope: str | None = None,
    authorise: Authorise | None = None,
) -> None:
    entity = live.world.entities[action.entity]
    key_field = entity.key

    async def handler(ctx: Context | None = None, **arguments: Any) -> dict[str, Any]:
        key = arguments[key_field]
        row = live.get(action.entity, key)
        session = (
            await authorise(action_name, dict(arguments), _meta(ctx))
            if authorise is not None
            else _session(ctx)
        )
        # Not yours is answered exactly as not there (F-016). A refusal that
        # confirmed the record exists — or, worse, returned it — would tell a
        # stranger their guess was right and what was behind it.
        if row is None or not action.visible_to(row, session):
            raise UnknownRecord(f"no {action.entity} {key}")

        if action.side_effect == "read":
            return {"found": True, **row}

        given = {i.name: arguments.get(i.name) for i in action.inputs}

        # The far end of F-017. The harness's ledger only knows what it saw
        # succeed; when the effect lands and the reply is lost, only the system
        # that applied it can recognise the retry.
        key = _idempotency_key(ctx)
        if key is not None and key in live.answered:
            return copy.deepcopy(live.answered[key])
        answer = _apply(live, action_name, action, row, given)
        if key is not None:
            live.answered[key] = copy.deepcopy(answer)
        return answer

    handler.__name__ = action_name
    handler.__doc__ = action.description or action_name

    typed = _typed(handler, key_field, action.inputs)
    srv.tool(
        name=action_name,
        description=action.description or action_name,
        meta={
            META_SIDE_EFFECT: action.side_effect,
            **({META_REQUIRED_SCOPE: scope} if scope else {}),
        },
        structured_output=True,
    )(typed if wrap is None else wrap(action_name, typed))


def _apply(
    live: Live, action_name: str, action: Action, row: dict, given: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate the action's conditions against the row, and apply it if allowed.

    A refusal is a *result*, not an error: the model is expected to explain it to
    the customer, and an exception would deny it the chance — AAC-0053 is about
    recovery, not about crashing.
    """
    allowed, reason = action.evaluate(row)
    if not allowed:
        return {"allowed": False, "reason": reason, **row}
    row.update(action.sets)
    # An effect the spec writes from an input — `{address: $address}`. The value
    # is the caller's; the field it lands in is the spec's.
    row.update({field: given[name] for field, name in action.sets_from_input.items()})
    live.effects.append((action_name, str(row[live.world.entities[action.entity].key])))
    return {"allowed": True, "reason": "allowed", **row}


def _idempotency_key(ctx: Context | None) -> str | None:
    """The caller's idempotency key from the call's metadata, if it sent one."""
    try:
        meta = ctx.request_context.meta if ctx is not None else None
    except (AttributeError, ValueError):
        return None
    key = meta.get(IDEMPOTENCY_META) if isinstance(meta, dict) else None
    return key if isinstance(key, str) and key else None


def _meta(ctx: Context | None) -> dict[str, object]:
    """The call's metadata, or empty — never a guess."""
    try:
        meta = ctx.request_context.meta if ctx is not None else None
    except (AttributeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _session(ctx: Context | None) -> dict[str, object] | None:
    """The caller's session from the call's metadata, or `None` — never a guess."""
    session = _meta(ctx).get(SESSION_META)
    return session if isinstance(session, dict) else None


ANNOTATIONS: dict[str, type] = {"str": str, "int": int, "float": float, "bool": bool}


def _typed(handler, key_field: str, inputs: tuple[Input, ...] = ()):
    """Give the handler a signature and annotations MCP can build a schema from.

    The SDK derives `inputSchema` from the function signature and `outputSchema`
    from the return annotation, so a generated tool has to look like a written
    one. `outputSchema` is mandatory in this system, and a projected tool that
    could not declare one would be rejected by the agent's own registry — which
    is the right outcome and would make the projection useless, so it is built to
    satisfy the rule rather than to be exempt from it.
    """
    import inspect

    # The key, then whatever else the spec declares this operation takes. What
    # the system reads from its own row is never a parameter — which is why a
    # refund takes no amount (E3, F-014) and a change of address takes one.
    declared = [
        inspect.Parameter(
            i.name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ANNOTATIONS[i.type]
        )
        for i in inputs
    ]
    params = [
        inspect.Parameter(key_field, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        *declared,
        inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context, default=None),
    ]
    handler.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])
    handler.__annotations__ = {
        key_field: str,
        **{i.name: ANNOTATIONS[i.type] for i in inputs},
        "ctx": Context,
        "return": dict[str, Any],
    }
    return handler


__all__ = [
    "Live",
    "META_REQUIRED_SCOPE",
    "IDEMPOTENCY_META",
    "META_SIDE_EFFECT",
    "SESSION_META",
    "Authorise",
    "UnknownRecord",
    "project",
]
