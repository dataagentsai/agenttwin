"""The stand-in keeps its promises: a property-test helper (T-109).

    from agenttwin.promises import check_promises
    findings = check_promises(load("worlds/clothing.yaml"))

For each tool the projection serves, **hypothesis-jsonschema** generates
arguments from the tool's own `inputSchema` — mixed with the world's real keys,
so the rows that exist are hit as well as the ones that do not — and calls it
as a caller would, under the customer's session, a stranger's, or none. Then:

1. **the declared `outputSchema`** — every answer validates against what the
   tool says it returns. A failure here is the stand-in breaking its contract;
2. **a refusal is a result** — an unknown row, a stranger's row, or a write
   whose precondition fails comes back as a structured answer naming why
   (`allowed: false`, `reason`), never an exception and never `isError`;
3. **the AOAS's shape** — the row inside the answer matches the entity the
   AOAS declares (`output: order`): each field's type, enum and pattern, and
   the envelope `project` documents (`found`, `allowed`, `reason`). The
   declared `outputSchema` says only *an object*, so this is the check with
   teeth; what it finds is reported, never fixed here.

**A test dependency, and an adapter.** hypothesis-jsonschema is MPL-2.0 and
is installed with the `dev` extra only; nothing in the core imports this
module (ADOPTION.md, *The one constraint*). Synchronous on purpose: it runs
each call in its own event loop, so call it from a plain test function.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from mcp.client import Client

from agenttwin.projection import SESSION_META, Live, UnknownRecordMode, _system, project
from agenttwin.world import World

TYPES: dict[str, dict[str, Any]] = {
    "int": {"type": "integer"},
    "money": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
}
"""A field's JSON type by its AOAS type; anything not named is a string."""


@dataclass(frozen=True)
class Finding:
    tool: str
    promise: str
    """`outputSchema`, `refusal is a result`, or `AOAS shape`."""
    detail: str
    example: str = ""


def entity_schema(world: World, entity: str) -> dict[str, Any]:
    """A row of `entity`, as the AOAS declares it."""
    spec = world.entities[entity]
    properties: dict[str, Any] = {}
    for name, f in spec.fields.items():
        schema = dict(TYPES.get(f.type, {"type": "string"}))
        if f.type == "enum" and f.values:
            schema = {"enum": list(f.values)}
        if f.pattern:
            schema["pattern"] = f"^(?:{f.pattern})$"
        properties[name] = schema
    return {"type": "object", "properties": properties, "required": [spec.key]}


def answer_schema(world: World, entity: str, *, side_effect: str, many: bool) -> dict[str, Any]:
    """What `project` documents a tool answers, around the AOAS's row."""
    row = entity_schema(world, entity)
    unknown = {
        "type": "object",
        "properties": {
            "found": {"const": False},
            "allowed": {"const": False},
            "reason": {"type": "string"},
        },
        "required": ["found", "allowed", "reason"],
        "additionalProperties": False,
    }
    if many:
        return {
            "type": "object",
            "properties": {"found": {"const": True}, "items": {"type": "array", "items": row}},
            "required": ["found", "items"],
            "additionalProperties": False,
        }
    if side_effect == "read":
        found = copy.deepcopy(row)
        found["properties"]["found"] = {"const": True}
        found["required"] = [*row["required"], "found"]
        return {"oneOf": [found, unknown]}
    answered = copy.deepcopy(row)
    answered["properties"].update(
        allowed={"type": "boolean"},
        reason={"type": "string"},
        created={
            "type": "object",
            "properties": {"entity": {"type": "string"}, "id": {"type": "string"}},
            "required": ["entity", "id"],
        },
    )
    answered["required"] = [*row["required"], "allowed", "reason"]
    return {"oneOf": [answered, unknown]}


def sessions(world: World) -> list[dict[str, Any] | None]:
    """The customer's session, every other owner's, and none."""
    out: list[dict[str, Any] | None] = []
    for system in world.systems.values():
        for action in system.actions.values():
            for cond in action.session_when:
                field = cond.field.partition(".")[2] or cond.field
                for row in world.records.get(action.entity, ()):
                    s = {cond.session: row.get(field)}
                    if row.get(field) is not None and s not in out:
                        out.append(s)
    return [*out, None]


def check_promises(
    world: World,
    *,
    system: str | None = None,
    max_examples: int = 40,
    history: int = 2,
    live: Callable[[World], Live] = Live.start,
    unknown_record: UnknownRecordMode = "result",
    shrink: bool = True,
) -> list[Finding]:
    """Every finding, at most one per tool and promise: Hypothesis shrinks each
    to the smallest case that breaks it.

    Each case is up to `history` calls to the world's writes, then the call
    under test, on one fresh world: a promise a row keeps at t0 and breaks once
    a write has made or moved it is found only that way. `unknown_record` is
    `project`'s; `"result"` is the mode a refusal-is-a-result promise is about.
    `shrink=False` reports the first failing case rather than the smallest,
    which is much faster when something is broken.
    """
    started = live(world)
    srv = project(started, name=system, unknown_record=unknown_record)
    tools = {t.name: t for t in asyncio.run(_tools(srv))}
    actions = _system(started, system).actions
    calls = {name: _arguments(world, tool, actions[name]) for name, tool in tools.items()}
    # The history is there to make and move rows, so it acts on rows that exist.
    moves = {
        name: _arguments(world, tool, actions[name], known_only=True)
        for name, tool in tools.items()
    }
    writes = [n for n in tools if actions[n].side_effect != "read"]
    before = (
        st.lists(
            st.sampled_from(writes).flatmap(lambda n: moves[n].map(lambda a, n=n: (n, a))),
            max_size=history,
        )
        if writes and history
        else st.just([])
    )
    found: list[Finding] = []
    for name, tool in tools.items():
        found += _check_tool(
            world,
            system,
            tool,
            actions[name],
            calls[name],
            before,
            max_examples,
            live,
            unknown_record,
            shrink,
        )
    return found


