"""H0 contracts: structural validity is distinct from physical feasibility."""

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.hybrid import (
    Event,
    Plan,
    Scenario,
    Source,
    StateSnapshot,
    TimeWindow,
    canonical_json,
    fingerprint,
    load_scenario,
    save_scenario,
    validate_plan_references,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"


def window(start=0, end=1):
    return {
        "start_utc": f"2026-11-04T{start:02}:00:00Z",
        "end_utc": f"2026-11-04T{end:02}:00:00Z",
    }


def document():
    return {
        "schema_version": 1,
        "scenario_id": "benchmark",
        "revision": 0,
        "description": "Synthetic contract fixture; not a NASA mission plan.",
        "seed": 17,
        "window": window(0, 12),
        "missions": [
            {
                "mission_id": f"m{i}",
                "vehicle_id": f"v{i}",
                "deadline_utc": "2026-11-04T12:00:00Z",
                "source_ids": ["example"],
            }
            for i in range(3)
        ],
        "resources": [
            {
                "resource_id": "antenna",
                "mode": "concurrent",
                "unit": "channel",
                "capacity": 1,
                "availability": [window(0, 12)],
                "source_ids": ["example"],
            }
        ],
        "objectives": [
            {
                "metric": "science",
                "unit": "score",
                "direction": "maximize",
                "weight": 1,
                "scale": 10,
            }
        ],
        "candidates": [
            {
                "option_id": f"m{i}-o{j}",
                "mission_id": f"m{i}",
                "site": {
                    "site_id": "illustrative",
                    "latitude_deg": -86,
                    "longitude_deg_east": 0,
                },
                "window": window(j, j + 1),
                "demands": [
                    {
                        "resource_id": "antenna",
                        "amount": 1,
                        "unit": "channel",
                        "window": window(j, j + 1),
                    }
                ],
                "metrics": {"science": 10 - j},
                "source_ids": ["example"],
            }
            for i in range(3)
            for j in range(4)
        ],
        "sources": [
            {
                "source_id": "example",
                "kind": "synthetic",
                "description": "Explicit teaching assumptions.",
            }
        ],
    }


def state(**overrides):
    value = {
        "state_id": "state-1",
        "vehicle_id": "v0",
        "revision": 0,
        "epoch_utc": "2026-11-04T00:00:00Z",
        "record_kind": "estimate",
        "position_m": [1, 2, 3],
        "velocity_mps": [0, 0, 0],
        "total_mass_kg": 100,
        "propellant_kg": 20,
        "battery_energy_j": 10,
        "battery_capacity_j": 20,
        "source_ids": ["example"],
    }
    return value | overrides


def plan(scenario, **overrides):
    value = {
        "plan_id": "p1",
        "scenario_id": scenario.scenario_id,
        "scenario_revision": scenario.revision,
        "input_fingerprint": fingerprint(scenario),
        "state_revisions": {s.state_id: s.revision for s in scenario.states},
        "selected_option_ids": ["m0-o0", "m1-o1", "m2-o2"],
        "execution_mode": "classical",
        "solver_name": "manual-fixture",
    }
    return Plan.model_validate(value | overrides)


def test_common_problem_roundtrip_and_schema(tmp_path):
    scenario = Scenario.model_validate(document())
    assert len(scenario.missions) == 3 and len(scenario.candidates) == 12
    assert scenario.selection_rule == "exactly_one_per_mission"
    assert scenario.window.start_utc == datetime(2026, 11, 4, tzinfo=timezone.utc)
    path = tmp_path / "scenario.json"
    save_scenario(scenario, path)
    assert load_scenario(path) == scenario
    assert Scenario.model_validate_json(scenario.model_dump_json()) == scenario
    schema = Scenario.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "Candidate" in schema["$defs"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("revision", -1),
        ("revision", True),
        ("scenario_id", "bad id"),
        ("seed", -1),
        ("seed", "3"),
        ("description", "   "),
        ("missions", []),
        ("candidates", []),
        ("objectives", []),
    ],
)
def test_invalid_envelope(field, value):
    data = document()
    data[field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "time", ["2026-11-04T00:00:00", "2026-11-04T01:00:00+01:00", 1793750400, True]
)
def test_explicit_utc_only(time):
    with pytest.raises(ValidationError):
        TimeWindow.model_validate(window() | {"start_utc": time})


