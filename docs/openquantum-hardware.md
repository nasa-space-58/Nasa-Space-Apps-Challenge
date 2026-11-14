# H7 — OpenQuantum hardware evidence

H7 adds an optional Qiskit/OpenQuantum experiment adapter outside the backend and ingestion runtimes. Delivery is only to `codex/quantum-hybrid`, without a merge into `tahakrcc` or `main`.

Test-first history: skeleton `c5de984`; frozen acceptance tests `db20168`, provider/export checks `45f4dc7`, and physical-index/idle-register checks `e85e5e7`, and exact live billing labels `1b48779`. No earlier frozen tests were changed.

## Reproduce locally

Use Python 3.12 in a separate environment. These dependencies are not added to a service image:

```sh
uv venv --python 3.12 .venv-h7
uv pip sync --python .venv-h7/bin/python --require-hashes quantum/requirements.lock
.venv-h7/bin/python -m ipykernel install --user --name lunar-h7 --display-name 'Lunar H7 (OpenQuantum)'
.venv-h7/bin/python -m pytest quantum/tests -q
.venv-h7/bin/ruff check --isolated quantum
```

Open `notebooks/H7_openquantum_hardware.ipynb` with that kernel. Its 12 code cells execute offline by default. The live switches are separate and false, so Run All cannot submit work. Do not put credentials in cell source, outputs, Git, command-line arguments or screenshots. The local connection notebook uses hidden `getpass` prompts and writes a file with mode 0600 under `~/.config/lunar-openquantum/`; this file is outside the repository. Other machines can use the official SDK environment-variable configuration.

## Scientific scope

The synthetic H6 recovery example links a physically screened landing decision to subsequent mission slots. H5 computes the fault checkpoint; H6 recomputes feasible trajectories, fuel bounds and timing dependencies. A compact two-qubit circuit represents four campaign assignments. H1 enumeration provides an exact reference, and every observed bitstring is decoded and validated against those original constraints without repair.

Local parameter training uses two QAOA layers, 160 evaluations and seed 17. Training-seed sensitivity uses 17, 23 and 41. The selected hardware circuit is fixed before observing hardware output. The experiment does not use quantum hardware for parameter training. Qiskit's independent statevector matches the existing NumPy implementation at tolerance 1e-11 for one through three layers, including the separate six-qubit mapping test.

The logical two-qubit circuit has 6 RZ, 4 CX, 4 RX, 2 H and 2 measurement operations; logical depth is 12. Hardware targeting can add gates or change depth. Export retains physical qubit indices and measurement-to-classical-bit mapping, removing only trailing idle target-register qubits. OpenQuantum's current Rigetti target advertised 108 qubits but preparation rejected a 108-wide register with a maximum of 107; the actual circuit used qubits 0 and 1. Removing the unused tail solves that register mismatch without renumbering active qubits. A dedicated test preserves non-contiguous physical indices and global phase. The official plugin validates the circuit, and the saved QASM hash covers SDK-normalized upload bytes, including its gate-definition stripping behavior.

The noise experiment assumes 0.1% single-qubit depolarization, 1% two-qubit depolarization and symmetric 2% readout error. These are illustrative assumptions, not device calibration. Across three 1024-shot sampling seeds, ideal optimal-outcome frequencies were 96.68–97.36%; synthetic-noise frequencies were 89.45–90.63%. Every local run observed a feasible exact-optimum plan (gap zero). Raw counts, shot-based Wilson 95% intervals and timings are in the notebook. Parameter-seed sensitivity gives different ideal probabilities; this small benchmark is not a guarantee of general optimizer performance.

A higher optimal frequency than uniform sampling is not end-to-end quantum advantage. Exact enumeration already solves these four assignments cheaply. Training, physical screening, encoding, validation, network/queue time and billing must all be included in a larger comparison. Wilson intervals quantify sampling uncertainty only, not drift or model error.

## Connection and cost boundary

On 2026-11-14, the authenticated portal showed 25 Spark credits, zero Full credits, Auto Refill off and Spark expiry 2027-02-12 12:27 (portal display). A subsequent authenticated SDK query independently confirmed the 25/0 balance. Credentials remain local and are not part of this delivery.

The live device query distinguished actual QPU entries from simulators. In particular `ibm:qiskit` is an Aer simulator, not an IBM QPU. `rigetti:cepheus-1-108q` and `ionq:forte-1` were online and accepting work; several other QPUs were unavailable or not accepting submissions. Availability must be checked again per run.

`OpenQuantumGateway` separates preparation, submission and collection. The high-level SDK `.run()` is deliberately not used because it combines submission and spending. Preparation retrieves a quote without creating a running job. Submission refetches the quote and balance and requires:

- a fresh, locally persisted manifest with matching organization, backend identity, problem fingerprint, QASM hash and shot count;
- an online, accepting QPU, explicitly Public execution and Standard priority;
- a finite nonnegative quote covered by Spark and the chosen credit cap;
- zero Full balance and explicit confirmation that Auto Refill is off.

The first live experiment is one 256-shot trial with a maximum of 2 Spark credits. The SDK has no atomic Spark-only/max-price submission parameter. Therefore the adapter conservatively rejects nonzero paid balances and relies on the checked billing setting. Use a single controlling caller and no concurrent account spenders. The UI setting is an explicit operator attestation, not an API-verified invariant. The limit is per trial; do not generate new trial names or delete receipts to bypass a reviewed experiment budget. Paid credit use needs separate user approval.

