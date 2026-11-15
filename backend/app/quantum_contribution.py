"""Paired, local-only quantum contribution experiment; no provider imports.

One binary variable per native application choice. Sparse QUBO coefficients
are compiled analytically, not by enumerating a truth table. The bounded
statevector simulator still uses exponential memory and is explicitly counted.
"""

from typing import Annotated

from pydantic import Field
from shared.hybrid import Contract

METHODS = (
    "milp", "multistart", "annealing", "random", "qaoa", "warm_qaoa", "conflict_qaoa"
)
QUANTUM_METHODS = METHODS[4:]


class ContributionBudget(Contract):
    validator_calls: Annotated[int, Field(strict=True, ge=1, le=1024)] = 64
    energy_evaluations: Annotated[int, Field(strict=True, ge=1, le=100000)] = 2048
    wall_s: Annotated[float, Field(strict=True, gt=0, le=60)] = 3.0
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=16)] = 12
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    tuning_evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 24
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256


def ising_coefficients(qubo):
    """Return constant, Z coefficients and ZZ coefficients using x=(1-Z)/2."""
    raise NotImplementedError


def conflict_masks(instance):
    """Certify native binary at-most-one rows; reject all unsupported constraints."""
    raise NotImplementedError


def quantum_distribution(instance, gammas, betas, *, variant, initial, max_qubits=12):
    """Return local noiseless probabilities in little-endian decision-bit order."""
    raise NotImplementedError


def run_contribution_method(instance, method, *, budget=ContributionBudget(), seed=17):
    """Bound search, independently validate candidates and return cost/quality evidence."""
    raise NotImplementedError


def contribution_verdict(records):
    """Compare held-out paired runs; simulator evidence cannot certify QPU advantage."""
    raise NotImplementedError
