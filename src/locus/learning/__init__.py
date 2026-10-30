"""Learning primitive -- router policies, reward functions, learning."""

from __future__ import annotations

from locus.learning._stubs import (
    QueryAnalyzer,
    RewardFunction,
    RouterPolicy,
    RoutingContext,
)
from locus.learning.agents.agent_evolver import AgentConfigEvolver
from locus.learning.learning_orchestrator import LearningOrchestrator
from locus.learning.optimize.llm_optimizer import LLMOptimizer
from locus.learning.optimize.optimizer import OptimizationEngine
from locus.learning.optimize.store import OptimizationStore
from locus.learning.routing.complexity import (
    ComplexityQueryAnalyzer,
    score_complexity,
)
from locus.learning.routing.heuristic_reward import HeuristicRewardFunction
from locus.learning.routing.router import (
    HeuristicRouter,
    build_routing_context,
)
from locus.learning.training.data import TrainingDataMiner
from locus.learning.training.lora import HAS_TORCH, LoRATrainer, LoRATrainingConfig


def ensure_registered() -> None:
    """Ensure all learning policies are registered in RouterPolicyRegistry."""
    from locus.learning.routing.heuristic_policy import (
        ensure_registered as _reg_heuristic,
    )

    _reg_heuristic()

    from locus.learning.routing.learned_router import (
        ensure_registered as _reg_learned,
    )

    _reg_learned()

    # Intelligence training (optional deps)
    try:
        import locus.learning.intelligence  # noqa: F401
    except ImportError:
        pass

    # Orchestrator-specific training (optional deps)
    try:
        import locus.learning.intelligence.orchestrator  # noqa: F401
    except ImportError:
        pass

    # Agent optimizers (optional deps)
    try:
        import locus.learning.agents.dspy_optimizer  # noqa: F401
    except ImportError:
        pass
    try:
        import locus.learning.agents.gepa_optimizer  # noqa: F401
    except ImportError:
        pass
    try:
        import locus.learning.agents.ace_optimizer  # noqa: F401
    except ImportError:
        pass


__all__ = [
    "AgentConfigEvolver",
    "ComplexityQueryAnalyzer",
    "HAS_TORCH",
    "HeuristicRewardFunction",
    "HeuristicRouter",
    "LLMOptimizer",
    "LearningOrchestrator",
    "LoRATrainer",
    "LoRATrainingConfig",
    "OptimizationEngine",
    "OptimizationStore",
    "QueryAnalyzer",
    "RewardFunction",
    "RouterPolicy",
    "RoutingContext",
    "TrainingDataMiner",
    "build_routing_context",
    "ensure_registered",
    "score_complexity",
]
