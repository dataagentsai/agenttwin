# Agent World Description — the format

**The environment an agent is exercised in.** Draft, `apiVersion: awd/v0`. Not
citable. Schema: [schema/awd.schema.json](schema/awd.schema.json). Reference
loader: [agenttwin/loader.py](agenttwin/loader.py).

---

## The one rule

> **A world cites the agent's specification. It never declares the domain.**

Entities, operations, eligibility policy and obligations are the agent's
specification — an AOAS, defined in
[clean-ai-engineering](https://github.com/dataagentsai/clean-ai-engineering/blob/main/drafts/AOAS.md).
A world file that declares `entities`, `actions` or `policies` is rejected, by
the schema and by the loader.

The rule exists because the alternative already happened. Before this format,
the return window lived in the world file, and the second world was a fork of
the first. The fork drifted: a tool description that contradicted the rule it
described, invariants duplicated by hand. With one statement of each rule there
is nothing to drift from.

## What a world holds

| Section | Holds |
|---|---|
| `spec` | The citation — agent spec id and version — and a path to find it. The id and version are checked; the path is only a locator |
| `seed` | Everything random a run against this world does |
| `fidelity` | What the world is faithful about, and — the more useful half — what it is not |
| `systems` | Which of the spec's external systems each stand-in **projects**, its `resolution` (`mock` · `replay` · `real` · `shadow`), and how it **presents** each operation: its tool description and refusal text |
| `records` | The rows that exist at t₀, for the entities the projected systems own |

Refusal text is the *system's* words and lives here. What the agent offers
instead is the spec's `on_refusal` and does not.

## Composition

The loader reads the world, reads the spec it cites, and composes one world:

- **Entities** — exactly those the projected systems `own` in the spec.
  Approvals and escalations belong to the harness, not to any external system,
  and so are not in the world.
- **Actions** — the spec's operations listed under each projected system's
  `operations`, with their preconditions as the conditions the stand-in
  enforces, their `owed_when` as its obligations, and their effects as what it
  sets.

## What a world cannot enforce

A stand-in can only check what it can see. Two kinds of statement in a spec
are beyond any world, and each is **reported, never dropped**:

| Statement | Why no world can enforce it |
|---|---|
| A condition over another entity's field | A system checks the row it is asked about |
| An effect that writes an operation input (`$name`) | The projection carries no input beyond the key |

The composed world lists them as `unenforced`. A statement a world silently
skipped would read, in every run against it, as a statement that held.

**A comparison with the session is enforced.** The caller presents its session
in the call's `_meta` under `aoas/session` — a binding the agent's transport and
the stand-in share — and a row the caller may not touch is answered exactly as a
row that does not exist. Until 12 September 2026 this row read "a world has no
session", and ownership sat in the unenforced list: that was the reference
agent's critical defect, F-016, and the list was where it was visible.

## Variants

A world for a different store of the same agent cites a different spec. That
spec `extends` its base with an **RFC 7386 JSON Merge Patch** — objects merge,
`null` deletes, lists replace whole. Cited, not invented. The electronics
variant of the reference agent is 70 lines, where its fork was 131.

## Worked example

The reference agent's two worlds, `worlds/clothing.yaml` and
`worlds/electronics.yaml` in
[reference-agent](https://github.com/dataagentsai/reference-agent). They live
with the agent, not here: they cite its spec, they change when it does, and its
suite is what exercises them. This repository's own tests use a lending library
instead, so the format is tested on a domain it was not written for.

## Not yet in the format

- **Actors and perturbations** are still declared in code (`actor.py`,
  `perturbation.py`), not in the world file.
- **`x_binding`** — transport and scopes — is realisation and sits outside the
  format until the binding spec exists.
- **Shadow mode's diff** is named and not yet built. Until it is,
  `fidelity.verified_against` is `null` for every world, which is the honest value.
