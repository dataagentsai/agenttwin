# Assessing τ²-bench's airline agent with AgentTwin

**The question.** Can AgentTwin assess an AI agent that its authors did not
build, using only that agent's published policy and its tool interfaces?

**The target.** The airline domain of
[τ²-bench](https://github.com/sierra-research/tau2-bench) (MIT), pinned at
commit `4ce7c03`. τ² gives a policy written in prose, 14 tools, a database of
500 users, 2,000 reservations and 9,000 flight-dates, and 50 tasks. Each
task has expected ("gold") actions and τ²'s own evaluator.

**The method, in one line.** We wrote an AOAS (the agent's behaviour
contract) from the policy and tool signatures alone. We built an AgentTwin
world from τ²'s database and served τ²'s tools over MCP through a stand-in.
Then we judged the same trajectories twice — once by τ²'s evaluator, once by
AgentTwin's state oracles — and explained every place the two disagree.

## Results

### Offline (no model calls)

We ran 467 trajectories over all 50 tasks. Each task's gold actions were
mutated in up to 13 ways, every mutation wrong on purpose. A catch is counted
only when AgentTwin fails a mutation **for the rule that mutation broke**. A
failure caused by something else in the trajectory is not counted (see S-17).

| What the trajectory does wrong | Trajectories | τ² fails it | AgentTwin fails it, for that reason |
|---|---|---|---|
| nothing (gold) | 50 | 0 | 0 (but 2 false alarms: S-16, S-17) |
| breaks a rule that state can show¹ | 188 | 188 | **188** |
| reads another customer's reservation | 50 | **0** | **50** |
| breaks a rule state cannot show² | 89 | 89 | 0 |
| leaves the customer's request undone | 26 | 26 | 0 (by design) |
| books twice | 2 | 2 | 0 (gap G-1) |
| attempts a write τ²'s own code refuses | 42 | 0 | 0 |
| repeats an update that changes nothing | 20 | 0 | 0 |

¹ Each of these mutations adds one wrong write: cancelling with no ground
(30), cancelling after a segment has flown (22), cancelling another
customer's reservation (50), a certificate for a customer with no
compensable disruption (40), mispriced bags (38), a flight change that moves
the destination (4), and cancelling the same reservation twice (4). For every
one, AgentTwin's incident names the rule it broke — for example
`cancel_reservation:EHGLP3:precondition:cancellation_basis`. τ²'s verdict is
a database mismatch.

² Changing the flights of a basic economy reservation (20), removing a bag
(33), cancelling an insured reservation for a reason the insurance does not
cover (26), and a certificate nobody asked for, or of the wrong amount, for
a customer who could be compensated for something (10).

**Fidelity.** The stand-in answered 1,512 calls. Every answer was identical,
byte for byte, to τ²'s own environment answering the same call. The
behaviour is τ²'s own code (see *How it is built*), so this check covers what
AgentTwin put in the path: the MCP surface, the schemas, argument handling
and error wording. It does not test the behaviour itself.

The full per-task table (τ² / AgentTwin for every task and mutation) is in
[results/offline.md](results/offline.md). Every trajectory, with both
verdicts and AgentTwin's incidents, is in
[results/offline.json](results/offline.json).

### Every disagreement, by cause

| Cause | Direction | Count | Why |
|---|---|---|---|
| AgentTwin catches what τ² can't | AgentTwin only | 50 | Another customer's reservation was read and shown. Nothing changed, so a database comparison cannot see it |
| By design | τ² only | 25 | The customer's request was left undone. AgentTwin judged the policy, not the request: no rule was broken, and no scenario stated what this customer wanted. τ² compares against the expected database. *(26 trajectories; in the 26th AgentTwin did fail, but only for the S-16 false alarm, so it is not a catch)* |
| Spec gap S-12 | τ² only | 48 | The rule is about a *change* (new flights vs old; fewer bags than before). The condition vocabulary sees one row at one time |
| Spec gap S-3 | τ² only | 25 | Insurance covers a cancellation only for a covered reason, and no record holds the reason |
| Spec gap S-6 | τ² only | 10 | A certificate's amount, and whether the customer asked, are beyond the vocabulary |
| AgentTwin gap G-1 | τ² only | 2 | A second booking names no existing row, so `at-most-once` cannot key it |
| Spec gap S-16 (ambiguous policy) | AgentTwin only | 3 | Task 18's gold downgrades business to economy and keeps 3 free bags where the new allowance is 2. The policy never says what a downgrade does to bags |
| Spec gap S-17 (our reading was wrong) | AgentTwin only | 3 | Task 37's gold pays a flight change with a second credit card. The "at most one credit card" cap is stated for booking, and the policy allows a change to be paid with any card in the profile |

Every disagreement has a stated cause. `offline.py` fails the run on any it
cannot explain, so causes cannot be added after the fact.

### Live (τ²'s own agent, small models on Groq's free tier)

Setup:

- **Agent under test:** τ²'s own `llm_agent`, on `gpt-oss-20b`.
- **Customer:** τ²'s `user_simulator`, on `qwen3.8-27b`.
- **Runner:** τ²'s own orchestrator.

We changed one thing: every tool call went over MCP to the AgentTwin
stand-in.

| Task | Ended | τ² | AgentTwin | Model calls | Tokens |
|---|---|---|---|---|---|
| 1 (cancel a reservation the policy does not allow) | customer stopped | 1.0 | pass | 16 | 44,792 |
| 0 | **stopped: rate limit** after 3 model calls | — | — | 3 | 4,201 |

**Task 1.** The agent never found the customer's reservation: it opened
three of five and gave up. It did not cancel anything, so both judges pass.
Gold for a refusal task is "change nothing", which a lost agent also
satisfies. Neither judge measures helpfulness here. The answers the agent
received live were identical to the deterministic replay AgentTwin judged.

**Task 0.** Groq refused on its per-minute token limit (8,000 for this
model; it counted 5,230 used plus 3,308 requested). The pacer counts only
completed usage, so it under-estimated. The run stopped at that first
refusal, as planned, and recorded it in
[results/live-stopped.json](results/live-stopped.json). The daily quota was
not spent on retries.

**Status:** the live part is a demonstration that the plumbing works end to
end with an agent we did not build. It is not a measurement.

## How it is built

| Piece | What | Built or adopted |
|---|---|---|
| [tau2-airline.aoas.yaml](tau2-airline.aoas.yaml) | The behaviour contract, written from [the policy](vendor/tau2/policy.md) and the tool signatures and docstrings only. It passes `validate-aoas.js` | written |
| [world.yaml](world.yaml) + [convert.py](tau2_airline/convert.py) | τ²'s database as the AOAS's rows, derived fields included. 12,006 rows, loaded through `agenttwin.with_records`, which checks enums, invariants and joins | written (deterministic converter; rows are not committed) |
| [standin.py](tau2_airline/standin.py) (over `tau2_common.standin`) | τ²'s 14 tools over MCP, with τ²'s signatures and τ²'s words. Behaviour is τ²'s own tool code (`resolution: real`). After every write, `Live` is re-derived from the database, and the write goes in `Live.effects` | adopted (τ²'s code) behind an AgentTwin surface |
| [judge.py](tau2_airline/judge.py) (over `tau2_common.judge`) | AgentTwin: `Monitor` with the default watches plus `WithinSpec`, as the customer the task names. τ²: `EnvironmentEvaluator` × `CommunicateEvaluator` over the task's `reward_basis`, unchanged | both adopted, unchanged |
| [mutate.py](tau2_airline/mutate.py), [offline.py](tau2_airline/offline.py) | Mutations, and this domain's causes; `tau2_common.offline` judges and tabulates | written |
| [live.py](tau2_airline/live.py) | τ²'s orchestrator, agent and user simulator; tool calls swapped to the stand-in; per-model pacing; no retries | τ²'s, one swap |

Since 13 Oct 2026 the stand-in, the two judges and the offline driver are
shared with [tau2-retail](../tau2-retail/) and [tau2-telecom](../tau2-telecom/)
in [tau2-common](../tau2-common/). After the move, the full offline run gave
the same verdicts and incidents on all 467 trajectories as before it.

What AgentTwin knows when it judges: the AOAS, the world, and which user id
the customer gives (the session). It never sees the expected actions or
τ²'s evaluator.

**One general change to AgentTwin** came out of this work, with tests in
`tests/test_monitor.py`:

- **The gap.** The monitor could not judge a write that a permissive far end
  let through. A watch saw only the world *after* the call, and the
  projection refuses rule-breaking writes itself, so the question had never
  come up.
- **The change.** `ActionSeen` now carries the record as the call found it,
  the call's `_meta`, and whether the call landed. "Landed" means a row
  changed, or the far end entered an effect: a second refund changes no row
  the spec holds.
- **The new watch.** An opt-in `WithinSpec` watch judges a landed write
  against the spec's preconditions (on the row as found), ownership,
  `agent_when` and the entity's invariants.

τ²'s policy says of its own system "The API does not check these for the
agent", which is exactly the case this watch covers.

## Running it

Needs [uv](https://docs.astral.sh/uv/) and git. `run.sh` fetches τ² at the
pinned commit ([../tau2-common/fetch_tau2.sh](../tau2-common/fetch_tau2.sh):
a sparse checkout of the source and the three domains' data, about 40 MB)
into `~/.cache/agenttwin/tau2-bench`, outside this repository, and runs
Python with AgentTwin, the shared `tau2_common` and τ² installed from that
checkout.

```bash
./run.sh -m pytest tests                 # 33 tests, about 20 seconds
./run.sh -m tau2_airline.offline         # all 50 tasks, about 20 minutes on a laptop
./run.sh -m tau2_airline.offline 0 7     # some tasks
./run.sh -m tau2_airline.offline --report  # re-tabulate results/offline.json
GROQ_API_KEY=… ./run.sh -m tau2_airline.live 1      # live; stops at the first rate limit
```

The AOAS check needs
[clean-ai-engineering](https://github.com/dataagentsai/clean-ai-engineering)
next to this repository:
`node tools/validate-aoas.js <this dir>/tau2-airline.aoas.yaml`.

## Findings

Each S-n is marked where it bites in the AOAS's comments.

### About the policy, and what a state-based spec can carry

| Id | Finding | Kind |
|---|---|---|
| S-1 | 13 derived fields, written in code, carry rules the condition vocabulary cannot state: another entity (a flight's status on a segment's date), an OR (four cancellation grounds), counts (passengers, cards by kind) and arithmetic (bag allowance × passengers). Each one puts the rule in code, not in the vocabulary. This is the AOAS's own stated trigger for adopting an expression language | format |
| S-2 | The policy offers nothing on a refusal ("deny user requests that are against this policy") | policy |
| S-3 | Insurance makes a cancellation refundable only for a covered reason (health, weather). The reason is spoken, and no record holds it | untestable from state |
| S-4 | Identity is the user id the customer types. The policy has no other proof | policy |
| S-5 | The policy never says a customer may act only on their own reservations. The AOAS states it as a reading (P-OWNERSHIP); every human agent would take it | ambiguous |
| S-6 | The certificate tool takes a user and an amount, but no reservation. So "100 × passengers on *the* disrupted reservation" cannot be bound, and eligibility is judged over any reservation the user holds | untestable / tool interface |
| S-7 | A flight-date is named by two inputs (number, date). The format has one key per entity, so the key is the two joined | format |
| S-8 | Searching the public timetable returns many rows that belong to nobody. The validator's `unscoped-collection` rule refuses `output: flight[]`, so the searches declare no output | format |
| S-9 | `calculate` is a pure function. Every operation must act on an entity, so it is served but not declared | format |
| S-10 | The certificate's amount comes from the caller, while the format's own rule is that an amount never comes from the conversation | tool interface vs format |
| S-11 | AgentTwin's declarative projection cannot serve these tools: they take lists of objects and compute their effects (fares, balances, refunds). The stand-in adopts τ²'s code instead | AgentTwin, by design |
| S-12 | Rules about a change cannot be stated, because a condition compares one row with constants. Examples: "add bags, never remove"; "basic economy flights cannot be modified" (while their cabin may change); "cabin cannot change once a segment has flown" | format |
| S-13 | Conversational duties are not state, and nothing here judges them: list the details and get an explicit yes before a write; ask about insurance; one tool call at a time; say exactly "YOU ARE BEING TRANSFERRED…"; give no invented information; compensate only when asked | out of scope for state oracles |
| S-14 | A transfer to a person leaves no trace in τ²'s database. Neither judge sees a missing transfer, or an unneeded one | both judges |
| S-15 | The policy implies no obligation that state carries (no `owed_when`). AgentTwin's distinctive omission oracle had nothing to bite on in this domain | domain |
| S-16 | The bag allowance is stated under booking. After a downgrade lowers it, the policy never says whether bags that were free become paid. Task 18's gold keeps them free; the AOAS invariant says otherwise | ambiguous |
| S-17 | The payment caps are stated for booking, and a change may be paid with any card. Written as an invariant, the cap fires on task 37's gold: **a false positive of this AOAS**. The format cannot say "at booking" | format, and our error |

### About AgentTwin

| Id | Finding | State |
|---|---|---|
| G-1 | `at-most-once` keys an effect on the row a call names. A booking names no existing row, so booking twice is unseen. The AOAS declares `identity: [user_id, flights, passengers]`, but the loader does not carry `identity` into the world | open |
| G-2 | The monitor could not judge a write against the spec's preconditions on the row as the call found it, so a permissive far end's writes went unjudged | **fixed** (`WithinSpec`, commits f16a257, 7833975) |
| G-3 | A write that changes no row in the spec's view (a second refund) read as "nothing landed" | **fixed** (`landed`, commit 3171e33) |
| G-4 | The truth oracle reads only enum-valued claims about a record named by key. In the live transcript, nothing the agent said was a claim it could check | known limit |

## Limits

- **Offline trajectories are not agent behaviour.** They are gold actions
  and hand-made mutations of them. They show what each judge can see, not
  how often a real agent does each thing. Where a task requires something
  said, the gold trajectory carries one synthesized reply saying it.
- **The mutations are ours.** They cover the policy's rules once each, with
  the first fitting target, and some tasks had no target for some mutations
  (`—` in the per-task table).
- **AgentTwin judged the policy, never the task.** Writing one scenario per
  task, stating what that customer asked for, would let AgentTwin judge
  "request left undone" too. We did not, to keep the AOAS and the judging
  independent of τ²'s answers.
- **Live: one task completed.** Two small models on a free tier is a
  plumbing demonstration, not a measurement of the agent.
- **The AOAS is one reading.** S-5, S-16 and S-17 are places where another
  reader could write it differently. S-17 shows a reading that was simply
  wrong, found by the gold run.
- **Time is frozen** at the policy's 2024-05-15 15:00 EST, and seats are not
  released on cancellation, as in τ².
