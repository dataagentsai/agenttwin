"""The one entry point an implementation supplies to be driven by a suite.

`Subject` says what a scenario needs from an agent mid-run. This says how a
runner that has never seen the agent gets one: a single async context manager,
named in the agent's own repository as `module:attribute`, with this shape.

    @asynccontextmanager
    async def open_subject(live, *, wrap, clock, model):
        tools = connect(project(live, scopes=..., wrap=wrap))   # the twin's world
        llm = MyOpenAICompatibleClient(base_url=model.base_url,
                                       api_key=model.api_key, model=model.model)
        agent = build(tools=tools, llm=llm, clock=clock, ...)
        yield Subject(say=..., reviewer=..., colleague=..., opens=...)

**Three obligations, and a run means nothing if any is skipped:**

1. **Tools come from `project(live, ..., wrap=wrap)`.** `wrap` carries the
   scenario's tool faults and is opaque on purpose — forward it, never read it.
2. **Every model call goes to `model.base_url`.** That is the provider twin,
   serving the scenario's scripted answers or forwarding to a live provider, and
   serving the scenario's provider faults either way. Wrap your client exactly
   as production does, retries included: a simulation composed differently from
   the deployment is simulating a different agent (reference-agent F-029).
3. **`clock` is the only clock.** Whatever the agent stamps — an approval, an
   escalation, a read — reads this, or the offstage people act at a moment the
   agent never reached.

Generation run 2 wrote its own binding with its own signature, which is why no
runner could drive it with the reference's scenarios. This is that signature,
fixed.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol

from agenttwin.projection import Live
from agenttwin.provider import ModelEndpoint
from agenttwin.scenario import Clock
from agenttwin.subject import Subject


class Binding(Protocol):
    """`(live, *, wrap, clock, model) -> async context manager yielding a Subject`."""

    def __call__(
        self,
        live: Live,
        *,
        wrap: Callable[..., Any],
        clock: Clock,
        model: ModelEndpoint,
    ) -> AbstractAsyncContextManager[Subject]: ...


class BindingNotFound(Exception):
    pass


def load_binding(ref: str, *, root: Path | None = None) -> Binding:
    """Import `module:attribute`, with `root` (default: the working directory)
    first on the path — the binding lives in the agent's repository, not here."""
    module_name, _, attribute = ref.partition(":")
    if not module_name or not attribute:
        raise BindingNotFound(f"{ref!r} is not module:attribute")
    where = str((root or Path.cwd()).resolve())
    if where not in sys.path:
        sys.path.insert(0, where)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise BindingNotFound(f"cannot import {module_name}: {exc}") from exc
    found = getattr(module, attribute, None)
    if found is None:
        raise BindingNotFound(f"{module_name} has no {attribute}")
    return found


__all__ = ["Binding", "BindingNotFound", "load_binding"]
