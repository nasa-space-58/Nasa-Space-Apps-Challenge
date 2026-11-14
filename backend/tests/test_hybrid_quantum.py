"""H2 exact mapping identities and independently constructed quantum unitaries.

Numerical state/probability tolerance is 1e-12. Optimization tests check coherent
accounting and repeatability; no stochastic run is required to beat a baseline.
"""

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from app.hybrid_planning import validate_plan
from app.hybrid_quantum import (
    QaoaResult,
    audit_mapping,
    compact_model,
    compile_qubo,
    qaoa_statevector,
    qubo_energy,
    run_qaoa,
)
from shared.hybrid import Scenario, load_scenario

EXAMPLES = Path("/workspace/shared/examples/hybrid")
if not EXAMPLES.exists():
    EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"


def scenario(name="shared-conflict"):
    return load_scenario(EXAMPLES / f"{name}.json")


def changed(mutator):
    data = scenario().model_dump(mode="json")
    mutator(data)
    return Scenario.model_validate(data)


def bits(mask, n=12):
    return tuple((mask >> index) & 1 for index in range(n))


def test_all_4096_qubo_energies_equal_original_penalty_formula():
    source = scenario()
    model = compile_qubo(source)
    assert len(model.option_ids) == 12
    penalty = Fraction(model.penalty)
    assert penalty == Fraction(53, 5)
    values = {
        c.option_id: Fraction(str(c.metrics["science_value"])) / 10
        for c in source.candidates
    }
    minima, best = [], None
    for mask in range(4096):
        x = bits(mask)
        # Independent closed formula for this fixture: exactly one per mission;
        # at most one mission may use each of the four slots.
        counts = [sum(x[i * 4 : (i + 1) * 4]) for i in range(3)]
        collisions = sum(
            x[mission_a * 4 + slot] * x[mission_b * 4 + slot]
            for mission_a in range(3)
            for mission_b in range(mission_a + 1, 3)
            for slot in range(4)
        )
        expected = -sum(values[oid] * bit for oid, bit in zip(model.option_ids, x))
        expected += penalty * (sum((count - 1) ** 2 for count in counts) + collisions)
        assert qubo_energy(model, x) == expected
        if best is None or expected < best:
            best, minima = expected, [mask]
        elif expected == best:
            minima.append(mask)
    assert best == Fraction(-13, 5) and len(minima) == 6
    audit = audit_mapping(model, source)
    assert audit.binary_assignments == 4096
    assert audit.one_choice_assignments == 64
    assert audit.feasible_assignments == 24
    assert audit.optimal_assignments == 6
    assert audit.ground_matches_classical
    assert audit.ground_energy == "-13/5"


def test_infeasible_mapping_is_not_quantum_success():
    source = scenario("infeasible")
    audit = audit_mapping(compile_qubo(source), source)
    assert audit.feasible_assignments == 0 and not audit.ground_matches_classical
    with pytest.raises(ValueError, match="infeasible"):
        run_qaoa(source)


@pytest.mark.parametrize("name", ["missing-capacity", "resource-outage"])
def test_incomplete_and_event_inputs_rejected(name):
    with pytest.raises(ValueError, match="incomplete|unsupported"):
        compile_qubo(scenario(name))


def test_higher_order_capacity_not_silently_approximated():
    source = changed(lambda data: data["resources"][0].update(capacity=2))
    with pytest.raises(ValueError, match="higher.order|slack"):
        compile_qubo(source)


def test_deadlines_and_explicit_exclusion_mapping():
    def mutate(data):
        data["missions"][0]["deadline_utc"] = "2026-11-04T01:00:00Z"
        data["incompatibilities"] = [
            {
                "constraint_id": "pair",
                "option_ids": ["mission-1-option-1", "mission-2-option-2"],
            }
        ]

    source = changed(mutate)
    model = compile_qubo(source)
    assert len(model.forbidden_options) == 3
    assert audit_mapping(model, source).ground_matches_classical


@pytest.mark.parametrize("limit", [0, True, 1.5, 13])
def test_invalid_qubo_variable_cap(limit):
    with pytest.raises(ValueError):
        compile_qubo(scenario(), max_variables=limit)


def test_qubo_variable_budget_enforced():
    with pytest.raises(ValueError, match="variable"):
        compile_qubo(scenario(), max_variables=6)


@pytest.mark.parametrize(
    "bad_bits", [(0,) * 11, (0,) * 11 + (2,), (0,) * 11 + (True,), (0,) * 11 + (0.5,)]
)
def test_invalid_binary_assignment(bad_bits):
    with pytest.raises(ValueError):
        qubo_energy(compile_qubo(scenario()), bad_bits)


def test_stale_and_tampered_models_rejected():
    original = compile_qubo(scenario())
    source = changed(lambda data: data.update(revision=1))
    with pytest.raises(ValueError):
        audit_mapping(original, source)
    tampered = original.model_copy(update={"offset": "0"})
    with pytest.raises(ValueError):
        audit_mapping(tampered, scenario())


def test_compact_register_preserves_all_assignment_energies():
    model = compile_qubo(scenario())
    compact = compact_model(model)
    assert compact.logical_qubits == 6
    assert len(compact.one_hot_masks) == 64
    assert len(set(compact.one_hot_masks)) == 64
    assert compact.max_z_locality == 4
    assert compact.cx_per_cost_layer > 0
    for basis, mask in enumerate(compact.one_hot_masks):
        assert sum(bits(mask)) == 3
        expected = float(qubo_energy(model, bits(mask)))
        reconstructed = (
            compact.normalized_energies[basis] * compact.energy_scale
            + compact.energy_shift
        )
        assert reconstructed == pytest.approx(expected, abs=1e-12)
        z_energy = sum(
            coef * (-1 if (basis & term).bit_count() % 2 else 1)
            for term, coef in compact.z_terms
        )
        assert z_energy == pytest.approx(compact.normalized_energies[basis], abs=1e-12)


