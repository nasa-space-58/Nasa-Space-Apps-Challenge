# Mission resilience: acceptance protocol

Scope authorized on the existing `codex/unified-quantum-stage1` branch. No merge,
production deployment, changes to shared running services, or quantum submission.
C1/C2 evidence is immutable. Existing H6 notebook edits are excluded.

## Ordered acceptance

1. Strict immutable scenario and planner-view contracts. Planner receives no
   future event schedule, simulator truth or undelivered fault notice. Conflicting
   navigation sensors expand uncertainty and inhibit uncertified landing choices.
2. Deterministic offline replay with launch, cruise, descent and surface phases.
   Simulation MET (seconds from explicit UTC epoch) advances during declared
   planning latency. Revisions reject obsolete proposals; accepted choices are
   independently screened at the current state. No negative/clamped-away fuel
   or battery deficit is treated as success. No feasible choice is an explicit
   failure of the declared candidate set, not a fabricated safe hover or abort.
3. Landing uses existing H4 planar integration/H5 two-resolution checks, current
   propellant/thrust/terrain exclusions, and explicit navigation envelope probes.
   Surface uses finite energy balance, temperature limits, equipment/link gates,
   prioritized interruptible tasks and finite deadlines. Every elimination has
   a reason. Defer/cancel work and propagate stage delays/resources forward.
4. Launch windows/weather and cruise routes are **synthetic accounting models**,
   not launch dynamics or orbital propagation. Cruise-to-descent geometry is a
   declared terminal-state handoff; fuel/energy/time are carried across, never
   replenished. Thermal behavior is a declared linear model, not flight hardware.
5. Bounded durable offline replay jobs: idempotent input-bound request IDs,
   cancellation, deadline before/after computation, interrupted-job fencing,
   isolated SQLite state. No automated retry or quantum backend access.
6. API/UI expose assumptions, phase timeline, observed events vs post-hoc truth,
   estimated resources, uncertainty, candidate rejection reasons, validity
   conditions, obsolete decisions and replan history. Reproducible example
   scenarios and editable scenario inputs; no claim to cover all space hazards.
7. Frozen backend tests: sequential failures, second fallback failure, fuel/energy
   exhaustion, sensor disagreement, communications restoration, no future leak,
   repeat/cancel/timeout/stale results, invalid input and stage transitions.
   Then real HTTP + browser workflow checks, regression, lint/typecheck/build.

The newest explicit task asks for UI end-to-end checks and screen verification;
that overrides the older no-frontend-check/screenshots restriction for this task.
No production telemetry, actuator interface, validated probability model,
continuous terrain proof, or flight certification is in scope. Simulation-time
planning delay is user-declared, not inferred from workstation wall time.
