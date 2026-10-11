"""`tests` is this directory, whatever else is installed.

PyRIT (a `dev` dependency since 0.11.0, for the attack adapter's tests) pulls
`confusables` and `ecoji`, and both install a top-level package named `tests`.
A regular package anywhere on the path wins over this directory, which is a
namespace package — and must stay one: an editable install puts this
repository's root on every dependent agent's path, and a regular `tests` here
would shadow *their* suites (found by the reference agent's gates, 11 Oct). So
the name is bound here, for this suite only, before any test module imports
`tests.test_loader`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

HERE = str(Path(__file__).resolve().parent)

_found = sys.modules.get("tests")
if _found is None or HERE not in list(getattr(_found, "__path__", [])):
    _package = types.ModuleType("tests")
    _package.__path__ = [HERE]
    sys.modules["tests"] = _package
