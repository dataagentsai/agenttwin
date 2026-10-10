# What AgentTwin adopts, and what it keeps

**Informative, not part of the format.** [SPEC.md](SPEC.md) names no product, and
must not: a world file is the same whichever tools run it. This page is where the
products are named, the way a stack file names them for a harness. Written
2026-10-05; rows added 2026-10-11 from the landscape research (`DataAgents.ai/70-agenttwin-landscape.md`). Open work for every row lives in
[clean-ai-engineering/TODO.md](https://github.com/dataagentsai/clean-ai-engineering/blob/main/TODO.md)
(section *S4 · AgentTwin* and the *Open source* register), never here.

---

## The position

AgentTwin is an **assembly**. It adopts the best available tool for every job a
neighbouring tool already does well, and builds only what no tool does. SPEC.md's
*"What this is not, and what to use instead"* says this and names none; the table
below names them.

**What AgentTwin keeps as its own, because nothing else does it:**

1. **The world format** (AWD) and the loader's refusals.
2. **The projection**: a stand-in that holds state, enforces the domain's own
   preconditions and refuses the way the real system would.
3. **The four state oracles**, `owed` among them. Only an oracle that reads state
   can report *a refund was owed and never issued*: nothing changed, nothing untrue
   was said, and a judge reading the transcript would pass it.
4. **The mode switch**: `mock | replay | real | shadow`.

## The one constraint

**The format and the engine never import an adopted tool.** `actor.py` says why:
*a simulator that imported one would simulate one stack.* Every adopted tool sits
behind a declared seam, with its adapter outside the core, the way AAC's adapters
for promptfoo and DeepEval sit outside its normative text. What is bound by default
is a **default binding**, the counterpart of `open-stack.yaml` for a harness
(T-098).

## The decision table

**State** reads: ✅ adopted · a T-item that adopts it · *proposed* (added 5 Oct, not
yet in the register's Adopt table until its item closes).

| Seam | Adopt | What it brings | Stays AgentTwin's | Verdict | State |
|---|---|---|---|---|---|
| Model-driven customer (`ModelActor`) | **LangWatch Scenario**'s user simulator. On Azure, `azure-ai-evaluation`'s Simulator is the managed equivalent | A believable customer with a persona who keeps asking until answered | The persona catalogue, as data; the rule that one model-driven actor makes the world model-driven | Pursue | T-039 |
| Soft graders (tone, completeness) | **DeepEval** or **Inspect**, through the `eval_task` port | LLM judges with a rubric | The state oracles | Pursue | T-040 (open choice) |
| Adversarial cases | **promptfoo red-team** first (promptfoo is already adopted, T-046), **PyRIT** for indirect-injection orchestration (the planted-note scenarios). On Azure, the AI Red Teaming Agent wraps PyRIT | Hundreds of generated attacks, rewritten and retried | Running them against a world that refuses correctly, and judging from state | Pursue | T-099, proposed |
| Data volume and long tails | **Faker** now; **SDV** once there is real data to learn from | Skew, dirty rows, volume. SPEC.md admits a declared world is *small and clean* | Which rows are owed; the declared boundaries | Pursue | T-100, proposed |
| Boundary exploration | **Hypothesis** | Edge values beyond the declared ones | The conditions | Pursue | ✅ 14 Sep |
| "Days have passed", and time within a turn | **time-machine** for the agent's clock; back-dated seed rows for a real store | Time travel without a sleep | The world's timeline | Pursue | with T-023, T-051 |
| Network faults in `real` mode | **Toxiproxy** in front of the real store's MCP server | `slow` and `lost_reply` against a real system | Faults in the projected world; `stale_read`, which is semantic, not a network fault | Pursue | T-101, proposed |
| A real system per run | Per-run namespaces today (`AB-10003~r7`); **Testcontainers** if CI must start its own | A clean real store per test | The seed and the namespace | Pursue the namespace; evaluate Testcontainers | T-017 ✅ · T-101 |
| Record and replay | **VCR.py / pytest-recording** | HTTP cassettes | Marking a stale fixture as stale | Pursue | T-027 |
| Reliability measure | τ²-bench's **pass^k** and its `verify` actor strategy | A proven measure of consistency | Everything else | Pursue the idea; drop τ-bench's 165 tasks as a dependency (prose policy, fixed domains) | T-007 |
| Scenario runner | **pytest** | — | — | Pursue. Drop Inspect *as a runner*: a second runner. (Inspect stays a candidate for graders, T-040) | ✅ |
| The stand-in's tool surface | Schemas **generated from the real system's own MCP server or OpenAPI spec**, both sides contract-tested against them | The shape cannot drift | The behaviour, which shadow mode checks | Pursue | T-102, proposed |
| A day that keeps running (the lab, T-078) | **SimPy** (discrete-event simulation) for the schedule: arrivals, shifts, an incident calendar | A tested event engine | The world, its ledger and its obligations | Pursue (grant slice) | proposed 11 Oct |
| Which scenarios to run | **allpairspy** (pairwise) over intent × state × persona; our own walker over the AOAS state machines (GraphWalker's technique, not its Java) | A small set covering every pair | The state machines and what counts as a path | Pursue after the grant | T-109 |
| The projection keeps its own promises | **hypothesis-jsonschema**: property tests that every answer matches the tool's `outputSchema` | Generated inputs, shrinking | The schemas (from the AOAS) | Pursue (test dependency) | T-109 |
| Tools the spec does not cover | **AWS Strands ToolSimulator** (an LLM plays the tool, with shared state), for read-only, low-stakes systems only. **Never** for a system whose rules matter: an LLM can be talked into allowing what the real system refuses | The long tail without hand-written stand-ins | Every system the spec governs | Evaluate | T-110 |
| Attack corpus and dual control | **AgentDojo**'s injection tasks (injections in untrusted data, judged on state) as world data; **τ²-bench**'s dual control (a customer with tools) as an actor kind | Proven attacks; a known benchmark's idea | Judging by the world, not the words | Evaluate | T-111 |
| An agent that does not use MCP | **DNS interception** (Veris's approach): the test container's resolver sends the real API's domain to a local **mitmproxy** with its CA trusted; a per-API translator turns each REST call into a world operation | Any agent, unmodified | The translator, so the spec's rules still decide (Veris has an LLM play the API instead) | Later | T-107 |
| An agent that queries a database directly | A **real PostgreSQL per run** (template database copy, or **Testcontainers** in CI), the real app's own migrations, **Toxiproxy** for faults, **pgaudit** and **wal2json** (or a before/after diff) to see what it read and changed | A real database, exact behaviour | The entity-to-table mapping, the seeding, and the oracles reading SQL rows. A raw database refuses nothing, so the oracles catch violations afterwards | Later | T-108 |
| Sharing worlds with the RL community | **OpenEnv** (Meta + Hugging Face, Gymnasium-style API) as an export of an AgentTwin world | Reach | The world and its oracles | Later | T-112 |

## Does every agent need a real store?

**No.** The stand-in is enough for almost every test, because it holds state and
enforces the declared rules. Per new agent you write **data, not code**: a world
file citing the agent's AOAS. The electronics variant of the reference agent is 70
lines.

A canned mock that returns fixed answers (WireMock- or Prism-style) is **not**
enough: it would cancel a shipped order, and the agent's tests would pass while
proving nothing.

The real system has one job, **checking that the stand-in is still faithful**, in
`shadow` mode. It is borrowed, never built:

| Situation | The real side | Examples |
|---|---|---|
| The vendor offers a sandbox | Use it | Stripe test mode, Shopify development store, Salesforce sandbox, ServiceNow personal developer instance |
| An open-source equivalent exists | Run it, seeded per run | Saleor (chosen, T-017) or Medusa for shops; Odoo or ERPNext for ERP; QloApps for hotel booking (cycle 4, fit unchecked) |
| Neither exists | Shadow **reads only** against staging; never shadow a write | Order lookup against a staging API |

**Rule of thumb:** every test runs against the stand-in; shadow runs nightly or
weekly against a borrowed real system; a real system you build yourself, never.
