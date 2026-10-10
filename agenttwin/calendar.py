"""The world's calendar: what happens to it at set times (T-078).

A world that keeps running has incidents on a calendar as well as at random:
a carrier strike on day 4, a price change on day 9. `World.calendar` declares
them, as data; `fire` makes one happen to a live world. **When** each fires is
the runner's business — a scheduler adopted for the job (SimPy in the lab)
calls `fire` at `event.at_s`; this module never keeps time.

`fire` is deterministic: the rows an event picks are chosen with the world's
seed and the event's id, so the same world and seed hit the same rows.
"""

from __future__ import annotations

import random

from agenttwin.projection import IncoherentWorld, Live
from agenttwin.world import CalendarEvent


def matching(live: Live, event: CalendarEvent) -> tuple[str, ...]:
    """The keys the event's `where` selects now, in key order."""
    rows = live.rows.get(event.entity, {})
    return tuple(sorted(k for k, row in rows.items() if all(c.holds(row) for c in event.where)))


def fire(live: Live, event: CalendarEvent, *, seed: int | None = None) -> tuple[str, ...]:
    """Make the event happen; return the keys it changed.

    A `record_only` event changes the record and leaves the truth where it was
    (`Live.diverged`), so the record now says something that did not happen.
    Any other event is a real change, and settles a field the record had wrong.
    """
    keys = matching(live, event)
    if event.pick is not None and event.pick < len(keys):
        rng = random.Random(f"{live.world.seed if seed is None else seed}:{event.id}")
        keys = tuple(sorted(rng.sample(keys, event.pick)))
    entity = live.world.entities[event.entity]
    for key in keys:
        row = live.rows[event.entity][key]
        for name, value in event.sets.items():
            if event.record_only:
                live.diverged.setdefault((event.entity, key, name), (row.get(name), event.id))
            else:
                live.diverged.pop((event.entity, key, name), None)
            row[name] = value
        broken = entity.violations(row)
        if broken:
            raise IncoherentWorld(
                f"calendar event {event.id!r} put {event.entity} {key} in a state it declared "
                f"impossible — {broken[0].name!r}: {broken[0].because}"
            )
    return keys


__all__ = ["fire", "matching"]
