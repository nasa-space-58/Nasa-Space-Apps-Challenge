"""Keep physical indices while avoiding idle target-register padding."""

import numpy as np
from qiskit import QuantumCircuit, qasm2
from qiskit.quantum_info import Statevector

from quantum.openquantum_bridge import _trim_idle_tail


def test_trim_preserves_physical_indices_and_measurement_mapping():
    circuit = QuantumCircuit(108, 2)
    circuit.h(2)
    circuit.cx(2, 4)
    circuit.measure(4, 0)
    circuit.measure(2, 1)
    circuit.metadata = {"problem_fingerprint": "example"}
    trimmed = _trim_idle_tail(circuit)
    assert trimmed.num_qubits == 5
    assert trimmed.num_clbits == 2
    assert trimmed.metadata == circuit.metadata
    measurements = [
        (trimmed.find_bit(op.qubits[0]).index, trimmed.find_bit(op.clbits[0]).index)
        for op in trimmed.data
        if op.operation.name == "measure"
    ]
    assert measurements == [(4, 0), (2, 1)]
    assert qasm2.loads(qasm2.dumps(trimmed)).num_qubits == 5


def test_trim_preserves_unitary_state_and_global_phase():
    circuit = QuantumCircuit(8)
    circuit.global_phase = 0.37
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.rx(0.71, 1)
    trimmed = _trim_idle_tail(circuit)
    assert trimmed.num_qubits == 2
    full = Statevector.from_instruction(circuit).data
    np.testing.assert_allclose(Statevector.from_instruction(trimmed).data, full[:4])
    np.testing.assert_allclose(full[4:], 0)


def test_no_trimming_when_last_physical_qubit_is_used():
    circuit = QuantumCircuit(5)
    circuit.x(4)
    assert _trim_idle_tail(circuit).num_qubits == 5
