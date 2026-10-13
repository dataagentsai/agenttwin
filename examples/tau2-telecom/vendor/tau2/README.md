# Vendored from τ²-bench

From [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench)
at commit `4ce7c0397c1eb65c9bbe59aeacfe1ca44a1cd699`, under its MIT licence
(`LICENSE`, copied unchanged).

| File | Source path | Why it is here |
|---|---|---|
| `main_policy.md` | `data/tau2/domains/telecom/main_policy.md` | the main policy the AOAS was written from |
| `tech_support_manual.md` | `data/tau2/domains/telecom/tech_support_manual.md` | the technical-support part of the policy τ² gives the agent with it (τ²'s default, `policy_type="manual"`) |

Nothing else is vendored. The tool code, the databases and the tasks are read
from a pinned checkout outside this repository (`../tau2-common/fetch_tau2.sh`).
