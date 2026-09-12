"""AgentTwin — a twin of the agent's world, not of the agent.

The agent under test is real. Its environment is the twin.

Deliberately outside `support_agent`, and the import contract forbids the agent
from importing this package at all. A system that can see its own simulator is a
system whose test results mean nothing.
"""

from agenttwin.actor import Determinism, Rule, ScriptedActor, StateMachineActor, Transcript
from agenttwin.approver import Approver, Decision, Review
from agenttwin.checks import Check, Outcome
from agenttwin.desk import Answer, Desk, Handled
from agenttwin.loader import load
from agenttwin.omission import Obligation, nothing_was_omitted, omitted, owed
from agenttwin.personas import PERSONAS, brief_for
from agenttwin.perturbation import ChannelError, Slow, StaleRead, Timeline, perturbed
from agenttwin.projection import Live, project
from agenttwin.record import RunRecord, diff
from agenttwin.scenario import Clock, Scenario
from agenttwin.scenario import run as run_scenario
from agenttwin.scenario_file import ScenarioFile, load_scenario
from agenttwin.subject import Subject
from agenttwin.suite import Unrunnable, provider_faults, run_file, timeline_for
from agenttwin.truth import Contradiction, answer_is_true, contradictions
from agenttwin.world import World

__all__ = [
    "Approver",
    "Check",
    "Outcome",
    "ScenarioFile",
    "Subject",
    "Unrunnable",
    "load_scenario",
    "provider_faults",
    "run_file",
    "timeline_for",
    "ChannelError",
    "Clock",
    "Contradiction",
    "Answer",
    "Decision",
    "Desk",
    "Determinism",
    "Handled",
    "Review",
    "Live",
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