@pytest.mark.parametrize("interval", [window(1, 1), window(2, 1)])
def test_time_windows_must_be_positive(interval):
    with pytest.raises(ValidationError):
        TimeWindow.model_validate(interval)


def test_adjacent_resource_windows_are_allowed_but_overlap_is_not():
    data = document()
    data["resources"][0]["availability"] = [window(0, 2), window(2, 12)]
    Scenario.model_validate(data)
    data["resources"][0]["availability"][1] = window(1, 12)
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "collection", ["missions", "candidates", "resources", "sources", "objectives"]
)
def test_duplicate_identifiers(collection):
    data = document()
    data[collection].append(copy.deepcopy(data[collection][0]))
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra_field=1),
        lambda d: d["candidates"][0].update(mission_id="missing"),
        lambda d: d["candidates"][0]["demands"][0].update(resource_id="missing"),
        lambda d: d["candidates"][0].update(source_ids=["missing"]),
        lambda d: d["missions"][0].update(source_ids=["missing"]),
        lambda d: d["resources"][0].update(source_ids=["missing"]),
        lambda d: d["candidates"][0].update(source_ids=["example", "example"]),
        lambda d: d["candidates"][0]["metrics"].update(science=float("nan")),
        lambda d: d["candidates"][0]["metrics"].update(science=True),
        lambda d: d["candidates"][0].update(metrics={}),
        lambda d: d["candidates"][0]["metrics"].update(extra_metric=1),
        lambda d: d["resources"][0].update(capacity=-1),
        lambda d: d["resources"][0].update(capacity="1"),
        lambda d: d["resources"][0].update(mode="budget"),
        lambda d: d["candidates"][0]["demands"][0].update(unit="J"),
        lambda d: d["candidates"][0]["demands"][0].update(amount=-1),
        lambda d: d["candidates"][0]["demands"][0].update(window=window(0, 2)),
        lambda d: d["candidates"][0].update(window=window(12, 13)),
        lambda d: d["resources"][0].update(availability=[window(0, 13)]),
        lambda d: d["objectives"][0].update(weight=0),
        lambda d: d["objectives"][0].update(scale=0),
        lambda d: d["candidates"][0]["site"].update(latitude_deg=-91),
        lambda d: d["candidates"][0]["site"].update(longitude_deg_east=360),
        lambda d: d["candidates"][0]["site"].update(reference_frame="J2000"),
    ],
)
def test_nested_invalid_data(change):
    data = document()
    change(data)
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_contradictory_or_missing_physical_inputs_are_valid_problem_descriptions():
    data = document()
    data["resources"][0]["capacity"] = 0
    data["missions"][0]["deadline_utc"] = "2026-11-04T00:30:00Z"
    scenario = Scenario.model_validate(data)
    assert scenario.resources[0].capacity == 0
    data["resources"][0]["capacity"] = None
    data["candidates"][0]["metrics"]["science"] = None
    missing = Scenario.model_validate(data)
    assert missing.resources[0].capacity is None
    assert missing.candidates[0].metrics["science"] is None
    assert fingerprint(missing) != fingerprint(scenario)


def test_budget_resource_and_polar_coordinates():
    data = document()
    data["resources"][0].update(mode="budget", unit="J")
    for candidate in data["candidates"]:
        candidate["demands"][0]["unit"] = "J"
        candidate["site"]["latitude_deg"] = -90
    Scenario.model_validate(data)


def test_at_most_one_references_are_distinct_and_known():
    data = document()
    data["incompatibilities"] = [
        {"constraint_id": "pair", "option_ids": ["m0-o0", "m1-o0"]}
    ]
    Scenario.model_validate(data)
    for options in [["m0-o0"], ["m0-o0", "m0-o0"], ["m0-o0", "missing"]]:
        data["incompatibilities"][0]["option_ids"] = options
        with pytest.raises(ValidationError):
            Scenario.model_validate(data)


