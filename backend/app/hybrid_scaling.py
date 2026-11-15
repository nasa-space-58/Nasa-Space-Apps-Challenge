"""R10 scalable problem families, pairwise QUBO resources and a HiGHS MILP baseline.

Synthetic instances only. The pairwise model is a direct, enumeration-free
construction for static instances whose hard rules are exactly unary/pairwise.
The MILP baseline (scipy.optimize.milp, HiGHS) is a classical reference whose
answers are always revalidated by the independent H1 validator.
"""

import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from functools import lru_cache
from itertools import combinations
from time import perf_counter
from typing import Annotated, Literal

import numpy as np
from pydantic import Field
from scipy.optimize import Bounds, LinearConstraint, milp

from shared.hybrid import (
    Candidate,
    Contract,
    Demand,
    Identifier,
    Incompatibility,
    Mission,
    NonNegative,
    Objective,
    Plan,
    Resource,
    Scenario,
    Site,
    Source,
    TimeWindow,
    fingerprint,
)

from .hybrid_planning import (
    Issue,
    ValidationReport,
    _blocked_status,
    _blockers,
    _candidate_score,
    validate_plan,
)

Probability = Annotated[float, Field(strict=True, ge=0, le=1)]

MILP_SOLVER_NAME = "r10-highs-milp-v1"
GENERATOR_VERSION = "r10-generator-v1"
GENERATOR_START_UTC = datetime(2030, 1, 1, tzinfo=timezone.utc)
SOURCE_ID = "r10-synthetic-generator"
SECONDS_PER_HOUR = 3600  # Definition of the hour; not a physical constant.
# HiGHS works in floating point; incumbents farther than this from 0/1 are rejected.
INTEGRALITY_TOLERANCE = 1e-6


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

    Uses Python's random.Random(spec.seed) (stable across NumPy versions). IDs are
    mission-NN and mission-NN-option-KK (1-based, zero-padded); antennas are
    antenna-NN with capacity 1 channel over the whole window, starting
    2030-01-01T00:00:00Z UTC. Objectives: science_value (integer 1..20, score,
    maximize, weight 1, scale 10) and waiting_s (slot start minus scenario
    start, s, minimize, weight 1, scale 36000).
    Every mission keeps at least one option ending by its deadline. Raises
    ValueError for invalid specs. No feasibility claim is attached.
    """
    spec = InstanceSpec.model_validate(spec.model_dump())
    rng = random.Random(spec.seed)
    start = GENERATOR_START_UTC
    end = start + timedelta(hours=spec.slots)
    whole = TimeWindow(start_utc=start, end_utc=end)
    sources = (SOURCE_ID,)
    spec_json = json.dumps(spec.model_dump(mode="json"), sort_keys=True)
    missions, candidates = [], []
    for m in range(1, spec.missions + 1):
        mission_id = f"mission-{m:02d}"
        rows = [
            (
                k,
                rng.randrange(spec.slots),
                rng.randrange(spec.antennas) + 1,
                rng.randint(1, 20),
            )
            for k in range(1, spec.options_per_mission + 1)
        ]
        earliest_end_h = min(slot for _, slot, _, _ in rows) + 1
        latest_h = math.ceil(spec.slots * (1 - spec.deadline_tightness))
        deadline_h = rng.randint(max(earliest_end_h, latest_h), spec.slots)
        missions.append(
            Mission(
                mission_id=mission_id,
                vehicle_id=f"vehicle-{m:02d}",
                deadline_utc=start + timedelta(hours=deadline_h),
                source_ids=sources,
            )
        )
        for k, slot, antenna, science in rows:
            window = TimeWindow(
                start_utc=start + timedelta(hours=slot),
                end_utc=start + timedelta(hours=slot + 1),
            )
            candidates.append(
                Candidate(
                    option_id=f"{mission_id}-option-{k:02d}",
                    mission_id=mission_id,
                    site=Site(
                        site_id=f"synthetic-site-{m:02d}-{k:02d}",
                        latitude_deg=-85.0,
                        longitude_deg_east=float((m * 37 + k * 11) % 360),
                    ),
                    window=window,
                    demands=(
                        Demand(
                            resource_id=f"antenna-{antenna:02d}",
                            amount=1.0,
                            unit="channel",
                            window=window,
                        ),
                    ),
                    metrics={
                        "science_value": float(science),
                        "waiting_s": float(slot * SECONDS_PER_HOUR),
                    },
                    source_ids=sources,
                )
            )
    ordered = sorted(candidates, key=lambda c: c.option_id)
    incompatibilities = []
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            if first.mission_id == second.mission_id:
                continue
            if rng.random() < spec.incompatibility_probability:
                incompatibilities.append(
                    Incompatibility(
                        constraint_id=f"incompat-{first.option_id}-{second.option_id}",
                        option_ids=(first.option_id, second.option_id),
                    )
                )
    digest = hashlib.sha256(spec_json.encode()).hexdigest()
    return Scenario(
        scenario_id=f"r10-{spec.family[:48]}-{digest[:16]}",
        revision=0,
        description=(
            f"R10 synthetic benchmark instance: {spec.missions} missions x "
            f"{spec.options_per_mission} options, {spec.slots} one-hour slots, "
            f"{spec.antennas} capacity-1 antennas. No optimizer has run."
        ),
        seed=spec.seed,
        window=whole,
        missions=tuple(missions),
        candidates=tuple(candidates),
        resources=tuple(
            Resource(
                resource_id=f"antenna-{a:02d}",
                mode="concurrent",
                unit="channel",
                capacity=1.0,
                availability=(whole,),
                source_ids=sources,
            )
            for a in range(1, spec.antennas + 1)
        ),
        objectives=(
            Objective(
                metric="science_value",
                unit="score",
                direction="maximize",
                weight=1.0,
                scale=10.0,
            ),
            Objective(
                metric="waiting_s",
                unit="s",
                direction="minimize",
                weight=1.0,
                scale=36000.0,
            ),
        ),
        incompatibilities=tuple(incompatibilities),
        sources=(
            Source(
                source_id=SOURCE_ID,
                kind="synthetic",
                description=(
                    f"{GENERATOR_VERSION} seeded synthetic family member {spec_json}. "
                    "Not a NASA mission, DSN booking or validated landing site."
                ),
            ),
        ),
    )


def build_pairwise_model(scenario: Scenario) -> PairwiseModel:
    """Derive unary/pairwise hard rules without enumerating assignments.

    Supported only when exactness is guaranteed: each resource is either
    non-binding (the sum over missions of the largest per-option peak demand,
    or total joules, is within capacity) or every positive demand exceeds half
    its known capacity, so any violation already appears in a single option or
    pair. Conflict pairs involving a forbidden option are omitted, as in H2. Raise ValueError for events, unknown
    data, or rules needing slack variables or higher-order terms.
    """
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    blockers = _blockers(scenario)
    if blockers:
        raise ValueError(
            f"Pairwise model input {_blocked_status(blockers)}: "
            + "; ".join(issue.message for issue in blockers)
        )
    candidates = tuple(sorted(scenario.candidates, key=lambda c: c.option_id))
    ids = tuple(c.option_id for c in candidates)
    missions = tuple(sorted(scenario.missions, key=lambda m: m.mission_id))
    groups = tuple(
        tuple(i for i, c in enumerate(candidates) if c.mission_id == m.mission_id)
        for m in missions
    )
    mission_of = {i: c.mission_id for i, c in enumerate(candidates)}
    deadlines = {m.mission_id: m.deadline_utc for m in missions}
    scores = tuple(_candidate_score(c, scenario) for c in candidates)
    penalty = 1 + sum((abs(s) for s in scores), Fraction(0))
    forbidden = {
        i
        for i, c in enumerate(candidates)
        if c.window.end_utc > deadlines[c.mission_id]
    }
    conflicts = set()
    for resource in scenario.resources:
        positive = [
            (i, d)
            for i, c in enumerate(candidates)
            for d in c.demands
            if d.resource_id == resource.resource_id and d.amount > 0
        ]
        for i, demand in positive:
            if not _covered(demand.window, resource.availability):
                forbidden.add(i)
        if not positive:
            continue
        capacity = _exact(resource.capacity)
        if resource.mode == "budget":
            loads = {}
            for i, demand in positive:
                loads[i] = loads.get(i, Fraction(0)) + _exact(demand.amount)
        else:
            loads = {
                i: _peak([d for j, d in positive if j == i])
                for i in {i for i, _ in positive}
            }
        worst = sum(
            (
                max((loads.get(i, Fraction(0)) for i in group), default=Fraction(0))
                for group in groups
            ),
            Fraction(0),
        )
        if worst <= capacity:
            continue  # No one-per-mission selection can exceed this resource.
        # Exactness: with every concurrent demand (or every budget load) above
        # half capacity, any violating set already contains a violating pair.
        amounts = (
            loads.values()
            if resource.mode == "budget"
            else (_exact(d.amount) for _, d in positive)
        )
        if any(2 * amount <= capacity for amount in amounts):
            raise ValueError(
                f"Resource {resource.resource_id} requires slack variables or "
                "higher-order terms; pairwise mapping is not exact"
            )
        forbidden.update(i for i, load in loads.items() if load > capacity)
        if resource.mode == "budget":
            pairs = combinations(sorted(loads), 2)
        else:
            pairs = (
                (i, j)
                for (i, a), (j, b) in combinations(positive, 2)
                if i != j
                and a.window.start_utc < b.window.end_utc
                and b.window.start_utc < a.window.end_utc
            )
        conflicts.update(
            (min(i, j), max(i, j)) for i, j in pairs if mission_of[i] != mission_of[j]
        )
    index = {oid: i for i, oid in enumerate(ids)}
    for constraint in scenario.incompatibilities:
        members = sorted(index[oid] for oid in constraint.option_ids)
        conflicts.update(
            (i, j)
            for i, j in combinations(members, 2)
            if mission_of[i] != mission_of[j]
        )
    return PairwiseModel(
        input_fingerprint=fingerprint(scenario),
        option_ids=ids,
        mission_ids=tuple(m.mission_id for m in missions),
        mission_groups=groups,
        scores=tuple(str(s) for s in scores),
        penalty=str(penalty),
        forbidden_options=tuple(sorted(forbidden)),
        # Forbidden options already carry a penalty; H2 also omits their pairs.
        conflict_pairs=tuple(
            sorted(
                (i, j)
                for i, j in conflicts
                if i not in forbidden and j not in forbidden
            )
        ),
    )


def choice_energy(model: PairwiseModel, choice: tuple[int, ...]):
    """Exact Fraction energy of one option index per mission (compact domain).

    E = -sum(score) + penalty * (forbidden selected + conflicting pairs selected).
    Raise ValueError for a wrong length or an index outside its mission group.
    """
    tables = _tables(model)
    groups = model.mission_groups
    if type(choice) is not tuple or len(choice) != len(groups):
        raise ValueError("Choice must be a tuple with one option index per mission")
    for value, group in zip(choice, groups):
        if type(value) is not int or not 0 <= value < len(group):
            raise ValueError(f"Option index {value!r} outside mission group")
    return _indices_energy(tables, [g[c] for g, c in zip(groups, choice)])


def one_hot_energy(model: PairwiseModel, bits: tuple[int, ...]):
    """Exact Fraction energy of the one-hot QUBO (H2 penalty construction)."""
    tables = _tables(model)
    if len(bits) != len(model.option_ids) or any(
        type(bit) is not int or bit not in (0, 1) for bit in bits
    ):
        raise ValueError("Assignment must contain one integer binary bit per variable")
    scores, forbidden, conflicts, penalty = tables
    energy = -sum((scores[i] for i, bit in enumerate(bits) if bit), Fraction(0))
    energy += penalty * sum(
        (sum(bits[i] for i in group) - 1) ** 2 for group in model.mission_groups
    )
    energy += penalty * sum(bits[i] for i in forbidden)
    energy += penalty * sum(bits[i] * bits[j] for i, j in conflicts)
    return energy


def compact_energies(model: PairwiseModel, *, max_qubits: int = 8) -> tuple:
    """Return exact Fraction energies of every compact basis state, little-endian.

    Mission registers are ordered by mission ID with the first mission in the
    lowest bits. Requires power-of-two option counts; hard cap 8 qubits.
    """
    _integer_range(max_qubits, "max_qubits", 1, 8)
    widths = _widths(model)
    if widths is None:
        raise ValueError("Compact encoding requires power-of-two options per mission")
    qubits = sum(widths)
    if not 1 <= qubits <= max_qubits:
        raise ValueError(f"Compact register needs {qubits} qubits; limit {max_qubits}")
    tables = _tables(model)
    energies = []
    for basis in range(1 << qubits):
        offset, indices = 0, []
        for group, width in zip(model.mission_groups, widths):
            indices.append(group[(basis >> offset) & ((1 << width) - 1)])
            offset += width
        energies.append(_indices_energy(tables, indices))
    return tuple(energies)


def resource_profile(model: PairwiseModel) -> ResourceProfile:
    """Count one-hot QUBO terms and exact compact Pauli-Z expansion resources.

    The compact Z expansion is computed per mission and per conflicting mission
    pair with exact rational Walsh-Hadamard transforms; no 2^n enumeration.
    """
    groups = model.mission_groups
    scores, forbidden, conflicts, penalty = _tables(model)
    cross = sum(len(a) * len(b) for a, b in combinations(groups, 2))
    common = dict(
        input_fingerprint=model.input_fingerprint,
        missions=len(groups),
        options=len(model.option_ids),
        assignments=math.prod(len(g) for g in groups),
        one_hot_variables=len(model.option_ids),
        one_hot_quadratic_terms=sum(math.comb(len(g), 2) for g in groups)
        + len(conflicts),
        forbidden_options=len(forbidden),
        conflict_pairs=len(conflicts),
        conflict_density=len(conflicts) / cross if cross else 0.0,
    )
    widths = _widths(model)
    if widths is None:
        return ResourceProfile(
            compact_qubits=None,
            compact_z_terms=None,
            compact_max_z_locality=None,
            compact_locality_histogram=None,
            cx_per_layer=None,
            rz_per_layer=None,
            rx_per_layer=None,
            statevector_amplitudes=None,
            compact_unsupported_reason=(
                "Compact binary registers require power-of-two options per mission"
            ),
            **common,
        )
    offsets = [sum(widths[:m]) for m in range(len(widths))]
    position = {i: (m, c) for m, g in enumerate(groups) for c, i in enumerate(g)}
    coefficients = {}

    def accumulate(values, registers):
        # Exact Walsh-Hadamard transform of one local function; map local masks
        # (concatenated register bits) back to global qubit positions.
        for local, value in enumerate(_walsh_hadamard(values)):
            if not value:
                continue
            mask, shift = 0, 0
            for m in registers:
                mask |= ((local >> shift) & ((1 << widths[m]) - 1)) << offsets[m]
                shift += widths[m]
            coefficients[mask] = coefficients.get(mask, Fraction(0)) + value

    for m, group in enumerate(groups):
        accumulate(
            [-scores[i] + (penalty if i in forbidden else 0) for i in group], (m,)
        )
    by_pair = {}
    for i, j in conflicts:
        (a, ca), (b, cb) = sorted((position[i], position[j]))
        by_pair.setdefault((a, b), []).append((ca, cb))
    for (a, b), cells in sorted(by_pair.items()):
        values = [Fraction(0)] * (1 << (widths[a] + widths[b]))
        for ca, cb in cells:
            values[ca | (cb << widths[a])] += penalty
        accumulate(values, (a, b))
    terms = [mask for mask, value in coefficients.items() if mask and value]
    histogram = {}
    for mask in terms:
        key = str(mask.bit_count())
        histogram[key] = histogram.get(key, 0) + 1
    qubits = sum(widths)
    return ResourceProfile(
        compact_qubits=qubits,
        compact_z_terms=len(terms),
        compact_max_z_locality=max((m.bit_count() for m in terms), default=0),
        compact_locality_histogram=dict(sorted(histogram.items())),
        cx_per_layer=sum(2 * (m.bit_count() - 1) for m in terms),
        rz_per_layer=len(terms),
        rx_per_layer=qubits,
        statevector_amplitudes=1 << qubits,
        **common,
    )


def solve_milp(scenario: Scenario, *, time_limit_s: float = 10.0) -> MilpResult:
    """Solve the original linear 0/1 program with HiGHS; revalidate with H1.

    Rows: one option per mission, deadline/availability bounds, at-most-one sets,
    per-interval channel capacity and cumulative joule budgets. objective_bound
    is HiGHS's dual bound expressed as an upper bound on the maximization score.
    A validator disagreement raises RuntimeError. time_limit_s must be a finite
    float in (0, 3600]; otherwise ValueError.
    """
    started = perf_counter()
    if (
        type(time_limit_s) is not float
        or not math.isfinite(time_limit_s)
        or not 0 < time_limit_s <= 3600
    ):
        raise ValueError("time_limit_s must be a finite float in (0, 3600]")
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    digest = fingerprint(scenario)
    candidates = tuple(sorted(scenario.candidates, key=lambda c: c.option_id))
    n = len(candidates)
    common = dict(input_fingerprint=digest, variables=n, time_limit_s=time_limit_s)
    blockers = _blockers(scenario)
    if blockers:
        return MilpResult(
            status=_blocked_status(blockers),
            constraints=0,
            issues=blockers,
            elapsed_s=perf_counter() - started,
            **common,
        )
    rows, lower, upper = _milp_rows(scenario, candidates)
    bounds_upper = np.ones(n)
    deadlines = {m.mission_id: m.deadline_utc for m in scenario.missions}
    resources = {r.resource_id: r for r in scenario.resources}
    for i, c in enumerate(candidates):
        late = c.window.end_utc > deadlines[c.mission_id]
        uncovered = any(
            d.amount > 0
            and not _covered(d.window, resources[d.resource_id].availability)
            for d in c.demands
        )
        if late or uncovered:
            bounds_upper[i] = 0
    matrix = np.zeros((len(rows), n))
    for r, row in enumerate(rows):
        for i, value in row.items():
            matrix[r, i] = value
    objective = -np.array([float(_candidate_score(c, scenario)) for c in candidates])
    result = milp(
        objective,
        integrality=np.ones(n),
        bounds=Bounds(np.zeros(n), bounds_upper),
        constraints=LinearConstraint(matrix, np.array(lower), np.array(upper)),
        options={"time_limit": time_limit_s, "mip_rel_gap": 0.0, "disp": False},
    )
    if result.status not in (0, 1, 2):
        raise RuntimeError(f"HiGHS returned status {result.status}: {result.message}")
    plan = validation = None
    if result.x is not None:
        values = np.asarray(result.x)
        if np.max(np.abs(values - np.round(values))) > INTEGRALITY_TOLERANCE:
            raise RuntimeError("HiGHS returned a non-integral incumbent")
        ids = tuple(c.option_id for c, x in zip(candidates, values) if x > 0.5)
        plan = _plan(ids, scenario, digest)
        validation = validate_plan(plan, scenario)
        if validation.status != "feasible":
            raise RuntimeError(
                "MILP incumbent disagrees with the independent validator: "
                + "; ".join(issue.message for issue in validation.issues)
            )
    status = {0: "optimal", 1: "limit_reached", 2: "infeasible"}[result.status]
    bound = getattr(result, "mip_dual_bound", None)
    if status == "optimal" and (bound is None or not math.isfinite(bound)):
        bound = result.fun
    gap = getattr(result, "mip_gap", None)
    issues = ()
    if status == "limit_reached":
        issues = (
            Issue(
                code="search_limit",
                message=f"HiGHS stopped at the {time_limit_s} s limit; optimum not certified.",
            ),
        )
    return MilpResult(
        status=status,
        constraints=len(rows),
        plan=plan,
        validation=validation,
        objective_bound=-float(bound)
        if bound is not None and math.isfinite(bound)
        else None,
        mip_gap=float(gap) if gap is not None and math.isfinite(gap) else None,
        issues=issues,
        elapsed_s=perf_counter() - started,
        **common,
    )


def scaling_sweep(
    specs: tuple[InstanceSpec, ...], *, milp_time_limit_s: float = 10.0
) -> tuple[ScalingPoint, ...]:
    """Generate each spec, profile its QUBO/circuit size and time the MILP.

    Unsupported pairwise mappings are reported (profile None), never dropped.
    Raise ValueError for an empty or oversized (more than 64) spec tuple.
    """
    if not 1 <= len(specs) <= 64:
        raise ValueError("scaling_sweep requires 1..64 specs")
    points = []
    for spec in specs:
        scenario = generate_instance(spec)
        try:
            profile = resource_profile(build_pairwise_model(scenario))
        except ValueError:
            # Documented unsupported mapping: report it rather than drop the point.
            profile = None
        result = solve_milp(scenario, time_limit_s=milp_time_limit_s)
        points.append(
            ScalingPoint(
                spec=spec,
                input_fingerprint=result.input_fingerprint,
                pairwise_supported=profile is not None,
                profile=profile,
                milp_status=result.status,
                milp_variables=result.variables,
                milp_constraints=result.constraints,
                milp_elapsed_s=result.elapsed_s,
            )
        )
    return tuple(points)


def _exact(value):
    # Exact rational of the canonical decimal spelling, as in H1.
    return Fraction(str(value))


def _integer_range(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer in [{lower}, {upper}]")


def _covered(window, availability):
    cursor = window.start_utc
    for available in sorted(availability, key=lambda w: w.start_utc):
        if available.end_utc <= cursor:
            continue
        if available.start_utc > cursor:
            return False
        cursor = available.end_utc
        if cursor >= window.end_utc:
            return True
    return False


def _active_sums(demands):
    """Yield (start, end, summed amount) over elementary half-open intervals."""
    boundaries = sorted(
        {t for d in demands for t in (d.window.start_utc, d.window.end_utc)}
    )
    for start, end in zip(boundaries, boundaries[1:]):
        active = [d for d in demands if d.window.start_utc <= start < d.window.end_utc]
        yield start, end, active


def _peak(demands):
    return max(
        (
            sum((_exact(d.amount) for d in active), Fraction(0))
            for _, _, active in _active_sums(demands)
        ),
        default=Fraction(0),
    )


@lru_cache(maxsize=64)
def _tables(model):
    return (
        tuple(Fraction(v) for v in model.scores),
        frozenset(model.forbidden_options),
        frozenset(model.conflict_pairs),
        Fraction(model.penalty),
    )


def _indices_energy(tables, indices):
    scores, forbidden, conflicts, penalty = tables
    violations = sum(i in forbidden for i in indices) + sum(
        (min(a, b), max(a, b)) in conflicts
        for n, a in enumerate(indices)
        for b in indices[n + 1 :]
    )
    return -sum((scores[i] for i in indices), Fraction(0)) + penalty * violations


def _widths(model):
    widths = []
    for group in model.mission_groups:
        size = len(group)
        if size < 1 or size & (size - 1):
            return None
        widths.append(size.bit_length() - 1)
    return widths


def _walsh_hadamard(values):
    """Exact coefficients c_S with f(x) = sum_S c_S prod_{q in S} Z_q."""
    coefficients = [Fraction(v) for v in values]
    step = 1
    while step < len(coefficients):
        for start in range(0, len(coefficients), 2 * step):
            for k in range(start, start + step):
                left, right = coefficients[k], coefficients[k + step]
                coefficients[k], coefficients[k + step] = left + right, left - right
        step *= 2
    return [c / len(coefficients) for c in coefficients]


def _milp_rows(scenario, candidates):
    """Linear rows of the original problem (not derived from the validator)."""
    index = {c.option_id: i for i, c in enumerate(candidates)}
    rows, lower, upper = [], [], []
    for mission in sorted(scenario.missions, key=lambda m: m.mission_id):
        rows.append(
            {
                i: 1.0
                for i, c in enumerate(candidates)
                if c.mission_id == mission.mission_id
            }
        )
        lower.append(1.0)
        upper.append(1.0)
    for constraint in scenario.incompatibilities:
        rows.append({index[oid]: 1.0 for oid in constraint.option_ids})
        lower.append(-np.inf)
        upper.append(1.0)
    for resource in sorted(scenario.resources, key=lambda r: r.resource_id):
        owned = [
            (i, d)
            for i, c in enumerate(candidates)
            for d in c.demands
            if d.resource_id == resource.resource_id and d.amount > 0
        ]
        if not owned or resource.capacity is None:
            continue
        capacity = _exact(resource.capacity)
        if resource.mode == "budget":
            groups = [[d for _, d in owned]]
            owners = {id(d): i for i, d in owned}
        else:
            owners = {id(d): i for i, d in owned}
            groups = [active for _, _, active in _active_sums([d for _, d in owned])]
        for active in groups:
            coefficients = {}
            for demand in active:
                i = owners[id(demand)]
                coefficients[i] = coefficients.get(i, Fraction(0)) + _exact(
                    demand.amount
                )
            if sum(coefficients.values(), Fraction(0)) <= capacity:
                continue  # Non-binding row omitted.
            rows.append({i: float(v) for i, v in coefficients.items()})
            lower.append(-np.inf)
            upper.append(float(capacity))
    return rows, lower, upper


def _plan(ids, scenario, digest):
    ids = tuple(sorted(ids))
    return Plan(
        plan_id="r10-"
        + hashlib.sha256(
            json.dumps([digest, ids, MILP_SOLVER_NAME]).encode()
        ).hexdigest(),
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=digest,
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=ids,
        execution_mode="classical",
        solver_name=MILP_SOLVER_NAME,
    )
