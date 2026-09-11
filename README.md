# AgentTwin

**Twins the agent's world, not the agent.** The agent under test is real; its
environment is the twin.

Status: **working draft 0.1.0.** Extracted from the reference implementation on
11 September 2026, where it was built alongside the agent it exercises.

---

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
| `world.py`, `loader.py` | the world description and its validation |
| `projection.py` | projecting world state as the systems a run sees |
| `actor.py` | scripted and state-machine actors, including the customer |
| `approver.py`, `desk.py` | the human surface — approval and escalation |
| `scenario.py` | running a scenario against a projected world |
| `record.py` | the run record, and diffing two of them |
| `truth.py` | whether an answer is true of the world it was produced in |
| `omission.py` | what was owed and never said — the failure logs cannot show |
| `perturbation.py` | slowness, channel errors, stale reads, timeline control |

## The contract that matters most

The agent must never be able to see its simulator. In the reference
implementation this is enforced, not intended — an import contract fails the build
if the agent package imports this one.

> *A system that can see its own simulator is a system whose results mean nothing.*

## Status and what is not here yet

**Tests and example worlds still live in the reference implementation**, where
they were written. Most of them are integration tests that drive a real agent
against a world and belong there permanently. The exception is world loading and
validation, which is pure and will move — together with the question of whether
the two example worlds are *fixtures of that agent* or *worked examples of this
format*. That is an open decision, recorded rather than quietly settled.

## Licence

Code under [Apache 2.0](LICENSE). Specification prose under CC BY 4.0.
