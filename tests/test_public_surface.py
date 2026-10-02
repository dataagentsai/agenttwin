"""What a builder may read of AgentTwin names no implementation of the agent
it tests.

A generated agent may read the SPEC, the README, the schema and the public
API's docstrings. Generation run 3 found the reference implementation's package
name in a module docstring, and renamed its own package to avoid copying it.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import agenttwin

ROOT = Path(__file__).resolve().parents[1]

# Names that identify one implementation's layout, not the world it lives in.
LEAKS = ["support_agent"]

MODULES = [m.name for m in pkgutil.iter_modules(agenttwin.__path__, "agenttwin.")]


@pytest.mark.parametrize("module", ["agenttwin", *MODULES])
def test_no_module_docstring_names_an_implementation(module: str) -> None:
    doc = importlib.import_module(module).__doc__ or ""
    assert not [n for n in LEAKS if n in doc]


@pytest.mark.parametrize("doc", ["SPEC.md", "README.md"])
def test_no_readable_document_names_an_implementation(doc: str) -> None:
    text = (ROOT / doc).read_text()
    assert not [n for n in LEAKS if n in text]
