"""H2 audited QUBO and compact-register QAOA; local noiseless simulation only.

QAOA: Farhi, Goldstone, Gutmann (2014), https://arxiv.org/abs/1411.4028.
The compact diagonal Hamiltonian may contain higher-than-quadratic Z terms.
"""

from fractions import Fraction
from typing import Literal

import numpy as np

from shared.hybrid import Contract, Plan, Scenario


class QuadraticTerm(Contract):
    left: int
    right: int
    coefficient: str


class QuboModel(Contract):
    input_fingerprint: str
    option_ids: tuple[str, ...]
    mission_groups: tuple[tuple[int, ...], ...]
    offset: str
    diagonal: tuple[str, ...]
    quadratic: tuple[QuadraticTerm, ...]
    penalty: str
    forbidden_options: tuple[int, ...]
    conflict_pairs: tuple[tuple[int, int], ...]


class MappingAudit(Contract):
    binary_assignments: int
    one_choice_assignments: int
    feasible_assignments: int
    optimal_assignments: int
    ground_energy: str
    ground_matches_classical: bool


class CompactModel(Contract):
    qubo: QuboModel
    logical_qubits: int
    one_hot_masks: tuple[int, ...]
    normalized_energies: tuple[float, ...]
    energy_shift: float
    energy_scale: float
    z_terms: tuple[tuple[int, float], ...]
    cx_per_cost_layer: int
    max_z_locality: int


class QuantumSample(Contract):
    basis_index: int
    count: int
    probability: float
    selected_option_ids: tuple[str, ...]
    feasible: bool
    optimal: bool
    score: float | None


class QaoaResult(Contract):
    execution_mode: Literal["noiseless_simulator"] = "noiseless_simulator"
    input_fingerprint: str
    logical_qubits: int
    qubo_variables: int
    depth: int
    shots: int
    seed: int
    objective_evaluations: int
    gammas: tuple[float, ...]
    betas: tuple[float, ...]
    expected_normalized_energy: float
    feasible_probability: float
    optimal_probability: float
    uniform_feasible_probability: float
    uniform_optimal_probability: float
    sampled_feasible_rate: float
    sampled_optimal_rate: float
    best_sampled_plan: Plan | None
    samples: tuple[QuantumSample, ...]
    cx_gate_estimate: int
    rz_gate_estimate: int
    rx_gate_count: int
    max_z_locality: int
    elapsed_s: float
    hardware_jobs: Literal[0] = 0
    repair_applied: Literal[False] = False


def compile_qubo(scenario: Scenario, *, max_variables: int = 12) -> QuboModel:
    """Build exact rational one-hot QUBO after exhaustive constraint consistency.

    Support static complete instances whose hard rules are exactly unary/pairwise
    on one-choice assignments. Raise ValueError for unsupported inputs, higher-order
    resource constraints, or more than max_variables (hard cap 12) binary variables.
    """
    raise NotImplementedError


def qubo_energy(model: QuboModel, bits: tuple[int, ...]) -> Fraction:
    """Return exact dimensionless QUBO energy; reject wrong length/non-binary bits."""
    raise NotImplementedError


def audit_mapping(model: QuboModel, scenario: Scenario) -> MappingAudit:
    """Compare every binary energy/ground state against the original H1 rules.

    Raise ValueError for stale or inconsistent models. Bound to 12 variables.
    """
    raise NotImplementedError


def compact_model(model: QuboModel, *, max_qubits: int = 6) -> CompactModel:
    """Encode power-of-two options per mission, without auxiliary variables.

    Return a normalized diagonal Hamiltonian and logical all-to-all gate estimates.
    More-than-quadratic Z terms are explicit; routing/transpilation is not estimated.
    Raise ValueError outside 1..max_qubits (hard cap 8) or for non-power-of-two groups.
    """
    raise NotImplementedError


def qaoa_statevector(energies, gammas, betas) -> np.ndarray:
    """Return exp(-i beta sum X) exp(-i gamma C) layers acting on uniform |+>.

    Little-endian basis indexing; finite dimensionless energies/angles required.
    Support 1..8 qubits and 1..3 layers; invalid input raises ValueError.
    """
    raise NotImplementedError


def run_qaoa(
    scenario: Scenario,
    *,
    depth: int = 2,
    evaluations: int = 160,
    shots: int = 512,
    seed: int = 17,
    max_qubits: int = 6,
) -> QaoaResult:
    """Run bounded seeded classical tuning, then sample the noiseless QAOA state.

    Validate every sampled assignment with H1; report raw feasible/optimal rates,
    exact distribution probabilities and uniform baselines separately. No repair,
    provider API, noise, hardware, queue time or quantum advantage is claimed.
    Raise ValueError for invalid budgets or unsupported/infeasible problems.
    """
    raise NotImplementedError
