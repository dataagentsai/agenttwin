# Assessing τ²-bench's telecom agent with AgentTwin — the agent's side

**The question.** Can AgentTwin assess an agent its authors did not build, in
a third domain, where the customer also acts, on their own phone?

**The short answer.** Partly, and the line between the two parts is clear.
AgentTwin can judge what the agent does to the carrier's records. It cannot
judge what the agent tells the customer to do on the phone. That second part
is most of this domain, and most of what τ²'s verdict reads. So this is a
**reduced version** (finding T-1), and the comparison shows where each judge
can see.

**The target.** The telecom domain of
[τ²-bench](https://github.com/sierra-research/tau2-bench) (MIT), pinned at
commit `4ce7c03`, as in [tau2-airline](../tau2-airline/) and
[tau2-retail](../tau2-retail/). τ² gives a main policy and a technical-support
manual in prose, and 13 agent tools over a small carrier database (4
customers, 9 lines, 6 bills, 5 plans). The customer has about 30 tools of
their own on a simulated phone. There are 2,285 tasks. This run uses the 114
of τ²'s `base` split, which is what τ² runs by default. In all of them the
customer is John Smith, calling about line L1002.

**The method** is airline's, unchanged where it could be:

- An AOAS written from the two policy texts and the agent's tool signatures
  only. It covers the agent's tools and the carrier's records.
- A world made from τ²'s database. Each task's own starting state (a line
  suspended for an overdue bill, data used up) is set by τ²'s environment
  before the first call.
- τ²'s agent tools served over MCP through the stand-in. The customer's phone
  tools run on τ²'s environment directly, as the customer would use them.
  AgentTwin's view is re-read after each one, because paying a bill on the
  phone changes the bill's record.
- The same trajectories judged by τ²'s evaluators and by AgentTwin's
  oracles, and every disagreement explained.

## Results (offline, no model calls)

We ran 1,132 trajectories over 114 tasks. A trajectory is a task's expected
actions (the agent's and the customer's) or one mutation of them. A catch is
counted only when AgentTwin fails a mutation **for the rule that mutation
broke**.

| What the trajectory does wrong | Trajectories | τ² fails it | AgentTwin fails it, for that reason |
|---|---|---|---|
| nothing (gold) | 114 | 0 | 0 |
| a write to the carrier's records that breaks a rule state can show¹ | 491 | 50 | **491** |
| reads another customer's record | 114 | **0** | **114** |
| leaves the customer's own step on the phone undone | 94 | 94 | 0 (outside the format, T-1) |
| leaves the agent's last write undone (or its transfer) | 91 | 91 | 0 (by design) |
| suspends a line nobody asked about | 114 | 0 | 0 (no stated rule, T-8) |
| attempts a write τ²'s own code refuses | 114 | 0 | 0 |

¹ Each adds one write, all carried out by τ²'s code:

| Write | Trajectories | AgentTwin's incident |
|---|---|---|
| refuel 3 GB, over the 2 GB cap | 114 | `refuel_data:L1002:invariant:at most 2 GB refuelled` |
| refuel the customer's *other* line, not the phone they called about | 114 | `refuel_data:L1001:owner` |
| refuel another customer's line | 114 | `refuel_data:L1004:owner` |
| send a payment request for a bill that is not overdue | 114 | `send_payment_request:B1002:precondition:status` |
| refuel the line while it is suspended (its docstring says it must be Active) | 21 | `refuel_data:L1002:precondition:status` |
| resume the line before the overdue bill is paid | 6 | `resume_line:L1002:precondition:owner_overdue_bills` |
| resume a line whose contract has ended | 8 | `resume_line:L1002:precondition:contract_ended` |

τ² failed 50 of the 491, all of them "refuel 3 GB" in tasks that assert the
refuelled amount.

**Fidelity.** The stand-in answered the agent's 1,993 calls. Every answer
was identical, byte for byte, to τ²'s own environment answering the same
call. The other 3,806 calls were the customer's, on the phone. Both sides run
those on τ²'s environment directly, so their agreement checks nothing
AgentTwin put in the path. The per-task table is in
[results/offline.md](results/offline.md). Every
trajectory, with both verdicts and AgentTwin's incidents, is in
[results/offline.json](results/offline.json).

### Every disagreement, by cause

| Cause | Direction | Count | Why |
|---|---|---|---|
| τ² checks no records here (T-2) | AgentTwin only | 441 | τ²'s verdict for telecom is `ENV_ASSERTION` (and `ACTION` for 20 tasks), never `DB`. It reads the phone, and in some tasks one fact of the records (the refuelled amount in 50, an overdue bill paid in 9). A write that no assertion names passes |
| AgentTwin catches what τ² can't | AgentTwin only | 114 | Another customer's record was read and shown. Nothing changed |
| Outside the format: dual control (T-1) | τ² only | 94 | The step left undone is the customer's, on the phone. The phone is in neither the AOAS nor the world, and AgentTwin has no customer who acts with tools |
| By design | τ² only | 91 | The agent's request-serving write was left out. No rule is broken; AgentTwin judged the policy, not the request |

Gold: all 114 agree (both pass). `offline.py` fails the run on any
disagreement it cannot explain; there were none.

## How it is built

The same pieces as airline, most of them now shared
([tau2-common](../tau2-common/)):

| Piece | What | Built or adopted |
|---|---|---|
| [tau2-telecom.aoas.yaml](tau2-telecom.aoas.yaml) | The behaviour contract for the agent's side, from [the main policy](vendor/tau2/main_policy.md), [the manual](vendor/tau2/tech_support_manual.md) and the agent's tool signatures and docstrings. It passes `validate-aoas.js` | written |
| [world.yaml](world.yaml) + [convert.py](tau2_telecom/convert.py) | The carrier's records as the AOAS's rows, with three derived fields: a line's owner, whether its contract has ended, its owner's overdue bills | written |
| [judge.py](tau2_telecom/judge.py) | The bench: τ²'s two databases, and the session (customer id *and* phone number, from the phone number the task's words give) | written |
| [mutate.py](tau2_telecom/mutate.py), [offline.py](tau2_telecom/offline.py) | Mutations, and this domain's causes | written |
| `tau2_common` | Stand-in (τ²'s code behind MCP; the customer's tools beside it, never served), both judges, the offline driver | shared with airline and retail |