def test_source_lineage_must_resolve_without_cycles():
    data = document()
    data["sources"].append(
        {
            "source_id": "derived",
            "kind": "derived",
            "description": "Derived example.",
            "version": "model-v1",
            "parent_source_ids": ["example"],
        }
    )
    Scenario.model_validate(data)
    data["sources"][1]["parent_source_ids"] = ["missing"]
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)
    data["sources"][1]["parent_source_ids"] = ["derived"]
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)
    data["sources"][1]["parent_source_ids"] = ["example"]
    data["sources"][0]["parent_source_ids"] = ["derived"]
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "source",
    [
        {"source_id": "x", "kind": "observed", "description": "Unknown origin"},
        {
            "source_id": "x",
            "kind": "derived",
            "description": "No parents",
            "version": "v1",
        },
        {"source_id": "x", "kind": "synthetic", "description": "", "sha256": "no"},
    ],
)
def test_incomplete_provenance_is_rejected(source):
    with pytest.raises(ValidationError):
        Source.model_validate(source)


@pytest.mark.parametrize(
    "overrides",
    [
        {"propellant_kg": 101},
        {"battery_energy_j": 21},
        {"total_mass_kg": 0},
        {"available_thrust_n": -1},
        {"position_m": [1, 2]},
        {"position_m": [1, 2, float("inf")]},
        {"position_std_m": [-1, 0, 0]},
        {"record_kind": "observation"},
        {"record_kind": "ground_truth", "position_std_m": [1, 1, 1]},
        {"record_kind": "estimate", "sensor_id": "sensor"},
    ],
)
def test_invalid_vehicle_states(overrides):
    with pytest.raises(ValidationError):
        StateSnapshot.model_validate(state(**overrides))


def test_missing_state_and_zero_values_are_distinct():
    snapshot = StateSnapshot.model_validate(
        state(propellant_kg=None, available_thrust_n=0)
    )
    assert snapshot.propellant_kg is None and snapshot.available_thrust_n == 0
    observation = StateSnapshot.model_validate(
        state(
            record_kind="observation",
            sensor_id="lidar",
            sensor_health="degraded",
            position_std_m=[1, 2, 3],
        )
    )
    assert observation.record_kind == "observation"
    with pytest.raises(ValidationError):
        StateSnapshot.model_validate(
            state(
                position_m=None,
                velocity_mps=None,
                total_mass_kg=None,
                propellant_kg=None,
                battery_energy_j=None,
                battery_capacity_j=None,
            )
        )


def test_events_are_typed_linked_and_ordered():
    data = document()
    event = {
        "event_id": "outage",
        "epoch_utc": "2026-11-04T01:00:00Z",
        "sequence": 0,
        "payload": {
            "kind": "resource_outage",
            "resource_id": "antenna",
            "window": window(1, 2),
        },
        "source_ids": ["example"],
    }
    data["events"] = [event]
    Scenario.model_validate(data)
    for payload in [
        {"kind": "thrust_loss", "vehicle_id": "v0", "available_thrust_n": 0},
        {
            "kind": "sensor_degradation",
            "vehicle_id": "v0",
            "sensor_id": "lidar",
            "position_std_m": [1, 1, 1],
        },
    ]:
        Scenario.model_validate(data | {"events": [event | {"payload": payload}]})
    bad = copy.deepcopy(event)
    bad["payload"]["resource_id"] = "missing"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data | {"events": [bad]})
    with pytest.raises(ValidationError):
        Event.model_validate(event | {"payload": {"kind": "arbitrary"}})
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            data | {"events": [event, event | {"event_id": "other"}]}
        )
    late = event | {
        "event_id": "late",
        "epoch_utc": "2026-11-04T02:00:00Z",
        "sequence": 0,
        "payload": {"kind": "thrust_loss", "vehicle_id": "v0", "available_thrust_n": 1},
    }
    with pytest.raises(ValidationError):
        Scenario.model_validate(data | {"events": [late, event | {"sequence": 1}]})


