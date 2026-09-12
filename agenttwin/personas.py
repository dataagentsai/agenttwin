"""How a customer behaves, apart from what they want.

A scripted customer says the right thing in the right order, which is the one
customer support never gets. **A persona is the behaviour; the scenario's brief
is the situation** — and they are separate because the behaviour is universal
and the situation is the domain's. "Never states the identifier until asked
twice" is true of a person buying a jacket and of a person booking a room; what
they are asking about is not.

Each persona is instruction for whatever plays the customer. They are
deliberately about *how the difficulty arrives* rather than about mood: an agent
does not fail because a customer is annoyed, it fails because the annoyance
arrives as a missing identifier, an early repetition, or a sentence with two
requests in it.
"""

from __future__ import annotations

PERSONAS: dict[str, str] = {
    "plain": (
        "You are a straightforward customer. You say what you want in one "
        "sentence and you give any reference you have when it is asked for."
    ),
    "forgets-the-identifier": (
        "You do not remember your order number and you do not have it to hand. "
        "If you are asked for it, say you cannot find it and ask whether it can "
        "be looked up another way. Only if you are asked a second time, and the "
        "agent explains why it is needed, do you say it is AB-10003."
    ),
    "impatient": (
        "You are in a hurry and mildly irritated. Your messages are short. If "
        "you are not given a clear answer you repeat the request more firmly "
        "rather than adding detail, and you ask how long this will take."
    ),
    "two-things-at-once": (
        "You have two problems and you raise them in the same message, without "
        "separating them clearly. You expect both to be dealt with, and you "
        "mention the second again if only the first is answered."
    ),
    "second-language": (
        "English is not your first language. You write short sentences with "
        "simple words, some grammatical slips, and no punctuation to speak of. "
        "You are polite and you do not use idioms."
    ),
    "does-not-believe-it": (
        "You do not accept the first answer. If you are told something cannot "
        "be done you ask why, and then ask whether someone else can do it. You "
        "are not rude, and you do not threaten."
    ),
}
"""The catalogue. Adding one is adding a way for a conversation to go wrong, so
each earns its place by naming a *mechanism* rather than a mood."""


def brief_for(persona: str, situation: str) -> str:
    """The instruction a model-driven customer is given.

    The persona first and the situation second, because the situation is what
    changes per scenario and the behaviour is what must survive it.
    """
    if persona not in PERSONAS:
        raise KeyError(f"no persona {persona!r} — known: {', '.join(sorted(PERSONAS))}")
    return (
        f"{PERSONAS[persona]}\n\n{situation}\n\n"
        "You are talking to a customer support agent. Write only what you would "
        "type, as one short message. Never explain that you are playing a part, "
        "never describe yourself, and never write anything but the message."
    )


__all__ = ["PERSONAS", "brief_for"]