## Findings

### About the domain, the policy, and what a state-based spec can carry

| Id | Finding | Kind |
|---|---|---|
| T-1 | **Dual control.** The customer has tools, and most of the manual is the agent telling them which to use ("guide the user to use `toggle_airplane_mode()`"). Most of τ²'s verdict reads the phone afterwards. The AOAS describes operations *the agent* performs; it has no operation another actor performs at the agent's word. AgentTwin has no customer who acts with tools (ADOPTION.md lists τ²'s dual control as *evaluate*, T-111). So this spec and world cover the carrier's records only. The 94 `drop-customer-fix` disagreements are this gap. So is "enable roaming for a customer abroad", whose condition (where the customer is) is the phone's | format, and AgentTwin |
| T-2 | **τ²'s telecom verdict never compares the carrier's records.** The basis is `ENV_ASSERTION` (+`ACTION`), not `DB`. Of 491 rule-breaking writes, τ² failed only the 50 that hit an asserted fact. This is a property of the benchmark's tasks, not a flaw in τ²'s evaluator: the tasks are about fixing a phone | τ²'s judging |
| T-3 | The policy never says the agent acts only on the identified customer's account. The AOAS states it as a reading (P-OWN-ACCOUNT), as airline's S-5 | ambiguous |
| T-4 | The policy's "Change Plan" section has no tool. No agent tool applies a plan | policy vs tools |
| T-5 | "The maximum amount of data that can be refueled is 2GB": per refuel, or per line per cycle? The tool takes any amount and adds it up. The AOAS caps the total, which every refuel over 2 GB also breaks | ambiguous |
| T-6 | "There are five bill status types" lists six | policy |
| T-7 | `get_details_by_id` reads a customer, line, device, bill or plan, depending on the id's first letter. An operation acts on one entity in the format; it is declared on the customer | format |
| T-8 | The policy lists why a line *can be* suspended and never says when the agent may suspend one. A suspension nobody asked for breaks no stated rule, and neither judge fails it (114 trajectories) | policy, both judges |
| T-9 | `refuel_data`'s docstring says "Checks: Line status must be Active"; the check is commented out in the code. The AOAS states the docstring's rule, and AgentTwin catches a refuel on a suspended line (21) | tool docstring vs code |
| T-10 | The session needs two fields. The manual acts on "the line associated with the phone number the user provided", so a refuel on the customer's *other* line breaks a rule that ownership alone does not. `equals_session` on the line's phone number states it. It catches all 114 | format: worked |

### About AgentTwin

| Id | Finding | State |
|---|---|---|
| G-1 | (airline's, again) `at-most-once` keys an irreversible effect on the row a call names. The AOAS's `identity: [line_id, gb_amount]` is not carried into the world, so two refuels of different amounts on one line read as the same effect landing twice. In the 50 tasks whose gold refuels, `refuel-over-cap` raised this false alarm beside its real incident | open |
| G-5 | `own-rows-only` judges an answer by the owner field the answer carries. τ²'s line answers (`get_data_usage`, `get_details_by_id` for a line) name no customer, and list answers (`get_customer_by_name`, `get_bills_for_customer`) are bare lists the monitor does not read. So a read of another customer's *line* would go unseen. Seen by reading the monitor; no mutation here exercises it | open |
| — | Each task's starting state is τ²'s own, set before the first call. The world's view is re-read then, and after each customer action. This needed no AgentTwin change | — |

## Limits

- **A reduced assessment.** Everything the agent does on the phone through
  the customer is out of scope (T-1). In the base split that is the larger
  part of what makes a telecom task pass or fail.
- **114 of 2,285 tasks**: τ²'s `base` split, its default. The others vary the
  same faults and personas.
- **The mutations are ours**, and all but `drop-customer-fix` change what the agent does. Their
  targets are fixed by the tiny database: the customer's other line L1001,
  another customer's line L1004, the issued bill B1002.
- **Offline trajectories are not agent behaviour**, as in airline.
- **No live run** this time (Groq's free-tier limits).
- **The AOAS is one reading.** T-3, T-5 and T-8 are places where another
  reader could write it differently.

## Running it

As airline: `./run.sh` fetches τ² at the pinned commit through
[../tau2-common/fetch_tau2.sh](../tau2-common/fetch_tau2.sh).

```bash
./run.sh -m pytest tests                       # 23 tests, about 10 seconds
./run.sh -m tau2_telecom.offline               # 114 tasks, about 3 minutes on a laptop
./run.sh -m tau2_telecom.offline @ids.txt      # some tasks (ids hold brackets and pipes: one per line in a file)
./run.sh -m tau2_telecom.offline --report      # re-tabulate results/offline.json
```
