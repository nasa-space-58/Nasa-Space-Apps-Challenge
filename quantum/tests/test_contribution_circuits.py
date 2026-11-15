"""Independent Qiskit gate implementation of C1 mixers; no provider or credits."""

import math
from fractions import Fraction

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit.library import RXGate
from qiskit.quantum_info import Statevector

from backend.app.quantum_applications import (
    ApplicationInstance,
    Evaluation,
    Qubo,
    milp_model,
)
from backend.app.quantum_contribution import quantum_distribution


@pytest.mark.parametrize("variant", ["qaoa", "warm_qaoa", "conflict_qaoa"])
def test_gate_circuit_matches_contribution_statevector(variant):
    # Independent hand-derived x=(1-Z)/2 coefficients of path-three MWIS.
    # z_i = -linear_i/2 - sum_j quadratic_ij/4.
    z = (-4.0, -8.5, -3.0)
    zz = {(0, 1): 5.0, (1, 2): 5.0}
    scale = sum(abs(v) for v in z) + sum(zz.values())
    initial = (1, 0, 1)
    gammas, betas = (0.47, -0.23), (0.81, -0.32)
    instance = ApplicationInstance(
        "comm_slots",
        "circuit-test",
        ("a", "b", "c"),
        lambda bits: Evaluation(True, Fraction(0), Fraction(0), {}),
        Qubo(0.0, (-2.0, -3.0, -4.0), {(0, 1): 20.0, (1, 2): 20.0}, True, "test"),
        milp_model(
            (2, 3, 4),
            (({0: 1, 1: 1}, -math.inf, 1), ({1: 1, 2: 1}, -math.inf, 1)),
            decision_count=3,
        ),
        "a" * 64,
        "hand-calculated circuit",
    )
    circuit = QuantumCircuit(3)
    theta = [2 * math.asin(math.sqrt(0.8 if b else 0.2)) for b in initial]
    for i, b in enumerate(initial):
        if variant == "qaoa":
            circuit.h(i)
        elif variant == "warm_qaoa":
            circuit.ry(theta[i], i)
        elif b:
            circuit.x(i)
    neighbors = ((1,), (0, 2), (1,))
    for gamma, beta in zip(gammas, betas):
        for i, v in enumerate(z):
            circuit.rz(2 * gamma * v / scale, i)
        for (i, j), v in zz.items():
            circuit.rzz(2 * gamma * v / scale, i, j)
        for i in range(3):
            if variant == "qaoa":
                circuit.rx(2 * beta, i)
            elif variant == "warm_qaoa":
                circuit.ry(-theta[i], i)
                circuit.rz(2 * beta, i)
                circuit.ry(theta[i], i)
            else:
                gate = RXGate(2 * beta).control(len(neighbors[i]), ctrl_state=0)
                circuit.append(gate, [*neighbors[i], i])
    expected = Statevector.from_instruction(circuit).probabilities()
    actual = quantum_distribution(
        instance, gammas, betas, variant=variant, initial=initial
    )
    assert np.allclose(actual, expected, atol=1e-12)
