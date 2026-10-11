# AgentTwin

AgentTwin tests an AI agent by placing the real agent in a simulated world (its
customers, the systems it calls, their data and their faults) and then checking
what actually changed in that world, not only what the agent said.

**Status: version 0.12.0, released 11 October 2026, a working draft.**

- **Done:** the world format, AWD (Agent World Description: [SPEC.md](SPEC.md),
  [schema/](schema/)). Also done: the loader, and the simulator that presents a
  world as the systems an agent calls, including as an MCP (Model Context
  Protocol) tool server. Scripted customers and approvers, injected faults
  (slowness, lost replies, stale reads), state checks that catch what was owed
  and never done, and the `agenttwin run` command are all in place. 0.10.0 adds
  `agenttwin coverage` and `scaffold --pairwise` (which scenarios a suite is
  missing) and a property test that the stand-in keeps its tool schemas. 0.11.0
  adds attack sources: a scenario's `generate` block can draw its planted
  instructions from PyRIT or AgentDojo as well as the built-in templates, and
  each case's goal is judged by what changed in the world. 0.12.0 adds a
  model-played customer: `actor: {kind: model, via: langwatch, model: …}` hands
  the customer to LangWatch Scenario's user simulator (an optional extra), still
  judged by state, and every result says whether it was model-driven. The suite
  has 377 passing tests, and 14 more for the model-played customer where the
  `langwatch` extra is installed.
- **Planned:** most of the outside tools AgentTwin intends to adopt rather than
  build. Of those listed in [ADOPTION.md](ADOPTION.md), only **Hypothesis**
  (generated edge-case values), **pytest** (the test runner), **allpairspy**
  (pairwise scenario choice), **hypothesis-jsonschema** (a test dependency),
  **PyRIT** (attack text, an optional extra), **AgentDojo** (attack templates
  and injection tasks, vendored as data) and **LangWatch Scenario** (a
  model-played customer, an optional extra, no LangWatch account or upload) are
  adopted so far. LLM graders, a model-driven red team, realistic data
  volume, network faults and record-and-replay are still planned or being
  evaluated.
