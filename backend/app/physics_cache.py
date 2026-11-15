"""Bounded process-local reuse of identical deterministic descent calculations.

Code/constants are immutable for a cache lifetime. Do not persist entries across
revisions. Every returned object is copied; terrain safety is never cached here.
"""

import json
from collections import OrderedDict
from copy import deepcopy
from time import perf_counter

from . import descent


class PhysicsCache:
    """Opt-in, single-worker cache; risk classification always runs again."""

    def __init__(self, *, enabled=True, max_entries=128):
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self.enabled = enabled
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._simulate = descent.simulate_powered_descent
        self._plan = descent.plan_descent
        self._stats = {
            kind: dict(hits=0, misses=0, compute_s=0.0, overhead_s=0.0)
            for kind in ("nominal", "dynamics")
        }

    def _lookup(self, kind, inputs, compute):
        started = perf_counter()
        stat = self._stats[kind]
        key = (kind, json.dumps(inputs, sort_keys=True, allow_nan=False))
        compute_s = 0.0
        try:
            if self.enabled and key in self._entries:
                stat["hits"] += 1
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key])
            stat["misses"] += 1
            mark = perf_counter()
            try:
                result = compute()
            finally:
                compute_s = perf_counter() - mark
                stat["compute_s"] += compute_s
            if self.enabled:
                self._entries[key] = deepcopy(result)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            return result
        finally:
            stat["overhead_s"] += perf_counter() - started - compute_s

    def plan(self, request, terrain_height_m):
        """Reuse a nominal plan only for identical inputs and resolved vehicle."""
        resolved = request.custom_profile
        if resolved is None:
            resolved = descent.load_profiles()[request.profile_id]
        return self._lookup(
            "nominal",
            [
                request.model_dump(mode="json"),
                terrain_height_m,
                resolved.model_dump(mode="json"),
            ],
            lambda: self._plan(request, terrain_height_m),
        )

    def simulate(
        self,
        profile,
        site_radius_m,
        pdi_range_m,
        periapsis_speed_mps,
        dt_s=0.1,
        record=True,
        approach_s=None,
        terminal_s=None,
    ):
        """Reuse the full trajectory keyed by all eight dynamics arguments."""
        args = (
            site_radius_m,
            pdi_range_m,
            periapsis_speed_mps,
            dt_s,
            record,
            approach_s,
            terminal_s,
        )
        return self._lookup(
            "dynamics",
            [profile.model_dump(mode="json"), *args],
            lambda: self._simulate(profile, *args),
        )

    def snapshot(self):
        """Return cumulative disjoint timings, call counts and current entries."""
        return deepcopy(self._stats) | {"entries": len(self._entries)}
