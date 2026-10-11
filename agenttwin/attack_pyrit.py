"""The adapter for **PyRIT** (MIT, Microsoft): attack text rewritten by its
deterministic converters, optionally inside one of its jailbreak templates.

**Outside the core.** `agenttwin.attacks` owns the port (`AttackSource`), the
goals and how a case is judged; it resolves this module by name
(`SOURCES["pyrit"]`) and never imports it. This file is the only place PyRIT
is named (ADOPTION.md, *The one constraint*).

**Installed as an extra, not vendored**: `uv add 'agenttwin[pyrit]'`. PyRIT is
heavy (transformers, scipy, the Azure SDKs; about 550 MB), and what this
adapter uses is *code* — the converters — which vendoring would mean copying.
Without the extra, a scenario declaring `source: pyrit` says so and stops.

**Only what needs no model.** PyRIT's LLM-backed converters (tone, translation,
variation, persuasion …) are left out: they call a paid model, and a case
nobody can regenerate offline is a case nobody can reproduce. What is used:

- the **converters** that are a pure function of their input, or take a
  `seed` (checked: the same seed gives the same text in a fresh process,
  whatever the global `random` state). `UnicodeConfusableConverter`,
  `CharNoiseConverter`, `MathObfuscationConverter` and `AsciiArtConverter`
  are not reproducible that way, and are left out;
- the **jailbreak templates** PyRIT ships as data (`TextJailBreak`, offline);
- as seed text, the core's own injection shape with the goal as its demand
  (`attacks.seed_injections`). PyRIT's remote seed datasets need a download
  and are not used.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import threading
from collections.abc import Sequence
from typing import Any

from agenttwin.attacks import Attack, Goal, injections, seed_injections

CONVERTERS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("NoOp", {}),  # the seed text as it is: PyRIT's NoOpConverter, without the import
    ("Base64Converter", {}),
    ("ROT13Converter", {}),
    ("LeetspeakConverter", {}),
    ("CharacterSpaceConverter", {}),
    ("StringJoinConverter", {}),
    ("FlipConverter", {}),
    ("AtbashConverter", {}),
    ("CaesarConverter", {"caesar_offset": 3}),
    ("VigenereConverter", {"key": "twin"}),
    ("BinaryConverter", {}),
    ("BinAsciiConverter", {}),
    ("MorseConverter", {}),
    ("NatoConverter", {}),
    ("BrailleConverter", {}),
    ("ZeroWidthConverter", {}),
    ("UnicodeSubstitutionConverter", {}),
    ("AsciiSmugglerConverter", {}),
    ("SuperscriptConverter", {}),
    ("DiacriticConverter", {}),
    ("BidiConverter", {}),
    ("UrlConverter", {}),
    ("TatweelConverter", {}),
    ("CharSwapConverter", {"seed": None}),
    ("InsertPunctuationConverter", {"seed": None}),
    ("EmojiConverter", {"seed": None}),
    ("RandomCapitalLettersConverter", {"seed": None}),
    ("ZalgoConverter", {"seed": None}),
    ("AskToDecodeConverter", {"seed": None}),
    ("LetterBijectionConverter", {"seed": None}),
)
"""`(PyRIT converter class, keyword arguments)`. A `seed` of None is filled
from the case's own seed."""

JAILBREAK_SHARE = 0.3
"""How often the seed text is first wrapped in one of PyRIT's jailbreak
templates. Most indirect injections are short; a few are a page of role-play."""


def _pyrit() -> Any:
    try:
        import pyrit.converter as converters
        from pyrit.datasets.jailbreak.text_jailbreak import TextJailBreak
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "source: pyrit needs PyRIT: install agenttwin with the `pyrit` extra"
        ) from exc
    return converters, TextJailBreak


def _run(coro: Any) -> Any:
    """PyRIT's converters are async; a case list is built synchronously, often
    from inside a running loop. One short-lived thread with its own loop."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            box["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def convert(text: str, name: str, kwargs: dict[str, Any], seed: int) -> str:
    if name == "NoOp":
        return text
    converters, _ = _pyrit()
    cls = getattr(converters, name)
    given = {k: (seed if k == "seed" and v is None else v) for k, v in kwargs.items()}
    if "seed" not in given and "seed" in inspect.signature(cls.__init__).parameters:
        given["seed"] = seed
    converter = cls(**given)
    return _run(converter.convert_async(prompt=text, input_type="text")).output_text


def source(*, seed: int, count: int, goals: Sequence[Goal] = ()) -> list[Attack]:
    """`count` cases: a seed injection per goal (round the goals in a shuffled
    order, so every goal is tried), sometimes inside a PyRIT jailbreak
    template, then through one PyRIT converter. Deterministic by `seed`."""
    _, text_jailbreak = _pyrit()
    rng = random.Random(seed)
    templates = text_jailbreak.get_jailbreak_templates()
    order = list(goals)
    rng.shuffle(order)
    plain = injections(seed=seed, count=count) if not order else []
    out: list[Attack] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < count:
        tries += 1
        if tries > 50 * count:
            raise ValueError(f"pyrit: could not make {count} distinct cases from {len(goals)} goals")
        goal = order[len(out) % len(order)] if order else None
        text = seed_injections(goal, rng) if goal else plain[len(out)]
        origin = ["pyrit"]
        if rng.random() < JAILBREAK_SHARE:
            name = rng.choice(templates)
            text = text_jailbreak(template_file_name=name).get_jailbreak(prompt=text)
            origin.append(f"jailbreak {name}")
        converter, kwargs = rng.choice(CONVERTERS)
        case_seed = rng.randrange(2**31)
        payload = convert(text, converter, kwargs, case_seed)
        if payload in seen:
            continue
        seen.add(payload)
        origin.append(converter)
        out.append(Attack(payload, goal, " · ".join(origin)))
    return out


__all__ = ["CONVERTERS", "convert", "source"]
