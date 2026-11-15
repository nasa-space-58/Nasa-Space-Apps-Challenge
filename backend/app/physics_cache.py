"""Bounded process-local reuse of identical deterministic descent calculations."""


class PhysicsCache:
    """Opt-in cache; terrain classification and risk aggregation always rerun."""

    def __init__(self, *, enabled=True, max_entries=128):
        raise NotImplementedError

    def plan(self, request, terrain_height_m):
        """Return an isolated nominal result keyed by resolved vehicle and inputs."""
        raise NotImplementedError

    def simulate(self, profile, site_radius_m, pdi_range_m, periapsis_speed_mps,
                 dt_s=0.1, record=True, approach_s=None, terminal_s=None):
        """Return an isolated trajectory; key includes every dynamics argument."""
        raise NotImplementedError

    def snapshot(self):
        """Return cumulative misses, hits and disjoint compute/overhead times."""
        raise NotImplementedError

