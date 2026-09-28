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
month-three one. It did not turn out awkward, and the evidence is that every
golden case passes against a projected server and a hand-written one.

## What the agent cannot tell

The projected server speaks MCP, declares `outputSchema`, carries side-effect and
scope metadata, and answers on the same wire. The agent binds to it by changing a
URL. **The agent never knows; the harness always does** — the run record carries
the resolution, so a verdict is interpretable even though the agent could not
distinguish the two.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from agenttwin.world import Action, Input, World

SESSION_META = "aoas/session"
"""Where a caller presents its session in a call's `_meta`. A binding between
the agent's transport and this stand-in — the order system's contract says the
caller's identity reaches it; which key carries it is realisation."""

IDEMPOTENCY_META = "aoas/idempotency-key"
"""Where a caller presents the key that makes a repeat recognisable here — at
the far end, where the effect lands (F-017). Same binding as `SESSION_META`."""

APPROVAL_META = "aoas/approval"
"""Where a caller names the approval a call acts on, in a call's `_meta`. The
value is the approval's id — a reference, never the record. Approvals are the
harness's, not the world's: the far end loads the record by this id through a
hook the binding supplies (see `authority_check`). Same binding as
`SESSION_META`."""

META_SIDE_EFFECT = "side_effect"
META_REQUIRED_SCOPE = "required_scope"


Authorise = Callable[
    [str, dict[str, Any], dict[str, object]], Awaitable[Mapping[str, object] | None]
]
"""`async (operation, arguments, meta) -> session | None` — the `authorise` hook.

Must be a coroutine function (`async def`); `project` refuses anything else.

- `operation` — the tool name, which is the spec's operation name.
- `arguments` — what the caller sent: the entity key (e.g. `id`) and any
  declared inputs. A copy; mutating it changes nothing.
- `meta` — the call's `_meta` dict, or `{}`. Carries `aoas/session`,
  `aoas/idempotency-key` and `aoas/approval` when the caller sent them.

Returns the session the call acts under: a mapping holding every field the
spec's `session` block declares and its ownership preconditions compare against
(`equals_session: customer_id` needs `{"customer_id": ...}`). Extra keys are
ignored. `None` means no session — owned rows are then invisible and a listing
is empty. Raise to refuse the call; the caller sees an `isError` result, not a
structured one. Raise `mcp.server.mcpserver.exceptions.ToolError` (or
`NotAuthorised`, which is one) for the reason to reach the caller; any other
exception reads only "Error executing tool <name>", like a crash."""

UnknownRecordMode = Literal["raise", "result"]


class NotAuthorised(ToolError):
    """Raised by `authority_check` when a call's authority does not cover it:
    a required scope the caller lacks, or `agent_when` false on the live row,
    with no approval that the binding's approval hook accepts.

    A `ToolError`, so the caller reads the reason (`isError`, text
    "Error executing tool <name>: <reason>") rather than the generic crash text
    any other exception from a hook produces."""


