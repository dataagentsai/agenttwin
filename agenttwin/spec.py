"""Reading the agent specification a world cites.

AgentTwin does not define the domain — the agent's specification (AOAS) does,
and a world cites it. This module reads one, resolving `extends`, and nothing
more. **It does not validate an AOAS.** The format's own validator does that, in
the repository that defines it; a second validator here would be a second
opinion about the same format, and two opinions eventually disagree.

What it does check is only what composition would otherwise get wrong silently:
that the file is an AOAS at all, and that an `extends` chain leads where it says.

## `extends` is RFC 7386, not a new mechanism

A variant — the same agent, one store's return window instead of another's — is
the base specification plus a JSON Merge Patch: objects merge, `null` deletes,
and anything else (lists included) replaces. Cited rather than invented, so the
semantics are already written down somewhere a stranger can read them, and a
second world costs a short file instead of a fork.

Lists replacing wholesale is the known cost. Overriding one precondition means
restating that operation's list. That is the right trade for a format whose
readers are people: a patch you can read beats a patch language you must learn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

API_VERSION = "aoas/v0"
MAX_DEPTH = 8


class InvalidSpec(Exception):
    """The file is not an agent specification this format can compose from."""


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 §2, as written."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def load_spec(path: Path | str, *, _depth: int = 0) -> dict:
    path = Path(path)
    if _depth > MAX_DEPTH:
        raise InvalidSpec(f"{path}: `extends` is nested more than {MAX_DEPTH} deep — a cycle?")
    if not path.exists():
        raise InvalidSpec(f"no agent specification at {path}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or raw.get("apiVersion") != API_VERSION:
        raise InvalidSpec(f"{path} is not an agent specification (apiVersion {API_VERSION})")

    parent = raw.pop("extends", None)
    if parent is None:
        return raw

    base = load_spec(path.parent / parent["path"], _depth=_depth + 1)
    found = (base["agent"]["id"], base["agent"]["version"])
    wanted = (parent["id"], parent["version"])
    if found != wanted:
        raise InvalidSpec(
            f"{path} extends {wanted[0]}@{wanted[1]}, and {parent['path']} is {found[0]}@{found[1]}"
        )
    return merge_patch(base, raw)


__all__ = ["API_VERSION", "InvalidSpec", "load_spec", "merge_patch"]