def test_state_references_and_plan_identity():
    data = document()
    data["states"] = [state()]
    scenario = Scenario.model_validate(data)
    result = plan(scenario)
    validate_plan_references(result, scenario)
    assert result.validation_status == "unvalidated"
    for change in [
        {"scenario_id": "other"},
        {"scenario_revision": 1},
        {"input_fingerprint": "0" * 64},
        {"state_revisions": {}},
        {"selected_option_ids": ["missing"]},
    ]:
        with pytest.raises(ValueError):
            validate_plan_references(plan(scenario, **change), scenario)
    with pytest.raises(ValidationError):
        plan(scenario, selected_option_ids=["m0-o0", "m0-o0"])
    with pytest.raises(ValidationError):
        plan(scenario, validation_status="valid")
    data["states"][0]["vehicle_id"] = "missing"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_plan_reference_validation_does_not_claim_feasibility():
    scenario = Scenario.model_validate(document())
    # Two options of the same mission are structurally known, but infeasible under H1.
    result = plan(scenario, selected_option_ids=["m0-o0", "m0-o1"])
    validate_plan_references(result, scenario)
    assert result.validation_status == "unvalidated"


def test_canonicalization_is_stable_but_preserves_vectors_and_values():
    data = document()
    data["states"] = [state()]
    first = Scenario.model_validate(data)
    shuffled = copy.deepcopy(data)
    for key in ["missions", "candidates", "resources", "sources", "objectives"]:
        shuffled[key].reverse()
    shuffled["window"]["start_utc"] = "2026-11-04T00:00:00+00:00"
    shuffled["candidates"][0]["site"]["longitude_deg_east"] = -0.0
    second = Scenario.model_validate(shuffled)
    assert canonical_json(first) == canonical_json(second)
    assert (
        fingerprint(first) == hashlib.sha256(canonical_json(first).encode()).hexdigest()
    )
    assert json.loads(canonical_json(first))["schema_version"] == 1
    changed = copy.deepcopy(data)
    changed["states"][0]["position_m"] = [3, 2, 1]
    assert fingerprint(first) != fingerprint(Scenario.model_validate(changed))
    for key, value in [
        ("revision", 1),
        ("seed", 19),
        ("description", "Changed source context"),
    ]:
        assert fingerprint(first) != fingerprint(
            Scenario.model_validate(data | {key: value})
        )


def test_mutated_nested_data_is_revalidated_before_hashing():
    scenario = Scenario.model_validate(document())
    scenario.candidates[0].metrics["science"] = float("nan")
    with pytest.raises(ValueError):
        fingerprint(scenario)


def test_loading_rejects_duplicate_json_keys_and_invalid_files(tmp_path):
    path = tmp_path / "input.json"
    for text in [
        '{"scenario_id":"a","scenario_id":"b"}',
        "[]",
        "{broken",
        '{"seed":NaN}',
    ]:
        path.write_text(text)
        with pytest.raises(ValueError):
            load_scenario(path)
    with pytest.raises(OSError):
        load_scenario(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "name",
    [
        "independent",
        "shared-conflict",
        "infeasible",
        "missing-capacity",
        "resource-outage",
    ],
)
def test_shipped_examples_are_portable_and_explicitly_synthetic(name):
    scenario = load_scenario(EXAMPLES / f"{name}.json")
    assert len(scenario.missions) == 3 and len(scenario.candidates) == 12
    assert all(s.kind == "synthetic" for s in scenario.sources)
    assert Scenario.model_validate_json(canonical_json(scenario)) == scenario
    if name == "independent":
        assert scenario.resources[0].capacity == 3
    if name == "infeasible":
        assert scenario.resources[0].capacity == 0
    if name == "missing-capacity":
        assert scenario.resources[0].capacity is None
    if name == "resource-outage":
        assert scenario.events[0].payload.kind == "resource_outage"
