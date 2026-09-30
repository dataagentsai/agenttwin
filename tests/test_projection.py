"""The projection's contract with its caller — the parts generation run 2 had to
find by probing (NOTES §1, §2).

Table-driven, on the lending library from `test_loader`: a hook that is not
async, an unknown row read as a result, and the optional authority check a
binding's `authorise` hook can call.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from agenttwin import (
    APPROVAL_META,
    SESSION_META,
    Live,
    NotAuthorised,
    authority_check,
    load,
    project,
)
from tests.test_loader import SPEC, WORLD, write

RENEW_ALONE = {"agent_when": [{"field": "days_overdue", "at_most": 3}]}
SCOPES = {"note_due_date": "loans:write"}


def library(tmp_path: Path) -> Live:
    spec, world = copy.deepcopy(SPEC), copy.deepcopy(WORLD)
    spec["operations"]["renew"]["authority"] = RENEW_ALONE
    world["records"]["member"].append({"id": "M-2", "email": "n@example.org"})
    world["records"]["loan"] += [
        {"id": "L-2", "member_id": "M-1", "status": "open", "days_overdue": 5},
        {"id": "L-3", "member_id": "M-2", "status": "open", "days_overdue": 5},
    ]
    return Live.start(load(write(tmp_path, spec, world)))


async def call(srv, tool: str, arguments: dict[str, Any], meta: dict[str, object]):
    async with Client(srv) as client:
        return await client.call_tool(tool, arguments, meta=meta)  # type: ignore[arg-type]


def text(result) -> str:
    return "".join(getattr(b, "text", "") for b in (result.content or []))


# ---------------------------------------------------------------- the hook


def sync_hook(operation, arguments, meta):
    return {"member_id": "M-1"}


async def list_hook(operation, arguments, meta):
    return ["M-1"]


def test_a_sync_authorise_hook_is_refused_when_projecting_and_named(tmp_path: Path) -> None:
    """Run 2 met this as an opaque tool error on the first call."""
    with pytest.raises(TypeError, match=r"authorise hook .*sync_hook.* is synchronous"):
        project(library(tmp_path), name="catalogue", authorise=sync_hook)


async def test_a_hook_returning_a_non_mapping_says_so_to_the_caller(tmp_path: Path) -> None:
    srv = project(library(tmp_path), name="catalogue", authorise=list_hook)
    result = await call(srv, "get_loan", {"id": "L-1"}, {})
    assert result.is_error
    assert "must return the session as a mapping" in text(result)


# ------------------------------------------------------ which system

# [name, the name asked for, error or None]. The world keys its stand-in
# `branch-stock`; the AOAS calls the contract `catalogue` (generation run 2
# met the same split as `ecom` / `order_system`).
NAMES = [
    ("the world's own key", "branch-stock", None),
    ("the AOAS external it projects", "catalogue", None),
    ("neither", "archive", r"declares no system 'archive'"),
]


@pytest.mark.parametrize(("name", "asked", "error"), NAMES, ids=[n[0] for n in NAMES])
async def test_a_system_is_found_by_its_key_or_the_contract_it_projects(
    tmp_path: Path, name: str, asked: str, error: str | None
) -> None:
    world = copy.deepcopy(WORLD)
    world["systems"] = {"branch-stock": world["systems"]["catalogue"]}
    live = Live.start(load(write(tmp_path, copy.deepcopy(SPEC), world)))
    if error:
        with pytest.raises(KeyError, match=error):
            project(live, name=asked)
        return
    result = await call(project(live, name=asked, unknown_record="result"), "get_loan", {"id": "L-1"}, MINE)
    assert result.structured_content["found"] is True


# ------------------------------------------------------ unknown, readably

MINE ={SESSION_META: {"member_id": "M-1"}}

# [name, mode, key, readable result or None for isError]
UNKNOWN = [
    (
        "missing, result",
        "result",
        "L-9",
        {"found": False, "allowed": False, "reason": "no loan L-9"},
    ),
    (
        "a stranger's, result",
        "result",
        "L-3",
        {"found": False, "allowed": False, "reason": "no loan L-3"},
    ),
    ("missing, raise", "raise", "L-9", None),
    ("a stranger's, raise", "raise", "L-3", None),
    ("mine, either mode", "result", "L-1", "found"),
]


@pytest.mark.parametrize(("name", "mode", "key", "expected"), UNKNOWN, ids=[u[0] for u in UNKNOWN])
async def test_an_unknown_row_is_readable_and_never_reveals_ownership(
    tmp_path: Path, name: str, mode: str, key: str, expected
) -> None:
    srv = project(library(tmp_path), name="catalogue", unknown_record=mode)  # type: ignore[arg-type]
    result = await call(srv, "get_loan", {"id": key}, MINE)
    if expected is None:
        assert result.is_error
    elif expected == "found":
        assert not result.is_error and result.structured_content["found"] is True
    else:
        assert not result.is_error
        assert result.structured_content == expected


@pytest.mark.parametrize("mode", ["result", "raise"])
async def test_a_stranger_s_row_and_a_missing_one_read_the_same(tmp_path: Path, mode: str) -> None:
    """Same channel, same shape; only the key the caller sent differs."""
    srv = project(library(tmp_path), name="catalogue", unknown_record=mode)  # type: ignore[arg-type]
    stranger = await call(srv, "renew", {"id": "L-3"}, MINE)
    missing = await call(srv, "renew", {"id": "L-4"}, MINE)
    assert stranger.is_error == missing.is_error
    assert text(stranger).replace("L-3", "?") == text(missing).replace("L-4", "?")
    assert (stranger.structured_content is None) == (missing.structured_content is None)


def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown_record"):
        project(library(tmp_path), name="catalogue", unknown_record="quiet")  # type: ignore[arg-type]


# ------------------------------------------------------- authority_check


async def accepts(approval_id, operation, arguments, meta) -> None:
    if approval_id != "AP-1":
        raise NotAuthorised(f"approval {approval_id} does not cover {operation}")


# [name, operation, arguments, granted scopes, approval named, hook bound, refusal regex or None]
AUTHORITY = [
    ("within agent_when", "renew", {"id": "L-1"}, (), None, True, None),
    (
        "outside agent_when, no approval",
        "renew",
        {"id": "L-2"},
        (),
        None,
        True,
        "days_overdue.*none was named",
    ),
    ("outside agent_when, a matching approval", "renew", {"id": "L-2"}, (), "AP-1", True, None),
    (
        "outside agent_when, a wrong approval",
        "renew",
        {"id": "L-2"},
        (),
        "AP-2",
        True,
        "AP-2 does not cover",
    ),
    (
        "outside agent_when, no approval hook",
        "renew",
        {"id": "L-2"},
        (),
        "AP-1",
        False,
        "no approval hook",
    ),
    ("a stranger's row is left to read as unknown", "renew", {"id": "L-3"}, (), None, True, None),
    ("a missing row is left to read as unknown", "renew", {"id": "L-9"}, (), None, True, None),
    ("scope held", "note_due_date", {"id": "L-1", "note": "x"}, ("loans:write",), None, True, None),
    (
        "scope lacked",
        "note_due_date",
        {"id": "L-1", "note": "x"},
        (),
        None,
        True,
        "lacks scope 'loans:write'",
    ),
    ("scope lacked, approved", "note_due_date", {"id": "L-1", "note": "x"}, (), "AP-1", True, None),
    ("no such operation", "burn", {"id": "L-1"}, (), None, True, "not an operation"),
]


@pytest.mark.parametrize(
    ("name", "operation", "arguments", "granted", "approval", "bound", "refused"),
    AUTHORITY,
    ids=[a[0] for a in AUTHORITY],
)
async def test_authority_check(
    tmp_path: Path, name, operation, arguments, granted, approval, bound, refused
) -> None:
    check = authority_check(
        library(tmp_path), scopes=SCOPES, approval=accepts if bound else None, system="catalogue"
    )
    meta = {APPROVAL_META: approval} if approval else {}
    session = {"member_id": "M-1"}
    if refused is None:
        await check(operation, arguments, meta, session=session, granted=granted)
    else:
        with pytest.raises(NotAuthorised, match=refused):
            await check(operation, arguments, meta, session=session, granted=granted)


async def test_a_hook_using_the_check_refuses_readably_at_the_far_end(tmp_path: Path) -> None:
    """End to end: the refusal's reason reaches the caller, and nothing lands."""
    live = library(tmp_path)
    check = authority_check(live, scopes=SCOPES, approval=accepts, system="catalogue")

    async def authorise(operation, arguments, meta):
        session = {"member_id": "M-1"}  # a verified credential, in a real binding
        await check(operation, arguments, meta, session=session, granted=())
        return session

    srv = project(live, name="catalogue", scopes=SCOPES, authorise=authorise)
    refused = await call(srv, "renew", {"id": "L-2"}, {})
    assert refused.is_error and "needs an approval" in text(refused)
    assert live.count("renew") == 0

    approved = await call(srv, "renew", {"id": "L-2"}, {APPROVAL_META: "AP-1"})
    assert not approved.is_error and approved.structured_content["allowed"] is True
    assert live.count("renew") == 1
