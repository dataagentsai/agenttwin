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
  and so are not in the world. That does not stop a projected far end checking
  one (AHC-0057 says the executing side loads the approval and confirms it
  matches): the approval is **carried by reference** — its id in the call's
  `_meta` under `aoas/approval` — and **loaded through a hook the binding
  supplies**, which reads the harness's approval records. The world holds no
  approval rows; the far end still does the check. See
  [The far end's contract](#the-far-ends-contract).
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

## The far end's contract

What a builder binding an agent to a projected system needs, and — until
generation run 2 found each by probing (its NOTES §1, §2) — could not read
anywhere but the modules.

**The call's `_meta` keys.** `aoas/session` (the caller's session or a
credential for it), `aoas/idempotency-key` (a string), `aoas/approval` (an
approval's id). All three are optional on the wire; what a missing one means is
the hook's decision.

**The `authorise` hook.** `project(live, authorise=hook)` takes

    async def hook(operation: str, arguments: dict, meta: dict) -> Mapping | None

- It **must be `async def`**. `project` refuses a plain function with a
  `TypeError` naming the hook, and a callable that returns something not
  awaitable is refused on the call with a readable error.
- `arguments` is what the caller sent: the entity key and the declared inputs.
  `meta` is the call's `_meta`, or `{}`.
- It returns **the session the call acts under**: a mapping with every field
  the spec's ownership preconditions compare (`equals_session: customer_id`
  needs `customer_id`). Extra keys are ignored. `None` is no session: owned rows
  are invisible and listings empty. Anything else is refused as a contract
  error.
- It **raises to refuse**. A `ToolError` (from `mcp`) — `NotAuthorised` is one —
  reaches the caller with its reason; any other exception reads as
  "Error executing tool <name>", indistinguishable from a crash.
- Without a hook, the session is whatever `aoas/session` asserts.

**Scope and authority are the hook's duty.** The projection enforces
preconditions (`allowed_when`) and ownership (`session_when`) and nothing else.
It *publishes* each operation's `required_scope` in the tool's `_meta` and
carries `agent_when` on each composed action, but checks neither — which
credential carries which scope, and where approvals are kept, are binding facts
a world cannot know. A hook that verifies a credential and stops there projects
a far end that lets any verified caller refund anything: generation run 2's
probe refunded an order that needed a person with no approval at all. AHC-0057
puts the check where the action executes, so it belongs in the hook.

`authority_check(live, scopes=..., approval=...)` is the ready-made version, for
a hook to call after verifying the credential:

    await check(operation, arguments, meta, session=session, granted=scopes_held)

It requires an approval when the caller lacks the operation's scope, or when any
`agent_when` clause is false **on the live row**; a call that needs one must name
it under `aoas/approval`, and the binding's `approval` hook —
`async (approval_id, operation, arguments, meta)` — loads it from the harness's
records and raises unless it matches (granted, unexpired, same operation,
arguments and customer, not self-approved). With no `approval` hook, every call
that needs one is refused. A row that is missing or not the caller's is let
through to be answered as unknown, so the check never reveals what a stranger's
row is worth. Using it is optional; skipping it is a decision the binding
should record.

**Answers.** Every case, and the channel it arrives on:

| Case | Channel | Structured result |
|---|---|---|
| read | result | `{found: true, ...row}` |
| listing | result | `{found: true, items: [...]}` — the rows the session may see |
| write, allowed | result | `{allowed: true, reason: "allowed", ...row}` |
| write, a precondition fails | result | `{allowed: false, reason, ...row}` |
| unknown row, or not the caller's — `unknown_record="result"` | result | `{found: false, allowed: false, reason: "no <entity> <key>"}` |
| the same — `unknown_record="raise"` (the default) | protocol error (`isError`) | — |
| the hook refused | protocol error (`isError`) | — |

`unknown_record="result"` is the **documented behaviour to build against**: the
AOAS lists `unknown_record` as a failure mode a caller reads, and a protocol
error cannot be told from the server crashing. `"raise"` stays the default only
because the reference agent's suite reads the protocol error today. In both
modes a row the caller may not touch is answered exactly as a missing one —
same channel, same text but for the key it was asked about.

## The runner's contract with an implementation

`run_file(path, subject=...)` drives a declared scenario against a `Subject`
the binding builds. Generation run 2 found every one of these shapes by calling
with spies (NOTES §8); each is now a `Protocol` in `agenttwin.subject`,
`agenttwin.approver` or `agenttwin.desk`, exported from `agenttwin`.

