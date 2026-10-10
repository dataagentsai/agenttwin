# AgentTwin

AgentTwin tests an AI agent by placing the real agent in a simulated world (its
customers, the systems it calls, their data and their faults) and then checking
what actually changed in that world, not only what the agent said.

**Status: version 0.7.0, released 10 October 2026, a working draft.**

- **Done:** the world format, AWD (Agent World Description: [SPEC.md](SPEC.md),
  [schema/](schema/)). Also done: the loader, and the simulator that presents a
  world as the systems an agent calls, including as an MCP (Model Context
  Protocol) tool server. Scripted customers and approvers, injected faults
  (slowness, lost replies, stale reads), state checks that catch what was owed
  and never done, and the `agenttwin run` command are all in place. The suite
  has 210 passing tests.
- **Planned:** most of the outside tools AgentTwin intends to adopt rather than
  build. Of those listed in [ADOPTION.md](ADOPTION.md), only **Hypothesis**
  (generated edge-case values) and **pytest** (the test runner) are adopted so
  far. A model-played customer, LLM graders, generated attacks, realistic data
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
| `actor.py` | scripted and state-machine actors, including the customer |
| `approver.py`, `desk.py` | the human surface — approval and escalation |
| `scenario.py` | running a scenario against a projected world |
| `record.py` | the run record, and diffing two of them |
| `truth.py` | whether an answer is true of the world it was produced in |
| `omission.py` | what was owed and never said — the failure logs cannot show |
| `perturbation.py` | slowness, channel errors, stale reads, timeline control |
| `provider.py` | the provider twin — an OpenAI-compatible model endpoint, scripted or forwarding, with the scenario's provider faults |
| `binding.py` | the one entry point an implementation supplies: `(live, *, wrap, clock, model) -> Subject` |
| `runner.py`, `__main__.py` | `python -m agenttwin run` — a suite against any binding, five statuses, overruns reported |
| `scaffold.py` | `python -m agenttwin scaffold <aoas> --out <repo>` — a new agent's world, scenarios, binding and gates file from its AOAS (Application Operation Agent Spec) alone |

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
