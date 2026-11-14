# H10 — Measured problem scaling

H10 compares two classical algorithms on the same seeded, synthetic capacitated slot-assignment problem. Each of m missions selects one of o slots; each slot has capacity ceil(m/o). Preferences are integer draws from NumPy PCG64, with a recorded seed. This is a structured scheduling benchmark, not a claim about every mission-planning problem.

## Measurements

`shared/examples/scaling/report.json` contains a SHA-256 envelope, the complete generated inputs and all repeated results. The recorded experiment uses 2 or 4 options and 3, 6, 10, 16 or 24 missions, three repeats, at most 65,536 enumeration evaluations and a 0.5-second limit per solver run. Execution order alternates between enumeration and MILP. Medians summarize all repeats; no unfavorable measurement is dropped.

The exact enumerator checks complete assignments with an independent capacity validator. It reports `limit_reached` if either its count or time budget expires; an incumbent is not certified optimal. Timing includes enumeration and validation. The time boundary is checked between assignments, so a run can exceed the nominal budget by one evaluation.

The optional SciPy/HiGHS MILP uses one binary variable per mission-slot choice, one equality per mission and capacity inequalities per slot. Every incumbent is checked for integrality, one-choice semantics and original capacity feasibility. It requests zero relative MIP gap but reports solver termination status separately. Timing includes matrix construction, solver execution and validation; import/environment startup is excluded. Solver time limits do not include all Python overhead. No CP-SAT measurement is claimed. See [SciPy MILP documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html).

SciPy is already installed and pinned transitively in the optional Qiskit environment. It is not added to backend or ingestion images. Measurements run locally; the web API only serves a fixed recorded report, with no arbitrary benchmark submission or provider job.

## Reading the charts

The horizontal axis is mission count. Vertical axes are logarithmic. Separate series identify option count and algorithm. White-ring points reached the enumeration budget: their heights are elapsed partial-search times, not complete-solve estimates. The table includes every repeat's status.

Candidate count is o^m. Compact register size is m*ceil(log2(o)); one-hot variable count is m*o. For non-power-of-two options, compact registers include unused states. These are encoding-size calculations, not executed circuits, and exclude routing, extra qubits, preparation, variational optimization and error correction. Fewer logical qubits can still require deep, high-locality circuits, as H9 demonstrated.

Dashed extrapolations use the first fully enumerated measured case as their disclosed anchor, multiplying its median cost per assignment by a larger Cartesian count. They are estimates, not observations or lower bounds. They do not predict MILP runtime. No measured point is silently extended into a claimed solution time.

MILP found optimal solutions for every recorded case, including large cases where enumeration stopped. Thus the benchmark shows the weakness of exhaustive enumeration, not a demonstrated classical limit or quantum advantage. Three-mission cases remain well within classical capability. Different conflict structures, scales, machines and budgets need separate measurements.

## Reproduction and checks

Open `notebooks/H10_problem_scaling.ipynb` with the existing Lunar H7 kernel. Its seven code cells inspect recorded inputs/results, independently compare both methods, plot the evidence and run a small fresh benchmark. Run All makes no network or provider calls and spends no credits.

New tests: skeleton `0e1fbdc`; frozen algorithm tests `64eeb3c`; report API skeleton `f54e930`; frozen report/API tests `2b884e4`. Algorithm tests cover an independent combinatorial oracle, determinism, invalid budgets, non-power-of-two register counts, partial-search status and exact/MILP agreement. API tests cover checksum failures, fixed report selection and display coordinates. Frontend verification uses TypeScript/Vite only, per repository instructions.

The backend serves `/research/scaling`; the interface is at `#/scaling`. Raw observations remain available through the JSON export. Final combined verification is recorded with H11 delivery.

Final combined verification: **1178 backend, 191 ingestion and 91 optional quantum tests passed**, Ruff and TypeScript/Vite passed, and both new notebooks executed (17 code cells total). The measured host was Apple M5, recorded alongside Python/NumPy/SciPy and OS metadata. See [H11 combined verification](relay-orbit-selection.md#final-combined-verification).
