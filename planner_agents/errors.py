"""Error boundary for planning orchestration and its read-only tools."""


class PlannerError(RuntimeError):
    """Base exception for planner failures."""


class PlannerOutputError(PlannerError):
    """Raised when a model response cannot become a PlanDraft."""


class PlannerToolError(PlannerError):
    """Raised when a planning-context provider fails or violates its contract."""


class PlannerConfigurationError(PlannerError):
    """Raised when required planner configuration is missing or invalid."""


class PlanValidationError(PlannerError):
    """Raised when a model cannot produce a conflict-free plan."""
