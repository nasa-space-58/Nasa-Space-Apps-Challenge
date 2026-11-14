"""R10 scalable problem families, pairwise QUBO resources and a HiGHS MILP baseline.

Synthetic instances only. The pairwise model is a direct, enumeration-free
construction for static instances whose hard rules are exactly unary/pairwise.
The MILP baseline (scipy.optimize.milp, HiGHS) is a classical reference whose
answers are always revalidated by the independent H1 validator.
"""

from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Identifier, NonNegative, Plan, Scenario

from .hybrid_planning import Issue, ValidationReport

Probability = Annotated[float, Field(strict=True, ge=0, le=1)]

MILP_SOLVER_NAME = "r10-highs-milp-v1"
GENERATOR_VERSION = "r10-generator-v1"


class InstanceSpec(Contract):
    """Seeded synthetic family member: size, constraint density and contention.

    Each option occupies one 1-hour UTC slot on one capacity-1 shared antenna.
    Fewer slots/antennas raise contention; incompatibility_probability adds
    explicit cross-mission at-most-one pairs; deadline_tightness moves mission
    deadlines earlier (0 keeps every deadline at the scenario end).
    """

    family: Identifier
    missions: Annotated[int, Field(strict=True, ge=1, le=32)]
    options_per_mission: Annotated[int, Field(strict=True, ge=1, le=16)]
    slots: Annotated[int, Field(strict=True, ge=1, le=96)]
    antennas: Annotated[int, Field(strict=True, ge=1, le=16)]
    incompatibility_probability: Probability = 0.0
    deadline_tightness: Probability = 0.0
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]


class PairwiseModel(Contract):
    """Exact unary/pairwise hard-rule structure plus exact rational scores.

    Option order is sorted option ID; mission order is sorted mission ID. Score,
    penalty and energies are dimensionless normalized H0 objective values.
    """

    input_fingerprint: str
    option_ids: tuple[str, ...]
    mission_ids: tuple[str, ...]
    mission_groups: tuple[tuple[int, ...], ...]
    scores: tuple[str, ...]
    penalty: str
    forbidden_options: tuple[int, ...]
    conflict_pairs: tuple[tuple[int, int], ...]


class ResourceProfile(Contract):
    """Logical problem and circuit size; all-to-all estimates, not transpiled."""

    input_fingerprint: str
    missions: int
    options: int
    assignments: int
    one_hot_variables: int
    one_hot_quadratic_terms: int
    forbidden_options: int
    conflict_pairs: int
    conflict_density: NonNegative
    compact_qubits: int | None
    compact_z_terms: int | None
    compact_max_z_locality: int | None
    compact_locality_histogram: dict[str, int] | None
    cx_per_layer: int | None
    rz_per_layer: int | None
    rx_per_layer: int | None
    statevector_amplitudes: int | None
    compact_unsupported_reason: str | None = None


class MilpResult(Contract):
    """HiGHS status mapped to H1 terms; plans are always independently revalidated."""

    status: Literal[
        "optimal", "infeasible", "limit_reached", "incomplete", "unsupported"
    ]
    solver_name: Literal["r10-highs-milp-v1"] = MILP_SOLVER_NAME
    input_fingerprint: str
    variables: int
    constraints: int
    time_limit_s: float
    plan: Plan | None = None
    validation: ValidationReport | None = None
    objective_bound: float | None = None
    mip_gap: float | None = None
    issues: tuple[Issue, ...] = ()
    elapsed_s: NonNegative = 0


class ScalingPoint(Contract):
    """One family member: logical/circuit resource growth and MILP effort."""

    spec: InstanceSpec
    input_fingerprint: str
    pairwise_supported: bool
    profile: ResourceProfile | None
    milp_status: str
    milp_variables: int
    milp_constraints: int
    milp_elapsed_s: NonNegative


def generate_instance(spec: InstanceSpec) -> Scenario:
    """Return a deterministic synthetic H0 scenario for the given family member.

    Uses Python's random.Random(spec.seed) (stable across NumPy versions). Every
    mission keeps at least one option before its deadline. Raises ValueError for
    invalid specs. No feasibility claim is attached to the result.
    """
    raise NotImplementedError


def build_pairwise_model(scenario: Scenario) -> PairwiseModel:
    """Derive unary/pairwise hard rules without enumerating assignments.

    Supported only when exactness is guaranteed: every positive concurrent demand
    exceeds half its known capacity, and every budget is either non-binding or
    exceeded by any two positive demands. Raise ValueError for events, unknown
    data, or rules needing slack variables or higher-order terms.
    """
    raise NotImplementedError


def choice_energy(model: PairwiseModel, choice: tuple[int, ...]):
    """Exact Fraction energy of one option index per mission (compact domain).

    E = -sum(score) + penalty * (forbidden selected + conflicting pairs selected).
    Raise ValueError for a wrong length or an index outside its mission group.
    """
    raise NotImplementedError


def one_hot_energy(model: PairwiseModel, bits: tuple[int, ...]):
    """Exact Fraction energy of the one-hot QUBO (H2 penalty construction)."""
    raise NotImplementedError


def compact_energies(model: PairwiseModel, *, max_qubits: int = 8) -> tuple:
    """Return exact Fraction energies of every compact basis state, little-endian.

    Mission registers are ordered by mission ID with the first mission in the
    lowest bits. Requires power-of-two option counts; hard cap 8 qubits.
    """
    raise NotImplementedError


def resource_profile(model: PairwiseModel) -> ResourceProfile:
    """Count one-hot QUBO terms and exact compact Pauli-Z expansion resources.

    The compact Z expansion is computed per mission and per conflicting mission
    pair with exact rational Walsh-Hadamard transforms; no 2^n enumeration.
    """
    raise NotImplementedError


def solve_milp(scenario: Scenario, *, time_limit_s: float = 10.0) -> MilpResult:
    """Solve the original linear 0/1 program with HiGHS; revalidate with H1.

    Rows: one option per mission, deadline/availability bounds, at-most-one sets,
    per-interval channel capacity and cumulative joule budgets. A validator
    disagreement raises RuntimeError. Invalid time limits raise ValueError.
    """
    raise NotImplementedError


def scaling_sweep(
    specs: tuple[InstanceSpec, ...], *, milp_time_limit_s: float = 10.0
) -> tuple[ScalingPoint, ...]:
    """Generate each spec, profile its QUBO/circuit size and time the MILP.

    Unsupported pairwise mappings are reported (profile None), never dropped.
    Raise ValueError for an empty or oversized (more than 64) spec tuple.
    """
    raise NotImplementedError
