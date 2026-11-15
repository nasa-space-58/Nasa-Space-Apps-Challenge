"""Offline C3 stress-discovery experiment; no runtime or provider integration."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import minimize
from scipy.stats import qmc

from .mission_resilience import MissionConfig, MissionEvent, MissionScenario, replay_mission

FAMILIES = {
    "shade_link": "development",
    "thermal_radio": "development",
    "energy_camera": "validation",
    "link_thermal": "validation",
    "heater_shade": "test",
    "radio_energy": "test",
}
METHODS = ("random", "sobol", "cross_entropy", "annealing", "kernel", "qcbm", "qcbm_inverse_cdf")
CLASSICAL = METHODS[:5]
QUBITS = 6


@dataclass(frozen=True)
class Budget:
    calls: int = 16
    seconds: float | None = None

    def __post_init__(self):
        if type(self.calls) is not int or not 2 <= self.calls <= 65:
            raise ValueError("Replay calls must be an integer in [2, 65]")
        if self.seconds is not None and (not math.isfinite(self.seconds) or self.seconds <= 0):
            raise ValueError("Wall time must be positive and finite")


def make_scenario(family: str, index: int | None):
    """None constructs the no-fault qualification case; index encodes six bits."""
    if family not in FAMILIES or index is not None and (type(index) is not int or not 0 <= index < 64):
        raise ValueError("Unknown family or six-bit candidate")
    # The terminal schema is required by the existing replay, but unused here.
    path = Path(__file__).resolve().parents[2] / "shared/examples/recovery/combined_recovery.json"
    base = json.loads(path.read_text())
    config = MissionConfig.model_validate(dict(
        scenario_id=f"c3-{family}", source=base['uncertainty']['source'],
        initial_phase='surface', terminal=base['initial_request'], targets={'primary': 0.0},
        routes=[dict(route_id='unused', duration_s=10.0, fuel_kg=2.0, min_thrust_n=1000.0)],
        tasks=[dict(task_id='science', duration_s=12, power_w=60.0, priority=3,
                    hardware='camera', deadline_met_s=30),
               dict(task_id='transmit', duration_s=8, power_w=30.0, priority=2,
                    hardware='radio', requires_link=True, deadline_met_s=38)],
        horizon_s=44, initial_energy_wh=1.0, battery_capacity_wh=2.0,
        reserve_energy_wh=0.1, initial_temperature_c=12.0,
        temperature_limits_c=(0.0, 30.0), thermal_drift_cps=0.0,
        solar_power_w=12.0, planning_delay_s=1,
    ))
    if index is None:
        return MissionScenario(config=config)
    bits = [(index >> q) & 1 for q in range(6)]
    onset, gap, duration = 2 + 6*bits[0], 2 + 4*bits[1], 6 + 12*bits[2]
    high, delay, offset = bits[3], 3*bits[4], 5*bits[5]
    events = []

    def add(at, kind, value, subject='vehicle'):
        events.append(MissionEvent(event_id=f'fault-{len(events)}', phase='surface',
                                   at_s=at, kind=kind, value=float(value), subject=subject,
                                   notice_delay_s=delay))

    second, restored = onset+gap, onset+gap+duration+offset
    if family == 'shade_link':
        add(onset, 'solar', 0 if high else 6)
        add(second, 'link', 0)
        add(restored, 'link', 1)
        add(restored+1, 'solar', 12)
    elif family == 'thermal_radio':
        add(onset, 'thermal', 0.85 if high else 0.25)
        add(second, 'hardware', 0, 'radio')
        add(restored, 'hardware', 1, 'radio')
        add(restored+1, 'thermal', 0)
    elif family == 'energy_camera':
        add(onset, 'energy_loss', 0.65 if high else 0.25)
        add(second, 'hardware', 0, 'camera')
        add(restored, 'hardware', 1, 'camera')
    elif family == 'link_thermal':
        add(onset, 'link', 0)
        add(second, 'thermal', 0.85 if high else 0.25)
        add(restored, 'thermal', 0)
        add(restored+1, 'link', 1)
    elif family == 'heater_shade':
        add(onset, 'hardware', 0, 'heater')
        add(second, 'thermal', -0.9 if high else -0.3)
        add(second+1, 'solar', 0)
        add(restored, 'hardware', 1, 'heater')
        add(restored+1, 'thermal', 0)
        add(restored+2, 'solar', 12)
    elif family == 'radio_energy':
        add(onset, 'hardware', 0, 'radio')
        add(second, 'energy_loss', 0.65 if high else 0.25)
        add(restored, 'hardware', 1, 'radio')
    return MissionScenario(config=config, events=tuple(events))


def summarize_replay(result: dict) -> dict:
    config = result['inputs']['config']
    unfinished = sorted(t['task_id'] for t in result['unfinished_tasks'])
    priorities = {t['task_id']: t['priority'] for t in config['tasks']}
    lost = sum(priorities[t] for t in unfinished) / sum(priorities.values())
    low, high = config['temperature_limits_c']
    breached = any(
        f['truth']['energy_wh'] < config['reserve_energy_wh']-1e-9
        or f['truth']['fuel_kg'] < config['terminal']['reserve_kg']-1e-9
        or not low-1e-9 <= f['truth']['temperature_c'] <= high+1e-9
        for f in result['frames']
    )
    failed = result['status'] != 'completed'
    key = [result['status'], sorted(set(result['reasons'])), unfinished]
    coverage = sorted({d['selected']['id'] for d in result['decisions']
                       if d.get('status') == 'accepted' and d.get('selected')})
    return dict(failed=failed, cluster=json.dumps(key, separators=(',', ':')) if failed else None,
                score=lost + 2*int(breached), unfinished_priority_fraction=lost,
                resource_breach=breached, action_coverage=coverage, status=result['status'])


def born_probabilities(angles: np.ndarray) -> np.ndarray:
    """Little endian RY layer, nearest-neighbor CX chain, RY layer."""
    angles = np.asarray(angles, dtype=float)
    if angles.shape != (12,) or not np.all(np.isfinite(angles)):
        raise ValueError('Exactly twelve finite RY angles are required')
    state = np.zeros(64)
    state[0] = 1
    indices = np.arange(64)

    def rotate(q, angle):
        low = indices[(indices & (1 << q)) == 0]
        high = low | (1 << q)
        a, b = state[low].copy(), state[high].copy()
        cosine, sine = np.cos(angle/2), np.sin(angle/2)
        state[low], state[high] = cosine*a-sine*b, sine*a+cosine*b

    for q in range(6):
        rotate(q, angles[q])
    for q in range(5):
        permutation = indices ^ (((indices >> q) & 1) << (q+1))
        state = state[permutation]
    for q in range(6):
        rotate(q, angles[6+q])
    probabilities = state**2
    return probabilities / probabilities.sum()


def fit_born(history: list[dict], seed: int) -> dict:
    if not history:
        raise ValueError('Training requires evaluated observations')
    started = perf_counter()
    elites = sorted(history, key=lambda h: (-h['score'], h['index']))[:max(1, math.ceil(len(history)/4))]
    ids = np.array([h['index'] for h in elites])
    if any(not 0 <= h['index'] < 64 or not math.isfinite(h['score']) for h in history):
        raise ValueError('Invalid observed training data')
    target = np.full(64, 0.05/64)
    for index in ids:
        target[index] += 0.95/len(ids)
    simulation_s, evaluations = 0.0, 0

    def evaluate(angles):
        nonlocal simulation_s, evaluations
        start = perf_counter()
        p = born_probabilities(angles)
        simulation_s += perf_counter()-start
        evaluations += 1
        return p

    def objective(angles):
        return -float(target @ np.log(np.maximum(evaluate(angles), 1e-12)))

    rng = np.random.default_rng(seed)
    initial = np.r_[np.full(6, np.pi/2), np.zeros(6)] + rng.normal(0, 0.05, 12)
    result = minimize(objective, initial, method='COBYLA', options={'maxiter': 64, 'rhobeg': 0.5})
    p = evaluate(result.x)
    elapsed = perf_counter()-started
    return dict(probabilities=p, angles=result.x, training_indices=ids.tolist(), evaluations=evaluations,
                simulation_s=simulation_s, training_s=max(0, elapsed-simulation_s))


def _sobol_order(seed):
    points = qmc.Sobol(6, scramble=True, seed=seed).random_base2(6)
    return ((points >= 0.5).astype(int) @ (1 << np.arange(6))).tolist()


def _classical_probabilities(method, history):
    bits = (np.arange(64)[:, None] >> np.arange(6)) & 1
    elites = sorted(history, key=lambda h: (-h['score'], h['index']))[:max(1, math.ceil(len(history)/4))]
    elite_bits = bits[[h['index'] for h in elites]]
    if method == 'cross_entropy':
        mean = (elite_bits.sum(axis=0) + 1)/(len(elites)+2)
        p = np.prod(np.where(bits, mean, 1-mean), axis=1)
    else:  # Correlated classical mixture of Hamming kernels, not factorized CE.
        distances = np.sum(bits[:, None, :] != elite_bits[None, :, :], axis=2)
        p = np.mean(0.2**distances * 0.8**(6-distances), axis=1)
    return p/p.sum()


def run_search(family: str, method: str, seed: int, budget: Budget, *, oracle=None, clock=None) -> dict:
    if family not in FAMILIES or method not in METHODS or type(seed) is not int or seed < 0:
        raise ValueError('Unknown family/method or invalid seed')
    oracle, clock = oracle or replay_mission, clock or perf_counter
    start = clock()
    deadline = start + budget.seconds if budget.seconds is not None else float('inf')
    timing = dict(preparation=0.0, replay=0.0, training=0.0, simulation=0.0, sampling=0.0, postprocess=0.0)
    history, clusters, coverage, training_log = [], set(), set(), []
    calls, late, shots, fallbacks, fit_evaluations = 0, 0, 0, 0, 0
    qualified = False

    def evaluate(index):
        nonlocal calls
        t = clock()
        scenario = make_scenario(family, index)
        timing['preparation'] += clock()-t
        if clock() >= deadline:
            return None
        t = clock()
        result = oracle(scenario)
        calls += 1
        timing['replay'] += clock()-t
        t = clock()
        outcome = summarize_replay(result)
        outcome['input_sha256'] = hashlib.sha256(scenario.model_dump_json().encode()).hexdigest()
        timing['postprocess'] += clock()-t
        return outcome

    baseline = evaluate(None)
    if baseline is not None and clock() <= deadline and not baseline['failed']:
        qualified = True
    elif baseline is not None and clock() > deadline:
        late += 1
    rng = np.random.default_rng(seed)
    order = _sobol_order(seed)
    random_order = rng.permutation(64).tolist()
    probabilities = np.full(64, 1/64)
    anneal_current = None
    anneal_score = 0.0
    while qualified and calls < budget.calls and clock() < deadline and len(history) < 64:
        used = {h['index'] for h in history}
        remaining = [i for i in range(64) if i not in used]
        n = len(history)
        if n >= 4 and n % 4 == 0:
            if method.startswith('qcbm'):
                # Fit receives only already purchased observations, never the replay oracle.
                fitted = fit_born(history, seed+n)
                probabilities = fitted['probabilities']
                timing['simulation'] += fitted['simulation_s']
                timing['training'] += fitted['training_s']
                fit_evaluations += fitted['evaluations']
                training_log.append(dict(observed_indices=sorted(used), training_indices=fitted['training_indices'],
                                         angles=fitted['angles'].tolist(), evaluations=fitted['evaluations']))
            elif method in ('cross_entropy', 'kernel'):
                t = clock()
                probabilities = _classical_probabilities(method, history)
                timing['training'] += clock()-t
        if clock() >= deadline:
            break
        t = clock()
        if n < 4 or method == 'sobol':
            index = next(i for i in order if i not in used)
        elif method == 'random':
            index = next(i for i in random_order if i not in used)
        elif method == 'annealing':
            if anneal_current is None or n % 8 == 0:
                anneal_current = int(rng.choice([h['index'] for h in history]))
                anneal_score = next(h['score'] for h in history if h['index'] == anneal_current)
            neighbors = [anneal_current ^ (1 << q) for q in range(6)]
            neighbors = [i for i in neighbors if i not in used]
            index = int(rng.choice(neighbors or remaining))
        else:
            # Equal 128 draws and dedup policy for learned classical/quantum models.
            if method == 'qcbm_inverse_cdf':
                draws = np.searchsorted(np.cumsum(probabilities), rng.random(128), side='right')
                draws = np.minimum(draws, 63)
            else:
                draws = rng.choice(64, size=128, p=probabilities)
            shots += 128
            index = next((int(i) for i in draws if int(i) not in used), None)
            if index is None:
                index = int(rng.choice(remaining))
                fallbacks += 1
        timing['sampling'] += clock()-t
        if clock() >= deadline:
            break
        outcome = evaluate(index)
        if outcome is None:
            break
        if clock() > deadline:
            late += 1
            break
        if method == 'annealing':
            temperature = max(0.05, 1-n/64)
            if anneal_current is None or rng.random() < math.exp(min(0, (outcome['score']-anneal_score)/temperature)):
                anneal_current, anneal_score = index, outcome['score']
        history.append(dict(index=index, calls_at_discovery=calls, **outcome))
        if outcome['cluster']:
            clusters.add(outcome['cluster'])
        coverage.update(outcome['action_coverage'])
    total = clock()-start
    timing['other'] = max(0, total-sum(timing.values()))
    timing['total'] = total
    return dict(family=family, split=FAMILIES[family], method=method, seed=seed,
                budget=dict(calls=budget.calls, seconds=budget.seconds), qualified=qualified,
                baseline=baseline, replay_calls=calls, observations=history, clusters=sorted(clusters),
                coverage=sorted(coverage), unique_failing_inputs=sum(h['failed'] for h in history),
                first_failure_call=next((h['calls_at_discovery'] for h in history if h['failed']), None),
                resource_breach_inputs=sum(h['resource_breach'] for h in history),
                late_results=late, overrun_s=max(0, total-budget.seconds) if budget.seconds is not None else 0,
                timing_s=timing, training_log=training_log, statevector_evaluations=fit_evaluations,
                simulated_draws=shots, uniform_fallbacks=fallbacks, provider_calls=0)


def paired_summary(runs: list[dict]) -> dict:
    pairs = {}
    for row in runs:
        key = (row['family'], row['seed'])
        if row['method'] in pairs.setdefault(key, {}):
            raise ValueError('Duplicate paired method')
        pairs[key][row['method']] = len(row['clusters'])
    if not pairs or any(not set((*CLASSICAL, 'qcbm')).issubset(p) for p in pairs.values()):
        raise ValueError('Complete quantum and classical pairs are required')
    groups = {}
    for (family, seed), scores in pairs.items():
        groups.setdefault(family, []).append(scores['qcbm']-max(scores[m] for m in CLASSICAL))
    families = sorted(groups)
    means = [np.mean(groups[f]) for f in families]
    rng = np.random.default_rng(20261115)
    bootstrap = []
    for _ in range(2000):
        selected = rng.choice(families, size=len(families), replace=True)
        bootstrap.append(np.mean([np.mean(rng.choice(groups[f], size=len(groups[f]), replace=True)) for f in selected]))
    interval = np.quantile(bootstrap, [0.025, 0.975]).tolist()
    gain = float(np.mean(means))
    return dict(mean_gain=gain, interval_95=interval, root_count=len(groups), paired_count=len(pairs),
                screen_passed=bool(gain >= 1 and interval[0] > 0),
                interpretation='Exploratory two-root uncertainty; not a quantum advantage claim')


def campaign(*, seeds=(11, 29, 47, 71, 101), calls=16, seconds=0.35) -> dict:
    Budget(calls, seconds)
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError('Unique nonempty seeds required')
    started = perf_counter()
    runs = []
    for mode, budget in (('calls', Budget(calls)), ('time', Budget(65, seconds))):
        for family in FAMILIES:
            for seed in seeds:
                # Order changes per root/seed; timing competitors run sequentially.
                order_seed = seed + list(FAMILIES).index(family)*1009
                methods = np.random.default_rng(order_seed).permutation(METHODS)
                for method in methods:
                    row = run_search(family, str(method), seed, budget)
                    row['mode'] = mode
                    runs.append(row)
    search_s = perf_counter()-started
    # Ground truth is computed only after every search has ended, in fresh calls.
    audit_started = perf_counter()
    audits = []
    for family in FAMILIES:
        base = summarize_replay(replay_mission(make_scenario(family, None)))
        outcomes = [dict(index=i, **summarize_replay(replay_mission(make_scenario(family, i)))) for i in range(64)]
        clusters = sorted({o['cluster'] for o in outcomes if o['cluster']})
        audits.append(dict(family=family, qualified=not base['failed'], replay_calls=65,
                           clusters=clusters, failing_inputs=sum(o['failed'] for o in outcomes), outcomes=outcomes))
    audit_s = perf_counter()-audit_started
    summaries = {mode: {split: paired_summary([r for r in runs if r['mode'] == mode and r['split'] == split])
                        for split in ('development', 'validation', 'test')} for mode in ('calls', 'time')}
    passed = all(summaries[m][s]['screen_passed'] for m in summaries for s in ('validation', 'test'))
    qualified = all(r['qualified'] for r in runs) and all(a['qualified'] for a in audits)
    verdict = 'EXPLORATORY_SIGNAL' if passed and qualified else 'NO_GO'
    return dict(protocol='C3-v1', seeds=list(seeds), qubits=6, parameters=12, cx_gates=5,
                runs=runs, audits=audits, summaries=summaries, decision=verdict,
                decision_reason='Both held-out splits and both budgets must pass; baseline qualification required',
                search_s=search_s, exhaustive_audit_s=audit_s, total_s=perf_counter()-started,
                replay_calls=sum(r['replay_calls'] for r in runs)+sum(a['replay_calls'] for a in audits),
                provider_calls=0, credits_spent=0,
                limitations=['Synthetic surface accounting only; no descent/dynamics advantage established',
                             'Two final-test roots; all settings fixed, no tuning or external blind dataset',
                             'Classically simulated six-qubit distribution; no hardware or quantum speedup evidence',
                             'Shortfalls include safe deferral and are not necessarily implementation bugs'])