async def _tools(srv) -> list:
    async with Client(srv) as client:
        return list((await client.list_tools()).tools)


def _arguments(
    world: World, tool, action, *, known_only: bool = False
) -> st.SearchStrategy[dict[str, Any]]:
    """From the tool's own `inputSchema`; half the time — or every time, with
    `known_only` — with a key the world holds."""
    key = world.entities[action.entity].key
    keys = [str(r[key]) for r in world.records.get(action.entity, ()) if r.get(key) is not None]
    args = from_schema(tool.input_schema)
    if keys and key in (tool.input_schema.get("properties") or {}):
        keyed = st.builds(lambda a, k: {**a, key: k}, args, st.sampled_from(keys))
        args = keyed if known_only else st.one_of(args, keyed)
    return args


PROMISES = ("outputSchema", "refusal is a result", "AOAS shape")


def _check_tool(
    world, system, tool, action, args, before, max_examples, live, unknown_record, shrink
) -> list[Finding]:
    shape = answer_schema(world, action.entity, side_effect=action.side_effect, many=action.many)
    findings: dict[str, Finding] = {}

    def property_for(promise: str):
        @settings(
            max_examples=max_examples,
            database=None,
            derandomize=True,
            deadline=None,
            suppress_health_check=list(HealthCheck),
            phases=list(Phase) if shrink else [Phase.explicit, Phase.reuse, Phase.generate],
        )
        @given(
            earlier=before,
            arguments=args,
            session=st.sampled_from(sessions(world)),
        )
        def holds(earlier, arguments, session) -> None:
            srv = project(live(world), name=system, unknown_record=unknown_record)
            meta = {SESSION_META: session} if session is not None else {}
            try:
                result = asyncio.run(_calls(srv, [*earlier, (tool.name, arguments)], meta))
            except Exception as exc:  # the finding is that it raised
                if promise == "refusal is a result":
                    raise AssertionError(f"raised {type(exc).__name__}: {exc}") from exc
                return
            content = result.structured_content
            if promise == "refusal is a result":
                assert not result.is_error, f"isError: {_text(result)[:160]}"
                assert isinstance(content, dict), "no structured answer"
                if content.get("allowed") is False or content.get("found") is False:
                    assert isinstance(content.get("reason"), str) and content["reason"], (
                        "a refusal that names no reason"
                    )
            elif result.is_error or content is None:
                return  # the promise above reports it
            else:
                schema = (tool.output_schema or {}) if promise == "outputSchema" else shape
                broken = _violations(content, schema)
                assert not broken, "; ".join(broken)

        return holds

    for promise in PROMISES:
        try:
            property_for(promise)()
        except AssertionError as exc:
            findings[promise] = Finding(tool.name, promise, _first_line(exc), _example(exc))
    return list(findings.values())


async def _calls(srv, calls: list[tuple[str, dict[str, Any]]], meta: dict[str, Any]):
    """Every call in order on one connection; the last one's result."""
    async with Client(srv) as client:
        result = None
        for name, arguments in calls:
            result = await client.call_tool(name, arguments, meta=meta or None)  # type: ignore[arg-type]
        return result


def _violations(content: Any, schema: dict[str, Any]) -> list[str]:
    """Every way `content` breaks `schema`, one line each. A `oneOf` that no
    branch matches is opened into the branch the answer meant (a found row, an
    answered write), so the line names the field, not the envelope."""
    validator = jsonschema.Draft202012Validator(schema)
    out: set[str] = set()
    for error in validator.iter_errors(content):
        leaves = [error]
        if error.validator == "oneOf" and error.context:
            leaves = [e for e in error.context if e.relative_schema_path[0] == 0]
        for e in leaves:
            where = "/".join(str(p) for p in e.absolute_path) or "(the answer)"
            out.add(f"{re.sub(r'/[0-9]+/', '/*/', where)}: {e.message[:120]}")
    return sorted(out)


def _text(result) -> str:
    return "".join(getattr(b, "text", "") for b in (result.content or []))


def _first_line(exc: BaseException) -> str:
    return (str(exc).splitlines() or [type(exc).__name__])[0][:600]


def _example(exc: BaseException) -> str:
    notes = getattr(exc, "__notes__", None) or []
    return re.sub(r"\s+", " ", " ".join(n for n in notes if "Falsifying" in n or "=" in n))[:300]


__all__ = ["PROMISES", "Finding", "answer_schema", "check_promises", "entity_schema", "sessions"]