An exclusive durable claim is written before `create_job`. A network timeout leaves an uncertain claim, blocking automatic duplicate submissions even after restarting Python. Reconcile ambiguous submissions with the provider before any further action. A successful receipt binds collection to the original preparation, execution plan, priority and input fingerprint. Collection does not submit or retry jobs. Signed input/output URLs and secrets are excluded from reports.

## Landing acceptance boundary

`resolve_hardware_recovery` rebuilds the current physical/campaign problem, computes a fresh classical fallback and validates raw observed counts. Expired, stale, malformed, simulated or inferior evidence retains the current classical result. The supplied UTC timestamp is an offline decision snapshot, not a hard real-time execution clock. Cloud queue latency does not advance the synthetic flight automatically. This is not an onboard controller, continuous collision certification or latency-compensated live descent.

A result labeled hardware is tied to a QPU selected from the provider inventory and a matching provider job receipt. It is not independent cryptographic proof of physical execution. Unfinished or failed jobs are not represented as hardware measurements.

## Verification

- 60 H7 tests passed, including independent unitaries, bit order, malformed counts, free-credit gating, identity checks, uncertain submission, duplicate prevention, late/stale recovery and register trimming.
- 827 backend and 112 ingestion tests passed. The existing Starlette/AnyIO deprecation warning remains.
- H7, backend/shared and ingestion Ruff checks passed. H7 uses the same Ruff 0.14.5 and pytest 8.4.2 versions as the backend.
- The 12-cell H7 notebook executed without errors with live actions disabled. The completed hardware result was loaded and replayed without any additional provider submission.
- Frozen tests remain unchanged. No frontend code or tests are involved.

## Sources

- [OpenQuantum Qiskit plugin](https://docs.openquantum.com/qiskit-plugin/overview/)
- [SDK authentication](https://docs.openquantum.com/core-sdk/authentication/)
- [Job preparation and submission](https://docs.openquantum.com/core-sdk/job-submission/)
- [Credit behavior](https://docs.openquantum.com/concepts/credits/)
- [Backend types](https://docs.openquantum.com/concepts/backends/)
- [Published pricing](https://www.openquantum.com/pricing) — actual preparation quote and account debit take precedence.
- Pinned installed SDK 1.2.1 and Qiskit plugin 1.1.2 source for request models, export normalization and lifecycle behavior.

## Provider attribution

Provider execution uses the Open Quantum Public Plan. Infrastructure attribution follows the [Open Quantum citation guidance](https://www.openquantum.com/citation): Kuhn, R., Armbruster, O., and Wold, B. (2026), *Open Quantum: An open infrastructure layer for quantum computing*, Zenodo, [doi:10.5281/zenodo.22103261](https://doi.org/10.5281/zenodo.22103261). This acknowledges infrastructure and does not imply provider endorsement of the results.

## Completed hardware experiment — 2026-11-14

One provider QPU job completed successfully. This is a bounded single-trial result, not a scaling or advantage benchmark.

| Item | Observed value |
| --- | --- |
| Provider / device | Open Quantum / Rigetti `rigetti:cepheus-1-108q` |
| Job ID | `4c0adc79-6bd3-4578-9147-bbf8ab7b2209` |
| Circuit / measurements | 2 active qubits, 2 QAOA layers, 256 shots |
| Training | local noiseless seed 17, 160 evaluations |
| Submission UTC | 2026-11-14T20:10:08.326044Z |
| Completion first observed UTC | 2026-11-14T20:12:07.120647Z |
| Observed submission-to-completion interval | 118.795 s, including network/queue/polling; device execution time is not isolated |
| Quote and observed debit | 1 Spark credit; balance 25 → 24 Spark, Full remained 0 |
| Optimal samples | 200 / 256 = 78.125% |
| Optimal-frequency Wilson 95% interval | 72.666%–82.753% |
| Feasible samples | 247 / 256 = 96.484% |
| Best observed plan gap to exact reference | 0 |
| Repair | none |
| Problem fingerprint | `176fe28ceab9f98edef3ddc8d64175e0574d3459de96242a3705275cfbdf6bf3` |
| Uploaded QASM SHA-256 | `54275a0b2994acac9eb46f152aa9215d9ce12be4c922e24c4022dbb49c72298d` |

Raw Qiskit bitstrings: `00: 18`, `01: 29`, `10: 9`, `11: 200`. The 9 samples at `10` violate the original scheduling constraints and are excluded from candidate acceptance, not repaired or hidden. The optimum `11` selects `land-remote` and `follow-late`. Actual hardware optimal frequency was lower than both the ideal circuit and the illustrative noise model; the synthetic noise model is therefore not a calibrated prediction of this device.

The 12-cell notebook replays the completed evidence at the matching post-fault checkpoint. Before the fault, the classical plan selects `land-primary` and `follow-early`; after the fault the validated hardware proposal selects `land-remote` and `follow-late`. The synthetic vehicle lands at UTC 2030-01-01T00:00:34.999982Z with 76.814439 kg propellant remaining, and fuel accounting balances. A deliberately expired acceptance deadline keeps the freshly computed classical fallback. No additional cloud job is submitted during this replay. The cloud result is reused offline; it does not imply the vehicle could have waited the observed 119 seconds in flight.

This completes the bounded H7 demonstration. Repeated device trials, calibrated noise, larger scaling cases and live latency compensation remain later work. Local private receipts retain the request and circuit for reconciliation; public notebook outputs contain only sanitized result metadata and raw counts.