ApprovalCheck = Callable[[str, str, dict[str, Any], dict[str, object]], Awaitable[object]]
"""`async (approval_id, operation, arguments, meta) -> anything` — the binding's
half of carrying a grant (AHC-0057).

Loads the approval named by `approval_id` from wherever the harness keeps it
and confirms it covers *this* call: granted, unexpired, same operation, same
argument values, same customer, not approved by the customer. Raise to refuse;
any return is acceptance. AgentTwin never holds approvals, so it cannot do
this part itself."""


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
    name: str | None = None,
    wrap=None,
    scopes: Mapping[str, str] | None = None,
    authorise: Authorise | None = None,
    unknown_record: UnknownRecordMode = "raise",
) -> MCPServer:
    """Build an MCP server from a live world.

    **Answers, by case** (what a caller reads on the wire):

    - read, row visible → result `{found: True, **row}`
    - listing → result `{found: True, items: [rows the session may see]}`
    - write allowed → result `{allowed: True, reason: "allowed", **row}`
    - write refused by a precondition → result `{allowed: False, reason, **row}`
    - unknown row, or a row the session may not see:
      with `unknown_record="result"` → result
      `{found: False, allowed: False, reason: "no <entity> <key>"}`;
      with `unknown_record="raise"` (default) → MCP protocol error (`isError`)
    - `authorise` raised → MCP protocol error (`isError`)

    A row the caller may not touch is answered exactly as a missing one in
    both modes — same channel, same text — so ownership never leaks.

    `name` is the world's system to project. Omitted, it is the world's only
    system, and a world with several must be told which. It defaulted to
    `"ecom"` until 2026-09-28 — the name one world happened to choose — so a
    binding that never passed it worked only against worlds that had copied
    that name, and failed against the first scaffolded one, which names the
    system after the AOAS's own external.

    `unknown_record="result"` is the recommended mode: it lets a caller tell
    "no such record" (the AOAS failure mode `unknown_record`) from the server
    failing, which a protocol error cannot. `"raise"` is the default only
    because existing callers read the protocol error; it may flip later.

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
    would. Contract (see `Authorise`): an **`async def`** taking
    `(operation, arguments, meta)` and returning the session the call acts
    under — a mapping with the fields the spec's ownership preconditions compare
    (typically `{"customer_id": ...}`) — or `None` for no session; raise to
    refuse. A sync callable is refused here with a `TypeError` naming the hook.
    Without a hook, the session is whatever the caller's `_meta` asserts under
    `aoas/session`, which is what a simulation of the world alone needs. With
    one, it is whatever a verified credential says, and an asserted
    `customer_id` is ignored (reference-agent T-002).

    **Scope and authority are the hook's duty, not the projection's.** The
    projection enforces preconditions (`allowed_when`) and ownership
    (`session_when`) only. It publishes `required_scope` in each tool's `_meta`
    and carries `agent_when` on each `Action`, but checks neither: which
    credential carries which scope, and where approvals live, are binding
    facts. A hook that does not check them projects a far end that lets any
    verified caller perform any operation — AHC-0057 requires the check where
    the action executes. `authority_check` is a ready-made check a hook can
    call. What verifies a credential is a binding, not the world.
    """
    if authorise is not None:
        _require_async("authorise", authorise)
    if unknown_record not in ("raise", "result"):
        raise ValueError(f"unknown_record must be 'raise' or 'result', not {unknown_record!r}")
    if name is None:
        if len(live.world.systems) != 1:
            raise KeyError(
                f"world {live.world.name!r} declares {len(live.world.systems)} systems "
                f"({', '.join(sorted(live.world.systems))}); name the one to project"
            )
        name = next(iter(live.world.systems))
    srv = MCPServer(name)
    system = live.world.systems.get(name)
    if system is None:
        raise KeyError(f"world {live.world.name!r} declares no system {name!r}")

    required = dict(scopes or {})
    guarded = _checked(authorise) if authorise is not None else None
    for action_name, action in system.actions.items():
        _register(
            srv, live, action_name, action, wrap, required.get(action_name), guarded, unknown_record
        )

    return srv


def _is_async(hook: object) -> bool:
    if inspect.iscoroutinefunction(hook):
        return True
    call = getattr(type(hook), "__call__", None)  # noqa: B004 — an instance with async __call__
    return call is not None and inspect.iscoroutinefunction(call)


def _require_async(name: str, hook: object) -> None:
    """Refuse a hook that is plainly synchronous, at build time, by name.

    A sync `authorise` used to fail only when called, as a `TypeError` inside the
    tool that reached the caller as an opaque "Error executing tool" (generation
    run 2, NOTES §2). Callables whose asyncness cannot be seen (a lambda
    returning a coroutine) are checked again at call time by `_checked`.
    """
    if not callable(hook):
        raise TypeError(f"{name} hook must be an async callable, got {type(hook).__name__}")
    if (inspect.isfunction(hook) or inspect.ismethod(hook)) and not _is_async(hook):
        raise TypeError(
            f"{name} hook {getattr(hook, '__qualname__', hook)!r} is synchronous; it must "
            f"be `async def {name}(operation, arguments, meta)` returning the session "
            "mapping (or None), and raise to refuse"
        )