| Seam | Shape |
|---|---|
| `Subject.say` (`Say`) | `async (text, customer_id, conversation) -> (reply, conversation)`. `conversation` is `None` on the first turn, then whatever the last call returned |
| `Subject.reviewer` / `Subject.colleague` (`OffstageFactory`) | `(decision, by, delay_s) -> Offstage`, called once per run, synchronously. `decision` is the scenario's `approver.decides` (`grant` · `refuse` · `never` · `grant-twice`) or `desk.resolves` (`handled` · `never`), as written. `None` means the implementation has none, and a scenario needing one raises `Unrunnable` |
| `Subject.opens` | `async (customer_id) -> str` shown on opening, or `None` |
| `Offstage` | `await review(at=moment)` between turns, `moment` in scenario seconds |
| `Approver(store, decide)` / `Desk(store, close)` | `store` is a `Queue`: `async pending()` returning items with `.id` and `.created_at` (scenario seconds). An item is acted on once `moment >= created_at + delay_s` |
| `decide` (`Decide`) | `await decide(store, approval_id, *, granted, by, now)`; raise to reject — recorded as `refused` with the exception's text |
| `close` (`Close`) | `await close(store, escalation_id, *, outcome, by, note, now)`, `outcome` one of `resolved` · `agent_could_have` · `misrouted`; raise to reject |

### One entry point, and a model every implementation can reach

A `Subject` is what a scenario needs mid-run. **How a runner that has never
seen the implementation gets one** is `agenttwin.Binding`, named in the
implementation's repository as `module:attribute`:

    open_subject(live, *, wrap, clock, model) -> async context manager yielding a Subject

Three obligations: tools come from `project(live, ..., wrap=wrap)`, with `wrap`
forwarded unread; **every model call goes to `model.base_url`**; `clock` is the
only clock. Generation run 2 wrote its own binding with its own signature, so
no runner could drive it with another agent's scenarios — this is that
signature, fixed.

`model` is the **provider twin**: an OpenAI-compatible chat-completions server
started per run. Scripted, it serves the scenario's `model:` block, one answer
per call; live (`--live`), it forwards to a real provider. Either way it serves
the scenario's `provider_*` perturbations as the wire carries them — a throttle
is a 429 with `retry-after`, an outage a 503, malformed output a 200 with no
choice — so the implementation's own provider adapter is on the path.

    model:
      - calls: [{get_order: {id: AB-10003}}]     # operations the spec declares
      - says: That order was delivered.
        times: 3

**An absent `model:` block means the model must not be called.** A call past
the last answer is an **overrun**, answered 503 and counted; the runner reports
it beside the status, because a scenario that passed on an unscripted model
failure passed for a reason nobody wrote down (reference-agent F-060).

`python -m agenttwin run --binding module:attr scenarios/` runs a suite and
gives each scenario one of five statuses — `passed`, `failed`, `unrunnable` (a
capability the implementation lacks), `crashed` (usually the binding),
`invalid` (the file) — because those go to different people.

**Every scenario also asks one thing it does not declare**: *every reply is
true of the records it names* — each record a reply names, judged against the
world when the reply was read, allowing what the record held when the turn
began (stale is not invented). A scenario's own `truthful` check asks about the
one row its author thought of; a negative control that appended *"Order
AB-10002 has been cancelled and AB-10003 refunded"* to every reply of the
reference passed 23 of 36 scenarios without this, and 2 with it. A claim that
names no record (*"that order"*) stays out of reach.

**A `generate` block is many runs.** `run_generated(path, subject_for=...)`
runs the scenario once per case from `attack_cases`, each in a fresh world with
its payload `plant`ed, building the subject per case through
`subject_for(live, timeline, clock)`. `run_file` runs one: the case already
planted in the `live` it is handed. Handed a world with no generated payload
planted, it does not drive the subject and returns one failing outcome, *the
generated cases ran*. Until 26 September 2026 it ran the scenario once with
nothing planted and let it pass — generation run 2 saw twelve declared
injection cases claim AAC-0058 on zero attacks.

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
- **The provider twin speaks chat-completions only.** An implementation on
  another wire (Anthropic Messages, a streaming client) cannot be driven by it
  yet; streaming is refused with a 400 rather than half-served.
- **Shadow mode's diff** is named and not yet built. Until it is,
  `fidelity.verified_against` is `null` for every world, which is the honest value.
- **Time passes within a turn only where a call is declared `slow`.**
  `step_seconds` moves the clock between turns and `step_days` moves the world
  between them. Inside a turn the only thing that moves the clock is a `slow`
  perturbation, which advances the shared `Clock` by its `seconds` on the named
  call (given the clock `perturbed` was built with). This note used to say a
  within-turn gap — the reference agent's freshness window (AHC-0107) — could
  not be reached from a scenario at all. Generation run 2 (NOTES §8) reached it:
  the harness **stamps a read with when it was asked, not when it was
  answered**, so a `slow` on the read moves the clock *past* the stamp and the
  belief is already that old when the next call is planned. Stamped with the
  answer's arrival, the same `slow` moves the clock *before* the stamp, the
  belief is always fresh, and the window never opens — which is why the
  reference could not reach it. The fix is harness-side and needs nothing new
  here; a harness must stamp at the ask for the scenario to be expressible.
  Still out of reach: ageing a belief *without* a slow call, e.g. a model call
  that takes declared time, since the model channel does not move the clock.
