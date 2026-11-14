"""Explicit physical placement is transpilation input, never post-hoc renumbering."""

import numpy as np
import pytest
from qiskit import qasm2
from qiskit.quantum_info import Statevector

from quantum import openquantum_bridge as bridge
from quantum.tests.test_openquantum_bridge import gateway, problem


def prepare(g, layout):
    source = problem()
    circuit = bridge.build_qaoa_circuit(source, [0.2], [0.4])
    return g.prepare(
        source,
        circuit,
        backend_code="test:qpu",
        organization_id="org",
        shots=16,
        trial="layout-test",
        initial_layout=layout,
    )


@pytest.mark.parametrize("layout", [(0, 1), (1, 0)])
def test_explicit_layout_reaches_transpiler_and_preserves_measured_distribution(
    tmp_path, monkeypatch, layout
):
    service, g = gateway(tmp_path)
    actual = bridge.transpile
    captured = {}

    def observe(circuit, **kwargs):
        captured.update(kwargs)
        return actual(circuit, **kwargs)

    monkeypatch.setattr(bridge, "transpile", observe)
    ticket = prepare(g, layout)
    assert list(captured["initial_layout"]) == list(layout)
    native = qasm2.loads(
        (tmp_path / (ticket.circuit_sha256 + ".qasm")).read_text(),
        custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
    )
    # Reconstruct classical-bit probabilities from physical final measurements.
    mapping = {
        native.find_bit(op.clbits[0]).index: native.find_bit(op.qubits[0]).index
        for op in native.data
        if op.operation.name == "measure"
    }
    p = Statevector.from_instruction(
        native.remove_final_measurements(inplace=False)
    ).probabilities()
    observed = np.zeros(4)
    for basis, probability in enumerate(p):
        key = sum(
            ((basis >> physical) & 1) << classical
            for classical, physical in mapping.items()
        )
        observed[key] += probability
    ideal = bridge.build_qaoa_circuit(problem(), [0.2], [0.4], measure=False)
    np.testing.assert_allclose(
        observed,
        Statevector.from_instruction(ideal).probabilities(),
        atol=1e-11,
        rtol=0,
    )
    assert service.created == []


@pytest.mark.parametrize(
    "layout", [(0,), (0, 0), (-1, 0), (False, 1), (0, 2), (0.0, 1), "01"]
)
def test_invalid_layout_fails_before_upload(tmp_path, layout):
    service, g = gateway(tmp_path)
    with pytest.raises(ValueError):
        prepare(g, layout)
    assert service.uploads == service.created == []