def _checked(authorise: Authorise) -> Authorise:
    """The hook, held to its contract at call time with errors that name it."""

    async def call(operation: str, arguments: dict[str, Any], meta: dict[str, object]):
        pending = authorise(operation, arguments, meta)
        if not inspect.isawaitable(pending):
            raise ToolError(
                f"authorise hook returned {type(pending).__name__}, not an awaitable; "
                "it must be async"
            )
        session = await pending
        if session is not None and not isinstance(session, Mapping):
            raise ToolError(
                f"authorise hook returned {type(session).__name__}; it must return the "
                "session as a mapping (e.g. {'customer_id': ...}) or None"
            )
        return session

    return call


AuthorityCheck = Callable[..., Awaitable[None]]


def authority_check(
    live: Live,
    *,
    scopes: Mapping[str, str] | None = None,
    approval: ApprovalCheck | None = None,
    system: str = "ecom",
) -> AuthorityCheck:
    """A ready-made scope-and-authority check for an `authorise` hook to call.

    Optional. The projection does not enforce scope or `agent_when` on its own
    (see `project`); a hook that wants AHC-0057's check at the executing side
    calls this after verifying the credential:

        check = authority_check(live, scopes=SCOPES, approval=load_and_match)

        async def authorise(operation, arguments, meta):
            principal = verify(meta)                       # the binding's
            session = {"customer_id": principal.customer_id}
            await check(operation, arguments, meta,
                        session=session, granted=principal.scopes)
            return session

    The returned coroutine function is
    `await check(operation, arguments, meta, *, session, granted)` and raises
    `NotAuthorised` to refuse, returning `None` otherwise. It asks, in order:

    1. **Scope.** If `scopes[operation]` names a scope not in `granted`, the
       call needs an approval.
    2. **Authority conditions.** Otherwise, if the operation declares
       `agent_when` and any clause is false on the **live** row (read now, not
       the caller's copy), the call needs an approval.
    3. **Approval.** A call that needs one must name it in `_meta` under
       `aoas/approval`; the id is passed to `approval` (the binding's
       `ApprovalCheck`), which loads it and confirms it matches — raise there to
       refuse. With no `approval` hook every call that needs an approval is
       refused.

    A row that does not exist, or that `session` may not see, is let through
    unchecked so the projection answers it as unknown — refusing it here would
    tell a stranger the row exists and what it is worth.
    """
    world_system = live.world.systems.get(system)
    if world_system is None:
        raise KeyError(f"world {live.world.name!r} declares no system {system!r}")
    required = dict(scopes or {})
    if approval is not None:
        _require_async("approval", approval)

    async def check(
        operation: str,
        arguments: Mapping[str, Any],
        meta: Mapping[str, object],
        *,
        session: Mapping[str, object] | None,
        granted: Collection[str] = (),
    ) -> None:
        action = world_system.actions.get(operation)
        if action is None:
            raise NotAuthorised(f"{operation} is not an operation of {system}")

        why: str | None = None
        needed = required.get(operation)
        if needed is not None and needed not in set(granted):
            why = f"the caller lacks scope {needed!r}"
        elif action.agent_when and not action.many:
            key = arguments.get(live.world.entities[action.entity].key)
            row = live.get(action.entity, str(key)) if key is not None else None
            if row is None or not action.visible_to(row, session):
                return  # answered as unknown by the projection, never as refused
            failing = [c.field for c in action.agent_when if not c.holds(row)]
            if failing:
                why = f"the agent may not do this alone ({', '.join(failing)})"
        if why is None:
            return

        approval_id = meta.get(APPROVAL_META)
        if not isinstance(approval_id, str) or not approval_id:
            raise NotAuthorised(f"{operation} needs an approval: {why}, and none was named")
        if approval is None:
            raise NotAuthorised(f"{operation} needs an approval and no approval hook is bound")
        await approval(approval_id, operation, dict(arguments), dict(meta))

    return check


