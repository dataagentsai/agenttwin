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
| An effect that writes an input the operation does not declare | There is no value to write |

The composed world lists them as `unenforced`. A statement a world silently
skipped would read, in every run against it, as a statement that held.

**A declared input is carried.** A projected tool takes its entity's key and
whatever else the operation declares, so an effect written from an input —
`{address: $address}` — lands. Until 12 September 2026 the key was the only
input any projected tool took: `change_address` accepted an order, changed
nothing, and the statement sat in the unenforced list. Both reference worlds now
have an empty one.

**A comparison with the session is enforced.** The caller presents its session
in the call's `_meta` under `aoas/session` — a binding the agent's transport and
the stand-in share — and a row the caller may not touch is answered exactly as a
row that does not exist. Until 12 September 2026 this row read "a world has no
session", and ownership sat in the unenforced list: that was the reference
agent's critical defect, F-016, and the list was where it was visible.

**The authority an operation requires is not the world's.** Which operations are
privileged is the agent specification's business and what the privilege is
*called* belongs to whatever issues credentials, so `project` takes the scope
names from the caller. Worlds carried them in an `x_binding` block until
12 September 2026, when the binding spec existed to hold them; a world handed
none projects an ungated surface, which is a legitimate thing to simulate.

**A repeated write is recognised here.** A caller may present an idempotency key
in `_meta` under `aoas/idempotency-key`; a write carrying a key the stand-in has
answered before gets that same answer, and its effect does not land twice. This
is the far end a harness-side ledger cannot reach: when an effect lands and its
reply is lost, only the system that applied it can tell the retry from a second
request (F-017).

## Declared, never inferred — and what that buys

A world is told what is true; it infers nothing. Every question a data generator
would have to guess at is answered in the specification, and the loader **refuses
a world that disagrees with it**:

| The question | Where it is answered | What happens when a world disagrees |
|---|---|---|
| What joins to what | `ref: customer.id` on the field, flattened by `World.ontology()` | a row pointing at a record that does not exist fails at load, not when a scenario asks |
| Which values a column may hold | `type: enum` with `values`, or `of:` a state machine's states | a seeded value outside the set fails at load, naming the set it violated |
| Which of those values *matter* | the conditions' own bounds — `at_most: 30` says 0, 30 and 31 are the interesting ones | nothing: the generator asks the world what to vary rather than being told |
| Which rows could not exist at all | `invariants`, each with its `because` | the row is refused, quoting the rule and the reason |

The third row is the one worth dwelling on. **The declaration is not
documentation of a test space — it is the test space.** A condition carries its
own boundaries, so cases are derived from the rules rather than sampled around
them, and a world declaring a sixty-day return window is tested at sixty and
sixty-one without anybody editing a fixture.

The fourth row is what separates *well-typed* from *possible*. When invariants
arrived, the reference's golden set went from 29 cases to **26**: smaller, and
better, because three of them described situations that cannot occur, and a
verdict about an impossible situation is worse than no verdict.

**Where this is weak, stated plainly.** A declared world is small and clean. Real
estates have volume, skew, dirty long tails and rows that exist because of a
migration nobody remembers. `fidelity.not_faithful_about` is honesty about that
rather than a solution to it, and `shadow` — call the real system, serve the
mock, diff the two — is the designed answer and is not built.

## What this is not, and what to use instead

The surrounding tools solve neighbouring problems well, and a world is not an
attempt to replace them:

| Want | Use |
|---|---|
| A believable customer with a persona and a temper | a simulated-user framework. AgentTwin's actors are deliberately thin, and a model-driven one is a declared seam rather than a built thing |
| Ten thousand rows with real distributions | a relational synthetic-data generator. Those learn from real data; a world is what you have **before** there is real data to learn from, which is the situation every new agent starts in |
| Hundreds of injection or jailbreak cases | an adversarial corpus. Generating them is not a world's job; *running* them against a world that refuses correctly is |
| Tracing, datasets, prompt history | an observability platform |

**What a world is for, that none of those do: a stand-in that enforces the
domain's own preconditions and refuses the way the real system would, and
oracles that read state rather than asking a model.** That is why the omission
oracle can report *a refund was owed and never issued* — nothing changed,
nothing untrue was said, every bound was respected, and a judge scoring the
transcript would pass it. Only a world that declared `owed_when` can see it.

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
- **Actors, again**: the model provider is an external system a run depends on
  and a world cannot yet perturb it.
- **Shadow mode's diff** is named and not yet built. Until it is,
  `fidelity.verified_against` is `null` for every world, which is the honest value.