def test_non_power_of_two_options_explicitly_unsupported():
    source = changed(
        lambda data: data.update(
            candidates=[
                c for c in data["candidates"] if not c["option_id"].endswith("-4")
            ]
        )
    )
    with pytest.raises(ValueError, match="power.of.two"):
        compact_model(compile_qubo(source))


@pytest.mark.parametrize("limit", [0, True, 9, 5])
def test_compact_qubit_budget(limit):
    with pytest.raises(ValueError):
        compact_model(compile_qubo(scenario()), max_qubits=limit)


def test_qaoa_single_qubit_known_rotation():
    state = qaoa_statevector((0, 1), (-np.pi / 2,), (np.pi / 4,))
    assert np.abs(state) ** 2 == pytest.approx([1, 0], abs=1e-12)


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_qaoa_matches_independent_dense_matrix_product(depth):
    energies = np.array([0.2, -0.1, 0.8, -0.7])
    gammas = tuple(0.3 * (i + 1) for i in range(depth))
    betas = tuple(-0.2 * (i + 1) for i in range(depth))
    expected = np.ones(4, dtype=complex) / 2
    for gamma, beta in zip(gammas, betas):
        rx = np.array(
            [[np.cos(beta), -1j * np.sin(beta)], [-1j * np.sin(beta), np.cos(beta)]]
        )
        expected = np.kron(rx, rx) @ np.diag(np.exp(-1j * gamma * energies)) @ expected
    actual = qaoa_statevector(energies, gammas, betas)
    assert actual == pytest.approx(expected, abs=1e-12)
    assert np.vdot(actual, actual).real == pytest.approx(1, abs=1e-12)


def test_zero_angles_uniform_and_global_shift_only_global_phase():
    energies = np.arange(8, dtype=float) / 7
    state = qaoa_statevector(energies, (0,), (0,))
    assert np.abs(state) ** 2 == pytest.approx([1 / 8] * 8, abs=1e-12)
    original = qaoa_statevector(energies, (0.4,), (0.7,))
    shifted = qaoa_statevector(energies + 3, (0.4,), (0.7,))
    assert shifted == pytest.approx(original * np.exp(-1j * 1.2), abs=1e-12)


@pytest.mark.parametrize(
    "energies,gammas,betas",
    [
        ([0, 1, 2], [1], [1]),
        ([0], [1], [1]),
        ([0] * 512, [1], [1]),
        ([0, float("nan")], [1], [1]),
        ([0, 1], [], []),
        ([0, 1], [1, 2], [1]),
        ([0, 1], [float("inf")], [1]),
        ([0, 1], [1] * 4, [1] * 4),
    ],
)
def test_invalid_quantum_state_inputs(energies, gammas, betas):
    with pytest.raises(ValueError):
        qaoa_statevector(energies, gammas, betas)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"depth": 0},
        {"depth": 4},
        {"depth": True},
        {"evaluations": 0},
        {"evaluations": 513},
        {"evaluations": 1.5},
        {"shots": 0},
        {"shots": 8193},
        {"shots": True},
        {"seed": -1},
        {"seed": 1.5},
        {"max_qubits": 5},
    ],
)
def test_invalid_run_budgets(kwargs):
    with pytest.raises(ValueError):
        run_qaoa(scenario(), **kwargs)


def test_seeded_run_raw_samples_and_probabilities_are_auditable():
    source = scenario()
    result = run_qaoa(source, evaluations=24, shots=128, seed=17)
    assert result.execution_mode == "noiseless_simulator"
    assert result.logical_qubits == 6 and result.qubo_variables == 12
    assert result.objective_evaluations == 24 and result.shots == 128
    assert result.hardware_jobs == 0 and result.repair_applied is False
    assert sum(s.count for s in result.samples) == 128
    assert sum(s.probability for s in result.samples) == pytest.approx(1, abs=1e-12)
    assert (
        len(result.samples) == 64
    )  # Includes zero-count outcomes for complete evidence.
    assert result.uniform_feasible_probability == pytest.approx(24 / 64, abs=1e-12)
    assert result.uniform_optimal_probability == pytest.approx(6 / 64, abs=1e-12)
    assert result.feasible_probability == pytest.approx(
        sum(s.probability for s in result.samples if s.feasible), abs=1e-12
    )
    assert result.optimal_probability == pytest.approx(
        sum(s.probability for s in result.samples if s.optimal), abs=1e-12
    )
    assert result.sampled_feasible_rate == pytest.approx(
        sum(s.count for s in result.samples if s.feasible) / 128, abs=1e-12
    )
    assert result.sampled_optimal_rate == pytest.approx(
        sum(s.count for s in result.samples if s.optimal) / 128, abs=1e-12
    )
    if result.best_sampled_plan:
        report = validate_plan(result.best_sampled_plan, source)
        assert report.status == "feasible"
        sampled_best = max(s.score for s in result.samples if s.feasible and s.count)
        assert report.score == pytest.approx(sampled_best, abs=1e-12)
    compact = compact_model(compile_qubo(source))
    assert result.rx_gate_count == result.depth * 6
    assert result.cx_gate_estimate == result.depth * compact.cx_per_cost_layer
    assert result.rz_gate_estimate == result.depth * sum(
        mask != 0 for mask, _ in compact.z_terms
    )
    assert QaoaResult.model_validate_json(result.model_dump_json()) == result
    repeated = run_qaoa(source, evaluations=24, shots=128, seed=17)
    assert repeated.model_dump(exclude={"elapsed_s"}) == result.model_dump(
        exclude={"elapsed_s"}
    )
