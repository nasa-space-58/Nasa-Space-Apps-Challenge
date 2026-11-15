"""Independent Qiskit statevector reference, no provider access."""
import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from app.stress_discovery import born_probabilities


def test_born_sampler_matches_independent_qiskit_little_endian_circuit():
    for seed in (7, 41, 100):
        angles = np.random.default_rng(seed).uniform(-np.pi, np.pi, size=12)
        circuit = QuantumCircuit(6)
        for qubit in range(6):
            circuit.ry(float(angles[qubit]), qubit)
        for qubit in range(5):
            circuit.cx(qubit, qubit + 1)
        for qubit in range(6):
            circuit.ry(float(angles[6 + qubit]), qubit)
        expected = Statevector.from_instruction(circuit).probabilities()
        assert np.allclose(born_probabilities(angles), expected, rtol=1e-12, atol=1e-12)
