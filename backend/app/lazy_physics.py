"""Exact finite-scenario plan selection with demand-driven R6 evaluation."""


def select_lazy(sizes, *, runs, cost_limit_m, load_plan, max_batches=None):
    """Select two plans using safe optimistic bounds, never inferred safety.

    load_plan(lander_index, option_index) returns a complete RobustPlan.
    Finite Monte Carlo optimality is not a flight-safety certificate.
    """
    raise NotImplementedError