- The worked example worlds live with the
  [reference agent](https://github.com/dataagentsai/reference-agent). This
  repository's own tests use a lending library, so the format is tested on a
  domain it was not written for.

**Run it** (needs [uv](https://docs.astral.sh/uv/), which fetches Python 3.12+):

```bash
git clone https://github.com/dataagentsai/agenttwin && cd agenttwin
uv run --extra dev pytest      # the format and simulator's own suite, about 10 seconds
```

The model-played customer's own tests need its extra, which is kept out of
`dev` (about 380 MB, and it pins an older `openai`, so uv resolves it apart).
They use a scripted fake for the simulator's model call; one live check against
Groq's free tier runs only when asked:

```bash
uv run --extra langwatch --with pytest --with pytest-asyncio pytest tests/test_actor_langwatch.py
AGENTTWIN_LIVE_ACTOR=1 GROQ_API_KEY=… uv run --extra langwatch --with pytest \
    --with pytest-asyncio pytest tests/test_actor_langwatch.py -k live
```

**Part of a family.** Six public repositories that together specify, build and
test AI agents:

| Repository | Its job |
|---|---|
| [AI Assurance Catalog](https://github.com/dataagentsai/ai-assurance-catalog) (AAC) | what must be **true** of an AI application: test obligations |
| [AI Harness Catalog](https://github.com/dataagentsai/ai-harness-catalog) (AHC) | what must **exist** around the model call: harness capabilities |
| **AgentTwin** (this repository) | what an agent must **face**: a simulated world to test it in |
| [Clean AI Engineering](https://github.com/dataagentsai/clean-ai-engineering) | the specs, the four gates and the build-test-fix cycle that join the rest |
| [Reference Agent](https://github.com/dataagentsai/reference-agent) | the reference implementation: one agent built and tested to all of the above |
| [AgentTwin Lab](https://github.com/dataagentsai/agenttwin-lab) | the lab: a world that keeps running for days, for testing long-running agents |

**How to cite:** cite the release you used. Metadata is in
[CITATION.cff](CITATION.cff); GitHub's "Cite this repository" button renders it
as APA or BibTeX.

---

## Running a suite against an agent

**Twins the agent's world, not the agent.** The agent under test is real; its
environment is the twin. AgentTwin was extracted from the reference
implementation on 11 September 2026, where it was built alongside the agent it
exercises.

```bash
uv run python -m agenttwin run --help           # run a suite against an agent binding
```

## What this is

The third of the three questions in
[Clean AI Engineering](https://github.com/dataagentsai/clean-ai-engineering):

| Part | States |
|---|---|
| [AI Assurance Catalog](https://github.com/dataagentsai/ai-assurance-catalog) | what must be **TRUE** |
| [AI Harness Catalog](https://github.com/dataagentsai/ai-harness-catalog) | what must **EXIST** |
| **AgentTwin** | what must be **FACED** |

A world declares the environment a system is exercised in — entities, systems,
state, actors and timeline — and a scenario asserts over a run against it.

## The reframe that makes it tractable

**"Close to real" is the wrong target.** Perfect realism is unattainable and
unnecessary. What is needed is *fidelity sufficient for the property under test*,
and fidelity is **per-property, not global**.

A cancellation-eligibility test needs `order.status` and its transitions to be
exactly right. It does not need plausible product copy or a faithful ERP. A cost
test needs token counts to be real and is indifferent to order status.

So a world **declares what it is faithful about**, and a scenario asserts only
within that declaration. That is what converts one impossible problem into many
tractable ones.

## Not everything is mocked

| System type | Treatment |
|---|---|
| Data store | Project world state as rows |
| Contract API | Project as responses from the declared contract |
| Tool server | Project as tool results — the cleanest seam available |
| The customer | **Actor**, not a system |
| Human approver | **Actor + a queue** |
| Pure function | **Run it for real.** Mocking adds risk and removes nothing |

## What is here

| Module | Holds |
|---|---|
| `world.py`, `loader.py` | the composed world, and loading one from a world file plus the spec it cites |
| `spec.py` | reading an agent spec, resolving `extends` by RFC 7386 merge patch |
| `projection.py` | projecting world state as the systems a run sees |
| `actor.py` | scripted, state-machine and model-driven actors, including the customer; `MODEL_ACTORS`, the seam a `via:` names |
| `approver.py`, `desk.py` | the human surface — approval and escalation |
| `scenario.py` | running a scenario against a projected world |
| `record.py` | the run record, and diffing two of them |
| `truth.py` | whether an answer is true of the world it was produced in |
| `omission.py` | what was owed and never said — the failure logs cannot show |
| `calendar.py` | the world's calendar: an event fired at a set time, including one that makes a record lie (`record_only`) |
| `monitor.py` | the world as a monitor: four invariants judged as each call lands and each reply goes out; a violation is an incident record (`agenttwin-incident/v0`), de-duplicated by root cause |
| `perturbation.py` | slowness, channel errors, stale reads, timeline control |
| `provider.py` | the provider twin — an OpenAI-compatible model endpoint, scripted or forwarding, with the scenario's provider faults |
| `binding.py` | the one entry point an implementation supplies: `(live, *, wrap, clock, model) -> Subject` |
| `runner.py`, `__main__.py` | `python -m agenttwin run` — a suite against any binding, five statuses, overruns reported |
| `scaffold.py` | `python -m agenttwin scaffold <aoas> --out <repo>` — a new agent's world, scenarios, binding and gates file from its AOAS (Application Operation Agent Spec) alone |
| `coverage.py` | `python -m agenttwin coverage <aoas> <scenarios>` — which pairs of intent × state × persona × perturbation, and which state-machine transitions, a suite covers; `scaffold --pairwise` writes skeletons for the rest (our own transition walker, GraphWalker's technique) |
| `pairwise.py` | adapter, outside the core: binds the pairwise choice to **allpairspy** (`pairwise` extra) |
| `actor_langwatch.py` | adapter, outside the core: a model-played customer from **LangWatch Scenario**'s user simulator (`langwatch` extra, `actor: {kind: model, via: langwatch}`); nothing uploaded |
| `promises.py` | adapter, test-only: property tests from each tool's `inputSchema` (**hypothesis-jsonschema**) that every answer meets its `outputSchema` and the AOAS's row, and that a refusal is a result |

## The contract that matters most

The agent must never be able to see its simulator. In the reference
implementation this is enforced, not intended — an import contract fails the build
if the agent package imports this one.

> *A system that can see its own simulator is a system whose results mean nothing.*

## The format

**[SPEC.md](SPEC.md)** — a world cites the agent's specification and never
declares the domain. Schema in [schema/](schema/), loader tests in [tests/](tests/).

## Status and what is not here yet

**The example worlds live with the reference implementation**, and that is now
a decision rather than an open question: they cite that agent's spec, change
when it does, and are exercised by its suite. This repository's own tests use a
lending library, so the format is tested on a domain it was not written for.

Most of the integration tests that drive a real agent against a world also stay
there permanently.

## Licence

Code under [Apache 2.0](LICENSE). Specification prose under [CC BY 4.0](LICENSE-SPEC.txt).
