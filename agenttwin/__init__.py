"""AgentTwin — a twin of the agent's world, not of the agent.

The agent under test is real. Its environment is the twin.

Deliberately outside the agent under test, and an agent should forbid itself
from importing this package at all. A system that can see its own simulator is a
system whose test results mean nothing.
"""

from agenttwin.actor import Determinism, Rule, ScriptedActor, StateMachineActor, Transcript
from agenttwin.approver import Approver, Decide, Decision, Review
from agenttwin.binding import Binding, BindingNotFound, load_binding
from agenttwin.checks import Check, Outcome
from agenttwin.desk import Answer, Close, Desk, Handled
from agenttwin.loader import load
from agenttwin.omission import Obligation, nothing_was_omitted, omitted, owed
from agenttwin.personas import PERSONAS, brief_for
from agenttwin.perturbation import (
    ChannelError,
    Decline,
    LostReply,
    Slow,
    StaleRead,
    Timeline,
    perturbed,
)
from agenttwin.projection import (
    APPROVAL_META,
    IDEMPOTENCY_META,
    SESSION_META,
    Live,
    NotAuthorised,
    authority_check,
    project,
)
from agenttwin.provider import ModelEndpoint, ProviderTwin, Upstream
from agenttwin.record import RunRecord, diff
from agenttwin.runner import ScenarioResult, run_one, run_suite
from agenttwin.scenario import Clock, Scenario
from agenttwin.scenario import run as run_scenario
from agenttwin.scenario_file import ModelTurnFile, ScenarioFile, load_scenario
from agenttwin.subject import Offstage, OffstageFactory, Queue, Queued, Say, Subject
from agenttwin.suite import (
    Unrunnable,
    attack_cases,
    plant,
    provider_faults,
    run_file,
    run_generated,
    timeline_for,
)
from agenttwin.truth import Contradiction, answer_is_true, contradictions
from agenttwin.world import World

__all__ = [
    "Binding",
    "BindingNotFound",
    "load_binding",
    "ModelEndpoint",
    "ModelTurnFile",
    "ProviderTwin",
    "Upstream",
    "ScenarioResult",
    "run_one",
    "run_suite",
    "Approver",
    "Check",
    "Outcome",
    "ScenarioFile",
    "Subject",
    "Say",
    "Offstage",
    "OffstageFactory",
    "Queue",
    "Queued",
    "Decide",
    "Close",
    "Unrunnable",
    "load_scenario",
    "attack_cases",
    "plant",
    "provider_faults",
    "run_file",
    "run_generated",
    "timeline_for",
    "ChannelError",
    "Decline",
    "LostReply",
    "Clock",
    "Contradiction",
    "Answer",
    "Decision",
    "Desk",
    "Determinism",
    "Handled",
    "Review",
    "Live",
    "APPROVAL_META",
    "IDEMPOTENCY_META",
    "SESSION_META",
    "NotAuthorised",
    "authority_check",
    "Obligation",
    "PERSONAS",
    "brief_for",
    "Rule",
    "RunRecord",
    "Scenario",
    "ScriptedActor",
    "Slow",
    "StaleRead",
    "StateMachineActor",
    "Timeline",
    "Transcript",
    "World",
    "answer_is_true",
    "contradictions",
    "diff",
    "nothing_was_omitted",
    "omitted",
    "owed",
    "load",
    "perturbed",
    "project",
    "run_scenario",
]