def _register(
    srv: MCPServer,
    live: Live,
    action_name: str,
    action: Action,
    wrap=None,
    scope: str | None = None,
    authorise: Authorise | None = None,
    unknown_record: UnknownRecordMode = "raise",
) -> None:
    entity = live.world.entities[action.entity]
    key_field = entity.key

    if action.many:
        _register_listing(srv, live, action_name, action, wrap, scope, authorise)
        return

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
            reason = f"no {action.entity} {key}"
            if unknown_record == "result":
                # Readable on the execution channel (generation run 2, NOTES §2):
                # a caller can tell "no such record" from a crash. Built from
                # the key alone, so a stranger's row reads exactly as a missing one.
                return {"found": False, "allowed": False, "reason": reason}
            raise UnknownRecord(reason)

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


def _register_listing(
    srv: MCPServer,
    live: Live,
    action_name: str,
    action: Action,
    wrap=None,
    scope: str | None = None,
    authorise: Authorise | None = None,
) -> None:
    """A read of many rows: every row of the entity the caller's session may see.

    No key, so the session is the whole scope. A caller with no session sees
    nothing rather than everything, because `visible_to` fails closed; that is
    the difference between a listing and a leak.
    """

    async def handler(ctx: Context | None = None, **arguments: Any) -> dict[str, Any]:
        session = (
            await authorise(action_name, dict(arguments), _meta(ctx))
            if authorise is not None
            else _session(ctx)
        )
        rows = live.rows.get(action.entity, {}).values()
        return {
            "found": True,
            "items": [copy.deepcopy(r) for r in rows if action.visible_to(r, session)],
        }

    handler.__name__ = action_name
    handler.__doc__ = action.description or action_name
    typed = _typed(handler, None, action.inputs)
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


def _typed(handler, key_field: str | None, inputs: tuple[Input, ...] = ()):
    """Give the handler a signature and annotations MCP can build a schema from.

    The SDK derives `inputSchema` from the function signature and `outputSchema`
    from the return annotation, so a generated tool has to look like a written
    one. `outputSchema` is mandatory in this system, and a projected tool that
    could not declare one would be rejected by the agent's own registry — which
    is the right outcome and would make the projection useless, so it is built to
    satisfy the rule rather than to be exempt from it.
    """
    # The key, then whatever else the spec declares this operation takes. What
    # the system reads from its own row is never a parameter — which is why a
    # refund takes no amount (E3, F-014) and a change of address takes one.
    declared = [
        inspect.Parameter(
            i.name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ANNOTATIONS[i.type]
        )
        for i in inputs
    ]
    # A listing (`key_field is None`) has no row to name, so no key parameter.
    keyed = (
        [inspect.Parameter(key_field, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)]
        if key_field is not None
        else []
    )
    params = [
        *keyed,
        *declared,
        inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context, default=None),
    ]
    handler.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])
    handler.__annotations__ = {
        **({key_field: str} if key_field is not None else {}),
        **{i.name: ANNOTATIONS[i.type] for i in inputs},
        "ctx": Context,
        "return": dict[str, Any],
    }
    return handler


__all__ = [
    "APPROVAL_META",
    "ApprovalCheck",
    "Live",
    "NotAuthorised",
    "authority_check",
    "META_REQUIRED_SCOPE",
    "IDEMPOTENCY_META",
    "META_SIDE_EFFECT",
    "SESSION_META",
    "Authorise",
    "UnknownRecord",
    "project",
]
