"""Plan evaluation: tiered rule-based and LLM-powered decision plan evaluation."""

from opentrace.agents.journal.evaluation.decision_plan_evaluator import evaluate_decision_plan
from opentrace.agents.journal.evaluation.condition_tracker import (
    ConditionTracker,
    ConditionStateStore,
)
from opentrace.agents.journal.evaluation.thesis_state_machine import (
    ThesisPhase,
    ThesisStateMachine,
)
from opentrace.agents.journal.evaluation.news_event_inference import (
    infer_event_flags,
    event_inference_enabled,
)

try:  # pragma: no cover
    from opentrace.agents.journal.evaluation.llm_evaluator import (
        LLMClient,
        LLMEvaluator,
        build_llm_client_from_config,
    )
    from opentrace.agents.journal.evaluation.event_compiler import (
        EventCompiler,
        CheckerSpec,
        execute_checker_spec,
    )
    from opentrace.agents.journal.evaluation.smart_evaluator import SmartPlanEvaluator
except Exception:  # pragma: no cover
    pass

__all__ = [
    "evaluate_decision_plan",
    "ConditionTracker",
    "ConditionStateStore",
    "ThesisPhase",
    "ThesisStateMachine",
    "infer_event_flags",
    "event_inference_enabled",
]
